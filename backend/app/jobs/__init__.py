"""Scheduled jobs: the registry and the idempotent runner (v12, 02 §10).

A job is a plain function ``(db, now) -> dict`` registered by name::

    @register_job("collection.clock", period="day")
    def collection_clock(db: Session, now: datetime) -> dict:
        ...
        return {"refreshed": 12}

:func:`run_job` is the ONLY way a job runs (CLI, ``POST /internal/jobs/{name}``,
the EventBridge ``POST /events`` route). It writes one ``job_run`` row per slot,
where the slot (``scheduled_for``) is ``now`` truncated to the job's period —
``day`` at America/Santiago midnight (business dates are Santiago), ``hour``
and ``minute`` in UTC. The unique ``(job, scheduled_for)`` makes a slot run at
most once: a second trigger for a slot that is running, succeeded or was
skipped returns the existing row untouched; a FAILED slot may be retried and
reuses its row. A body that raises never escapes: the run is recorded as
``failed`` with the error, and the runner returns the row.

A body may return ``{"skipped": True, ...}`` to record ``skipped`` (nothing to
do / feature switched off). ``now`` is injected so every job is deterministic
under test.

Job bodies live in sibling modules owned by their lanes (``collection.py``,
``fx.py``, ``notifications.py`` — B2; ``payments.py`` — B3; ``mail.py``,
``notices.py`` — B4; ``rpa.py`` — B6). :func:`load_jobs` imports every module
of this package so their ``@register_job`` decorators run; nothing here knows
their names. Phase 1 registers only the trivial ``noop``.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base_class import utcnow
from app.models.enums import JobStatus
from app.models.jobs import JobRun

logger = logging.getLogger(__name__)

Period = Literal["day", "hour", "minute"]
JobFn = Callable[[Session, datetime], "dict[str, Any] | None"]

BUSINESS_TZ_NAME = "America/Santiago"

#: name -> body. The contract shape (02 §10): ``JOBS[name](db, now) -> dict``.
JOBS: dict[str, JobFn] = {}
#: name -> period of its idempotency slot.
JOB_PERIODS: dict[str, Period] = {}

_PERIODS: tuple[str, ...] = ("day", "hour", "minute")
_loaded = False


class UnknownJob(KeyError):
    """No job is registered under that name (router -> 404, CLI -> exit 2)."""


@dataclass(frozen=True)
class JobOutcome:
    """What a trigger did: the row, and whether this call executed the body."""

    run: JobRun
    executed: bool


def register_job(name: str, period: Period = "day") -> Callable[[JobFn], JobFn]:
    """Decorator: register ``fn`` as job ``name`` with an idempotency ``period``."""
    if period not in _PERIODS:
        raise ValueError(f"job period must be one of {_PERIODS}, got {period!r}")
    if len(name) > 64:
        raise ValueError("job names are at most 64 characters (job_run.job)")

    def decorator(fn: JobFn) -> JobFn:
        JOBS[name] = fn
        JOB_PERIODS[name] = period
        return fn

    return decorator


def load_jobs() -> dict[str, JobFn]:
    """Import every module of ``app.jobs`` once so their jobs register."""
    global _loaded
    if not _loaded:
        _loaded = True
        for module in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
            if module.name.startswith("_"):
                continue
            try:
                importlib.import_module(f"{__name__}.{module.name}")
            except Exception:  # noqa: BLE001 - one broken lane module must not stop the others
                logger.exception("jobs: could not import app.jobs.%s", module.name)
    return JOBS


def _business_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(BUSINESS_TZ_NAME)
    except Exception:  # noqa: BLE001 - no tzdata in the image: UTC is still a stable key
        logger.warning("tz %s unavailable; job day slots fall back to UTC", BUSINESS_TZ_NAME)
        return timezone.utc


def as_aware_utc(value: datetime) -> datetime:
    """Naive datetimes are taken as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def slot_for(now: datetime, period: Period) -> datetime:
    """``now`` truncated to its period, returned as an aware UTC datetime."""
    moment = as_aware_utc(now)
    if period == "minute":
        return moment.replace(second=0, microsecond=0)
    if period == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    local = moment.astimezone(_business_tz())
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc)


def _find(db: Session, name: str, slot: datetime) -> JobRun | None:
    return db.scalar(select(JobRun).where(JobRun.job == name, JobRun.scheduled_for == slot))


def _claim(db: Session, name: str, slot: datetime) -> tuple[JobRun, bool]:
    """Insert the slot row as ``running`` (claimed=True) or return the existing one."""
    existing = _find(db, name, slot)
    if existing is not None:
        if existing.status == JobStatus.FAILED:
            existing.status = JobStatus.RUNNING
            existing.started_at = utcnow()
            existing.finished_at = None
            existing.error = None
            existing.stats = None
            db.commit()
            return existing, True
        return existing, False
    run = JobRun(job=name, scheduled_for=slot, status=JobStatus.RUNNING, started_at=utcnow())
    db.add(run)
    try:
        db.commit()
    except IntegrityError:  # a concurrent trigger won the slot
        db.rollback()
        existing = _find(db, name, slot)
        if existing is None:  # pragma: no cover - the winner rolled back
            raise
        return existing, False
    return run, True


def trigger_job(db: Session, name: str, now: datetime | None = None) -> JobOutcome:
    """Run job ``name`` for the slot of ``now``. Never raises for a body failure.

    Raises :class:`UnknownJob` for an unregistered name (nothing is recorded).
    """
    load_jobs()
    fn = JOBS.get(name)
    if fn is None:
        raise UnknownJob(name)
    moment = as_aware_utc(now or utcnow())
    slot = slot_for(moment, JOB_PERIODS.get(name, "day"))

    try:
        run, claimed = _claim(db, name, slot)
    except Exception as exc:  # noqa: BLE001 - the runner itself never raises
        db.rollback()
        logger.exception("job %s: could not claim slot %s", name, slot.isoformat())
        ghost = JobRun(
            job=name,
            scheduled_for=slot,
            status=JobStatus.FAILED,
            error=f"claim failed: {type(exc).__name__}: {exc}"[:4000],
        )
        return JobOutcome(run=ghost, executed=False)
    if not claimed:
        return JobOutcome(run=run, executed=False)

    run_id = run.id
    try:
        stats = fn(db, moment) or {}
        if not isinstance(stats, dict):
            stats = {"result": str(stats)}
        db.commit()
        status = JobStatus.SKIPPED if stats.get("skipped") else JobStatus.SUCCEEDED
        error = None
    except Exception as exc:  # noqa: BLE001 - a body failure is recorded, never raised
        db.rollback()
        logger.exception("job %s failed for slot %s", name, slot.isoformat())
        stats = None
        status = JobStatus.FAILED
        error = f"{type(exc).__name__}: {exc}"[:4000]

    try:
        run = db.get(JobRun, run_id) or run
        run.status = status
        run.stats = _jsonable(stats)
        run.error = error
        run.finished_at = utcnow()
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("job %s: could not record the outcome", name)
    return JobOutcome(run=run, executed=True)


def run_job(db: Session, name: str, now: datetime | None = None) -> JobRun:
    """Run job ``name`` for the slot of ``now`` and return its ``job_run`` row."""
    return trigger_job(db, name, now).run


def _jsonable(value: Any) -> Any:
    """Stats go into a JSON column: coerce Decimals/dates/sets to plain values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


# --- queries (for ``GET /platform/jobs``, lane B3 hosts the route) ----------

def list_job_runs(
    db: Session,
    *,
    job: str | None = None,
    status: JobStatus | str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[JobRun]:
    """``job_run`` rows, newest slot first; optional ``job`` / ``status`` filters."""
    stmt = select(JobRun)
    if job:
        stmt = stmt.where(JobRun.job == job)
    if status:
        stmt = stmt.where(JobRun.status == JobStatus(status))
    stmt = stmt.order_by(JobRun.scheduled_for.desc(), JobRun.id.desc()).offset(offset).limit(limit)
    return list(db.scalars(stmt))


def latest_runs(db: Session) -> dict[str, JobRun | None]:
    """The most recent run of EVERY registered job (``None`` = never ran)."""
    out: dict[str, JobRun | None] = {}
    for name in sorted(load_jobs()):
        out[name] = db.scalar(
            select(JobRun)
            .where(JobRun.job == name)
            .order_by(JobRun.scheduled_for.desc(), JobRun.id.desc())
            .limit(1)
        )
    return out


# --- built-in --------------------------------------------------------------

@register_job("noop", period="minute")
def noop(db: Session, now: datetime) -> dict[str, Any]:
    """Does nothing; proves the scheduler -> runner -> job_run path end to end."""
    return {"ok": True, "now": as_aware_utc(now).isoformat()}


__all__ = [
    "JOBS",
    "JOB_PERIODS",
    "JobOutcome",
    "UnknownJob",
    "as_aware_utc",
    "latest_runs",
    "list_job_runs",
    "load_jobs",
    "register_job",
    "run_job",
    "slot_for",
    "trigger_job",
]
