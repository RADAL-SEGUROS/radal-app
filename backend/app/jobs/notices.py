"""Assisted-collection jobs (v12, 03 §7): payer reminders and auto status inquiries.

Owner: lane **B4**.

* ``collection.notices`` — :func:`app.services.notices.run_collection_notices`:
  one reminder per cuota per cadence step, only behind every switch (broker
  master switch + approved templates, plan ``notices_enabled``, not paused,
  payer contact, level not ``record``).
* ``collection.inquiries`` — :func:`app.services.mail.inquiries.run_collection_inquiries`:
  when the broker enabled ``auto_status_inquiries``, ask the insurer about
  cuotas ``inquiry_after_days`` overdue; one open inquiry per policy.

Both run once per Santiago day (``period="day"``) and take ``now`` injected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.jobs import register_job


@register_job("collection.notices", period="day")
def collection_notices(db: Session, now: datetime) -> dict[str, Any]:
    from app.services.notices import run_collection_notices  # noqa: PLC0415

    return run_collection_notices(db, now)


@register_job("collection.inquiries", period="day")
def collection_inquiries(db: Session, now: datetime) -> dict[str, Any]:
    from app.services.mail.inquiries import run_collection_inquiries  # noqa: PLC0415

    return run_collection_inquiries(db, now)
