"""Pydantic v2 schemas for share links — broker side and public side (v12, 02 §7).

The public schemas are a WHITELIST: anything not declared here can never reach
an insured or an insurer, whatever the projection builder hands over. They carry
no ``broker_id``, no document keys, no commission, no notes and no contact data.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.urls import safe_http_url
from app.models.enums import PaymentCurrency, ShareAudience, ShareEntity
from app.schemas.utc import UtcDatetime


def _utc(value: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; every stored datetime is UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# =============================================================================
# Broker side (/share-links)
# =============================================================================


class SharePermissions(BaseModel):
    """The four link capabilities. Omitted keys take the audience defaults."""

    can_pay: bool | None = None
    can_upload_receipt: bool | None = None
    can_report_status: bool | None = None
    show_documents: bool | None = None


class ShareLinkCreate(BaseModel):
    entity_type: ShareEntity
    entity_id: int = Field(gt=0)
    audience: ShareAudience
    permissions: SharePermissions | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    label: str | None = Field(default=None, max_length=160)


class ShareLinkRead(BaseModel):
    """One link as the broker sees it (token + ready-to-send URLs included)."""

    id: int
    entity_type: ShareEntity
    entity_id: int
    audience: ShareAudience
    permissions: dict[str, bool]
    label: str | None = None
    token: str
    url: str
    whatsapp_url: str
    whatsapp_text: str
    is_live: bool
    expires_at: datetime | None = None
    revoked_at: UtcDatetime | None = None
    view_count: int = 0
    last_viewed_at: UtcDatetime | None = None
    created_by_user_id: int | None = None
    created_at: UtcDatetime | None = None

    @field_validator("expires_at", "revoked_at", "last_viewed_at", "created_at")
    @classmethod
    def _norm(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class ShareLinkList(BaseModel):
    items: list[ShareLinkRead]
    total: int


# =============================================================================
# Public side (/public/share/{token})
# =============================================================================


class PublicBroker(BaseModel):
    name: str | None = None
    logo_url: str | None = None


class PublicInstructions(BaseModel):
    """How to pay the insurer directly (Radal profile ⊕ broker override)."""

    insurer_name: str | None = None
    portal_url: str | None = None
    bank_name: str | None = None
    account_type: str | None = None
    account_number: str | None = None
    account_holder: str | None = None
    account_rut: str | None = None
    transfer_email: str | None = None
    receipt_email: str | None = None
    notes: str | None = None

    @field_validator("portal_url")
    @classmethod
    def _safe_portal_url(cls, value: str | None) -> str | None:
        # Unauthenticated and rendered as an href on the app origin: http(s) only.
        return safe_http_url(value)


class PublicPolicy(BaseModel):
    id: int
    policy_number: str | None = None
    insurer_name: str | None = None
    line_name: str | None = None
    subtype_code: str | None = None
    subtype_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    insured_name: str | None = None
    currency: str = "UF"
    #: Insured audience only, and only when some cuota is paid off-platform.
    payment_instructions: PublicInstructions | None = None


class PublicReported(BaseModel):
    status: str | None = None
    reported_at: UtcDatetime | None = None

    @field_validator("reported_at")
    @classmethod
    def _norm(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class PublicInstallment(BaseModel):
    id: int
    policy_id: int
    number: int
    coupon_number: str | None = None
    due_date: date | None = None
    currency: str = "UF"
    amount_uf: Decimal
    amount_clp: int | None = None
    paid_uf: Decimal
    balance_uf: Decimal
    balance_clp: int | None = None
    state: str
    partial: bool = False
    days_late: int = 0
    #: Insured audience only: ``native`` | ``assisted`` | ``record``.
    level: str | None = None
    #: ``pay`` (native + can_pay) | ``instructions`` | ``report`` (insurer) | ``none``.
    action: Literal["pay", "instructions", "report", "none"] = "none"
    can_upload_receipt: bool = False
    can_report: bool = False
    #: Insurer audience: the last status this link already reported.
    reported: PublicReported | None = None
    #: Insured audience: the latest receipt the payer sent that the broker has
    #: not decided yet (``status`` = suggested | needs_review).
    receipt_pending: PublicReported | None = None


class PublicCaseFile(BaseModel):
    reference: str | None = None
    title: str | None = None
    period_label: str | None = None


class PublicInquiry(BaseModel):
    reference: str
    status: str | None = None


class PublicSharePermissions(BaseModel):
    can_pay: bool = False
    can_upload_receipt: bool = False
    can_report_status: bool = False
    show_documents: bool = False


class PublicShareRead(BaseModel):
    """The audience projection. Unknown/revoked/expired tokens never get here (404)."""

    model_config = ConfigDict(extra="ignore")

    audience: ShareAudience
    entity_type: ShareEntity
    label: str | None = None
    expires_at: datetime | None = None
    permissions: PublicSharePermissions
    today: date
    broker: PublicBroker
    policy: PublicPolicy | None = None
    policies: list[PublicPolicy] = []
    installments: list[PublicInstallment] = []
    uf_rate: Decimal | None = None
    case_file: PublicCaseFile | None = None
    inquiry: PublicInquiry | None = None
    #: Insurer + can_report_status: the statuses the report form offers.
    report_statuses: list[str] = []

    @field_validator("expires_at")
    @classmethod
    def _norm(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class PublicReportCreate(BaseModel):
    installment_id: int = Field(gt=0)
    status: Literal["paid", "unpaid", "partial", "cancelled"]
    amount: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=4)
    currency: PaymentCurrency = PaymentCurrency.UF
    paid_on: date | None = None
    comment: str | None = Field(default=None, max_length=2000)


class PublicWriteResult(BaseModel):
    """What a public write answers: an acknowledgement, never internal ids."""

    received: bool = True
    installment_id: int
    reported_status: str
    #: False when the same report had already been received (idempotent).
    created: bool = True
    message: str


class PublicCheckoutCreate(BaseModel):
    installment_ids: list[int] = Field(min_length=1, max_length=60)


class PublicCheckoutRead(BaseModel):
    checkout_url: str


__all__ = [
    "PublicBroker",
    "PublicCaseFile",
    "PublicCheckoutCreate",
    "PublicCheckoutRead",
    "PublicInquiry",
    "PublicInstallment",
    "PublicInstructions",
    "PublicPolicy",
    "PublicReportCreate",
    "PublicReported",
    "PublicShareRead",
    "PublicSharePermissions",
    "PublicWriteResult",
    "SharePermissions",
    "ShareLinkCreate",
    "ShareLinkList",
    "ShareLinkRead",
]
