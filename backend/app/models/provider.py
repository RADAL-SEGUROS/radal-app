"""Payment-provider plumbing (Forpay) — accounts, charges, payouts, webhook inbox (v12).

Facts and flows: docs/v12/03-collections-forpay.md §3-§6.

* ``provider_account`` [platform] — one Forpay "company" credential set,
  configured by RADAL platform admins only. Secrets live ENCRYPTED in
  ``*_ciphertext`` (``app.core.crypto``) and are write-only in the API.
  ``webhook_key`` is the public id that rides in the generic webhook's query
  string; ``sig`` is derived from the encrypted ``webhook_secret``.
* ``provider_charge`` [workspace] — one row per Forpay installment we learn
  about (webhook or ``GET /receivables``). Unique per
  ``(provider_account_id, provider_installment_id)``; ``payment.provider_charge_id``
  is unique too, so a replayed webhook can never create a second payment.
* ``provider_payout`` [platform] — a dispersion (abono al comercio).
* ``provider_event`` [platform] — the webhook INBOX: every POST is stored
  (signature valid or not) before processing, deduplicated by
  ``(provider, dedup_key)``. ``broker_id`` is a plain resolved integer, not a
  FK: the event exists before we know whose it is, and it is never listed
  through a broker endpoint.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin, utcnow
from app.models.enums import (
    EventProcessing,
    PaymentProviderName,
    ProviderAccountStatus,
    ProviderEnvironment,
    ProviderOwner,
    sql_enum,
)
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.insurer import Insurer


class ProviderAccount(Base, TimestampMixin):
    """One provider credential set (a Forpay commerce). PLATFORM — no broker_id."""

    __tablename__ = "provider_account"
    __table_args__ = (Index("ix_provider_account_provider_status", "provider", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[PaymentProviderName] = mapped_column(
        sql_enum(PaymentProviderName), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    owner: Mapped[ProviderOwner] = mapped_column(sql_enum(ProviderOwner), nullable=False)
    # Set when ``owner == insurer``.
    insurer_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurer.id", ondelete="SET NULL"), index=True
    )
    environment: Mapped[ProviderEnvironment] = mapped_column(
        sql_enum(ProviderEnvironment), default=ProviderEnvironment.SANDBOX, nullable=False
    )
    # Sandbox: settings.FORPAY_DEFAULT_BASE_URL. Production is not public.
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    # Forpay request headers.
    commerce: Mapped[str | None] = mapped_column(String(64))
    channel_header: Mapped[str | None] = mapped_column(String(64))
    process_id: Mapped[str | None] = mapped_column(String(16), default="WHITE_LABEL")
    username: Mapped[str | None] = mapped_column(String(128))
    sales_executive_id: Mapped[str | None] = mapped_column(String(64))
    # Encrypted JSON {api_key, password, hmac_key, public_key_pem, jwe_alg,
    # jwe_enc, signature_fields, previous_webhook_secret}. Write-only.
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    # Public id in the webhook query string (``secrets.token_urlsafe(18)``).
    webhook_key: Mapped[str] = mapped_column(
        String(48), unique=True, index=True, nullable=False
    )
    # Encrypted random secret behind the ``sig`` query param.
    webhook_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProviderAccountStatus] = mapped_column(
        sql_enum(ProviderAccountStatus), default=ProviderAccountStatus.DRAFT, nullable=False
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    insurer: Mapped["Insurer | None"] = relationship(foreign_keys=[insurer_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProviderAccount id={self.id} {self.provider}:{self.label!r} {self.status}>"


class ProviderCharge(Base, TimestampMixin):
    """One provider-side installment (a Forpay cuota) we know about."""

    __tablename__ = "provider_charge"
    __table_args__ = (
        UniqueConstraint(
            "provider_account_id",
            "provider_installment_id",
            name="uq_provider_charge_account_installment",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_account.id", ondelete="SET NULL"), index=True
    )
    intent_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_intent.id", ondelete="SET NULL"), index=True
    )
    mandate_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_mandate.id", ondelete="SET NULL"), index=True
    )
    installment_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_installment.id", ondelete="SET NULL"), index=True
    )
    provider_mandate_id: Mapped[str | None] = mapped_column(String(64))
    # Forpay ``installment.internalId``.
    provider_installment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Ours (Forpay ``installment.id``).
    external_installment_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # Forpay state id (1 pendiente · 2 pagada · 6 anulada · 12 reversada · -99 rechazada …).
    state: Mapped[int | None] = mapped_column(Integer)
    state_name: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    amount_clp: Mapped[int | None] = mapped_column(Integer)
    authorization_code: Mapped[str | None] = mapped_column(String(64))
    payment_method: Mapped[str | None] = mapped_column(String(64))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payout_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_payout.id", ondelete="SET NULL"), index=True
    )
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    provider_account: Mapped["ProviderAccount | None"] = relationship(
        foreign_keys=[provider_account_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProviderCharge id={self.id} ext={self.provider_installment_id!r} state={self.state}>"


class ProviderPayout(Base, TimestampMixin):
    """A dispersion credited to a commerce (``hasPayout=true``). PLATFORM."""

    __tablename__ = "provider_payout"
    __table_args__ = (
        UniqueConstraint(
            "provider_account_id", "dispersion_id", name="uq_provider_payout_account_dispersion"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        ForeignKey("provider_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dispersion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    insurer_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurer.id", ondelete="SET NULL"), index=True
    )
    credited_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    deposit_date: Mapped[date | None] = mapped_column(Date)
    destination_account_masked: Mapped[str | None] = mapped_column(String(64))
    bank_tx_number: Mapped[str | None] = mapped_column(String(64))
    status_name: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONType)


class ProviderEvent(Base, TimestampMixin):
    """One inbound webhook, stored BEFORE processing. PLATFORM."""

    __tablename__ = "provider_event"
    __table_args__ = (
        UniqueConstraint("provider", "dedup_key", name="uq_provider_event_dedup"),
        Index("ix_provider_event_status_received", "processing_status", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[PaymentProviderName] = mapped_column(
        sql_enum(PaymentProviderName), nullable=False
    )
    provider_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_account.id", ondelete="SET NULL"), index=True
    )
    # ``installment.internalId + ":" + installment.state.id`` for Forpay.
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(32))
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    processing_status: Mapped[EventProcessing] = mapped_column(
        sql_enum(EventProcessing), default=EventProcessing.RECEIVED, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    # Resolved tenant, when known. Deliberately NOT a FK (see module docstring).
    broker_id: Mapped[int | None] = mapped_column(Integer, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProviderEvent id={self.id} {self.provider}:{self.dedup_key!r} {self.processing_status}>"


__all__ = ["ProviderAccount", "ProviderCharge", "ProviderPayout", "ProviderEvent"]
