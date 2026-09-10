"""``broker_proposal`` (v8) — the OUTBOUND broker artifact (la propuesta).

TERMINOLOGY TRAP: ``Proposal`` already means the insurer's INBOUND offer
(``app.models.proposal``). This is the DIFFERENT thing — the outbound artifact
the broker builds SOLELY from a comparison snapshot, ratifies, and sends. It is
never a ``Proposal``.

Shape (the ``record_expediente`` satellite pattern): typed columns for what we
compare/filter (``content_hash``, ``status``, the ratification), a JSON
``payload`` for the rendered content tail, provenance FKs, lifecycle columns.

Plain SET NULL FKs only (no ``use_alter``): it points DOWN at ``comparison`` /
``proposal`` (the winning inbound offer) / ``case_file`` / ``document`` / ``user``
and nothing points back. The "cannot exist without its comparison" rule is
enforced at the SERVICE layer (like ``proposal``'s insurer rut/cmf rule), not by
a NOT NULL/RESTRICT FK — ``comparison_id`` is a plain SET NULL FK per the v8
new-table convention.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.comparison import Comparison
    from app.models.document import Document
    from app.models.proposal import Proposal
    from app.models.user import User


class BrokerProposalStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    RATIFIED = "ratified"
    SUPERSEDED = "superseded"


class BrokerProposal(Base, TimestampMixin):
    """The outbound propuesta artifact, built from a comparison snapshot."""

    __tablename__ = "broker_proposal"
    __table_args__ = (
        Index("ix_broker_proposal_broker_status", "broker_id", "status"),
        Index("ix_broker_proposal_case", "case_file_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The account expediente this propuesta belongs to.
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )
    # The source comparison snapshot (mandatory at the SERVICE layer; plain SET
    # NULL FK per the v8 convention).
    comparison_id: Mapped[int | None] = mapped_column(
        ForeignKey("comparison.id", ondelete="SET NULL"), index=True
    )
    # The selected inbound insurer offer (the winning ``proposal``).
    winning_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True
    )

    # A fixed scalar we compare for tamper / version detection.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # The assembled outbound content snapshot — heterogeneous, rendered only.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # The generated artifact (rule 8: the S3 key lives on the document row).
    pdf_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[BrokerProposalStatus] = mapped_column(
        sql_enum(BrokerProposalStatus), default=BrokerProposalStatus.DRAFT, nullable=False
    )
    # The ratification (same lifecycle-column pattern as
    # ``record_expediente.registered_at`` / ``registered_by_id``).
    is_ratified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ratified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ratified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship()
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    comparison: Mapped["Comparison | None"] = relationship(foreign_keys=[comparison_id])
    winning_proposal: Mapped["Proposal | None"] = relationship(
        foreign_keys=[winning_proposal_id]
    )
    pdf_document: Mapped["Document | None"] = relationship(foreign_keys=[pdf_document_id])
    ratified_by: Mapped["User | None"] = relationship(foreign_keys=[ratified_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<BrokerProposal id={self.id} case={self.case_file_id} status={self.status}>"


__all__ = ["BrokerProposal", "BrokerProposalStatus"]
