"""The connector registry (06-rpa §1).

``CONNECTORS`` holds the BUILT connectors. ``UPCOMING_PORTALS`` are portals
the product shows as "pronto" (disabled, with a reason) so the portal page and
the failure screen can offer "Otros portales" without a dead button; they have
no connector and a search against them is refused.
"""
from __future__ import annotations

from app.services.rpa.base import PortalConnector
from app.services.rpa.connectors.hdi import HdiConnector

CONNECTORS: dict[str, PortalConnector] = {"hdi": HdiConnector()}

#: (key, display name). Placeholders until the team picks the next portals.
UPCOMING_PORTALS: tuple[tuple[str, str], ...] = (
    ("mapfre", "MAPFRE Seguros"),
    ("sura", "SURA Seguros"),
    ("bci", "BCI Seguros"),
    ("zurich", "Zurich Seguros"),
)

#: Spanish, user-facing.
UPCOMING_REASON = "Próximamente: este portal aún no está conectado a Radal."


def get_connector(key: str | None) -> PortalConnector | None:
    """The built connector for ``key`` (case-insensitive), or ``None``."""
    if not key:
        return None
    return CONNECTORS.get(str(key).strip().lower())


def is_upcoming(key: str | None) -> bool:
    return bool(key) and str(key).strip().lower() in {k for k, _ in UPCOMING_PORTALS}


__all__ = ["CONNECTORS", "UPCOMING_PORTALS", "UPCOMING_REASON", "get_connector", "is_upcoming"]
