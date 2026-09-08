"""Pydantic v2 schemas for ``case_pack`` — the generated downloadables."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CaseSection, PackKind, PackStatus
from app.schemas.document import DocumentDownload


class RecipientRead(BaseModel):
    """A native insurer + the contact the broker+line → broker → line → global
    precedence resolves to."""

    insurer_id: int
    legal_name: str
    is_native: bool
    contact_name: str | None = None
    contact_email: str | None = None
    resolution_level: str | None = None


class RecipientList(BaseModel):
    case_file_id: int
    insurance_line_id: int | None = None
    items: list[RecipientRead] = Field(default_factory=list)


class CasePackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    case_file_id: int
    kind: PackKind
    section: CaseSection | None = None
    status: PackStatus
    pdf_document_id: int | None = None
    zip_document_id: int | None = None
    summary: str | None = None
    summary_model: str | None = None
    summary_prompt_version: str | None = None
    is_summary_confirmed: bool = False
    recipients: list[RecipientRead] = Field(default_factory=list)
    generated_at: datetime | None = None
    sent_at: datetime | None = None
    generated_by_id: int | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    pdf: DocumentDownload | None = None
    zip: DocumentDownload | None = None

    @field_validator("recipients", mode="before")
    @classmethod
    def _recipients_never_null(cls, value):
        """``case_pack.recipients`` is a nullable JSON snapshot; the API says ``[]``."""
        if value is None:
            return []
        return value


class CasePackList(BaseModel):
    case_file_id: int
    items: list[CasePackRead] = Field(default_factory=list)


class PackSummaryConfirm(BaseModel):
    """Suggest → human edit → confirm, applied to prose (§7.5)."""

    model_config = ConfigDict(extra="forbid")

    summary: str | None = None
    is_summary_confirmed: bool = True


PackPart = Literal["pdf", "zip"]


__all__ = [
    "RecipientRead",
    "RecipientList",
    "CasePackRead",
    "CasePackList",
    "PackSummaryConfirm",
    "PackPart",
]
