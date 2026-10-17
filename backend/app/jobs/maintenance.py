"""One-off database maintenance jobs, run from INSIDE the VPC.

The dev RDS is private and must stay private, so schema migrations and seeds run
inside the backend Lambda through the authenticated jobs doors
(``POST /api/v1/internal/jobs/{name}`` with ``X-Radal-Job-Token``, or the
``/events`` pass-through). Nothing here is ever scheduled.

Double gate: the jobs token (as every job) **and** ``ALLOW_DB_MAINTENANCE=true``
in the Lambda environment. Without the flag a run is recorded as skipped and
touches nothing — set it in ``deploy/backend.env`` only for the deploy window.

* ``db.migrate``      — the additive planner of ``scripts/migrate_case_files.py``
  (create missing tables, add missing columns/indexes/FKs), then verify zero drift.
* ``db.seed_catalog`` — the closed ramo catalogue + collection defaults (idempotent).
* ``db.seed_demo``    — the v12 demo broker (idempotent: an existing demo broker is
  detected and left alone); its generated PDFs are uploaded to the documents bucket
  under the exact keys the ``document`` rows carry.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs import register_job

log = logging.getLogger(__name__)

FLAG = "ALLOW_DB_MAINTENANCE"


def _allowed() -> bool:
    return os.environ.get(FLAG, "").strip().lower() == "true"


def _skip() -> dict[str, Any]:
    return {"skipped": True, "reason": f"{FLAG} is not 'true' in the environment"}


@register_job("db.migrate", period="minute")
def db_migrate(db: Session, now: datetime) -> dict[str, Any]:
    if not _allowed():
        return _skip()
    from scripts.migrate_case_files import apply_plan, build_online_plan, validate_manifest

    errors = validate_manifest()
    if errors:
        raise RuntimeError(f"invalid migration manifest: {errors}")
    engine = db.get_bind()
    plan = build_online_plan(engine)
    pending = len(plan.statements)
    kinds: dict[str, int] = {}
    for kind, _sql in plan.statements:
        kinds[kind] = kinds.get(kind, 0) + 1
    if pending:
        apply_plan(engine, plan)
    verify = build_online_plan(engine)
    if verify.has_drift:
        raise RuntimeError(f"{len(verify.statements)} statement(s) still pending after apply")
    return {"applied": pending, "by_kind": kinds, "drift_after": 0}


@register_job("db.seed_catalog", period="minute")
def db_seed_catalog(db: Session, now: datetime) -> dict[str, Any]:
    if not _allowed():
        return _skip()
    from app.db.seed_v12 import seed_catalog, seed_collection_defaults

    catalog = seed_catalog(db)
    defaults = seed_collection_defaults(db)
    db.commit()
    return {"catalog": _jsonable(catalog), "defaults": _jsonable(defaults)}


@register_job("db.seed_demo", period="minute")
def db_seed_demo(db: Session, now: datetime) -> dict[str, Any]:
    if not _allowed():
        return _skip()
    from scripts.seed_v12_demo import seed_demo

    media_dir = "/tmp/radal-demo-media"
    summary, created = seed_demo(settings.DATABASE_URL, media_dir=media_dir)
    uploaded = _upload_media(Path(media_dir)) if created else 0
    return {
        "created": created,
        "uploaded_files": uploaded,
        "share_links": [link.get("url") for link in summary.get("share_links", [])],
        "counts": _jsonable(summary.get("counts")),
    }


def _upload_media(root: Path) -> int:
    """Put every generated file in the documents bucket under its relative key."""
    if not root.exists():
        return 0
    import boto3

    client = boto3.client("s3", region_name=settings.S3_REGION)
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            key = path.relative_to(root).as_posix()
            client.upload_file(str(path), settings.S3_BUCKET, key)
            count += 1
    return count


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
