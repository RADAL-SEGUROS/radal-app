"""AI persistence: ``extraction`` (the audit trail) + ``agent_thread`` / ``agent_message``.

Every AI write path is **suggest -> human confirm -> commit**; nothing auto-
commits. That is only auditable if the job itself is a row, so an ``extraction``
always records the model, the prompt version, the RAW output, the PARSED result,
a confidence and the source ``document`` it read.

``raw_output`` is stored in the database rather than as an S3 object so the
audit trail cannot drift from the row it explains (rule 8 keeps S3 keys in
``document`` only).

Insurer matching from a parsed extraction is by NORMALISED cmf_code / rut only —
never by name.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import EntityType, StrEnum, sql_enum
from app.models.types import PCT, JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.document import Document
    from app.models.proposal import Proposal
    from app.models.user import User


class ExtractionKind(StrEnum):
    PROPOSAL = "proposal"
    POLICY = "policy"
    INSPECTION = "inspection"
    OTHER = "other"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentScope(StrEnum):
    PROPOSAL = "proposal"
    QUOTE = "quote"
    GENERAL = "general"


class AgentRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Extraction(Base, TimestampMixin):
    """One AI parse of one document. Retained forever for traceability."""

    __tablename__ = "extraction"
    __table_args__ = (
        Index("ix_extraction_broker_status", "broker_id", "status"),
        Index("ix_extraction_document", "document_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The file that was read. Mandatory: an extraction with no source is not evidence.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ExtractionKind] = mapped_column(
        sql_enum(ExtractionKind), default=ExtractionKind.PROPOSAL, nullable=False
    )

    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # 0-100.
    confidence: Mapped[Decimal | None] = mapped_column(PCT)

    status: Mapped[ExtractionStatus] = mapped_column(
        sql_enum(ExtractionStatus), default=ExtractionStatus.PENDING, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship()
    document: Mapped["Document"] = relationship(foreign_keys=[document_id])
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id])
    proposals: Mapped[list["Proposal"]] = relationship(
        back_populates="extraction", foreign_keys="Proposal.extraction_id"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Extraction id={self.id} model={self.model!r} status={self.status}>"


class AgentThread(Base, TimestampMixin):
    """A chat conversation, optionally anchored to one entity."""

    __tablename__ = "agent_thread"
    __table_args__ = (
        Index("ix_agent_thread_broker_user", "broker_id", "user_id"),
        Index("ix_agent_thread_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[AgentScope] = mapped_column(
        sql_enum(AgentScope), default=AgentScope.GENERAL, nullable=False
    )
    # Anchor, when the thread is about a specific proposal / quote / client.
    entity_type: Mapped[EntityType | None] = mapped_column(sql_enum(EntityType))
    entity_id: Mapped[int | None] = mapped_column()
    title: Mapped[str | None] = mapped_column(String(255))
    is_archived: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    broker: Mapped["Broker"] = relationship()
    user: Mapped["User"] = relationship(back_populates="agent_threads")
    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AgentMessage.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentThread id={self.id} scope={self.scope}>"


class AgentMessage(Base, TimestampMixin):
    """One turn in an ``agent_thread``."""

    __tablename__ = "agent_message"
    __table_args__ = (Index("ix_agent_message_thread", "thread_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_thread.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[AgentRole] = mapped_column(sql_enum(AgentRole), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONType)
    model: Mapped[str | None] = mapped_column(String(160))
    tokens: Mapped[int | None] = mapped_column(Integer)

    thread: Mapped["AgentThread"] = relationship(back_populates="messages")


__all__ = [
    "Extraction",
    "ExtractionKind",
    "ExtractionStatus",
    "AgentThread",
    "AgentScope",
    "AgentMessage",
    "AgentRole",
]
