"""``placement`` — the operating folder: one asset × one insurance line × one period.

Everything downstream (inspection requests, quote requests, proposals, the
awarded policy) hangs off a placement, which is what gives the workflow its
status machine: draft -> inspection -> pre_underwriting -> quoting ->
negotiating -> awarded -> active -> closed.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.client import Client
    from app.models.document import Document
    from app.models.inspection import InspectionRequest
    from app.models.insurance_line import InsuranceLine
    from app.models.policy import Policy
    from app.models.quote import QuoteRequest


class PlacementStatus(StrEnum):
    DRAFT = "draft"
    INSPECTION = "inspection"
    PRE_UNDERWRITING = "pre_underwriting"
    QUOTING = "quoting"
    NEGOTIATING = "negotiating"
    AWARDED = "awarded"
    ACTIVE = "active"
    CLOSED = "closed"


class Placement(Base, TimestampMixin):
    """The unit of operational work that a quote request is issued from."""

    __tablename__ = "placement"
    __table_args__ = (
        Index("ix_placement_broker_status", "broker_id", "status"),
        Index("ix_placement_broker_client", "broker_id", "client_id"),
        Index("ix_placement_asset_line", "asset_id", "insurance_line_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("asset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurance_line_id: Mapped[int] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Human label for the cover period, e.g. "2026-2027".
    period: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)

    status: Mapped[PlacementStatus] = mapped_column(
        sql_enum(PlacementStatus), default=PlacementStatus.DRAFT, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    # The technical brief (bases técnicas). FK to `document` — never a raw S3 key.
    brief_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    # Convenience back-pointer to the account expediente that wraps this
    # placement 1:1. The AUTHORITY is ``case_file.placement_id``.
    # ``use_alter``: placement <-> case_file is a genuine FK cycle (see
    # ``document.case_file_id`` for the same treatment).
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "case_file.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_placement_case_file",
        ),
        index=True,
    )

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    client: Mapped["Client"] = relationship(back_populates="placements")
    asset: Mapped["Asset"] = relationship(back_populates="placements")
    insurance_line: Mapped["InsuranceLine"] = relationship(back_populates="placements")
    brief_document: Mapped["Document | None"] = relationship(
        foreign_keys=[brief_document_id]
    )
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    quote_requests: Mapped[list["QuoteRequest"]] = relationship(
        back_populates="placement", cascade="all, delete-orphan"
    )
    inspection_requests: Mapped[list["InspectionRequest"]] = relationship(
        back_populates="placement"
    )
    policies: Mapped[list["Policy"]] = relationship(back_populates="placement")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Placement id={self.id} status={self.status}>"
