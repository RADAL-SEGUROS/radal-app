"""Pydantic v2 schemas for inspection requests, inspections and boundaries.

Shape follows docs/v2-data-modeling-decisions.md §7:
  * scores are flat COLUMNS (filterable), validated 0-100;
  * the checklist is free-form versioned JSON;
  * boundaries (colindancias) are child rows with their own CRUD.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Priority
from app.models.inspection import InspectionRequestStatus, InspectionStatus

def _score() -> Any:
    """A 0-100 score field. A fresh FieldInfo per field (never share one)."""
    return Field(default=None, ge=0, le=100)


# =============================================================================
# inspection_request
# =============================================================================

class InspectionRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(gt=0)
    placement_id: int | None = Field(default=None, gt=0)
    insurance_line_id: int | None = Field(default=None, gt=0)
    reason: str | None = None
    urgency: Priority = Priority.NORMAL
    target_date: date | None = None
    status: InspectionRequestStatus = InspectionRequestStatus.PENDING


class InspectionRequestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    placement_id: int | None = Field(default=None, gt=0)
    insurance_line_id: int | None = Field(default=None, gt=0)
    reason: str | None = None
    urgency: Priority | None = None
    target_date: date | None = None
    status: InspectionRequestStatus | None = None


class InspectionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    asset_id: int
    placement_id: int | None = None
    insurance_line_id: int | None = None
    reason: str | None = None
    urgency: Priority
    target_date: date | None = None
    status: InspectionRequestStatus
    requested_by_id: int | None = None
    requested_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InspectionRequestListResponse(BaseModel):
    total: int
    items: list[InspectionRequestRead]


# =============================================================================
# inspection_boundary (child)
# =============================================================================

class InspectionBoundaryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orientation: str | None = Field(default=None, max_length=64)
    description: str | None = None
    distance: str | None = Field(default=None, max_length=64)
    aggravating: str | None = Field(default=None, max_length=255)
    is_aggravating: bool = False
    sort_order: int = 0


class InspectionBoundaryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orientation: str | None = Field(default=None, max_length=64)
    description: str | None = None
    distance: str | None = Field(default=None, max_length=64)
    aggravating: str | None = Field(default=None, max_length=255)
    is_aggravating: bool | None = None
    sort_order: int | None = None


class InspectionBoundaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inspection_id: int
    orientation: str | None = None
    description: str | None = None
    distance: str | None = None
    aggravating: str | None = None
    is_aggravating: bool
    sort_order: int


# =============================================================================
# inspection
# =============================================================================

class InspectionScores(BaseModel):
    """The eight promoted score columns, all 0-100."""

    technical_score: Decimal | None = _score()
    commercial_score: Decimal | None = _score()
    location_score: Decimal | None = _score()
    loss_estimate_score: Decimal | None = _score()
    overall_score: Decimal | None = _score()
    pml_pct: Decimal | None = _score()
    eml_pct: Decimal | None = _score()
    risk_classification: str | None = Field(default=None, max_length=255)


class InspectionCreate(InspectionScores):
    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(gt=0)
    inspection_request_id: int | None = Field(default=None, gt=0)
    inspector_id: int | None = Field(default=None, gt=0)
    # Omit to let the server assign the next version for this asset.
    version: int | None = Field(default=None, ge=1)
    status: InspectionStatus = InspectionStatus.DRAFT
    visit_date: date | None = None
    report_date: date | None = None
    folio: str | None = Field(default=None, max_length=64)
    findings_summary: str | None = None
    checklist: dict[str, Any] | None = None
    checklist_version: int | None = Field(default=None, ge=1)
    report_document_id: int | None = Field(default=None, gt=0)
    boundaries: list[InspectionBoundaryCreate] = Field(default_factory=list)


class InspectionUpdate(InspectionScores):
    model_config = ConfigDict(extra="forbid")

    inspection_request_id: int | None = Field(default=None, gt=0)
    inspector_id: int | None = Field(default=None, gt=0)
    status: InspectionStatus | None = None
    visit_date: date | None = None
    report_date: date | None = None
    folio: str | None = Field(default=None, max_length=64)
    findings_summary: str | None = None
    checklist: dict[str, Any] | None = None
    checklist_version: int | None = Field(default=None, ge=1)
    report_document_id: int | None = Field(default=None, gt=0)


class InspectionAssign(BaseModel):
    """Assign (or clear) the inspector who owns the report."""

    model_config = ConfigDict(extra="forbid")

    inspector_id: int | None = Field(default=None, gt=0)


class InspectionStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: InspectionStatus


class InspectionChecklistUpdate(BaseModel):
    """Replace the checklist wholesale; the version tracks its template."""

    model_config = ConfigDict(extra="forbid")

    checklist: dict[str, Any]
    checklist_version: int | None = Field(default=None, ge=1)


class InspectionVersionCreate(BaseModel):
    """Fork an existing inspection into the next version for the same asset."""

    model_config = ConfigDict(extra="forbid")

    copy_checklist: bool = True
    copy_scores: bool = False
    copy_boundaries: bool = True
    inspector_id: int | None = Field(default=None, gt=0)
    visit_date: date | None = None


class InspectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    asset_id: int
    inspection_request_id: int | None = None
    inspector_id: int | None = None
    version: int
    status: InspectionStatus
    visit_date: date | None = None
    report_date: date | None = None
    folio: str | None = None
    findings_summary: str | None = None
    technical_score: Decimal | None = None
    commercial_score: Decimal | None = None
    location_score: Decimal | None = None
    loss_estimate_score: Decimal | None = None
    overall_score: Decimal | None = None
    pml_pct: Decimal | None = None
    eml_pct: Decimal | None = None
    risk_classification: str | None = None
    checklist: dict[str, Any] | None = None
    checklist_version: int | None = None
    report_document_id: int | None = None
    boundaries: list[InspectionBoundaryRead] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("checklist", mode="before")
    @classmethod
    def _empty_checklist_is_none(cls, v: Any) -> Any:
        return v or None


class InspectionListResponse(BaseModel):
    total: int
    items: list[InspectionRead]


__all__ = [
    "InspectionRequestCreate",
    "InspectionRequestUpdate",
    "InspectionRequestRead",
    "InspectionRequestListResponse",
    "InspectionBoundaryCreate",
    "InspectionBoundaryUpdate",
    "InspectionBoundaryRead",
    "InspectionScores",
    "InspectionCreate",
    "InspectionUpdate",
    "InspectionAssign",
    "InspectionStatusUpdate",
    "InspectionChecklistUpdate",
    "InspectionVersionCreate",
    "InspectionRead",
    "InspectionListResponse",
]
