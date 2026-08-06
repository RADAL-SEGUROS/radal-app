"""``document`` — THE ONLY PLACE AN S3 KEY LIVES.

Rule 8 of the rebuild: domain entities never store a raw S3 key string; where a
file matters they hold a FK to ``document.id``
(``proposal.source_document_id`` NOT NULL, ``inspection.report_document_id``,
``placement.brief_document_id``, ``offering.pdf_document_id``).

Why: the file's metadata — who uploaded it, when, mime, size, category — must
live in exactly one row. A bare string drifts, cannot be joined, and leaves no
audit trail; a FK gives referential integrity and one lifecycle for S3 cleanup.

The key is DERIVED, never free-form:
``documents/{entity_type}/{entity_id}/{category}-{n}.{ext}``.

(The ``media/`` prefix is a different, deterministic namespace for derived
512x512 WEBP logos/avatars — those live on ``broker.logo_key`` etc. and are not
documents.)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import EntityType, StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.user import User


class DocumentCategory(StrEnum):
    """The 13 categories observed in the real corpus, plus a safety valve."""

    CMF_CERTIFICATE = "cmf_certificate"
    APPOINTMENT = "appointment"
    LOGO = "logo"
    ASSET_SHEET = "asset_sheet"
    ASSET_PHOTO = "asset_photo"
    VALUATION = "valuation"
    INSURED_AMOUNTS = "insured_amounts"
    EVIDENCE = "evidence"
    INSPECTION_REPORT = "inspection_report"
    TECHNICAL_BRIEF = "technical_brief"
    CLAIMS_HISTORY = "claims_history"
    PROPOSAL = "proposal"
    POWER_OF_ATTORNEY = "power_of_attorney"
    OFFERING = "offering"
    OTHER = "other"


class Document(Base, TimestampMixin):
    """One stored file, attached polymorphically to a domain entity."""

    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_entity", "entity_type", "entity_id"),
        Index("ix_document_broker_category", "broker_id", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Polymorphic attachment. Deliberately NOT a FK: one table serves every
    # entity, and the pair is always resolved inside the broker's own scope.
    entity_type: Mapped[EntityType] = mapped_column(sql_enum(EntityType), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)

    # The single source of truth for where the bytes are.
    s3_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)

    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(128))

    category: Mapped[DocumentCategory] = mapped_column(
        sql_enum(DocumentCategory), default=DocumentCategory.OTHER, nullable=False
    )
    # Workflow phase label the file was captured in (free text, UI grouping only).
    phase: Mapped[str | None] = mapped_column(String(120))

    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship(back_populates="documents")
    uploaded_by: Mapped["User | None"] = relationship(foreign_keys=[uploaded_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document id={self.id} key={self.s3_key!r}>"


__all__ = ["Document", "DocumentCategory"]
