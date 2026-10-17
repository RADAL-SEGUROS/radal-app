"""Payments against cuotas, checkout intents and recurring mandates (v12).

``payment`` is THE truth of cobranza: one row per money movement registered
against a ``collection_installment``. It is never deleted — an error, a bounced
debit or a chargeback is a REVERSAL (``status=reversed`` + reason), so the
ledger keeps its history. ``amount_uf`` is the normalised figure the state
machine sums (``app.services.collection.status``); the original ``amount`` +
``currency`` (+ ``uf_rate`` when CLP) are kept verbatim.

``evidence_level`` is ordered weakest -> strongest (declared < reported <
insurer_confirmed < provider_confirmed < settled). A signed provider webhook
is not AI and commits directly as ``provider_confirmed``; everything that came
from a mail or a share link goes through ``payment_evidence`` and a human.

``payment_intent`` is one checkout = one Forpay ONEPAY mandate over 1..N cuotas
(``payment_intent_item``); ``payment_mandate`` is a recurring PAC/Oneclick
DYNAMIC mandate attached to a plan. Every external id we hand Forpay is OURS and
unique (``rdl-intent-<hex>``, ``rdl-cuota-<installment_id>-<intent_hex>``), so a
webhook can always be routed back without trusting the provider's numbering.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import (
    EvidenceLevel,
    IntentStatus,
    MandateStatus,
    PaymentChannel,
    PaymentCurrency,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    ReversalReason,
    sql_enum,
)
from app.models.types import UF

if TYPE_CHECKING:
    from app.models.collection import CollectionInstallment, CollectionPlan
    from app.models.document import Document
    from app.models.mail import PaymentEvidence
    from app.models.policy_party import PolicyParty
    from app.models.provider import ProviderCharge


class Payment(Base, TimestampMixin):
    """One payment (or partial payment) of a cuota. Reversed, never deleted."""

    __tablename__ = "payment"
    __table_args__ = (
        Index("ix_payment_broker_paid_on", "broker_id", "paid_on"),
        Index("ix_payment_installment_status", "installment_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("collection_installment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # As paid, verbatim.
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)
    currency: Mapped[PaymentCurrency] = mapped_column(
        sql_enum(PaymentCurrency), nullable=False
    )
    # UF value used to convert when ``currency == CLP``.
    uf_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    # Normalised — what the state machine sums.
    amount_uf: Mapped[Decimal] = mapped_column(UF, nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[PaymentMode | None] = mapped_column(sql_enum(PaymentMode))

    source: Mapped[PaymentSource] = mapped_column(sql_enum(PaymentSource), nullable=False)
    evidence_level: Mapped[EvidenceLevel] = mapped_column(
        sql_enum(EvidenceLevel), nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        sql_enum(PaymentStatus), default=PaymentStatus.ACTIVE, nullable=False
    )

    # --- Reversal (never delete) --------------------------------------------
    reversal_reason: Mapped[ReversalReason | None] = mapped_column(sql_enum(ReversalReason))
    reversal_note: Mapped[str | None] = mapped_column(Text)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    # --- Insurer confirmation -----------------------------------------------
    confirmed_by_insurer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    # Comprobante (rule 8: FK to document, never a key).
    receipt_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    # Exactly one payment per provider charge — the webhook idempotency anchor.
    provider_charge_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_charge.id", ondelete="SET NULL"), unique=True, index=True
    )
    # The confirmed suggestion this payment came from. ``use_alter``:
    # payment <-> payment_evidence is a genuine FK cycle
    # (``payment_evidence.payment_id``).
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "payment_evidence.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_payment_evidence",
        ),
        index=True,
    )
    registered_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)

    installment: Mapped["CollectionInstallment"] = relationship(back_populates="payments")
    receipt_document: Mapped["Document | None"] = relationship(
        foreign_keys=[receipt_document_id]
    )
    provider_charge: Mapped["ProviderCharge | None"] = relationship(
        foreign_keys=[provider_charge_id]
    )
    evidence: Mapped["PaymentEvidence | None"] = relationship(
        foreign_keys=[evidence_id], post_update=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Payment id={self.id} installment={self.installment_id} "
            f"uf={self.amount_uf} status={self.status}>"
        )


class PaymentIntent(Base, TimestampMixin):
    """One checkout = one Forpay ONEPAY mandate over one or more cuotas."""

    __tablename__ = "payment_intent"
    __table_args__ = (Index("ix_payment_intent_broker_status", "broker_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Ours: ``rdl-intent-<hex>``.
    external_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    share_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("share_link.id", ondelete="SET NULL"), index=True
    )
    payer_party_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy_party.id", ondelete="SET NULL"), index=True
    )
    provider_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_account.id", ondelete="SET NULL"), index=True
    )
    provider_mandate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    checkout_url: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[IntentStatus] = mapped_column(
        sql_enum(IntentStatus), default=IntentStatus.CREATED, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    items: Mapped[list["PaymentIntentItem"]] = relationship(
        back_populates="intent",
        cascade="all, delete-orphan",
        order_by="PaymentIntentItem.id",
    )
    payer_party: Mapped["PolicyParty | None"] = relationship(foreign_keys=[payer_party_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PaymentIntent id={self.id} ext={self.external_id!r} status={self.status}>"


class PaymentIntentItem(Base, TimestampMixin):
    """One cuota inside a checkout."""

    __tablename__ = "payment_intent_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("payment_intent.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("collection_installment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    insurer_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurer.id", ondelete="SET NULL"), index=True
    )
    amount_uf: Mapped[Decimal] = mapped_column(UF, nullable=False)
    # Transbank child store (``metadata.f_commerceCode`` when ``isMall``).
    store_commerce_code: Mapped[str | None] = mapped_column(String(32))
    # Ours: ``rdl-cuota-<installment_id>-<intent_hex>`` — Forpay ``installment.id``.
    external_installment_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    intent: Mapped["PaymentIntent"] = relationship(back_populates="items")
    installment: Mapped["CollectionInstallment"] = relationship(
        foreign_keys=[installment_id]
    )


class PaymentMandate(Base, TimestampMixin):
    """A recurring PAC / Oneclick mandate (Forpay DYNAMIC) charging one plan."""

    __tablename__ = "payment_mandate"
    __table_args__ = (Index("ix_payment_mandate_broker_status", "broker_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("collection_plan.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payer_party_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy_party.id", ondelete="SET NULL"), index=True
    )
    insurer_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurer.id", ondelete="SET NULL"), index=True
    )
    provider_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_account.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[PaymentChannel | None] = mapped_column(sql_enum(PaymentChannel))
    provider_mandate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # Forpay Magic Link for enrolment.
    magic_link_url: Mapped[str | None] = mapped_column(String(1024))
    consent_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[MandateStatus] = mapped_column(
        sql_enum(MandateStatus), default=MandateStatus.PENDING, nullable=False
    )
    bank_name: Mapped[str | None] = mapped_column(String(120))
    account_last4: Mapped[str | None] = mapped_column(String(8))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped["CollectionPlan"] = relationship(foreign_keys=[plan_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PaymentMandate id={self.id} plan={self.plan_id} status={self.status}>"


__all__ = ["Payment", "PaymentIntent", "PaymentIntentItem", "PaymentMandate"]
