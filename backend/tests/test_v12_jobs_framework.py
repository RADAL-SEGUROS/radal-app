"""v12 job framework: registry, idempotent runner, CLI and the two HTTP doors.

* one ``job_run`` per ``(job, scheduled_for)`` slot; a second trigger is a no-op;
* a body that raises is recorded ``failed`` and never escapes; a failed slot
  can be retried and reuses its row;
* ``POST /api/v1/internal/jobs/{name}`` (header token) and ``POST /events``
  (body token, outside the API prefix): blank ``JOBS_TOKEN`` => 404, wrong
  token => 401, unknown job => 404.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app import jobs as jobs_module
from app.core.config import settings
from app.jobs import (
    JOB_PERIODS,
    JOBS,
    UnknownJob,
    load_jobs,
    register_job,
    run_job,
    slot_for,
    trigger_job,
)
from app.jobs.__main__ import main as jobs_cli
from app.models.enums import JobStatus
from app.models.insurance_line import InsuranceLine
from app.models.jobs import JobRun
from tests.conftest import API

NOW = datetime(2026, 10, 16, 12, 34, 56, tzinfo=timezone.utc)
TOKEN = {"X-Radal-Job-Token": "test-jobs-token"}


def _count(db, name=None) -> int:
    stmt = select(func.count()).select_from(JobRun)
    if name:
        stmt = stmt.where(JobRun.job == name)
    return db.scalar(stmt)


@pytest.fixture()
def temp_job(monkeypatch):
    """Register a throwaway job for one test (restored afterwards)."""

    def _register(name, fn, period="day"):
        monkeypatch.setitem(JOBS, name, fn)
        monkeypatch.setitem(JOB_PERIODS, name, period)
        return fn

    return _register


# --- registry & slots ---------------------------------------------------------

def test_noop_is_the_only_phase1_job():
    assert "noop" in load_jobs()
    assert JOB_PERIODS["noop"] == "minute"


def test_register_job_validates(monkeypatch):
    monkeypatch.setattr(jobs_module, "JOBS", dict(JOBS))
    monkeypatch.setattr(jobs_module, "JOB_PERIODS", dict(JOB_PERIODS))
    with pytest.raises(ValueError):
        register_job("x", period="week")
    with pytest.raises(ValueError):
        register_job("x" * 65)

    @register_job("tmp.hourly", period="hour")
    def _tmp(db, now):
        return {}

    assert jobs_module.JOBS["tmp.hourly"] is _tmp
    assert jobs_module.JOB_PERIODS["tmp.hourly"] == "hour"


def test_slots():
    assert slot_for(NOW, "minute") == datetime(2026, 10, 16, 12, 34, tzinfo=timezone.utc)
    assert slot_for(NOW, "hour") == datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    # Day slots start at America/Santiago midnight (UTC-3 in October).
    assert slot_for(NOW, "day") == datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)
    late_evening_santiago = datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)  # 23:00 on the 16th
    assert slot_for(late_evening_santiago, "day") == slot_for(NOW, "day")
    # Naive = UTC.
    assert slot_for(NOW.replace(tzinfo=None), "minute") == slot_for(NOW, "minute")


# --- the runner ---------------------------------------------------------------

def test_run_is_idempotent_per_slot(db):
    first = trigger_job(db, "noop", NOW)
    assert first.executed is True
    assert first.run.status == JobStatus.SUCCEEDED
    assert first.run.stats["ok"] is True
    assert first.run.finished_at is not None

    again = trigger_job(db, "noop", NOW.replace(second=5))
    assert again.executed is False
    assert again.run.id == first.run.id
    assert _count(db, "noop") == 1

    nxt = run_job(db, "noop", NOW.replace(minute=35))
    assert nxt.id != first.run.id
    assert _count(db, "noop") == 2


def test_failing_body_is_recorded_never_raised_and_rolled_back(db, world, temp_job):
    calls = []

    def boom(session, now):
        calls.append(now)
        session.add(InsuranceLine(broker_id=None, name="Ramo fantasma"))
        session.flush()
        raise RuntimeError("kaput")

    temp_job("tmp.boom", boom)
    run = run_job(db, "tmp.boom", NOW)
    assert run.status == JobStatus.FAILED
    assert run.error == "RuntimeError: kaput"
    assert run.finished_at is not None
    assert db.scalar(select(InsuranceLine).where(InsuranceLine.name == "Ramo fantasma")) is None
    assert calls == [NOW]


def test_failed_slot_can_be_retried_and_reuses_its_row(db, temp_job):
    attempts = {"n": 0}

    def flaky(session, now):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ValueError("first try")
        return {"attempt": attempts["n"]}

    temp_job("tmp.flaky", flaky)
    failed = run_job(db, "tmp.flaky", NOW)
    failed_id = failed.id
    assert failed.status == JobStatus.FAILED
    retried = trigger_job(db, "tmp.flaky", NOW)
    assert retried.executed is True
    assert retried.run.id == failed_id
    assert retried.run.status == JobStatus.SUCCEEDED
    assert retried.run.error is None
    assert retried.run.stats == {"attempt": 2}
    # Succeeded now: a third trigger does nothing.
    assert trigger_job(db, "tmp.flaky", NOW).executed is False
    assert attempts["n"] == 2


def test_skipped_and_non_dict_results(db, temp_job):
    temp_job("tmp.skip", lambda session, now: {"skipped": True, "reason": "feature off"})
    temp_job("tmp.none", lambda session, now: None)
    temp_job("tmp.weird", lambda session, now: {"n": {1, 2}, "when": now})
    assert run_job(db, "tmp.skip", NOW).status == JobStatus.SKIPPED
    assert run_job(db, "tmp.none", NOW).stats == {}
    weird = run_job(db, "tmp.weird", NOW)
    assert weird.status == JobStatus.SUCCEEDED
    assert sorted(weird.stats["n"]) == [1, 2]
    assert isinstance(weird.stats["when"], str)


def test_unknown_job_raises_unknown_and_records_nothing(db):
    with pytest.raises(UnknownJob):
        run_job(db, "does.not.exist", NOW)
    assert _count(db) == 0


def test_body_gets_the_injected_now(db, temp_job):
    seen = []
    temp_job("tmp.now", lambda session, now: seen.append(now) or {})
    run_job(db, "tmp.now", datetime(2026, 1, 2, 3, 4, 5))
    assert seen == [datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)]


# --- CLI ----------------------------------------------------------------------

def test_cli(db, capsys, temp_job):
    assert jobs_cli(["--list"]) == 0
    assert "noop\tminute" in capsys.readouterr().out
    assert jobs_cli(["noop", "--now", "2026-10-16T12:00:00+00:00"]) == 0
    out = capsys.readouterr().out
    assert '"status": "succeeded"' in out and '"executed": true' in out
    assert jobs_cli(["noop", "--now", "2026-10-16T12:00:30+00:00"]) == 0
    assert '"executed": false' in capsys.readouterr().out
    assert jobs_cli(["nope"]) == 2
    temp_job("tmp.cli_boom", lambda session, now: 1 / 0)
    assert jobs_cli(["tmp.cli_boom"]) == 1


# --- POST /api/v1/internal/jobs/{name} ----------------------------------------

def test_internal_trigger_runs_once_per_slot(client, db):
    body = {"now": "2026-10-16T12:00:00Z"}
    first = client.post(f"{API}/internal/jobs/noop", json=body, headers=TOKEN)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["job"] == "noop" and data["status"] == "succeeded" and data["executed"] is True
    assert data["scheduled_for"].startswith("2026-10-16T12:00")
    second = client.post(f"{API}/internal/jobs/noop", json=body, headers=TOKEN)
    assert second.status_code == 200
    assert second.json()["executed"] is False
    assert second.json()["id"] == data["id"]
    # No body at all: "now" defaults to the wall clock.
    assert client.post(f"{API}/internal/jobs/noop", headers=TOKEN).status_code == 200


def test_internal_trigger_auth(client, monkeypatch):
    assert client.post(f"{API}/internal/jobs/noop").status_code == 401
    bad = client.post(f"{API}/internal/jobs/noop", headers={"X-Radal-Job-Token": "wrong"})
    assert bad.status_code == 401
    assert client.post(f"{API}/internal/jobs/nope", headers=TOKEN).status_code == 404
    bad_body = client.post(f"{API}/internal/jobs/noop", json={"now": "not-a-date"}, headers=TOKEN)
    assert bad_body.status_code == 422


def test_internal_trigger_is_404_without_a_configured_token(client, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_TOKEN", "")
    assert client.post(f"{API}/internal/jobs/noop", headers={"X-Radal-Job-Token": ""}).status_code == 404
    assert client.post(f"{API}/internal/jobs/noop", headers=TOKEN).status_code == 404
    assert client.post(f"{API}/internal/jobs/noop", json={"now": "garbage"}).status_code == 404


def test_a_user_jwt_is_not_a_job_token(client, headers_a):
    assert client.post(f"{API}/internal/jobs/noop", headers=headers_a).status_code == 401


# --- POST /events (EventBridge via LWA) ---------------------------------------

def test_events_route_is_closed_by_default(client):
    # JOBS_EVENTS_ENABLED defaults to False even with a token configured.
    resp = client.post("/events", json={"job": "noop", "token": "test-jobs-token"})
    assert resp.status_code == 404


def test_events_route_when_enabled(client, monkeypatch, db):
    monkeypatch.setattr(settings, "JOBS_EVENTS_ENABLED", True)
    ok = client.post("/events", json={"job": "noop", "token": "test-jobs-token",
                                      "now": "2026-10-16T12:00:00Z"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "succeeded"
    assert client.post("/events", json={"job": "noop", "token": "nope"}).status_code == 401
    assert client.post("/events", json={"job": "noop"}).status_code == 401
    assert client.post("/events", json={"job": "nope", "token": "test-jobs-token"}).status_code == 404
    assert client.post("/events", json={"token": "test-jobs-token"}).status_code == 422
    # Extra EventBridge fields are ignored.
    extra = client.post("/events", json={"job": "noop", "token": "test-jobs-token", "source": "aws.scheduler"})
    assert extra.status_code == 200
    assert _count(db, "noop") >= 1


def test_events_route_is_404_without_a_configured_token(client, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_EVENTS_ENABLED", True)
    monkeypatch.setattr(settings, "JOBS_TOKEN", "")
    assert client.post("/events", json={"job": "noop", "token": ""}).status_code == 404
    assert client.post("/events", json="garbage").status_code == 404


def test_events_lives_outside_the_api_prefix(client, monkeypatch):
    monkeypatch.setattr(settings, "JOBS_EVENTS_ENABLED", True)
    resp = client.post(f"{API}/events", json={"job": "noop", "token": "test-jobs-token"})
    assert resp.status_code in (404, 405)
