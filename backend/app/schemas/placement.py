"""Pydantic v2 schemas for ``placement`` — the operating folder.

One placement = one asset x one insurance line x one period. It carries the
workflow status machine every downstream artefact hangs off:

``draft -> inspection -> pre_underwriting -> quoting -> negotiating -> awarded
-> active -> closed``

Status is NEVER changed through the generic PATCH: it moves only through
``POST /placements/{id}/transition``, which validates the move against
:data:`app.api.routers.placements.ALLOWED_TRANSITIONS`. That keeps the UI free
of dead controls — ``GET /placements/{id}/transitions`` tells the client exactly
which buttons to enable.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.placement import PlacementStatus

Period = Annotated[str, Field(min_length=1, max_length=32)]


class PlacementCreate(BaseModel):
    """Open a placement on one of the broker's assets.

    ``client_id`` is derived from the asset — it is never taken from the body,
    so a placement can never be filed against another client's asset.
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: int
    insurance_line_id: int
    period: Period | None = None
    period_start: date | None = None
    period_end: date | None = None
    status: PlacementStatus = PlacementStatus.DRAFT
    notes: str | None = None
    # FK to `document` — never a raw S3 key (v2-architecture.md rule 8).
    brief_document_id: int | None = None

    @model_validator(mode="after")
    def _check_period(self) -> "PlacementCreate":
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end must not be earlier than period_start")
        return self


class PlacementUpdate(BaseModel):
    """Patch a placement. ``status`` is intentionally absent — use /transition."""

    model_config = ConfigDict(extra="forbid")

    insurance_line_id: int | None = None
    period: Period | None = None
    period_start: date | None = None
    period_end: date | None = None
    notes: str | None = None
    brief_document_id: int | None = None


class PlacementTransition(BaseModel):
    """Move a placement to the next status, with an optional audit note."""

    model_config = ConfigDict(extra="forbid")

    status: PlacementStatus
    note: str | None = None


class PlacementTransitionOptions(BaseModel):
    """What the UI may offer right now (so no control is ever a dead button)."""

    id: int
    status: PlacementStatus
    allowed: list[PlacementStatus]
    is_terminal: bool


class PlacementRefSummary(BaseModel):
    """A minimal reference (client / asset / insurance line) embedded in a row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None = None


class PlacementClientSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    legal_name: str | None = None
    rut: str | None = None


class PlacementListItem(BaseModel):
    """One row of the placements list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    client_id: int
    asset_id: int
    insurance_line_id: int
    period: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    status: PlacementStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None

    client: PlacementClientSummary | None = None
    asset: PlacementRefSummary | None = None
    insurance_line: PlacementRefSummary | None = None

    quote_requests_count: int = 0
    proposals_count: int = 0
    inspection_requests_count: int = 0
    days_to_period_end: int | None = None


class PlacementRead(PlacementListItem):
    """Full placement detail."""

    notes: str | None = None
    brief_document_id: int | None = None
    policies_count: int = 0
    allowed_transitions: list[PlacementStatus] = Field(default_factory=list)


class PlacementPage(BaseModel):
    items: list[PlacementListItem]
    total: int
    page: int
    page_size: int
    pages: int


class PlacementSummary(BaseModel):
    """Aggregate counters for the placements list view."""

    total: int
    by_status: dict[str, int]
    by_insurance_line: dict[str, int]
    open: int = Field(description="Not yet closed")
    in_market: int = Field(description="quoting or negotiating")
    awaiting_inspection: int
    expiring_within_60_days: int


__all__ = [
    "PlacementCreate",
    "PlacementUpdate",
    "PlacementTransition",
    "PlacementTransitionOptions",
    "PlacementRefSummary",
    "PlacementClientSummary",
    "PlacementListItem",
    "PlacementRead",
    "PlacementPage",
    "PlacementSummary",
]
