"""Product feature flags (v12 copilot pass).

One place answers "is this feature on, and if not, what do we tell the user".
The routers, the job and the agent registry all ask here, so a flag can never
be honoured in one door and forgotten in another.

Today there is one flag: **portal search** (RPA). Insurer portals need each
insurer's own user + password to apply the search filters, so the product
ships it as *pronto*: the code, tables and tests stay, but with
``PORTAL_SEARCH_ENABLED`` off

* ``POST /portal-searches`` and ``POST /portal-searches/{id}/retry`` -> 409
  with :data:`PORTAL_SEARCH_DISABLED_REASON`;
* ``GET /portal-searches/portals`` reports every portal ``coming_soon``;
* the ``rpa.run`` job is a recorded no-op (``skipped``);
* the agent is not offered ``run_portal_search`` / ``list_portal_searches`` /
  ``get_portal_search`` and the ``portal_quotes`` skill is neither listed nor
  loadable.

The reason strings are Spanish because the UI shows them verbatim.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings

__all__ = [
    "PORTAL_SEARCH",
    "PORTAL_SEARCH_DISABLED_REASON",
    "feature_enabled",
    "feature_reason",
    "features_payload",
    "portal_search_enabled",
]

#: Feature keys (English identifiers; the API payload uses them as-is).
PORTAL_SEARCH = "portal_search"

PORTAL_SEARCH_DISABLED_REASON = (
    "Pronto: la búsqueda en portales necesita el usuario y la clave de cada "
    "compañía para aplicar filtros."
)


def portal_search_enabled() -> bool:
    return bool(settings.PORTAL_SEARCH_ENABLED)


_FLAGS = {
    PORTAL_SEARCH: (portal_search_enabled, PORTAL_SEARCH_DISABLED_REASON),
}


def feature_enabled(key: str) -> bool:
    """Unknown keys are ON: a flag only ever switches a known feature off."""
    entry = _FLAGS.get(key)
    return True if entry is None else bool(entry[0]())


def feature_reason(key: str) -> str | None:
    """The Spanish "pronto" reason when ``key`` is off, else ``None``."""
    entry = _FLAGS.get(key)
    if entry is None or entry[0]():
        return None
    return entry[1]


def features_payload() -> dict[str, dict[str, Any]]:
    """``{"portal_search": {"enabled": bool, "reason": str | None}}``."""
    return {
        key: {"enabled": feature_enabled(key), "reason": feature_reason(key)} for key in _FLAGS
    }
