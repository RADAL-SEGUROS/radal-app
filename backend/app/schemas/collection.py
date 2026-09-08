"""Pydantic v2 schemas for ``collection_plan`` + ``collection_installment``.

The corpus invariant, true of every plan in the demo files::

    SUM(installment.gross_amount_uf)
        == policy gross premium + SUM(endorsement.total_premium_delta_uf)

with the last instalment absorbing rounding. It is checked with the existing
``MONEY_TOLERANCE``; a mismatch is a **422**, never a silent fix.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CollectionPlanStatus, InstallmentStatus, PaymentMode
from app.schemas.proposal import MONEY_TOLERANCE


def _d(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):  # pragma: no cover - defensive
        return Decimal("0")


def check_installment_total(
    *,
    installments_total: Any,
    policy_gross_uf: Any,
    endorsement_delta_uf: Any = None,
) -> dict[str, Any] | None:
    """Return a 422 detail when Σ instalments ≠ policy gross + Σ endorsement deltas.

    ``None`` means the plan balances (or there is nothing to compare against —
    a policy with no gross premium recorded yet cannot be validated, and refusing
    the write would block the importer for no gain).
    """
    if policy_gross_uf is None:
        return None
    expected = _d(policy_gross_uf) + _d(endorsement_delta_uf)
    actual = _d(installments_total)
    difference = actual - expected
    if abs(difference) <= MONEY_TOLERANCE:
        return None
    return {
        "field": "installments",
        "rule": (
            "sum(installment.gross_amount_uf) = policy gross premium + "
            "sum(endorsement.total_premium_delta_uf)"
        ),
        "expected": str(expected),
        "received": str(actual),
        "difference": str(difference),
        "tolerance": str(MONEY_TOLERANCE),
    }


# --- Instalments ---------------------------------------------------------------

class InstallmentBase(BaseModel):
    number: int = Field(ge=1)
    coupon_number: str | None = Field(default=None, max_length=32)
    due_date: date | None = None
    gross_amount_uf: Decimal
    net_premium_uf: Decimal | None = None
    commission_uf: Decimal | None = None
    paid_on: date | None = None
    days_late: int | None = Field(default=None, ge=0)
    status: InstallmentStatus = InstallmentStatus.PENDING
    endorsement_id: int | None = Field(default=None, gt=0)
    note: str | None = None


class InstallmentCreate(InstallmentBase):
    model_config = ConfigDict(extra="forbid")


class InstallmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coupon_number: str | None = Field(default=None, max_length=32)
    due_date: date | None = None
    gross_amount_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    commission_uf: Decimal | None = None
    paid_on: date | None = None
    days_late: int | None = Field(default=None, ge=0)
    status: InstallmentStatus | None = None
    endorsement_id: int | None = Field(default=None, gt=0)
    note: str | None = None


class InstallmentRead(InstallmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    collection_plan_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class InstallmentReplace(BaseModel):
    """Full replace of the ledger — the Σ is validated before anything is written."""

    model_config = ConfigDict(extra="forbid")

    installments: list[InstallmentCreate] = Field(default_factory=list)


# --- Plans ---------------------------------------------------------------------

class CollectionPlanBase(BaseModel):
    plan_number: str | None = Field(default=None, max_length=64)
    payment_mode: PaymentMode = PaymentMode.COUPON_BOOK
    installment_count: int | None = Field(default=None, ge=0)
    total_premium_uf: Decimal | None = None
    bank: str | None = Field(default=None, max_length=120)
    account_number: str | None = Field(default=None, max_length=64)
    monthly_interest_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    status: CollectionPlanStatus = CollectionPlanStatus.PENDING
    as_of_date: date | None = None
    days_without_cover: int | None = Field(default=None, ge=0)
    rehabilitation_cost_uf: Decimal | None = None
    art528_events: list[Any] | dict[str, Any] | None = None
    management_note: str | None = None


class CollectionPlanCreate(CollectionPlanBase):
    model_config = ConfigDict(extra="forbid")

    policy_id: int = Field(gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    installments: list[InstallmentCreate] = Field(default_factory=list)


class CollectionPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_number: str | None = Field(default=None, max_length=64)
    payment_mode: PaymentMode | None = None
    installment_count: int | None = Field(default=None, ge=0)
    total_premium_uf: Decimal | None = None
    bank: str | None = Field(default=None, max_length=120)
    account_number: str | None = Field(default=None, max_length=64)
    monthly_interest_rate_pct: Decimal | None = Field(default=None, ge=0, le=100)
    status: CollectionPlanStatus | None = None
    as_of_date: date | None = None
    terminated_at: datetime | None = None
    rehabilitated_at: datetime | None = None
    days_without_cover: int | None = Field(default=None, ge=0)
    rehabilitation_cost_uf: Decimal | None = None
    art528_events: list[Any] | dict[str, Any] | None = None
    management_note: str | None = None
    case_file_id: int | None = Field(default=None, gt=0)


class CollectionPlanRead(CollectionPlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    policy_id: int
    case_file_id: int | None = None
    terminated_at: datetime | None = None
    rehabilitated_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    installments: list[InstallmentRead] = Field(default_factory=list)


class CollectionPlanPage(BaseModel):
    items: list[CollectionPlanRead]
    total: int
    limit: int
    offset: int


class CollectionAlert(BaseModel):
    code: str
    severity: str
    detail: str


class CollectionStatusRead(BaseModel):
    """The derived dashboard: outstanding, overdue, compliance %, alerts."""

    collection_plan_id: int
    policy_id: int
    status: CollectionPlanStatus
    as_of_date: date | None = None
    installment_count: int
    total_scheduled_uf: Decimal
    paid_uf: Decimal
    outstanding_uf: Decimal
    overdue_uf: Decimal
    overdue_count: int
    paid_count: int
    compliance_pct: Decimal
    next_due_date: date | None = None
    expected_total_uf: Decimal | None = None
    balances: bool = True
    alerts: list[CollectionAlert] = Field(default_factory=list)


__all__ = [
    "check_installment_total",
    "InstallmentCreate",
    "InstallmentUpdate",
    "InstallmentRead",
    "InstallmentReplace",
    "CollectionPlanCreate",
    "CollectionPlanUpdate",
    "CollectionPlanRead",
    "CollectionPlanPage",
    "CollectionStatusRead",
    "CollectionAlert",
]
