"""``quote_request`` (+ ``quote_line_item``) — what the broker asks the market for.

Line items (partidas) are a CHILD TABLE, not a JSON array
(docs/v2-data-modeling-decisions.md §5): the invariant
``declared_value_uf == SUM(line_items.value_uf)`` must be checkable in SQL, and
the items are compared item-by-item against each proposal.

``recipient_insurer_ids`` stays JSON on purpose: it is a send-time snapshot of
who the request went to, not a relationship that is queried or joined.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import Priority, StrEnum, sql_enum
from app.models.types import UF, JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.offering import Offering
    from app.models.placement import Placement
    from app.models.proposal import Proposal
    from app.models.user import User


class QuoteRequestStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    RECEIVING = "receiving"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class QuoteRequest(Base, TimestampMixin):
    """A request for proposals issued from a placement."""

    __tablename__ = "quote_request"
    __table_args__ = (
        Index("ix_quote_request_broker_status", "broker_id", "status"),
        Index("ix_quote_request_broker_due", "broker_id", "due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    placement_id: Mapped[int] = mapped_column(
        ForeignKey("placement.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # ``use_alter``: case_file -> policy -> proposal -> quote_request -> case_file
    # is a genuine FK cycle. Emitting this constraint with ALTER TABLE keeps
    # MySQL's create ordering solvable; SQLite (supports_alter=False) keeps it
    # inline, which it accepts because forward references are legal there.
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "case_file.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_quote_request_case_file",
        ),
        index=True,
    )
    # Submission round. La Favorita ran a second round (03F re-remisión) after
    # three declinations, so the round is a first-class number, not a note.
    round_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # What is being insured, in the broker's words.
    insured_object: Mapped[str | None] = mapped_column(Text)
    # Validated against SUM(quote_line_item.value_uf) in the service layer.
    declared_value_uf: Mapped[Decimal | None] = mapped_column(UF)
    currency: Mapped[str] = mapped_column(String(8), default="UF", nullable=False)

    requested_coverages: Mapped[str | None] = mapped_column(Text)
    desired_start: Mapped[date | None] = mapped_column(Date)
    desired_end: Mapped[date | None] = mapped_column(Date)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[Priority] = mapped_column(
        sql_enum(Priority), default=Priority.NORMAL, nullable=False
    )
    status: Mapped[QuoteRequestStatus] = mapped_column(
        sql_enum(QuoteRequestStatus), default=QuoteRequestStatus.DRAFT, nullable=False
    )

    # Send-time snapshot of the insurer ids the request was routed to.
    recipient_insurer_ids: Mapped[list[Any] | None] = mapped_column(JSONType)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    placement: Mapped["Placement"] = relationship(back_populates="quote_requests")
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id])
    line_items: Mapped[list["QuoteLineItem"]] = relationship(
        back_populates="quote_request",
        cascade="all, delete-orphan",
        order_by="QuoteLineItem.sort_order",
    )
    proposals: Mapped[list["Proposal"]] = relationship(
        back_populates="quote_request", cascade="all, delete-orphan"
    )
    offerings: Mapped[list["Offering"]] = relationship(
        back_populates="quote_request", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<QuoteRequest id={self.id} status={self.status}>"


class QuoteLineItem(Base, TimestampMixin):
    """One line item (partida) of the declared value: building, machinery, stock, ..."""

    __tablename__ = "quote_line_item"
    __table_args__ = (Index("ix_quote_line_item_order", "quote_request_id", "sort_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_request_id: Mapped[int] = mapped_column(
        ForeignKey("quote_request.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value_uf: Mapped[Decimal] = mapped_column(UF, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    quote_request: Mapped["QuoteRequest"] = relationship(back_populates="line_items")


__all__ = ["QuoteRequest", "QuoteRequestStatus", "QuoteLineItem"]
