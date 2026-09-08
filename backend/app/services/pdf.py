"""HTML → PDF rendering via a shared headless Chromium (Playwright, async).

The generic expediente template (:mod:`app.services.pdf_templates`) produces a
self-contained HTML string — fonts and logos embedded as ``data:`` URIs, no
runtime network dependency — which this module renders to A4 PDF bytes.

Design constraints (spec §D):

- **Async Playwright** (``playwright.async_api``): ``sync_playwright`` blocks and
  raises inside the FastAPI event loop.
- **One shared ``Browser``** per process, launched lazily by :func:`ensure_browser`
  and also from the app lifespan (``main.py``). Lazy-launch-on-first-render is the
  fallback so the app still boots where Chromium isn't installed (tests/dev).
- A missing ``playwright`` import (or a launch/render failure) raises
  :class:`PDFGenerationError`, never a raw ``ImportError`` at request time — the
  router maps it to 502 (mirrors ``packs.PackGenerationError``).
- Provisioning is a NEW prerequisite: ``playwright install chromium`` must have
  run in the environment (documented in ``docs/deployment.md``).
"""
from __future__ import annotations

import asyncio
from typing import Any

__all__ = ["PDFGenerationError", "ensure_browser", "close_browser", "render_html_to_pdf"]


class PDFGenerationError(Exception):
    """Raised on any HTML→PDF rendering failure (router maps to 502)."""


# Module-level shared browser + a lock so concurrent first-renders launch once.
_browser: Any = None
_playwright: Any = None
_lock: asyncio.Lock | None = None

_LAUNCH_ARGS = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def ensure_browser() -> Any:
    """Return the shared Chromium ``Browser``, launching it once if needed.

    Safe to call from the app lifespan (eager warm-up) and from the first
    render (lazy fallback). Raises :class:`PDFGenerationError` if Playwright or
    Chromium is unavailable.
    """
    global _browser, _playwright
    if _browser is not None:
        return _browser
    async with _get_lock():
        if _browser is not None:  # another coroutine won the race
            return _browser
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise PDFGenerationError(
                "Generación de PDF no disponible: falta 'playwright'. "
                "Instale 'playwright' y ejecute 'playwright install chromium'."
            ) from exc
        try:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=True, args=_LAUNCH_ARGS
            )
        except Exception as exc:  # noqa: BLE001
            # Reset partial state so a later call can retry cleanly.
            await _reset_after_failure()
            raise PDFGenerationError(
                "No se pudo iniciar Chromium para generar el PDF. "
                "Verifique 'playwright install chromium'."
            ) from exc
    return _browser


async def _reset_after_failure() -> None:
    global _browser, _playwright
    try:
        if _browser is not None:
            await _browser.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        if _playwright is not None:
            await _playwright.stop()
    except Exception:  # noqa: BLE001
        pass
    _browser = None
    _playwright = None


async def close_browser() -> None:
    """Close the shared browser at app shutdown. Idempotent, never raises."""
    global _browser, _playwright
    try:
        if _browser is not None:
            await _browser.close()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _browser = None
    try:
        if _playwright is not None:
            await _playwright.stop()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _playwright = None


async def render_html_to_pdf(html: str) -> bytes:
    """Render a self-contained HTML string to A4 PDF bytes.

    Raises :class:`PDFGenerationError` on any failure.
    """
    browser = await ensure_browser()
    page = None
    try:
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        # Fonts are embedded as data: URIs so this resolves ~instantly, but the
        # explicit wait keeps text metrics stable before paint.
        await page.wait_for_function(
            "document.fonts.ready.then(()=>true)", timeout=4500
        )
        await page.wait_for_timeout(300)
        await page.emulate_media(media="print")
        data = await page.pdf(
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        return data
    except PDFGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PDFGenerationError(f"No se pudo generar el PDF: {exc}") from exc
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
