"""What each insurer offers for cobranza — RADAL's profile, the broker's override,
and the native payment channels (v12).

Product decision (docs/v12/00-orchestration.md §1.3-1.4): Forpay is configured
MANUALLY per insurer by RADAL after meetings with the insurer. Brokers never see
"on its way": ``onboarding_status``/``expected_live_on``/``status_note`` are
internal and never leave a ``/platform`` endpoint. Assisted collection is always
available whatever the insurer's status.

* ``insurer_collection_profile`` [platform] — canonical, one per insurer (PK =
  ``insurer_id``), written only by platform admins.
* ``broker_insurer_collection`` [workspace] — the broker admin's per-insurer
  override (contact, instructions field-by-field, native opt-out).
* ``insurer_payment_channel`` [platform] — a native channel (webpay, oneclick,
  bank transfer, PAC) and the provider account/store that serves it. The
  resolver goes native only with >= 1 ``active`` channel on an ``active``
  provider account.

``payment_instructions`` is JSON (``{portal_url, bank_name, account_type,
account_number, account_holder, account_rut, transfer_email, receipt_email,
notes}``): rendered into reminders and the share page, never filtered in SQL.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import ChannelStatus, OnboardingStatus, PaymentChannel, sql_enum
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.insurer import Insurer
    from app.models.provider import ProviderAccount


class InsurerCollectionProfile(Base, TimestampMixin):
    """RADAL's cobranza profile of one insurer. PLATFORM — no broker_id."""

    __tablename__ = "insurer_collection_profile"

    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="CASCADE"), primary_key=True
    )
    # INTERNAL — never exposed to brokers.
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        sql_enum(OnboardingStatus), default=OnboardingStatus.NOT_CONTACTED, nullable=False
    )
    status_note: Mapped[str | None] = mapped_column(Text)
    expected_live_on: Mapped[date | None] = mapped_column(Date)
    # ``portal_url`` here wins over the legacy ``insurer.payment_url``.
    payment_instructions: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # Default recipient of status inquiries.
    collections_email: Mapped[str | None] = mapped_column(String(255))
    # e.g. ["hdi.cl"] — classifies inbound mail as coming from this insurer.
    sender_domains: Mapped[list[Any] | None] = mapped_column(JSONType)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    insurer: Mapped["Insurer"] = relationship(foreign_keys=[insurer_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InsurerCollectionProfile insurer={self.insurer_id} {self.onboarding_status}>"


class BrokerInsurerCollection(Base, TimestampMixin):
    """A broker admin's override of one insurer's cobranza profile."""

    __tablename__ = "broker_insurer_collection"
    __table_args__ = (
        UniqueConstraint("broker_id", "insurer_id", name="uq_broker_insurer_collection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    collections_email: Mapped[str | None] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(160))
    # Same shape as the profile's; overrides field by field.
    payment_instructions: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # False = this broker opts out of native collection for this insurer.
    native_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    insurer: Mapped["Insurer"] = relationship(foreign_keys=[insurer_id])


class InsurerPaymentChannel(Base, TimestampMixin):
    """One native payment channel of an insurer. PLATFORM — no broker_id."""

    __tablename__ = "insurer_payment_channel"
    __table_args__ = (
        UniqueConstraint("insurer_id", "channel", name="uq_insurer_payment_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[PaymentChannel] = mapped_column(sql_enum(PaymentChannel), nullable=False)
    provider_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_account.id", ondelete="SET NULL"), index=True
    )
    # Transbank child store of the mall.
    store_commerce_code: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[ChannelStatus] = mapped_column(
        sql_enum(ChannelStatus), default=ChannelStatus.PENDING, nullable=False
    )
    fee_model: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    insurer: Mapped["Insurer"] = relationship(foreign_keys=[insurer_id])
    provider_account: Mapped["ProviderAccount | None"] = relationship(
        foreign_keys=[provider_account_id]
    )


__all__ = ["InsurerCollectionProfile", "BrokerInsurerCollection", "InsurerPaymentChannel"]
