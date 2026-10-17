"""Broker-controlled URLs that Radal renders as links (v12 security fix).

A payment portal URL (``payment_instructions.portal_url``, the legacy
``insurer.payment_url``) is shown as an ``<a href>`` on the app origin and on the
public share page (``/s/{token}``). Only ``http(s)`` with a host may ever reach
an ``href``: ``javascript:``, ``data:``, ``vbscript:`` … would be stored XSS.

* :func:`safe_http_url` is the READ-side choke point: it returns a safe absolute
  URL or ``None`` (never raises). A bare domain (``www.hdi.cl/pagos`` — the
  legacy CMF import carries some) is upgraded to ``https://``.
* :func:`validate_http_url` is the WRITE-side check used by the schemas: it
  raises ``ValueError`` with a Spanish message (pydantic turns it into a 422).
"""
from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
INVALID_URL_MESSAGE = "La URL del portal debe comenzar con https:// (o http://) y tener un dominio"


def _has_unsafe_chars(text: str) -> bool:
    # Control characters / whitespace inside a URL are how scheme filters get
    # bypassed ("java\tscript:"); a real portal URL never carries them.
    return any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in text)


def _http_ok(text: str) -> bool:
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    return parts.scheme.lower() in ALLOWED_SCHEMES and bool(parts.hostname)


def safe_http_url(value: object) -> str | None:
    """``value`` as an absolute ``http(s)`` URL, or ``None`` when it is not one."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or _has_unsafe_chars(text):
        return None
    if _http_ok(text):
        return text
    try:
        scheme = urlsplit(text).scheme
    except ValueError:
        return None
    if not scheme and not text.startswith("/") and "\\" not in text:
        # A bare domain ("www.hdi.cl/pagos"): the host must look like one.
        candidate = f"https://{text}"
        if _http_ok(candidate) and "." in (urlsplit(candidate).hostname or ""):
            return candidate
    return None


def validate_http_url(value: str | None, *, max_length: int | None = None) -> str | None:
    """Pydantic helper: ``None``/blank pass; anything else must be http(s) (else ``ValueError``).

    Returns the normalised URL (a bare domain gains ``https://``), which must
    still fit ``max_length`` (the column width).
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return value
    safe = safe_http_url(text)
    if safe is None:
        raise ValueError(INVALID_URL_MESSAGE)
    if max_length is not None and len(safe) > max_length:
        raise ValueError(f"La URL del portal admite hasta {max_length} caracteres")
    return safe


__all__ = ["ALLOWED_SCHEMES", "INVALID_URL_MESSAGE", "safe_http_url", "validate_http_url"]
