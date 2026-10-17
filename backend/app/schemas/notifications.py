"""Pydantic v2 schemas for the bell (``notification``, v12 02 §4)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.enums import NotificationKind, NotificationStatus, NotificationTray
from app.schemas.utc import UtcDatetime


class NotificationRead(BaseModel):
    """One bell entry. ``params`` is render data (the frontend formats it)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tray: NotificationTray
    kind: NotificationKind
    level: str | None = None
    origin_type: str
    origin_id: int
    params: dict[str, Any] | None = None
    link_path: str | None = None
    status: NotificationStatus
    created_at: UtcDatetime
    #: When the alert was last raised/escalated — the bell's sort key.
    updated_at: UtcDatetime
    closed_at: datetime | None = None
    #: ``scope=all`` only (the admin team view): who owns the row.
    recipient_name: str | None = None

    @field_validator("created_at", "updated_at", "closed_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        # SQLite hands back naive datetimes; every notification time is UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class NotificationPage(BaseModel):
    items: list[NotificationRead]
    total: int
    limit: int
    offset: int


class NotificationCount(BaseModel):
    """Unseen (``new``) rows per tray — the bell badge."""

    todo_new: int
    fyi_new: int


__all__ = ["NotificationCount", "NotificationPage", "NotificationRead"]
