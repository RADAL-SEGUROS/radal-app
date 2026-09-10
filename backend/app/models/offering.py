"""``offering`` — the shareable package the broker sends to the insured.

One quote request can be packaged more than once (a re-send after a
negotiation), so this is 1:N. ``share_token`` is the unguessable public handle
used by the WhatsApp/email link; the generated PDF is referenced by FK to
``document`` (rule 8: no raw S3 keys on domain entities).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.document import Document
    from app.models.proposal import Proposal
    from app.models.quote import QuoteRequest
    from app.models.user import User


class OfferingChannel(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    DOWNLOAD = "download"
    LINK = "link"


class OfferingStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    VIEWED = "viewed"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class Offering(Base, TimestampMixin):
    """A packaged, shareable comparison highlighting one recommended proposal."""

    __tablename__ = "offering"
    __table_args__ = (
        Index("ix_offering_broker_status", "broker_id", "status"),
        Index("ix_offering_quote", "quote_request_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_request_id: Mapped[int] = mapped_column(
        ForeignKey("quote_request.id", ondelete="CASCADE"), nullable=False, index=True
    )
    selected_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True
    )

    # --- The INSURED's decision on the public share surface (v8) -------------
    # ``selected_proposal_id`` is the broker's RECOMMENDATION; these record what
    # the insured actually chose (and why) from the shared comparison.
    decided_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True
    )
    decided_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Unguessable public handle for the share link.
    share_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # Generated branded PDF. FK to `document` — never a raw S3 key.
    pdf_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    sent_via: Mapped[OfferingChannel | None] = mapped_column(sql_enum(OfferingChannel))
    status: Mapped[OfferingStatus] = mapped_column(
        sql_enum(OfferingStatus), default=OfferingStatus.DRAFT, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship()
    quote_request: Mapped["QuoteRequest"] = relationship(back_populates="offerings")
    selected_proposal: Mapped["Proposal | None"] = relationship(
        foreign_keys=[selected_proposal_id]
    )
    decided_proposal: Mapped["Proposal | None"] = relationship(
        foreign_keys=[decided_proposal_id]
    )
    pdf_document: Mapped["Document | None"] = relationship(foreign_keys=[pdf_document_id])
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Offering id={self.id} status={self.status}>"


__all__ = ["Offering", "OfferingChannel", "OfferingStatus"]
