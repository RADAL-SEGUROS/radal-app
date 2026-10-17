"""Lane B2 job bodies: ``fx.daily``, ``collection.clock``, ``notifications.expire``.

All three are ``day`` jobs (one run per America/Santiago day, idempotent per
slot through :func:`app.jobs.trigger_job`). ``now`` is injected; nothing here
reads the wall clock.

* ``fx.daily`` — GET ``settings.FX_SOURCE_URL`` (mindicador.cl JSON) with a
  timeout, :func:`app.services.fx.parse_mindicador` →
  :func:`app.services.fx.upsert_rates`. A fetch/parse failure raises
  :class:`FxFetchError`; the runner records the slot ``failed`` with the error
  (never escapes to the caller) and a failed slot may be re-triggered the same
  day. The HTTP call lives in :func:`fetch_fx_payload` so tests replace it.
* ``collection.clock`` — :func:`app.services.collection.clock.run_collection_clock`.
* ``notifications.expire`` — :func:`app.services.notifications.expire_fyi`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs import register_job
from app.services import fx
from app.services.collection.clock import run_collection_clock
from app.services.notifications import expire_fyi

FX_TIMEOUT_SECONDS = 15.0


class FxFetchError(RuntimeError):
    """The rate source could not be read (network, HTTP status, JSON)."""


def fetch_fx_payload(url: str, *, timeout: float = FX_TIMEOUT_SECONDS) -> Any:
    """GET the rate source and return its JSON. Raises :class:`FxFetchError`."""
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 - normalised into one error type
        raise FxFetchError(f"no se pudo leer {url}: {type(exc).__name__}: {exc}") from exc


@register_job("fx.daily", period="day")
def fx_daily(db: Session, now: datetime) -> dict[str, Any]:
    url = settings.FX_SOURCE_URL
    if not url:
        return {"skipped": True, "reason": "FX_SOURCE_URL is blank"}
    payload = fetch_fx_payload(url)
    rows = fx.parse_mindicador(payload)
    if not rows:
        raise FxFetchError("la fuente de tipos de cambio no trajo valores UF/USD")
    changed = fx.upsert_rates(db, rows)
    return {
        "rows": len(rows),
        "changed": changed,
        "dates": sorted({row[0].isoformat() for row in rows}),
    }


@register_job("collection.clock", period="day")
def collection_clock(db: Session, now: datetime) -> dict[str, Any]:
    return run_collection_clock(db, now=now)


@register_job("notifications.expire", period="day")
def notifications_expire(db: Session, now: datetime) -> dict[str, Any]:
    return {"closed": expire_fyi(db, now=now)}


__all__ = ["FxFetchError", "collection_clock", "fetch_fx_payload", "fx_daily", "notifications_expire"]
