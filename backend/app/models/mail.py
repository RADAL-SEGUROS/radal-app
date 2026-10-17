"""Mail: accounts, the message log, status inquiries and payment evidence (v12).

docs/v12/04-mail.md is the contract. Radal cannot read the bank-transfer
comprobantes (payments land in the INSURER's account/mailbox), so assisted
collection ASKS: a ``status_inquiry`` mails the insurer with a token in the
subject (``[RADAL-INQ-XXXXXX]``) and a public share link; replies are matched
back by token / ``In-Reply-To`` and become ``payment_evidence`` SUGGESTIONS a
human confirms (rule 6: suggest -> confirm -> commit).

* ``mail_account`` — ``broker_id`` NULLABLE: the single NULL-broker row is the
  Radal system mailbox (Zoho), kept only to store its IMAP cursor. Passwords
  are ENCRYPTED in ``secret_ciphertext`` (``app.core.crypto``), write-only.
* ``mail_message`` — every outbound mail (body stored: it is ours) and every
  KEPT inbound one. Privacy (Ley 21.719): an inbound mail that fails the
  prefilter is stored as ``{provider_uid, occurred_at, status=ignored}`` only —
  no sender, subject or body. ``broker_id`` is NULL for unmatched system-inbox
  mail. The ``.eml`` lives in ``document`` (rule 8), never here.
* ``status_inquiry`` / ``payment_evidence`` — workspace rows. Evidence points
  at the payment it produced once confirmed (``payment_id``); the payment
  points back (``payment.evidence_id``, the ``use_alter`` side of the cycle).
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

from app.models.base_class import Base, TimestampMixin
from app.models.enums import (
    EvidenceSource,
    EvidenceStatus,
    InboundClass,
    InquiryStatus,
    MailAccountStatus,
    MailDirection,
    MailMessageStatus,
    MailProvider,
    MailSecurity,
    PaymentCurrency,
    ReportedStatus,
    sql_enum,
)
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.payment import Payment


class MailAccount(Base, TimestampMixin):
    """A mailbox Radal sends from / polls. NULL broker = the Radal system box."""

    __tablename__ = "mail_account"
    __table_args__ = (
        UniqueConstraint("broker_id", "address", name="uq_mail_account_broker_address"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), index=True
    )
    # NULL = a shared broker mailbox.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[MailProvider] = mapped_column(sql_enum(MailProvider), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))

    smtp_host: Mapped[str | None] = mapped_column(String(255))
    smtp_port: Mapped[int | None] = mapped_column(Integer)
    smtp_security: Mapped[MailSecurity | None] = mapped_column(sql_enum(MailSecurity))
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None] = mapped_column(Integer)
    imap_security: Mapped[MailSecurity | None] = mapped_column(sql_enum(MailSecurity))
    username: Mapped[str | None] = mapped_column(String(255))
    # Encrypted password. Write-only in the API (``has_secret`` only).
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)

    send_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    receive_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # {uidvalidity, last_uid}
    sync_cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    status: Mapped[MailAccountStatus] = mapped_column(
        sql_enum(MailAccountStatus), default=MailAccountStatus.DRAFT, nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MailAccount id={self.id} {self.provider}:{self.address!r} {self.status}>"


class MailMessage(Base, TimestampMixin):
    """One outbound or kept inbound mail."""

    __tablename__ = "mail_message"
    __table_args__ = (
        UniqueConstraint("mail_account_id", "provider_uid", name="uq_mail_message_account_uid"),
        Index("ix_mail_message_broker_direction", "broker_id", "direction", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), index=True
    )
    mail_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_account.id", ondelete="SET NULL"), index=True
    )
    direction: Mapped[MailDirection] = mapped_column(sql_enum(MailDirection), nullable=False)
    message_id_header: Mapped[str | None] = mapped_column(String(255), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    # Our ``RADAL-XXXXXX`` thread token.
    thread_token: Mapped[str | None] = mapped_column(String(32), index=True)
    from_address: Mapped[str | None] = mapped_column(String(255))
    to_addresses: Mapped[list[Any] | None] = mapped_column(JSONType)
    cc_addresses: Mapped[list[Any] | None] = mapped_column(JSONType)
    subject: Mapped[str | None] = mapped_column(String(500))
    # Outbound: full. Inbound: only when classified as related.
    body_text: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # IMAP UID (as text) inside ``mail_account_id``'s INBOX.
    provider_uid: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[InboundClass | None] = mapped_column(sql_enum(InboundClass))
    extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )
    # The .eml (rule 8).
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[MailMessageStatus] = mapped_column(
        sql_enum(MailMessageStatus), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)

    mail_account: Mapped["MailAccount | None"] = relationship(foreign_keys=[mail_account_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MailMessage id={self.id} {self.direction} {self.status}>"


class StatusInquiry(Base, TimestampMixin):
    """Radal asks an insurer "¿está pagada la cuota…?" about 1..N cuotas."""

    __tablename__ = "status_inquiry"
    __table_args__ = (Index("ix_status_inquiry_broker_status", "broker_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ``INQ-`` + 6 base32 — rides in the subject as ``[RADAL-INQ-XXXXXX]``.
    token: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    # Ids of the ``collection_installment`` rows asked about.
    installment_ids: Mapped[list[Any]] = mapped_column(JSONType, nullable=False)
    to_address: Mapped[str | None] = mapped_column(String(255))
    outbound_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_message.id", ondelete="SET NULL"), index=True
    )
    # The insurer can answer through a public link (audience insurer).
    share_link_id: Mapped[int | None] = mapped_column(
        ForeignKey("share_link.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[InquiryStatus] = mapped_column(
        sql_enum(InquiryStatus), default=InquiryStatus.DRAFT, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    created_by_job: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    outbound_message: Mapped["MailMessage | None"] = relationship(
        foreign_keys=[outbound_message_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StatusInquiry id={self.id} {self.token} {self.status}>"


class PaymentEvidence(Base, TimestampMixin):
    """A SUGGESTION that a cuota's status changed. A human confirms or rejects it."""

    __tablename__ = "payment_evidence"
    __table_args__ = (Index("ix_payment_evidence_broker_status", "broker_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installment_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_installment.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[EvidenceSource | None] = mapped_column(sql_enum(EvidenceSource))
    mail_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_message.id", ondelete="SET NULL"), index=True
    )
    status_inquiry_id: Mapped[int | None] = mapped_column(
        ForeignKey("status_inquiry.id", ondelete="SET NULL"), index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )
    reported_status: Mapped[ReportedStatus | None] = mapped_column(sql_enum(ReportedStatus))
    reported_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    reported_currency: Mapped[PaymentCurrency | None] = mapped_column(
        sql_enum(PaymentCurrency)
    )
    reported_paid_on: Mapped[date | None] = mapped_column(Date)
    # Match confidence 0-100 + why.
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    reasons: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)
    status: Mapped[EvidenceStatus] = mapped_column(
        sql_enum(EvidenceStatus), default=EvidenceStatus.SUGGESTED, nullable=False
    )
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The payment the confirmation produced.
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)

    payment: Mapped["Payment | None"] = relationship(foreign_keys=[payment_id])
    status_inquiry: Mapped["StatusInquiry | None"] = relationship(
        foreign_keys=[status_inquiry_id]
    )
    mail_message: Mapped["MailMessage | None"] = relationship(foreign_keys=[mail_message_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PaymentEvidence id={self.id} {self.reported_status} {self.status}>"


__all__ = ["MailAccount", "MailMessage", "StatusInquiry", "PaymentEvidence"]
