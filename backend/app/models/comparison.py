"""``comparison`` (v8) — the incremental proposal comparison milestone.

The comparison is an expediente milestone: proposals arrive one at a time, each
is extracted DYNAMICALLY (a fixed money/period core that commits to the typed
``proposal`` columns, plus an open ``facets`` list), cached per-proposal, then
re-aligned over the cached compact facets against a monotonic canonical
dictionary that is version-snapshotted.

Two storage layers, following the ``record_expediente`` satellite pattern
(a container with typed columns for what we sort/filter, a JSON payload for the
dynamic tail, provenance FKs, a status/version):

  * ``comparison_source`` — LAYER 1, the per-proposal dynamic-extraction cache,
    keyed by proposal so it survives across re-runs. The fixed money/period core
    is NOT stored here — it lives on the typed ``proposal`` columns (no
    double-sourcing). Only the compact ``facets`` and the wrong-file verdict do.
  * ``comparison`` (+ ``comparison_entry``) — LAYER 2, one row per RE-RUN
    (1:N per case_file, history preserved). ``canonical_version`` is monotonic;
    ``dictionary`` is the canonical facet dictionary SNAPSHOTTED at that version
    so a later dictionary edit never reshapes a stored comparison; the aligned
    result is rendered from ``aligned_matrix`` (JSON, never SQL-filtered).

Plain SET NULL FKs only (no ``use_alter``): the comparison points DOWN at
``case_file`` / ``placement`` / ``proposal`` / ``document`` / ``extraction`` and
nothing points back, so there is no cycle to close (same topology as
``record_expediente``).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.ai import Extraction
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.document import Document
    from app.models.placement import Placement
    from app.models.proposal import Proposal


class ComparisonStatus(StrEnum):
    DRAFT = "draft"
    ALIGNED = "aligned"
    SUPERSEDED = "superseded"


class ComparisonSource(Base, TimestampMixin):
    """Per-proposal dynamic-extraction cache (LAYER 1). One live row per proposal."""

    __tablename__ = "comparison_source"
    __table_args__ = (
        UniqueConstraint("broker_id", "proposal_id", name="uq_comparison_source_proposal"),
        Index("ix_comparison_source_broker", "broker_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The inbound offer this cache is for. SET NULL (plain FK; no cycle).
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True
    )
    # The AI parse behind the cache (rule 6: one Extraction row per attempt).
    source_extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )
    # The compact dynamic facet list re-aligned across comparisons — heterogeneous,
    # per-line, rendered/re-aligned, never SQL-filtered (decision #3 logic).
    facets: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)
    # Wrong-file detection verdict. Default False (valid until flagged); the
    # bool is filtered ("show rejected uploads"), the reason is prose.
    is_wrong_file: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wrong_file_reason: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    broker: Mapped["Broker"] = relationship()
    proposal: Mapped["Proposal | None"] = relationship(foreign_keys=[proposal_id])
    source_extraction: Mapped["Extraction | None"] = relationship(
        foreign_keys=[source_extraction_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ComparisonSource id={self.id} proposal={self.proposal_id}>"


class Comparison(Base, TimestampMixin):
    """One aligned comparison RE-RUN of an account (LAYER 2). 1:N per case_file."""

    __tablename__ = "comparison"
    __table_args__ = (
        Index("ix_comparison_broker_case", "broker_id", "case_file_id"),
        Index("ix_comparison_broker_status", "broker_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The account expediente this comparison belongs to. Plain SET NULL FK:
    # re-runs are kept as history and may outlive a detached folder.
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )
    placement_id: Mapped[int | None] = mapped_column(
        ForeignKey("placement.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[ComparisonStatus] = mapped_column(
        sql_enum(ComparisonStatus), default=ComparisonStatus.DRAFT, nullable=False
    )
    # Monotonic re-alignment version; the snapshot key of ``dictionary``.
    canonical_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # The canonical facet dictionary SNAPSHOTTED at this version — a later edit
    # never reshapes a stored comparison (mirrors ``record_expediente.schema_version``).
    dictionary: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)
    # The rendered aligned result (rows × proposals); rendered only, never joined.
    aligned_matrix: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)
    # The generated comparison pack (rule 8: the S3 key lives on the document row).
    pdf_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    source_extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship()
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    placement: Mapped["Placement | None"] = relationship(foreign_keys=[placement_id])
    pdf_document: Mapped["Document | None"] = relationship(foreign_keys=[pdf_document_id])
    source_extraction: Mapped["Extraction | None"] = relationship(
        foreign_keys=[source_extraction_id]
    )
    entries: Mapped[list["ComparisonEntry"]] = relationship(
        back_populates="comparison",
        cascade="all, delete-orphan",
        order_by="ComparisonEntry.sort_order",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Comparison id={self.id} case={self.case_file_id} v{self.canonical_version}>"


class ComparisonEntry(Base, TimestampMixin):
    """One proposal column of a comparison run. A PURE CHILD — no ``broker_id``.

    Same precedent as ``proposal_coverage``: addressed only through its parent,
    so the tenant scope comes from ``comparison.broker_id``.
    """

    __tablename__ = "comparison_entry"
    __table_args__ = (
        Index("ix_comparison_entry_comparison", "comparison_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("comparison.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The inbound offer this column is. Plain SET NULL FK.
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True
    )
    # The per-proposal cache this column was aligned from.
    comparison_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("comparison_source.id", ondelete="SET NULL"), index=True
    )
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    comparison: Mapped["Comparison"] = relationship(back_populates="entries")
    proposal: Mapped["Proposal | None"] = relationship(foreign_keys=[proposal_id])
    comparison_source: Mapped["ComparisonSource | None"] = relationship(
        foreign_keys=[comparison_source_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ComparisonEntry id={self.id} comparison={self.comparison_id}>"


__all__ = [
    "Comparison",
    "ComparisonEntry",
    "ComparisonSource",
    "ComparisonStatus",
]
