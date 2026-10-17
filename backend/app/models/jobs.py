"""``job_run`` — one execution of a scheduled job (v12). PLATFORM table.

The unique ``(job, scheduled_for)`` is the idempotency key of the job runner:
``scheduled_for`` is ``now`` truncated to the job's period, so EventBridge
firing twice (or a manual ``POST /internal/jobs/{name}``) for the same slot
finds the existing row instead of running the body again. ``stats`` is the
body's free-form summary (counts), rendered on ``/platform``, never queried.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin
from app.models.enums import JobStatus, sql_enum
from app.models.types import JSONType


class JobRun(Base, TimestampMixin):
    """One run of one job for one scheduled slot. No broker_id."""

    __tablename__ = "job_run"
    __table_args__ = (UniqueConstraint("job", "scheduled_for", name="uq_job_run_slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Registry key, e.g. "collection.clock".
    job: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        sql_enum(JobStatus), default=JobStatus.RUNNING, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<JobRun {self.job} @ {self.scheduled_for} {self.status}>"


__all__ = ["JobRun"]
