"""Broker cobranza settings, notice templates and the sent-notice log (v12).

* ``collection_settings`` — ONE row per broker (unique ``broker_id``), created
  lazily with the defaults below the first time it is read. ``cadence_days``
  is the reminder/escalation cadence relative to the due date
  (``[-15, -5, 0, 3, 10]``: three heads-ups, two overdue steps; past the last
  one a cuota is ``termination_risk``). Tolerances absorb rounding when a
  payment is compared with the cuota.
* ``notice_template`` — the broker's own Spanish templates (``{{var}}``
  placeholders). When a broker has none for a kind, the defaults come from
  ``app/db/seed_assets/notice_templates_es.json`` (data, not code). Nothing is
  sent to payers until the broker approves its templates (``approved_at``).
* ``collection_notice`` — one row per outbound reminder/aviso. The unique
  ``(installment_id, kind, cadence_level, channel)`` is what makes the daily
  job idempotent: a cuota gets each cadence step at most once per channel.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import MailSender, NoticeChannel, NoticeKind, NoticeStatus, sql_enum
from app.models.types import PCT, UF, JSONType

if TYPE_CHECKING:
    from app.models.collection import CollectionInstallment

DEFAULT_CADENCE_DAYS: tuple[int, ...] = (-15, -5, 0, 3, 10)


def _default_cadence() -> list[int]:
    return list(DEFAULT_CADENCE_DAYS)


class CollectionSettings(Base, TimestampMixin):
    """The broker's cobranza configuration. Exactly one row per broker."""

    __tablename__ = "collection_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    cadence_days: Mapped[list[Any]] = mapped_column(
        JSONType, default=_default_cadence, nullable=False
    )
    # Days between two status inquiries / follow-ups on the same cuota.
    followup_days: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    tolerance_uf: Mapped[Decimal] = mapped_column(UF, default=Decimal("0.0090"), nullable=False)
    tolerance_pct: Mapped[Decimal] = mapped_column(PCT, default=Decimal("1.000"), nullable=False)
    tolerance_clp: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    # Broker-wide master switch for payer reminders.
    notices_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notices_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notices_approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    default_sender: Mapped[MailSender] = mapped_column(
        sql_enum(MailSender), default=MailSender.RADAL, nullable=False
    )
    sender_mail_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_account.id", ondelete="SET NULL"), index=True
    )
    native_collection_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    auto_status_inquiries: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Days past due before the job auto-asks the insurer.
    inquiry_after_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CollectionSettings broker={self.broker_id}>"


class NoticeTemplate(Base, TimestampMixin):
    """A broker's own template for one notice kind (+ optional cadence level)."""

    __tablename__ = "notice_template"
    __table_args__ = (
        Index("ix_notice_template_broker_kind", "broker_id", "kind", "cadence_level"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[NoticeKind] = mapped_column(sql_enum(NoticeKind), nullable=False)
    # Cadence step (-5, 0, 3, 10 …); NULL = any step of the kind.
    cadence_level: Mapped[int | None] = mapped_column(Integer)
    channel: Mapped[NoticeChannel] = mapped_column(
        sql_enum(NoticeChannel), default=NoticeChannel.EMAIL, nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # Spanish body with ``{{variable}}`` placeholders.
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollectionNotice(Base, TimestampMixin):
    """One outbound reminder / aviso about a cuota."""

    __tablename__ = "collection_notice"
    __table_args__ = (
        UniqueConstraint(
            "installment_id", "kind", "cadence_level", "channel", name="uq_collection_notice_once"
        ),
        Index("ix_collection_notice_broker_status", "broker_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installment_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_installment.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[NoticeKind] = mapped_column(sql_enum(NoticeKind), nullable=False)
    cadence_level: Mapped[int | None] = mapped_column(Integer)
    channel: Mapped[NoticeChannel] = mapped_column(sql_enum(NoticeChannel), nullable=False)
    recipient_party_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy_party.id", ondelete="SET NULL"), index=True
    )
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    cc: Mapped[list[Any] | None] = mapped_column(JSONType)
    subject: Mapped[str | None] = mapped_column(String(255))
    mail_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_message.id", ondelete="SET NULL"), index=True
    )
    share_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("share_link.id", ondelete="SET NULL"), index=True
    )
    payment_intent_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_intent.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[NoticeStatus] = mapped_column(sql_enum(NoticeStatus), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    installment: Mapped["CollectionInstallment | None"] = relationship(
        foreign_keys=[installment_id]
    )


__all__ = [
    "CollectionSettings",
    "NoticeTemplate",
    "CollectionNotice",
    "DEFAULT_CADENCE_DAYS",
]
