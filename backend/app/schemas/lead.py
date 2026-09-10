"""Pydantic v2 schemas for ``sales_lead`` — the data-point-only lead.

The RUT is OPTIONAL by design: a lead exists before identity is known and the
broker is never blocked (rule 7). When one IS supplied it is validated mod-11 and
normalized to ``BODY-DV``, exactly like a client's.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import LeadStatus
from app.schemas.analytics import GroupedSummary
from app.services.identifiers import InvalidRut, validate_rut


def _optional_rut(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return validate_rut(text)
    except InvalidRut as exc:
        raise ValueError(str(exc)) from exc


class LeadBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    rut: str | None = Field(default=None, max_length=16)
    contact_name: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=80)
    insurance_line_id: int | None = Field(default=None, gt=0)
    estimated_premium_uf: Decimal | None = Field(default=None, ge=0)
    follow_up_on: date | None = None
    owner_user_id: int | None = Field(default=None, gt=0)
    summary: str | None = None

    @field_validator("rut", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        return _optional_rut(value)


class LeadCreate(LeadBase):
    model_config = ConfigDict(extra="forbid")

    status: LeadStatus = LeadStatus.NEW


class LeadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    rut: str | None = Field(default=None, max_length=16)
    contact_name: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=80)
    insurance_line_id: int | None = Field(default=None, gt=0)
    estimated_premium_uf: Decimal | None = Field(default=None, ge=0)
    status: LeadStatus | None = None
    follow_up_on: date | None = None
    owner_user_id: int | None = Field(default=None, gt=0)
    lost_reason: str | None = Field(default=None, max_length=255)
    summary: str | None = None

    @field_validator("rut", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        return _optional_rut(value)


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    name: str
    rut: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    source: str | None = None
    insurance_line_id: int | None = None
    estimated_premium_uf: Decimal | None = None
    status: LeadStatus
    follow_up_on: date | None = None
    owner_user_id: int | None = None
    lost_reason: str | None = None
    converted_client_id: int | None = None
    converted_case_file_id: int | None = None
    summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    insurance_line_name: str | None = None
    notes_count: int = 0


class LeadListResponse(BaseModel):
    items: list[LeadRead]
    total: int
    limit: int
    offset: int


class LeadSummary(BaseModel):
    total: int
    by_status: dict[str, int]
    due_this_week: int
    overdue: int
    grouped: GroupedSummary | None = Field(
        default=None,
        description="Buckets listos para el gráfico cuando se pide ?group_by=.",
    )


class LeadAssetInput(BaseModel):
    """The minimum an asset needs so the placement has something to sit on."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    asset_type: str = Field(default="property", max_length=64)
    address: str | None = Field(default=None, max_length=255)
    commune: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)


class LeadConvert(BaseModel):
    """Advance a lead into a real case: insured + client + placement + case file."""

    model_config = ConfigDict(extra="forbid")

    insured_rut: str = Field(min_length=3, max_length=20)
    insured_legal_name: str = Field(min_length=1, max_length=255)
    insurance_line_id: int | None = Field(default=None, gt=0)
    period: str | None = Field(default=None, max_length=32)
    period_start: date | None = None
    period_end: date | None = None
    title: str | None = Field(default=None, max_length=255)
    asset: LeadAssetInput | None = None

    @field_validator("insured_rut", mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        try:
            return validate_rut(str(value))
        except InvalidRut as exc:
            raise ValueError(str(exc)) from exc


class LeadConversionResult(BaseModel):
    """Everything the conversion created, in one payload."""

    lead: LeadRead
    client_id: int
    insured_id: int
    asset_id: int
    placement_id: int
    case_file_id: int
    case_file_reference: str | None = None


__all__ = [
    "LeadCreate",
    "LeadUpdate",
    "LeadRead",
    "LeadListResponse",
    "LeadSummary",
    "LeadConvert",
    "LeadAssetInput",
    "LeadConversionResult",
]
