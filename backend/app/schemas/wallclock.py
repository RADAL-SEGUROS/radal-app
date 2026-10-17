"""``WallClockDatetime`` — a contractual datetime, Chile wall-clock, offset-less.

Product decision (2026-10-17): contractual datetimes — ``policy.period_start_at``
/ ``period_end_at``, ``endorsement.effective_at`` / ``ends_at``, the cobranza
``terminated_at`` / ``rehabilitated_at`` and the claim's ``occurred_at`` — are
stored and returned as **offset-less America/Santiago wall-clock** values. The
Chilean market's cover runs "desde las 12:00 horas del …": the noon printed on
the policy is the value, not an instant to be shifted.

* a **naive** value is already wall-clock and is kept as is;
* an **aware** value (``…Z`` / ``…-03:00`` — e.g. a client that still sends
  ``toISOString()``) is converted to America/Santiago and its tzinfo dropped,
  so ``2026-03-01T15:00:00Z`` is stored as ``2026-03-01T12:00:00``.

Event timestamps (``created_at``, ``sent_at``, ``reported_at`` …) are a
different thing: they stay tz-aware UTC through :mod:`app.schemas.utc`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Annotated

from pydantic import AfterValidator

logger = logging.getLogger(__name__)

CONTRACT_TZ_NAME = "America/Santiago"

#: The Chilean contractual noon ("desde las 12:00 horas").
CONTRACT_NOON = time(12, 0)


def contract_tz() -> tzinfo:
    """America/Santiago; a fixed UTC-3 when the image carries no tzdata."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(CONTRACT_TZ_NAME)
    except Exception:  # noqa: BLE001 - no tzdata: Chile's standard offset is still right most of the year
        logger.warning("tz %s unavailable; wall-clock falls back to UTC-3", CONTRACT_TZ_NAME)
        return timezone(timedelta(hours=-3))


def to_wall_clock(value: datetime | None) -> datetime | None:
    """Naive → kept; aware → America/Santiago wall-clock, tzinfo dropped."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=None)
    return value.astimezone(contract_tz()).replace(tzinfo=None)


def contract_noon(day: date) -> datetime:
    """``day`` at the contractual 12:00, offset-less (the storage convention)."""
    return datetime.combine(day, CONTRACT_NOON)


WallClockDatetime = Annotated[datetime, AfterValidator(to_wall_clock)]

__all__ = [
    "CONTRACT_NOON",
    "CONTRACT_TZ_NAME",
    "WallClockDatetime",
    "contract_noon",
    "contract_tz",
    "to_wall_clock",
]
