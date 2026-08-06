"""Inspections: the request, the report, and its boundary child rows.

Split by evidence (docs/v2-data-modeling-decisions.md §7):
  * SCORES are COLUMNS — one fixed 8-key shape across every real inspection, and
    they are filtered on ("show me risks scoring under 70").
  * The CHECKLIST is JSON and versioned — its section set follows the asset type
    (a "Refrigeración (NH3)" section only exists on the plant).
  * BOUNDARIES (colindancias) are a CHILD TABLE — a queryable 1:N where an
    aggravating neighbour materially changes underwriting.

The report file is referenced by FK to ``document`` (nullable until issued);
no raw S3 key ever lives here.
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
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import Priority, StrEnum, sql_enum
from app.models.types import PCT, SCORE, JSONType

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.broker import Broker
    from app.models.document import Document
    from app.models.insurance_line import InsuranceLine
    from app.models.placement import Placement
    from app.models.user import User


class InspectionRequestStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class InspectionStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    ISSUED = "issued"
    ARCHIVED = "archived"


class InspectionRequest(Base, TimestampMixin):
    """A request to inspect an asset, usually because the line requires it."""

    __tablename__ = "inspection_request"
    __table_args__ = (
        Index("ix_inspection_request_broker_status", "broker_id", "status"),
        Index("ix_inspection_request_broker_target", "broker_id", "target_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("asset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    placement_id: Mapped[int | None] = mapped_column(
        ForeignKey("placement.id", ondelete="SET NULL"), index=True
    )
    insurance_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="SET NULL"), index=True
    )

    reason: Mapped[str | None] = mapped_column(Text)
    urgency: Mapped[Priority] = mapped_column(
        sql_enum(Priority), default=Priority.NORMAL, nullable=False
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[InspectionRequestStatus] = mapped_column(
        sql_enum(InspectionRequestStatus),
        default=InspectionRequestStatus.PENDING,
        nullable=False,
    )
    requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    broker: Mapped["Broker"] = relationship()
    asset: Mapped["Asset"] = relationship(back_populates="inspection_requests")
    placement: Mapped["Placement | None"] = relationship(back_populates="inspection_requests")
    insurance_line: Mapped["InsuranceLine | None"] = relationship(
        foreign_keys=[insurance_line_id]
    )
    requested_by: Mapped["User | None"] = relationship(foreign_keys=[requested_by_id])
    inspections: Mapped[list["Inspection"]] = relationship(
        back_populates="inspection_request", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InspectionRequest id={self.id} status={self.status}>"


class Inspection(Base, TimestampMixin):
    """A (versioned) inspection report for an asset."""

    __tablename__ = "inspection"
    __table_args__ = (
        Index("ix_inspection_broker_status", "broker_id", "status"),
        Index("ix_inspection_asset_version", "asset_id", "version"),
        Index("ix_inspection_broker_score", "broker_id", "overall_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("asset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inspection_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspection_request.id", ondelete="SET NULL"), index=True
    )
    inspector_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[InspectionStatus] = mapped_column(
        sql_enum(InspectionStatus), default=InspectionStatus.DRAFT, nullable=False
    )
    visit_date: Mapped[date | None] = mapped_column(Date)
    report_date: Mapped[date | None] = mapped_column(Date)
    folio: Mapped[str | None] = mapped_column(String(64), index=True)
    findings_summary: Mapped[str | None] = mapped_column(Text)

    # --- Scores (0-100) — COLUMNS so they are filterable/sortable -----------
    technical_score: Mapped[Decimal | None] = mapped_column(SCORE)
    commercial_score: Mapped[Decimal | None] = mapped_column(SCORE)
    location_score: Mapped[Decimal | None] = mapped_column(SCORE)
    loss_estimate_score: Mapped[Decimal | None] = mapped_column(SCORE)
    overall_score: Mapped[Decimal | None] = mapped_column(SCORE)
    # Probable / Estimated Maximum Loss, 0-100.
    pml_pct: Mapped[Decimal | None] = mapped_column(PCT)
    eml_pct: Mapped[Decimal | None] = mapped_column(PCT)
    risk_classification: Mapped[str | None] = mapped_column(String(255))

    # --- Checklist (JSON, versioned) ---------------------------------------
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    checklist_version: Mapped[int | None] = mapped_column(Integer)

    # Issued report. FK to `document` — never a raw S3 key.
    report_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    broker: Mapped["Broker"] = relationship()
    asset: Mapped["Asset"] = relationship(back_populates="inspections")
    inspection_request: Mapped["InspectionRequest | None"] = relationship(
        back_populates="inspections"
    )
    inspector: Mapped["User | None"] = relationship(foreign_keys=[inspector_id])
    report_document: Mapped["Document | None"] = relationship(
        foreign_keys=[report_document_id]
    )
    boundaries: Mapped[list["InspectionBoundary"]] = relationship(
        back_populates="inspection",
        cascade="all, delete-orphan",
        order_by="InspectionBoundary.sort_order",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Inspection id={self.id} folio={self.folio!r} score={self.overall_score}>"


class InspectionBoundary(Base, TimestampMixin):
    """One neighbouring property (colindancia) of the inspected asset."""

    __tablename__ = "inspection_boundary"
    __table_args__ = (Index("ix_inspection_boundary_order", "inspection_id", "sort_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspection.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Cardinal orientation as reported (north / south / east / west), kept free
    # text because reports also use "northeast", "front", "rear".
    orientation: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    # As reported, e.g. "15 m" — the source is prose, not a number.
    distance: Mapped[str | None] = mapped_column(String(64))
    aggravating: Mapped[str | None] = mapped_column(String(255))
    is_aggravating: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    inspection: Mapped["Inspection"] = relationship(back_populates="boundaries")


__all__ = [
    "InspectionRequest",
    "InspectionRequestStatus",
    "Inspection",
    "InspectionStatus",
    "InspectionBoundary",
]
