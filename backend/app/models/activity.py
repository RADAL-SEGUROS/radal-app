"""``activity`` (audit trail) and ``note`` (human commentary).

Both are polymorphic over ``entity_type`` + ``entity_id``, both are
broker-scoped, and both are append-mostly. ``note.is_internal`` marks
broker-private commentary that must never reach an insured or insurer user.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin, utcnow
from app.models.enums import EntityType, sql_enum
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.user import User


class Activity(Base, TimestampMixin):
    """One audited event in the broker's workspace."""

    __tablename__ = "activity"
    __table_args__ = (
        Index("ix_activity_broker_occurred", "broker_id", "occurred_at"),
        Index("ix_activity_entity", "entity_type", "entity_id"),
        Index("ix_activity_broker_action", "broker_id", "action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    # Machine-readable verb, e.g. "client.created", "proposal.confirmed".
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[EntityType] = mapped_column(sql_enum(EntityType), nullable=False)
    entity_id: Mapped[int | None] = mapped_column()
    description: Mapped[str | None] = mapped_column(Text)
    # Structured payload (before/after, ids touched) for richer audit views.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    broker: Mapped["Broker"] = relationship(back_populates="activities")
    user: Mapped["User | None"] = relationship(foreign_keys=[user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Activity id={self.id} action={self.action!r}>"


class Note(Base, TimestampMixin):
    """A free-text note a user attached to any entity."""

    __tablename__ = "note"
    __table_args__ = (
        Index("ix_note_entity", "entity_type", "entity_id"),
        Index("ix_note_broker_internal", "broker_id", "is_internal"),
        Index("ix_note_broker_followup", "broker_id", "follow_up_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[EntityType] = mapped_column(sql_enum(EntityType), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Broker-private when true. Never surfaced to insured/insurer users.
    is_internal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    phase: Mapped[str | None] = mapped_column(String(120))
    # The team asked for notes WITH a follow-up date; this is that single date.
    follow_up_on: Mapped[date | None] = mapped_column(Date)

    broker: Mapped["Broker"] = relationship(back_populates="notes")
    author: Mapped["User | None"] = relationship(foreign_keys=[author_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Note id={self.id} entity={self.entity_type}:{self.entity_id}>"


__all__ = ["Activity", "Note"]
