"""Pydantic v2 schemas for ``/payments`` (v12, 02 §5) — lane B3."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.urls import validate_http_url
from app.models.enums import IntentStatus
from app.services.identifiers import validate_rut
from app.schemas.utc import UtcDatetime


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class IntentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installment_ids: list[int] = Field(min_length=1, max_length=50)
    share_link_id: int | None = None


class IntentItemRead(BaseModel):
    id: int
    installment_id: int
    installment_number: int | None = None
    due_date: date | None = None
    policy_id: int | None = None
    policy_number: str | None = None
    insurer_id: int | None = None
    insurer_name: str | None = None
    amount_uf: Decimal
    store_commerce_code: str | None = None
    external_installment_id: str
    paid: bool = False


class IntentRead(BaseModel):
    id: int
    external_id: str
    status: IntentStatus
    checkout_url: str | None = None
    provider_account_id: int | None = None
    share_link_id: int | None = None
    payer_party_id: int | None = None
    total_uf: Decimal
    expires_at: datetime | None = None
    created_by_user_id: int | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    items: list[IntentItemRead] = []

    @field_validator("expires_at", "created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class IntentCreated(BaseModel):
    intent: IntentRead
    checkout_url: str
    reused: bool = False


class IntentPage(BaseModel):
    items: list[IntentRead]
    total: int
    limit: int
    offset: int


class PaymentInstructions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    portal_url: str | None = Field(default=None, max_length=1000)
    bank_name: str | None = Field(default=None, max_length=1000)
    account_type: str | None = Field(default=None, max_length=1000)
    account_number: str | None = Field(default=None, max_length=1000)
    account_holder: str | None = Field(default=None, max_length=1000)
    account_rut: str | None = Field(default=None, max_length=1000)
    transfer_email: str | None = Field(default=None, max_length=1000)
    receipt_email: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("portal_url")
    @classmethod
    def _portal_url(cls, value: str | None) -> str | None:
        # Rendered as a link on the app and on the public share page: http(s) only.
        return validate_http_url(value, max_length=1000)

    @field_validator("account_rut")
    @classmethod
    def _account_rut(cls, value: str | None) -> str | None:
        # Non-negotiable 7: mod-11 validated, stored ``BODY-DV``. It is pasted
        # into payer reminders, so a wrong check digit must never be saved.
        # Blank stays blank (it clears the key).
        if value is None or not value.strip():
            return value
        return validate_rut(value)


class InsurerOverrideUpdate(BaseModel):
    """``PUT /payments/insurers/{insurer_id}/override``. Omitted fields are kept."""

    model_config = ConfigDict(extra="forbid")

    collections_email: EmailStr | None = None
    contact_name: str | None = Field(default=None, max_length=160)
    payment_instructions: PaymentInstructions | None = None
    native_enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)


class InsurerRef(BaseModel):
    id: int
    name: str
    legal_name: str
    rut: str | None = None


class BrokerOverrideRead(BaseModel):
    collections_email: str | None = None
    contact_name: str | None = None
    payment_instructions: dict[str, str] = {}
    native_enabled: bool = True
    notes: str | None = None
    updated_at: UtcDatetime | None = None

    @field_validator("updated_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class InsurerCapability(BaseModel):
    """What a broker sees of an insurer's cobranza. No onboarding status, ever."""

    insurer: InsurerRef
    level_available: str
    channels: list[str] = []
    instructions: dict[str, str] = {}
    collections_email: str | None = None
    broker_override: BrokerOverrideRead | None = None


__all__ = [
    "BrokerOverrideRead",
    "InsurerCapability",
    "InsurerOverrideUpdate",
    "InsurerRef",
    "IntentCreate",
    "IntentCreated",
    "IntentItemRead",
    "IntentPage",
    "IntentRead",
    "PaymentInstructions",
]
