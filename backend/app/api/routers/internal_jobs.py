"""Internal job triggers (v12, 02 §4/§10). Lane B2 owns this router.

Two doors into the SAME idempotent runner (:func:`app.jobs.trigger_job`):

* ``POST /api/v1/internal/jobs/{name}`` — header ``X-Radal-Job-Token``; for
  operators, smoke tests and backfills (``{"now": iso}`` pins the slot).
* ``POST /events`` (``events_router``, mounted WITHOUT the API prefix) — the
  EventBridge Scheduler -> Lambda invoke that the Lambda Web Adapter delivers
  as an HTTP POST (``AWS_LWA_PASS_THROUGH_PATH``). EventBridge cannot set
  headers, so the body carries ``{"job", "token"}``. CloudFront only forwards
  ``/api/*``, so this path is unreachable from the internet by construction.

Security: ``settings.JOBS_TOKEN`` blank => both doors answer **404** (the
routes do not exist as far as a caller can tell); ``/events`` additionally
needs ``JOBS_EVENTS_ENABLED``. A wrong/missing token is **401**. Tokens are
compared in constant time. An unknown job is 404. There is no user, no broker
scope and no RBAC gate here: a job is platform work and scopes itself.
"""
from __future__ import annotations

import hmac

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.jobs import UnknownJob, trigger_job
from app.schemas.jobs import JobRunRead, JobTriggerRequest, JobTriggerResponse, SchedulerEvent

router = APIRouter(prefix="/internal/jobs", tags=["internal"])
events_router = APIRouter(tags=["internal"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def token_matches(given: str | None) -> bool:
    """Constant-time comparison against ``JOBS_TOKEN`` (blank setting never matches)."""
    expected = settings.JOBS_TOKEN or ""
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _parse(model, raw: Any):  # noqa: ANN001
    """Validate the body AFTER the gate, so a closed door is a 404 whatever is sent."""
    try:
        return model.model_validate(raw if raw is not None else {})
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False, include_context=False),
        )


def _run(db: Session, name: str, now) -> JobTriggerResponse:  # noqa: ANN001
    try:
        outcome = trigger_job(db, name, now)
    except UnknownJob:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown job: {name}")
    body = JobRunRead.model_validate(outcome.run).model_dump()
    return JobTriggerResponse(**body, executed=outcome.executed)


@router.post("/{name}", response_model=JobTriggerResponse)
def run_internal_job(
    name: str,
    payload: Any = Body(default=None),
    x_radal_job_token: str | None = Header(default=None, alias="X-Radal-Job-Token"),
    db: Session = Depends(get_db),
) -> JobTriggerResponse:
    """Run job ``name`` now (idempotent per slot); returns the ``job_run``."""
    if not settings.JOBS_TOKEN:
        raise _NOT_FOUND
    if not token_matches(x_radal_job_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid job token")
    request = _parse(JobTriggerRequest, payload)
    return _run(db, name, request.now)


@events_router.post("/events", response_model=JobTriggerResponse, include_in_schema=False)
def scheduler_event(
    payload: Any = Body(default=None), db: Session = Depends(get_db)
) -> JobTriggerResponse:
    """EventBridge Scheduler entry point (token in the body)."""
    if not settings.JOBS_TOKEN or not settings.JOBS_EVENTS_ENABLED:
        raise _NOT_FOUND
    event = _parse(SchedulerEvent, payload)
    if not token_matches(event.token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid job token")
    return _run(db, event.job, event.now)
