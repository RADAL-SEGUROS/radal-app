"""Pydantic v2 schemas for ``/platform`` — RADAL's maintainer console (v12, 02 §5) — lane B3.

Secrets are WRITE-ONLY: the create/update bodies accept ``api_key``,
``password``, ``hmac_key``, ``public_key_pem``, ``jwe_alg``, ``jwe_enc`` and
``signature_fields``; no read model has a field that could carry one back
(``has_secret`` + the NAMES of the keys that are set only).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    ChannelStatus,
    EventProcessing,
    OnboardingStatus,
    PaymentChannel,
    PaymentProviderName,
    ProviderAccountStatus,
    ProviderEnvironment,
    ProviderOwner,
)
from app.schemas.jobs import JobRunRead
from app.schemas.payments import PaymentInstructions
from app.schemas.utc import UtcDatetime


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class _Aware(BaseModel):
    """Read models: SQLite hands back naive datetimes (all stored UTC)."""

    @field_validator("*", mode="after")
    @classmethod
    def _aware(cls, value: Any) -> Any:
        return _utc(value) if isinstance(value, datetime) else value


# --- insurer cobranza profiles ------------------------------------------------

class InsurerProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    onboarding_status: OnboardingStatus | None = None
    status_note: str | None = Field(default=None, max_length=4000)
    expected_live_on: date | None = None
    payment_instructions: PaymentInstructions | None = None
    collections_email: EmailStr | None = None
    sender_domains: list[str] | None = Field(default=None, max_length=20)

    @field_validator("sender_domains")
    @classmethod
    def _domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        out: list[str] = []
        for raw in value:
            domain = (raw or "").strip().lower().lstrip("@")
            if not domain:
                continue
            if " " in domain or "." not in domain or len(domain) > 253:
                raise ValueError(f"dominio inválido: {raw!r}")
            if domain not in out:
                out.append(domain)
        return out


class InsurerProfileCreate(InsurerProfileFields):
    insurer_id: int


class InsurerProfileUpdate(InsurerProfileFields):
    pass


class ChannelRead(_Aware):
    id: int
    insurer_id: int
    insurer_name: str | None = None
    channel: PaymentChannel
    provider_account_id: int | None = None
    provider_account_label: str | None = None
    store_commerce_code: str | None = None
    status: ChannelStatus
    fee_model: dict[str, Any] | None = None
    activated_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class InsurerProfileRead(_Aware):
    insurer_id: int
    insurer_name: str
    insurer_rut: str | None = None
    has_profile: bool
    onboarding_status: OnboardingStatus
    status_note: str | None = None
    expected_live_on: date | None = None
    payment_instructions: dict[str, str] = {}
    collections_email: str | None = None
    sender_domains: list[str] = []
    updated_by_user_id: int | None = None
    updated_at: UtcDatetime | None = None
    channels: list[ChannelRead] = []


# --- provider accounts ----------------------------------------------------------

class _SecretFields(BaseModel):
    """Write-only. ``None``/absent = keep; ``""`` = clear."""

    api_key: str | None = Field(default=None, max_length=512)
    password: str | None = Field(default=None, max_length=512)
    hmac_key: str | None = Field(default=None, max_length=512)
    public_key_pem: str | None = Field(default=None, max_length=8192)
    jwe_alg: str | None = Field(default=None, max_length=32)
    jwe_enc: str | None = Field(default=None, max_length=32)
    signature_fields: list[str] | dict[str, list[str]] | None = None


class ProviderAccountCreate(_SecretFields):
    model_config = ConfigDict(extra="forbid")

    provider: PaymentProviderName = PaymentProviderName.FORPAY
    label: str = Field(min_length=1, max_length=120)
    owner: ProviderOwner
    insurer_id: int | None = None
    environment: ProviderEnvironment = ProviderEnvironment.SANDBOX
    base_url: str | None = Field(default=None, max_length=255)
    commerce: str | None = Field(default=None, max_length=64)
    channel_header: str | None = Field(default=None, max_length=64)
    process_id: str | None = Field(default="WHITE_LABEL", max_length=16)
    username: str | None = Field(default=None, max_length=128)
    sales_executive_id: str | None = Field(default=None, max_length=64)
    status: ProviderAccountStatus = ProviderAccountStatus.DRAFT


class ProviderAccountUpdate(_SecretFields):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=120)
    owner: ProviderOwner | None = None
    insurer_id: int | None = None
    environment: ProviderEnvironment | None = None
    base_url: str | None = Field(default=None, max_length=255)
    commerce: str | None = Field(default=None, max_length=64)
    channel_header: str | None = Field(default=None, max_length=64)
    process_id: str | None = Field(default=None, max_length=16)
    username: str | None = Field(default=None, max_length=128)
    sales_executive_id: str | None = Field(default=None, max_length=64)
    status: ProviderAccountStatus | None = None


class ProviderAccountRead(_Aware):
    id: int
    provider: PaymentProviderName
    label: str
    owner: ProviderOwner
    insurer_id: int | None = None
    insurer_name: str | None = None
    environment: ProviderEnvironment
    base_url: str
    commerce: str | None = None
    channel_header: str | None = None
    process_id: str | None = None
    username: str | None = None
    sales_executive_id: str | None = None
    status: ProviderAccountStatus
    has_secret: bool
    #: Names of the secret keys that carry a value — never the values.
    secret_keys: list[str] = []
    webhook_key: str
    webhook_url: str | None = None
    webhook_urls: dict[str, str] = {}
    previous_webhook_secret_valid_until: datetime | None = None
    #: The LAST *Verificar* result (review v12 #17): ``ok`` | ``failed`` | ``never``.
    #: ``last_verified_at`` is the last SUCCESS; ``last_error`` the last failure.
    verify_status: str = "never"
    last_verified_at: UtcDatetime | None = None
    last_error: str | None = None
    #: True when native collection may ride on this account (active + verified).
    usable: bool = False
    #: Spanish note when the webhook origin is a fallback (``PUBLIC_API_URL`` unset).
    webhook_url_note: str | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class VerifyResult(BaseModel):
    ok: bool
    error: str | None = None
    account: ProviderAccountRead


# --- insurer channels -------------------------------------------------------------

class ChannelUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_account_id: int | None = None
    store_commerce_code: str | None = Field(default=None, max_length=32)
    status: ChannelStatus | None = None
    fee_model: dict[str, Any] | None = None


class ChannelBulkItem(ChannelUpsert):
    insurer_id: int
    channel: PaymentChannel


# --- events & jobs ------------------------------------------------------------------

class EventRead(_Aware):
    id: int
    provider: PaymentProviderName
    provider_account_id: int | None = None
    dedup_key: str
    event_type: str | None = None
    signature_valid: bool
    received_at: UtcDatetime
    processing_status: EventProcessing
    attempts: int
    error: str | None = None
    broker_id: int | None = None


class EventDetail(EventRead):
    payload: dict[str, Any] | None = None


class EventPage(BaseModel):
    items: list[EventRead]
    total: int
    limit: int
    offset: int


class JobPage(BaseModel):
    items: list[JobRunRead]
    total: int
    limit: int
    offset: int
    #: Every registered job name (so the console can show jobs that never ran).
    registered: list[str] = []


__all__ = [
    "ChannelBulkItem",
    "ChannelRead",
    "ChannelUpsert",
    "EventDetail",
    "EventPage",
    "EventRead",
    "InsurerProfileCreate",
    "InsurerProfileRead",
    "InsurerProfileUpdate",
    "JobPage",
    "ProviderAccountCreate",
    "ProviderAccountRead",
    "ProviderAccountUpdate",
    "VerifyResult",
]
