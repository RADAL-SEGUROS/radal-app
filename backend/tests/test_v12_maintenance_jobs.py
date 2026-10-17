"""The one-off DB maintenance jobs: double-gated, idempotent, and a no-op on a migrated schema."""
from __future__ import annotations

from datetime import datetime, timezone

from app.jobs import load_jobs, run_job

NOW = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)


def test_maintenance_jobs_skip_without_the_flag(db, monkeypatch):
    load_jobs()
    monkeypatch.delenv("ALLOW_DB_MAINTENANCE", raising=False)
    for name in ("db.migrate", "db.seed_catalog", "db.seed_demo"):
        run = run_job(db, name, NOW)
        assert run.status.value == "skipped", (name, run.status, run.error)


def test_migrate_is_a_no_op_on_a_current_schema(db, monkeypatch):
    load_jobs()
    monkeypatch.setenv("ALLOW_DB_MAINTENANCE", "true")
    run = run_job(db, "db.migrate", NOW)
    assert run.status.value == "succeeded", run.error
    assert run.stats["applied"] == 0 and run.stats["drift_after"] == 0


def test_seed_catalog_is_idempotent(db, monkeypatch):
    load_jobs()
    monkeypatch.setenv("ALLOW_DB_MAINTENANCE", "true")
    first = run_job(db, "db.seed_catalog", NOW)
    second = run_job(db, "db.seed_catalog", datetime(2026, 10, 17, 15, 5, tzinfo=timezone.utc))
    assert first.status.value == "succeeded", first.error
    assert second.status.value == "succeeded", second.error
