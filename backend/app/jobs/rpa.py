"""Job ``rpa.run`` — execute queued portal searches (lane B6, 02 §10, 06-rpa §3).

Oldest first, at most ``JOB_BATCH`` (3) per invocation; orphaned ``running``
runs are failed first. Minute slots: the scheduler may call it every minute
and each slot runs once. With ``RPA_ENABLED`` off it records ``skipped``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs import register_job


@register_job("rpa.run", period="minute")
def rpa_run(db: Session, now: datetime) -> dict[str, Any]:
    if not settings.RPA_ENABLED:
        return {"skipped": True, "reason": "RPA_ENABLED is off"}
    from app.services.rpa.engine import run_queued  # noqa: PLC0415  (lazy: playwright)

    return run_queued(db, now=now)


__all__ = ["rpa_run"]
