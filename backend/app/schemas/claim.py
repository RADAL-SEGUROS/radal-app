"""Pydantic v2 schemas for ``claim`` and its per-partida ``claim_item`` children."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ClaimItemKind, ClaimRuling
from app.models.policy import ClaimStatus


# --- Items ----------------------------------------------------------------------

class ClaimItemBase(BaseModel):
    kind: ClaimItemKind = ClaimItemKind.MATERIAL_DAMAGE
    item: str | None = Field(default=None, max_length=255)
    basis: str | None = None
    notified_uf: Decimal | None = None
    determined_uf: Decimal | None = None
    damage_uf: Decimal | None = None
    deductible_uf: Decimal | None = None
    indemnity_uf: Decimal | None = None
    sort_order: int = 0
    note: str | None = None


class ClaimItemCreate(ClaimItemBase):
    model_config = ConfigDict(extra="forbid")


class ClaimItemRead(ClaimItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    claim_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ClaimItemsReplace(BaseModel):
    """Full replace of the adjuster's table — the shape the reports arrive in."""

    model_config = ConfigDict(extra="forbid")

    items: list[ClaimItemCreate] = Field(default_factory=list)


class ClaimItemTotals(BaseModel):
    notified_uf: Decimal
    determined_uf: Decimal
    damage_uf: Decimal
    deductible_uf: Decimal
    indemnity_uf: Decimal


class ClaimItemsRead(BaseModel):
    claim_id: int
    items: list[ClaimItemRead]
    totals: ClaimItemTotals


# --- Claims -----------------------------------------------------------------------

class ClaimCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: int | None = Field(default=None, gt=0)
    client_id: int | None = Field(default=None, gt=0)
    asset_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)

    claim_number: str | None = Field(default=None, max_length=64)
    kind: str | None = Field(default=None, max_length=160)
    event_date: date | None = None
    reported_date: date | None = None
    occurred_at: datetime | None = None
    reported_at: datetime | None = None
    notice_deadline_days: int | None = Field(default=None, ge=0)
    description: str | None = None
    status: ClaimStatus = ClaimStatus.REPORTED

    adjuster_name: str | None = Field(default=None, max_length=160)
    adjuster_registry: str | None = Field(default=None, max_length=32)
    coverage_ruling: ClaimRuling = ClaimRuling.PENDING
    deductible_uf: Decimal | None = None
    loss_ratio_pct: Decimal | None = Field(default=None, ge=0)

    estimated_amount_uf: Decimal | None = None
    settled_amount_uf: Decimal | None = None
    paid_amount_uf: Decimal | None = None
    recovery_uf: Decimal | None = None
    reserve_uf: Decimal | None = None
    cost_uf: Decimal | None = None
    participation: list[Any] | None = None

    items: list[ClaimItemCreate] = Field(default_factory=list)


class ClaimUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: int | None = Field(default=None, gt=0)
    asset_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)

    claim_number: str | None = Field(default=None, max_length=64)
    kind: str | None = Field(default=None, max_length=160)
    event_date: date | None = None
    reported_date: date | None = None
    occurred_at: datetime | None = None
    reported_at: datetime | None = None
    notice_deadline_days: int | None = Field(default=None, ge=0)
    description: str | None = None
    status: ClaimStatus | None = None

    adjuster_name: str | None = Field(default=None, max_length=160)
    adjuster_registry: str | None = Field(default=None, max_length=32)
    coverage_ruling: ClaimRuling | None = None
    deductible_uf: Decimal | None = None
    loss_ratio_pct: Decimal | None = Field(default=None, ge=0)

    estimated_amount_uf: Decimal | None = None
    settled_amount_uf: Decimal | None = None
    paid_amount_uf: Decimal | None = None
    recovery_uf: Decimal | None = None
    reserve_uf: Decimal | None = None
    cost_uf: Decimal | None = None
    participation: list[Any] | None = None


class ClaimClose(BaseModel):
    """The adjuster's final ruling — the only way a claim reaches ``closed``."""

    model_config = ConfigDict(extra="forbid")

    coverage_ruling: ClaimRuling
    settled_amount_uf: Decimal | None = None
    paid_amount_uf: Decimal | None = None
    deductible_uf: Decimal | None = None
    recovery_uf: Decimal | None = None
    loss_ratio_pct: Decimal | None = Field(default=None, ge=0)
    note: str | None = None


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    policy_id: int | None = None
    client_id: int
    asset_id: int | None = None
    case_file_id: int | None = None

    claim_number: str | None = None
    kind: str | None = None
    event_date: date | None = None
    reported_date: date | None = None
    occurred_at: datetime | None = None
    reported_at: datetime | None = None
    notice_deadline_days: int | None = None
    description: str | None = None
    status: ClaimStatus

    adjuster_name: str | None = None
    adjuster_registry: str | None = None
    coverage_ruling: ClaimRuling
    deductible_uf: Decimal | None = None
    loss_ratio_pct: Decimal | None = None

    estimated_amount_uf: Decimal | None = None
    settled_amount_uf: Decimal | None = None
    paid_amount_uf: Decimal | None = None
    recovery_uf: Decimal | None = None
    reserve_uf: Decimal | None = None
    cost_uf: Decimal | None = None
    participation: list[Any] | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    items: list[ClaimItemRead] = Field(default_factory=list)
    policy_number: str | None = None


class ClaimPage(BaseModel):
    items: list[ClaimRead]
    total: int
    limit: int
    offset: int


__all__ = [
    "ClaimCreate",
    "ClaimUpdate",
    "ClaimClose",
    "ClaimRead",
    "ClaimPage",
    "ClaimItemCreate",
    "ClaimItemRead",
    "ClaimItemsReplace",
    "ClaimItemsRead",
    "ClaimItemTotals",
]
