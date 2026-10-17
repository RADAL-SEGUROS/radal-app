"""``collection_plan`` + ``collection_installment`` — premium billing and arrears.

Instalments are a CHILD TABLE, not a JSON array: we sum them, filter them by
status and compute arrears from them. The corpus invariant, true of every plan:

    SUM(installment.gross_amount_uf)
        == policy gross premium + SUM(endorsement.total_premium_delta_uf)

with the last instalment absorbing rounding. Validated in the schema layer with
the existing ``MONEY_TOLERANCE``; a mismatch is a 422, never a silent fix.

``art528_events`` stays JSON: the Art. 528 (Código de Comercio) non-payment
timeline is a heterogeneous list of dated events we render, never query.

v12 (docs/v12/03-collections-forpay.md): payments are their own CHILD TABLE
(``payment``), and ``collection_installment.status`` becomes a CACHE written by
``app.services.collection.status`` on every payment write and by the daily
clock — the payments are the truth, the column is what lists filter on.
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
    String,
    Text,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import (
    CollectionLevelPreference,
    CollectionPlanOrigin,
    CollectionPlanSource,
    CollectionPlanStatus,
    InstallmentStatus,
    PaymentMode,
    PolicyCurrency,
    sql_enum,
)
from app.models.types import PCT, UF, JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.document import Document
    from app.models.endorsement import Endorsement
    from app.models.payment import Payment, PaymentMandate
    from app.models.policy import Policy


class CollectionPlan(Base, TimestampMixin):
    """The payment plan of one policy (plan de pago / estado de cobranza)."""

    __tablename__ = "collection_plan"
    __table_args__ = (
        Index("ix_collection_plan_broker_status", "broker_id", "status"),
        Index("ix_collection_plan_policy", "policy_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )

    plan_number: Mapped[str | None] = mapped_column(String(64), index=True)
    payment_mode: Mapped[PaymentMode] = mapped_column(
        sql_enum(PaymentMode), default=PaymentMode.COUPON_BOOK, nullable=False
    )
    installment_count: Mapped[int | None] = mapped_column(Integer)
    total_premium_uf: Mapped[Decimal | None] = mapped_column(UF)

    bank: Mapped[str | None] = mapped_column(String(120))
    account_number: Mapped[str | None] = mapped_column(String(64))
    monthly_interest_rate_pct: Mapped[Decimal | None] = mapped_column(PCT)

    status: Mapped[CollectionPlanStatus] = mapped_column(
        sql_enum(CollectionPlanStatus),
        default=CollectionPlanStatus.PENDING,
        nullable=False,
    )
    as_of_date: Mapped[date | None] = mapped_column(Date)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rehabilitated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    days_without_cover: Mapped[int | None] = mapped_column(Integer)
    rehabilitation_cost_uf: Mapped[Decimal | None] = mapped_column(UF)
    # Dated Art. 528 non-payment events (aviso, suspensión, término, rehabilitación).
    art528_events: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)
    management_note: Mapped[str | None] = mapped_column(Text)

    # --- v12 cobranza -------------------------------------------------------
    origin: Mapped[CollectionPlanOrigin | None] = mapped_column(
        sql_enum(CollectionPlanOrigin)
    )
    source: Mapped[CollectionPlanSource | None] = mapped_column(
        sql_enum(CollectionPlanSource)
    )
    # The 09 plan de pago the schedule was read from (rule 8).
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    # The recurring PAC/Oneclick mandate that charges this plan, when any.
    # ``use_alter``: collection_plan <-> payment_mandate is a genuine FK cycle
    # (``payment_mandate.plan_id``), same treatment as ``policy.case_file_id``.
    mandate_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "payment_mandate.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_collection_plan_mandate",
        ),
        index=True,
    )
    level_preference: Mapped[CollectionLevelPreference] = mapped_column(
        sql_enum(CollectionLevelPreference),
        default=CollectionLevelPreference.AUTO,
        server_default=CollectionLevelPreference.AUTO.value,
        nullable=False,
    )
    # "Iniciar cobranza": the broker switched payer reminders ON for this plan.
    notices_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    notices_paused_until: Mapped[date | None] = mapped_column(Date)
    notices_pause_reason: Mapped[str | None] = mapped_column(Text)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    policy: Mapped["Policy"] = relationship(
        foreign_keys=[policy_id], back_populates="collection_plans"
    )
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    source_document: Mapped["Document | None"] = relationship(
        foreign_keys=[source_document_id]
    )
    mandate: Mapped["PaymentMandate | None"] = relationship(
        "PaymentMandate", foreign_keys=[mandate_id], post_update=True
    )
    installments: Mapped[list["CollectionInstallment"]] = relationship(
        back_populates="collection_plan",
        cascade="all, delete-orphan",
        order_by="CollectionInstallment.number",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CollectionPlan id={self.id} policy={self.policy_id} status={self.status}>"


class CollectionInstallment(Base, TimestampMixin):
    """One cuota of a payment plan."""

    __tablename__ = "collection_installment"
    __table_args__ = (
        Index("ix_collection_installment_plan_number", "collection_plan_id", "number"),
        Index("ix_collection_installment_due", "due_date"),
        Index("ix_collection_installment_broker_status", "broker_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    collection_plan_id: Mapped[int] = mapped_column(
        ForeignKey("collection_plan.id", ondelete="CASCADE"), nullable=False, index=True
    )

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    coupon_number: Mapped[str | None] = mapped_column(String(32))
    due_date: Mapped[date | None] = mapped_column(Date, index=True)

    gross_amount_uf: Mapped[Decimal] = mapped_column(UF, nullable=False)
    net_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    commission_uf: Mapped[Decimal | None] = mapped_column(UF)

    paid_on: Mapped[date | None] = mapped_column(Date)
    days_late: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[InstallmentStatus] = mapped_column(
        sql_enum(InstallmentStatus), default=InstallmentStatus.PENDING, nullable=False
    )
    # The endorsement that created or re-priced this cuota, when any.
    endorsement_id: Mapped[int | None] = mapped_column(
        ForeignKey("endorsement.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)

    # --- v12 cobranza -------------------------------------------------------
    currency: Mapped[PolicyCurrency] = mapped_column(
        sql_enum(PolicyCurrency),
        default=PolicyCurrency.UF,
        server_default=PolicyCurrency.UF.value,
        nullable=False,
    )
    # PAC / card debit bounced (flag ``direct_debit_rejected`` when after the
    # last active payment).
    direct_debit_rejected_at: Mapped[date | None] = mapped_column(Date)
    # The endorsement that ANNULLED this cuota (state ``voided``) — distinct from
    # ``endorsement_id``, the one that created or re-priced it.
    voided_by_endorsement_id: Mapped[int | None] = mapped_column(
        ForeignKey("endorsement.id", ondelete="SET NULL"), index=True
    )
    # Art. 528 notice sent by the insurer; termination = +15 days (computed).
    insurer_notice_sent_on: Mapped[date | None] = mapped_column(Date)
    # Latest *Promete pagar el* date.
    promise_until: Mapped[date | None] = mapped_column(Date)

    broker: Mapped["Broker"] = relationship()
    collection_plan: Mapped["CollectionPlan"] = relationship(back_populates="installments")
    endorsement: Mapped["Endorsement | None"] = relationship(foreign_keys=[endorsement_id])
    voided_by_endorsement: Mapped["Endorsement | None"] = relationship(
        foreign_keys=[voided_by_endorsement_id]
    )
    # Every payment ever registered (active AND reversed — never deleted).
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="installment",
        cascade="all, delete-orphan",
        order_by="Payment.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CollectionInstallment plan={self.collection_plan_id} n={self.number}>"


__all__ = ["CollectionPlan", "CollectionInstallment"]
