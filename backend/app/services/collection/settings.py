"""The broker's ``collection_settings`` row (v12, 01 §3) — lazy create.

One row per broker. Readers that must NOT write (the clock, the status cache)
use :func:`app.services.collection.status.broker_collection_params`; the
settings endpoint and anything that is about to change the row use
:func:`get_or_create_settings`, which inserts the defaults the first time.
Flushes only; the caller commits.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.collection_settings import CollectionSettings


def settings_or_none(db: Session, broker_id: int) -> CollectionSettings | None:
    return db.scalar(select(CollectionSettings).where(CollectionSettings.broker_id == broker_id))


def get_or_create_settings(db: Session, broker_id: int) -> CollectionSettings:
    """The broker's row, inserted with the model defaults when missing."""
    row = settings_or_none(db, broker_id)
    if row is not None:
        return row
    row = CollectionSettings(broker_id=broker_id)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:  # a concurrent request created it first
        row = settings_or_none(db, broker_id)
        if row is None:  # pragma: no cover - the winner rolled back
            raise
    return row


__all__ = ["get_or_create_settings", "settings_or_none"]
