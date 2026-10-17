"""Pydantic v2 schemas for the job runner (``job_run``) and its triggers (v12)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import JobStatus
from app.schemas.utc import UtcDatetime


class JobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    job: str
    scheduled_for: datetime
    status: JobStatus
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    stats: dict[str, Any] | None = None
    error: str | None = None

    @field_validator("scheduled_for", "started_at", "finished_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # SQLite hands back naive datetimes; every job datetime is stored UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class JobTriggerRequest(BaseModel):
    """``POST /internal/jobs/{name}`` body. ``now`` pins the slot (tests, backfills)."""

    now: datetime | None = None


class JobTriggerResponse(JobRunRead):
    #: False when the slot had already run (or is running): nothing executed.
    executed: bool = False


class SchedulerEvent(BaseModel):
    """The EventBridge Scheduler input delivered by LWA to ``POST /events``.

    EventBridge cannot set headers, so the shared token rides in the body.
    """

    model_config = ConfigDict(extra="ignore")

    job: str = Field(min_length=1, max_length=64)
    token: str = Field(default="", max_length=512)
    now: datetime | None = None
