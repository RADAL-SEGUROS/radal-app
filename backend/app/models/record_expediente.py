"""``record_expediente`` (v6) — the registered antecedentes instance.

One row per account (``case_file`` of ``kind=account``): the human-validated
consolidation of everything the insured communicated for that ramo × vigencia.
It follows CLAUDE.md rule 6 (extended for v6):
``extract -> human validates & completes -> register -> view + PDF``.

Nothing auto-writes. The AI consolidation produces an ``Extraction`` row
(``source_extraction_id``) and stages a suggested ``payload`` at status
``review``; only the human confirm step sets ``status = registered`` with
``registered_at`` / ``registered_by_id``. The generated branded PDF is a
``document`` row (rule 8: the only place an S3 key lives) reached through
``pdf_document_id``.

Plain FKs only — no dependency cycle (same topology as ``CasePack``): the
expediente points down at ``case_file``, ``line_record_schema``, ``extraction``
and ``document``; none of those points back.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import RecordExpedienteStatus, sql_enum
from app.models.types import PCT, JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.document import Document
    from app.models.insurance_line import InsuranceLine
    from app.models.line_record_schema import LineRecordSchema
    from app.models.user import User


class RecordExpediente(Base, TimestampMixin):
    """The registered / in-progress antecedentes expediente of one account."""

    __tablename__ = "record_expediente"
    __table_args__ = (
        # Exactly one expediente per account within a broker (rule 2).
        UniqueConstraint(
            "broker_id", "case_file_id", name="uq_record_expediente_broker_case"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The account this expediente consolidates (a ``case_file`` of kind=account).
    case_file_id: Mapped[int] = mapped_column(
        ForeignKey("case_file.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurance_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="SET NULL"), index=True
    )
    # The schema the payload was validated against + the version snapshot, so a
    # later schema edit never silently reshapes a registered expediente.
    line_record_schema_id: Mapped[int | None] = mapped_column(
        ForeignKey("line_record_schema.id", ondelete="SET NULL"), index=True
    )
    schema_version: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[RecordExpedienteStatus] = mapped_column(
        sql_enum(RecordExpedienteStatus),
        default=RecordExpedienteStatus.DRAFT,
        nullable=False,
    )
    # The human-validated sectioned data (mirrors the schema ``definition``).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # The AI consolidation attempt this expediente was suggested from (rule 6:
    # one Extraction row per attempt, suggest-only).
    source_extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )
    # The generated branded PDF (rule 8: the S3 key lives on the document row).
    pdf_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    ai_confidence: Mapped[Decimal | None] = mapped_column(PCT)

    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    # --- Relationships (read-only, for serialization) -----------------------
    broker: Mapped["Broker"] = relationship()
    case_file: Mapped["CaseFile"] = relationship(foreign_keys=[case_file_id])
    insurance_line: Mapped["InsuranceLine | None"] = relationship(
        foreign_keys=[insurance_line_id]
    )
    line_record_schema: Mapped["LineRecordSchema | None"] = relationship(
        foreign_keys=[line_record_schema_id]
    )
    pdf_document: Mapped["Document | None"] = relationship(
        foreign_keys=[pdf_document_id]
    )
    registered_by: Mapped["User | None"] = relationship(foreign_keys=[registered_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RecordExpediente id={self.id} case={self.case_file_id} "
            f"status={self.status}>"
        )


__all__ = ["RecordExpediente"]
