"""``UtcDatetime`` — a system event timestamp serialized as tz-aware UTC.

Every event timestamp is written with ``utcnow()`` (``models/base_class.py``),
but SQLite / MySQL hand it back naive, and a naive ISO string (no ``Z``) is read
by the browser as LOCAL time — so "Último ingreso" rendered three hours ahead in
Chile (QA v12). Tagging the value UTC on the way out fixes that.

Use it ONLY for event timestamps (``created_at``, ``sent_at``, ``last_login_at``,
…). Contractual datetimes (``effective_at``, ``period_start_at``, ``ends_at`` —
the noon convention) are wall-clock values and must stay offset-less.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def as_utc(value: datetime | None) -> datetime | None:
    """A naive datetime is UTC by the house rule; an aware one is converted."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]

__all__ = ["UtcDatetime", "as_utc"]
