"""CLI: ``python -m app.jobs <name> [--now ISO]`` · ``python -m app.jobs --list``.

Runs one job through the same idempotent runner the HTTP triggers use and
prints the ``job_run`` as JSON. Exit codes: 0 succeeded / skipped / slot
already done, 1 failed, 2 unknown job.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from app.db.session import SessionLocal
from app.jobs import JOB_PERIODS, UnknownJob, load_jobs, trigger_job
from app.models.enums import JobStatus


def _run_dict(run, executed: bool) -> dict:  # noqa: ANN001
    return {
        "id": run.id,
        "job": run.job,
        "scheduled_for": run.scheduled_for.isoformat() if run.scheduled_for else None,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
        "executed": executed,
        "stats": run.stats,
        "error": run.error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs", description=__doc__)
    parser.add_argument("name", nargs="?", help="registered job name, e.g. collection.clock")
    parser.add_argument("--now", help="ISO datetime to run as (default: now, UTC)")
    parser.add_argument("--list", action="store_true", help="list registered jobs and exit")
    args = parser.parse_args(argv)

    jobs = load_jobs()
    if args.list or not args.name:
        for name in sorted(jobs):
            print(f"{name}\t{JOB_PERIODS.get(name, 'day')}")
        return 0 if args.list else 2

    now = datetime.fromisoformat(args.now) if args.now else None
    db = SessionLocal()
    try:
        outcome = trigger_job(db, args.name, now)
        # Serialise while the session is open (the row expires on commit).
        payload = _run_dict(outcome.run, outcome.executed)
    except UnknownJob:
        print(f"unknown job: {args.name}", file=sys.stderr)
        return 2
    finally:
        db.close()
    print(json.dumps(payload, default=str, indent=2))
    return 1 if payload["status"] == JobStatus.FAILED.value else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
