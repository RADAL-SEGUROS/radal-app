"""Pydantic v2 schemas for ``document`` — the only table that holds an S3 key.

The key is never accepted from a client: it is DERIVED by the documents router
from ``documents/{entity_type}/{entity_id}/{category}-{n}.{ext}``. These schemas
therefore expose ``s3_key`` read-only and never on a write model.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentCategory
from app.models.enums import EntityType

# --- Entity types a document may be attached to ------------------------------
# Mirrors the S3 route documents/{entity_type}/{entity_id}/... The full
# EntityType vocabulary is allowed; the router additionally proves the target
# row exists inside the caller's broker.
ATTACHABLE_ENTITY_TYPES: tuple[EntityType, ...] = tuple(EntityType)


class DocumentBase(BaseModel):
    entity_type: EntityType
    entity_id: int = Field(gt=0)
    category: DocumentCategory = DocumentCategory.OTHER
    phase: str | None = Field(default=None, max_length=120)


class DocumentUpdate(BaseModel):
    """Metadata-only patch. The stored bytes and their key are immutable."""

    model_config = ConfigDict(extra="forbid")

    category: DocumentCategory | None = None
    phase: str | None = Field(default=None, max_length=120)
    original_name: str | None = Field(default=None, min_length=1, max_length=255)


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    entity_type: EntityType
    entity_id: int
    s3_key: str
    bucket: str
    original_name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    category: DocumentCategory
    phase: str | None = None
    uploaded_by_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentDownload(BaseModel):
    """A short-lived URL for the stored bytes.

    ``expires_in_seconds`` is ``None`` for the local backend, whose URLs are
    served statically and do not expire.
    """

    id: int
    original_name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    s3_key: str
    url: str
    expires_in_seconds: int | None = None


class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentRead]


__all__ = [
    "ATTACHABLE_ENTITY_TYPES",
    "DocumentBase",
    "DocumentUpdate",
    "DocumentRead",
    "DocumentDownload",
    "DocumentListResponse",
]
