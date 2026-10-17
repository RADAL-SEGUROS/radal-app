"""Pydantic v2 schemas for status inquiries, the evidence inbox and payer notices (v12, 02 §6)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    EvidenceSource,
    EvidenceStatus,
    InquiryStatus,
    MailMessageStatus,
    NoticeChannel,
    NoticeKind,
    NoticeStatus,
    PaymentCurrency,
    ReportedStatus,
)
from app.schemas.utc import UtcDatetime


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# --- inquiries -------------------------------------------------------------------

class InquiryCreate(BaseModel):
    policy_id: int
    installment_ids: list[int] = Field(min_length=1, max_length=60)
    to_address: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=4000)
    send_via: Literal["radal", "account", "auto"] = "auto"
    mail_account_id: int | None = None


class InquiryResend(BaseModel):
    message: str | None = Field(default=None, max_length=4000)
    send_via: Literal["radal", "account", "auto"] = "auto"
    mail_account_id: int | None = None


class InquiryInstallment(BaseModel):
    id: int
    number: int
    due_date: date | None = None
    gross_amount_uf: Decimal
    currency: str
    status: str
    #: The LIVE state (payments are the truth): a cuota paid after the inquiry
    #: was created reads ``paid`` here (review v12 P3).
    state: str | None = None
    is_paid: bool = False
    balance_uf: Decimal | None = None


class InquiryMail(BaseModel):
    id: int
    status: MailMessageStatus
    from_address: str | None = None
    to_addresses: list[Any] | None = None
    subject: str | None = None
    occurred_at: datetime | None = None
    error: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class InquiryRead(BaseModel):
    id: int
    token: str
    status: InquiryStatus
    policy_id: int
    policy_number: str | None = None
    insured_name: str | None = None
    #: The insured's RUT for display (``76.345.678-1``).
    insured_rut: str | None = None
    insurer_id: int
    insurer_name: str | None = None
    to_address: str | None = None
    installment_ids: list[int]
    installments: list[InquiryInstallment] = []
    share_link_id: int | None = None
    share_url: str | None = None
    outbound_message_id: int | None = None
    mail_status: MailMessageStatus | None = None
    sent_at: UtcDatetime | None = None
    replied_at: UtcDatetime | None = None
    resolved_at: UtcDatetime | None = None
    created_by_user_id: int | None = None
    created_by_job: bool = False
    created_at: UtcDatetime
    evidence_open: int = 0
    #: Every listed cuota is paid or voided: nothing left to ask.
    all_paid: bool = False

    @field_validator("sent_at", "replied_at", "resolved_at", "created_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class InquiryDetail(InquiryRead):
    messages: list[InquiryMail] = []
    evidence: list["EvidenceRead"] = []


class InquiryPage(BaseModel):
    items: list[InquiryRead]
    total: int
    limit: int
    offset: int


# --- evidence ---------------------------------------------------------------------

class EvidencePaymentLine(BaseModel):
    """One ACTIVE payment already on the cuota (the confirm form lists them)."""

    id: int
    amount_uf: Decimal
    evidence_level: str
    paid_on: date | None = None
    source: str | None = None


class EvidenceInstallmentSummary(BaseModel):
    """The cuota the evidence points at, as the ledger sees it NOW (review v12 #1).

    Amounts are in the cuota's ``currency`` (UF, or USD for dollar policies);
    ``paid_uf`` is capped at ``gross_uf``.
    """

    gross_uf: Decimal
    paid_uf: Decimal
    balance_uf: Decimal
    #: The live state (``upcoming`` | ``due_today`` | ``overdue`` | ``promised`` |
    #: ``termination_risk`` | ``paid`` | ``voided``).
    state: str
    currency: str
    payments: list[EvidencePaymentLine] = []


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: EvidenceStatus
    source: EvidenceSource | None = None
    installment_id: int | None = None
    installment_number: int | None = None
    installment_due_date: date | None = None
    installment_amount_uf: Decimal | None = None
    policy_id: int | None = None
    policy_number: str | None = None
    insurer_name: str | None = None
    status_inquiry_id: int | None = None
    inquiry_token: str | None = None
    mail_message_id: int | None = None
    mail_subject: str | None = None
    mail_from: str | None = None
    document_id: int | None = None
    extraction_id: int | None = None
    reported_status: ReportedStatus | None = None
    reported_amount: Decimal | None = None
    reported_currency: PaymentCurrency | None = None
    reported_paid_on: date | None = None
    score: Decimal | None = None
    reasons: list[Any] | dict[str, Any] | None = None
    #: Mail evidence only: ``False`` = the From is NOT the insurer/recipient
    #: Radal asked (reason ``sender_unverified``) -> the UI must warn.
    sender_verified: bool | None = None
    note: str | None = None
    payment_id: int | None = None
    decided_at: UtcDatetime | None = None
    created_at: UtcDatetime
    #: ``None`` when the evidence names no cuota yet.
    installment_summary: EvidenceInstallmentSummary | None = None
    #: What to pre-fill, in the cuota's currency: the open balance when the report
    #: says paid/unknown and its figure covers it, else the reported figure —
    #: never above the balance. ``None`` when nothing sensible can be proposed.
    suggested_amount_uf: Decimal | None = None

    @field_validator("decided_at", "created_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class EvidencePage(BaseModel):
    items: list[EvidenceRead]
    total: int
    limit: int
    offset: int


class EvidenceConfirm(BaseModel):
    installment_id: int | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    currency: PaymentCurrency | None = None
    paid_on: date | None = None
    confirmed_by_insurer: bool = False
    note: str | None = Field(default=None, max_length=2000)


class EvidenceReject(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class EvidenceDecision(BaseModel):
    evidence: EvidenceRead
    payment_id: int | None = None
    installment_status: str | None = None


# --- notices ----------------------------------------------------------------------

class NoticeRequest(BaseModel):
    installment_id: int
    kind: NoticeKind = NoticeKind.PAYER_REMINDER
    channel: NoticeChannel = NoticeChannel.EMAIL


class NoticePreview(BaseModel):
    installment_id: int
    kind: NoticeKind
    channel: NoticeChannel
    to_address: str | None = None
    subject: str
    text: str
    html: str
    level: Literal["record", "assisted", "native"]
    level_reason: str
    missing: list[str] = []
    warnings: list[str] = []
    template_source: str
    #: Who the notice greets — the party it is mailed to (review v12 #2).
    recipient_name: str | None = None
    #: What the notice asks for: the cuota's OPEN balance (cuota currency).
    amount_uf: Decimal | None = None
    #: Already paid (capped) and the cuota's gross, for "pagado X de Y".
    paid_uf: Decimal | None = None
    gross_uf: Decimal | None = None


class NoticeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    installment_id: int | None = None
    kind: NoticeKind
    cadence_level: int | None = None
    channel: NoticeChannel
    to_address: str
    subject: str | None = None
    status: NoticeStatus
    mail_message_id: int | None = None
    share_link_id: int | None = None
    sent_at: UtcDatetime | None = None
    error: str | None = None
    created_at: UtcDatetime

    @field_validator("sent_at", "created_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class NoticePage(BaseModel):
    items: list[NoticeRead]
    total: int
    limit: int
    offset: int


InquiryDetail.model_rebuild()

__all__ = [
    "EvidenceConfirm",
    "EvidenceInstallmentSummary",
    "EvidencePaymentLine",
    "EvidenceDecision",
    "EvidencePage",
    "EvidenceRead",
    "EvidenceReject",
    "InquiryCreate",
    "InquiryDetail",
    "InquiryInstallment",
    "InquiryMail",
    "InquiryPage",
    "InquiryRead",
    "InquiryResend",
    "NoticePage",
    "NoticePreview",
    "NoticeRead",
    "NoticeRequest",
]
