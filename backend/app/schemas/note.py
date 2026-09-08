"""Pydantic v2 schemas for ``note`` and the ``activity`` feed.

The ``note`` table has existed since the rebuild with no endpoints; the team
asked for notes explicitly, WITH a follow-up date. ``is_internal`` marks
broker-private commentary that must never reach an insured or insurer user.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EntityType


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType
    entity_id: int = Field(gt=0)
    body: str = Field(min_length=1)
    is_internal: bool = True
    phase: str | None = Field(default=None, max_length=120)
    follow_up_on: date | None = None


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(default=None, min_length=1)
    is_internal: bool | None = None
    phase: str | None = Field(default=None, max_length=120)
    follow_up_on: date | None = None


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    entity_type: EntityType
    entity_id: int
    author_id: int | None = None
    body: str
    is_internal: bool
    phase: str | None = None
    follow_up_on: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    author_name: str | None = None


class NoteListResponse(BaseModel):
    items: list[NoteRead]
    total: int
    limit: int
    offset: int


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    user_id: int | None = None
    action: str
    entity_type: EntityType
    entity_id: int | None = None
    description: str | None = None
    meta: dict[str, Any] | None = None
    occurred_at: datetime

    user_name: str | None = None


class ActivityListResponse(BaseModel):
    items: list[ActivityRead]
    total: int
    limit: int
    offset: int


__all__ = [
    "NoteCreate",
    "NoteUpdate",
    "NoteRead",
    "NoteListResponse",
    "ActivityRead",
    "ActivityListResponse",
]
