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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CollectionLevelPreference,
    CollectionPlanOrigin,
    CollectionPlanSource,
    CollectionPlanStatus,
    InstallmentStatus,
    PaymentMode,
    PolicyCurrency,
)
from app.schemas.proposal import MONEY_TOLERANCE
from app.schemas.utc import UtcDatetime
from app.schemas.wallclock import WallClockDatetime


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
    # v12 cobranza (``status`` above is the CACHE the state machine writes).
    currency: PolicyCurrency = PolicyCurrency.UF
    direct_debit_rejected_at: date | None = None
    voided_by_endorsement_id: int | None = None
    insurer_notice_sent_on: date | None = None
    promise_until: date | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


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
    # v12: where the plan came from and how it is collected.
    origin: CollectionPlanOrigin | None = None
    source: CollectionPlanSource | None = None
    level_preference: CollectionLevelPreference = CollectionLevelPreference.AUTO
    #: Start cobranza on save (payer reminders ON), like ``POST
    #: /collections/plans/{id}/start`` — review v12 #15.
    start_collection: bool = False


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
    terminated_at: WallClockDatetime | None = None
    rehabilitated_at: WallClockDatetime | None = None
    days_without_cover: int | None = Field(default=None, ge=0)
    rehabilitation_cost_uf: Decimal | None = None
    art528_events: list[Any] | dict[str, Any] | None = None
    management_note: str | None = None
    case_file_id: int | None = Field(default=None, gt=0)
    # v12 (``PATCH /collections/plans/{id}``; also accepted by the legacy PATCH).
    notices_enabled: bool | None = None
    level_preference: CollectionLevelPreference | None = None
    notices_paused_until: date | None = None
    notices_pause_reason: str | None = Field(default=None, max_length=2000)


class PastInstallmentsRead(BaseModel):
    """Review r2 (D1): the plan's past cuotas awaiting the broker's paid/unpaid
    decision (``POST /collections/plans/{plan_id}/past-installments``)."""

    plan_id: int
    count: int
    total_uf: Decimal
    first_due: date
    last_due: date


class PastInstallmentsDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["paid", "unpaid"]


class PastInstallmentsDecisionRead(BaseModel):
    plan_id: int
    decision: Literal["paid", "unpaid"]
    #: The same decision was already recorded: nothing was written.
    already_decided: bool = False
    payments_registered: int = 0
    past_installments_pending: PastInstallmentsRead | None = None
    #: Live state -> number of cuotas of the plan after the decision.
    counts: dict[str, int] = Field(default_factory=dict)


class CollectionPlanRead(CollectionPlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    policy_id: int
    case_file_id: int | None = None
    terminated_at: WallClockDatetime | None = None
    rehabilitated_at: WallClockDatetime | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    # v12 cobranza
    origin: CollectionPlanOrigin | None = None
    source: CollectionPlanSource | None = None
    source_document_id: int | None = None
    mandate_id: int | None = None
    level_preference: CollectionLevelPreference = CollectionLevelPreference.AUTO
    notices_enabled: bool = False
    notices_paused_until: date | None = None
    notices_pause_reason: str | None = None

    installments: list[InstallmentRead] = Field(default_factory=list)
    #: Spanish heads-ups from the write that produced this read (e.g. cuotas
    #: born overdue because their due date is already past). Empty on reads.
    warnings: list[str] = Field(default_factory=list)
    #: Review r2 (D1): past cuotas awaiting the paid/unpaid decision, or null.
    past_installments_pending: PastInstallmentsRead | None = None


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
