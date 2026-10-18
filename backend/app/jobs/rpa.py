"""Job ``rpa.run`` — execute queued portal searches (lane B6, 02 §10, 06-rpa §3).

Oldest first, at most ``JOB_BATCH`` (3) per invocation; orphaned ``running``
runs are failed first. Minute slots: the scheduler may call it every minute
and each slot runs once. With the product flag ``PORTAL_SEARCH_ENABLED`` off
(portal search is "pronto") or ``RPA_ENABLED`` off it records ``skipped`` and
never touches a run or a browser.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs import register_job


@register_job("rpa.run", period="minute")
def rpa_run(db: Session, now: datetime) -> dict[str, Any]:
    from app.services.features import portal_search_enabled  # noqa: PLC0415

    if not portal_search_enabled():
        return {"skipped": True, "reason": "PORTAL_SEARCH_ENABLED is off"}
    if not settings.RPA_ENABLED:
        return {"skipped": True, "reason": "RPA_ENABLED is off"}
    from app.services.rpa.engine import run_queued  # noqa: PLC0415  (lazy: playwright)

    return run_queued(db, now=now)


__all__ = ["rpa_run"]
