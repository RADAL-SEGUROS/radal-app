"""Pydantic v2 schemas for the v12 cobranza surface (02 §4).

The cuota board (``GET /collections/installments``), the cards
(``GET /collections/summary``), payment writes, promises, the level resolver,
broker settings and notice templates. Money is ``Decimal`` (UF 4 dp); CLP is
an integer. ``reason``/``detail`` strings meant for display are Spanish.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    CollectionLevelPreference,
    EvidenceLevel,
    InstallmentStatus,
    MailSender,
    NoticeChannel,
    NoticeKind,
    PaymentCurrency,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    PolicyCurrency,
    ReversalReason,
)
from app.schemas.analytics import EntitySummary
from app.schemas.utc import UtcDatetime

#: ``review`` (review r2 D1): past cuotas of a policy loaded late, awaiting the
#: broker's paid/unpaid decision (state ``pending_review``).
Bucket = Literal["upcoming", "overdue", "current", "settled", "review"]
Level = Literal["record", "assisted", "native"]


# --- board -------------------------------------------------------------------

class LastNote(BaseModel):
    id: int
    body: str
    created_at: UtcDatetime | None = None
    follow_up_on: date | None = None
    author_id: int | None = None


class InstallmentRow(BaseModel):
    """One cuota on the Cobranza board (live state, not the cache)."""

    id: int
    plan_id: int
    policy_id: int
    case_file_id: int | None = None
    number: int
    installment_count: int | None = None
    coupon_number: str | None = None
    due_date: date | None = None
    currency: PolicyCurrency = PolicyCurrency.UF
    gross_amount_uf: Decimal
    paid_uf: Decimal
    balance_uf: Decimal
    #: CLP of the open balance at today's UF (``None`` when no rate / USD cuota).
    balance_clp: int | None = None
    state: str
    flags: list[str] = Field(default_factory=list)
    buckets: list[str] = Field(default_factory=list)
    days_late: int = 0
    evidence: str | None = None
    paid_on: date | None = None
    status: InstallmentStatus
    level: Level
    level_reason: str
    level_missing: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    promise_until: date | None = None
    direct_debit_rejected_at: date | None = None
    last_note: LastNote | None = None
    policy_number: str
    insured_name: str | None = None
    insured_rut: str | None = None
    insurer_id: int | None = None
    insurer_name: str | None = None
    line_code: str | None = None
    line_name: str | None = None
    payment_mode: PaymentMode | None = None
    #: Which automatic debit a rejection refers to: ``pac`` (bank account,
    #: ``direct_debit``) | ``pat`` (card, ``card_debit``) | ``None`` — so the UI
    #: never says "PAC rechazado" on a PAT cuota (review v12 P3).
    direct_debit_kind: Literal["pac", "pat"] | None = None
    notices_enabled: bool = False


class InstallmentRowPage(BaseModel):
    items: list[InstallmentRow]
    total: int
    limit: int
    offset: int
    as_of: date
    uf_rate: Decimal | None = None
    counts: dict[str, int] = Field(default_factory=dict)


class MoneyCount(BaseModel):
    count: int
    uf: Decimal


class CollectionsSummary(EntitySummary):
    """The legacy generic summary (status split, scope, group_by) + the v12 cards."""

    as_of: date
    due_7d: MoneyCount
    due_30d: MoneyCount
    overdue: MoneyCount
    termination_risk: MoneyCount
    collected_month_uf: Decimal
    unconfirmed_payments: int
    pending_evidence: int
    plans_missing: int
    #: Review r2 (D1): cuotas pending review (never in ``overdue``).
    past_installments_pending: MoneyCount = Field(
        default_factory=lambda: MoneyCount(count=0, uf=Decimal("0"))
    )
    #: Review r2 (B5): ``mine`` when the cuota cards were narrowed with
    #: ``owner=me`` / ``mine=1`` (the team inboxes stay broker-wide).
    owner_scope: Literal["all", "mine"] = "all"
    #: Review r3 (R7, owner decision): the scope EACH card was computed in. The team
    #: inboxes (``unconfirmed_payments`` "Pagos sin confirmar", ``pending_evidence``
    #: "Respuestas por confirmar", ``plans_missing``) are ``"team"`` — broker-wide in
    #: both scopes, labelled "Equipo" in the UI; every cuota card is ``owner_scope``.
    card_scopes: dict[str, Literal["all", "mine", "team"]] = Field(default_factory=dict)


# --- payments ------------------------------------------------------------------

class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0)
    currency: PaymentCurrency = PaymentCurrency.UF
    paid_on: date
    method: PaymentMode | None = None
    receipt_document_id: int | None = Field(default=None, gt=0)
    confirmed_by_insurer: bool = False
    note: str | None = Field(default=None, max_length=4000)


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    installment_id: int
    amount: Decimal
    currency: PaymentCurrency
    uf_rate: Decimal | None = None
    amount_uf: Decimal
    paid_on: date
    method: PaymentMode | None = None
    source: PaymentSource
    evidence_level: EvidenceLevel
    status: PaymentStatus
    reversal_reason: ReversalReason | None = None
    reversal_note: str | None = None
    reversed_at: UtcDatetime | None = None
    confirmed_by_insurer: bool = False
    confirmed_at: UtcDatetime | None = None
    receipt_document_id: int | None = None
    registered_by_user_id: int | None = None
    note: str | None = None
    created_at: UtcDatetime | None = None


class InstallmentStateRead(BaseModel):
    state: str
    flags: list[str] = Field(default_factory=list)
    paid_uf: Decimal
    balance_uf: Decimal
    days_late: int
    evidence: str | None = None
    paid_on: date | None = None


class PaymentResult(BaseModel):
    """A payment write: the payment and the cuota's new state."""

    payment: PaymentRead
    installment_id: int
    status: InstallmentStatus
    state: InstallmentStateRead


class PaymentReverse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ReversalReason
    note: str | None = Field(default=None, max_length=4000)


class InstallmentResult(BaseModel):
    """A cuota write (debit rejected, promise): the cuota's new state."""

    installment_id: int
    status: InstallmentStatus
    state: InstallmentStateRead
    promise_until: date | None = None
    direct_debit_rejected_at: date | None = None
    note_id: int | None = None


class DirectDebitRejected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rejected_on: date
    note: str | None = Field(default=None, max_length=4000)


class PromiseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promise_until: date
    note: str | None = Field(default=None, max_length=4000)


class ModeRead(BaseModel):
    level: Level
    reason: str
    channels: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class PlanStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level_preference: CollectionLevelPreference | None = None


# --- settings ------------------------------------------------------------------

class CollectionSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cadence_days: list[int]
    followup_days: int
    tolerance_uf: Decimal
    tolerance_pct: Decimal
    tolerance_clp: int
    notices_enabled: bool
    notices_approved_at: UtcDatetime | None = None
    notices_approved_by_user_id: int | None = None
    default_sender: MailSender
    sender_mail_account_id: int | None = None
    native_collection_enabled: bool
    auto_status_inquiries: bool
    inquiry_after_days: int
    updated_at: UtcDatetime | None = None


class CollectionSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence_days: list[int] | None = Field(default=None, min_length=1, max_length=10)
    followup_days: int | None = Field(default=None, ge=1, le=60)
    tolerance_uf: Decimal | None = Field(default=None, ge=0, le=1)
    tolerance_pct: Decimal | None = Field(default=None, ge=0, le=10)
    tolerance_clp: int | None = Field(default=None, ge=0, le=100_000)
    notices_enabled: bool | None = None
    default_sender: MailSender | None = None
    sender_mail_account_id: int | None = Field(default=None, gt=0)
    native_collection_enabled: bool | None = None
    auto_status_inquiries: bool | None = None
    inquiry_after_days: int | None = Field(default=None, ge=0, le=90)

    @field_validator("cadence_days")
    @classmethod
    def _cadence(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(step < -90 or step > 180 for step in value):
            raise ValueError("cada paso de la cadencia debe estar entre -90 y 180 días")
        if len(set(value)) != len(value):
            raise ValueError("la cadencia no puede repetir días")
        return sorted(value)


# --- templates -----------------------------------------------------------------

class NoticeTemplateRead(BaseModel):
    id: int | None = None
    kind: NoticeKind
    cadence_level: int | None = None
    channel: NoticeChannel
    subject: str
    body: str
    is_active: bool = True
    is_default: bool = False
    #: Approval is PER TEMPLATE (review v12 #14): editing one template
    #: un-approves only that one; the job sends a slot only when it is approved.
    is_approved: bool = False
    approved_at: UtcDatetime | None = None
    approved_by_user_id: int | None = None
    approved_by_name: str | None = None


class NoticeTemplatesRead(BaseModel):
    items: list[NoticeTemplateRead]
    variables: list[str]
    #: True when EVERY active template is approved.
    approved: bool
    #: The broker's latest approval (any template); ``None`` = nothing approved.
    approved_at: UtcDatetime | None = None
    approved_by_user_id: int | None = None
    approved_count: int = 0
    #: Active templates still waiting for approval (they send nothing).
    pending_count: int = 0


class TemplateSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: NoticeKind
    cadence_level: int | None = Field(default=None, ge=-90, le=180)
    channel: NoticeChannel = NoticeChannel.EMAIL


class NoticeTemplatesApprove(BaseModel):
    """``POST /collections/templates/approve`` — omit ``items`` to approve all."""

    model_config = ConfigDict(extra="forbid")

    items: list[TemplateSlot] | None = Field(default=None, max_length=50)


class NoticeTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: NoticeKind
    cadence_level: int | None = Field(default=None, ge=-90, le=180)
    channel: NoticeChannel = NoticeChannel.EMAIL
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20_000)
    is_active: bool = True


class NoticeTemplatesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    templates: list[NoticeTemplateIn] = Field(min_length=1, max_length=50)


__all__ = [
    "CollectionSettingsRead",
    "CollectionSettingsUpdate",
    "CollectionsSummary",
    "DirectDebitRejected",
    "InstallmentResult",
    "InstallmentRow",
    "InstallmentRowPage",
    "InstallmentStateRead",
    "LastNote",
    "ModeRead",
    "MoneyCount",
    "NoticeTemplateIn",
    "NoticeTemplatesApprove",
    "TemplateSlot",
    "NoticeTemplateRead",
    "NoticeTemplatesRead",
    "NoticeTemplatesUpdate",
    "PaymentCreate",
    "PaymentRead",
    "PaymentResult",
    "PaymentReverse",
    "PlanStart",
    "PromiseCreate",
]
