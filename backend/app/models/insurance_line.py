"""Insurance lines (ramos) and the official CMF taxonomy they map onto.

``cmf_line`` is the official CMF/FECU reference taxonomy — global, cross-broker,
no ``broker_id``. ``insurance_line`` is what a broker actually operates with; it
may be global (``broker_id`` NULL, seeded by Radal) or broker-specific.

The mapping is a real JUNCTION table (``insurance_line_cmf_code``) rather than a
list of loose integers, so the taxonomy stays authoritative and joinable
(docs/v2-data-modeling-decisions.md §8).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.placement import Placement


class CmfLineKind(StrEnum):
    LINE = "line"
    SUBLINE = "subline"


class CmfLine(Base, TimestampMixin):
    """A row of the official CMF/FECU line taxonomy. GLOBAL — no broker_id.

    The taxonomy is TWO namespaces that reuse the same numbers: the 37 *lines*
    (ramos: 1 = Incendio, 4 = Terremoto y Tsunami …) and the 7 *subdivisions*
    (distribution channels: 1 = Individual, 4 = Industria, Infraestructura y
    Comercio). Uniqueness is therefore ``(kind, code)`` — a global unique on
    ``code`` alone would reject the official list.
    """

    __tablename__ = "cmf_line"
    __table_args__ = (UniqueConstraint("kind", "code", name="uq_cmf_line_kind_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Official code, e.g. "1", "4", "3A". Unique WITHIN its kind.
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[CmfLineKind] = mapped_column(
        sql_enum(CmfLineKind), default=CmfLineKind.LINE, nullable=False
    )
    group_code: Mapped[str | None] = mapped_column(String(32), index=True)
    group_name: Mapped[str | None] = mapped_column(String(255))

    insurance_line_links: Mapped[list["InsuranceLineCmfCode"]] = relationship(
        back_populates="cmf_line", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CmfLine code={self.code!r} name={self.name!r}>"


class InsuranceLine(Base, TimestampMixin):
    """An insurance line (ramo) the broker places business in.

    ``broker_id`` NULL = a global default line seeded by Radal.
    """

    __tablename__ = "insurance_line"
    __table_args__ = (
        UniqueConstraint("broker_id", "name", name="uq_insurance_line_broker_name"),
        Index("ix_insurance_line_broker_active", "broker_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    requires_inspection: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # CMF subdivision this line reports under, e.g. "4".
    cmf_subdivision: Mapped[str | None] = mapped_column(String(32))

    # Dynamic form/comparator configuration driven by the line, NOT by code:
    # `min_fields` shapes asset.attributes, `comparator_fields` picks which
    # proposal fields the comparison table surfaces.
    min_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    comparator_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    broker: Mapped["Broker | None"] = relationship(back_populates="insurance_lines")
    cmf_links: Mapped[list["InsuranceLineCmfCode"]] = relationship(
        back_populates="insurance_line", cascade="all, delete-orphan"
    )
    placements: Mapped[list["Placement"]] = relationship(back_populates="insurance_line")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InsuranceLine id={self.id} name={self.name!r}>"


class InsuranceLineCmfCode(Base, TimestampMixin):
    """Junction: which official CMF lines an ``insurance_line`` reports under."""

    __tablename__ = "insurance_line_cmf_code"
    __table_args__ = (
        UniqueConstraint(
            "insurance_line_id", "cmf_line_id", name="uq_insurance_line_cmf_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    insurance_line_id: Mapped[int] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cmf_line_id: Mapped[int] = mapped_column(
        ForeignKey("cmf_line.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Composite code as reported, e.g. "4.3" (subdivision + line).
    composite_code: Mapped[str | None] = mapped_column(String(32))

    insurance_line: Mapped["InsuranceLine"] = relationship(back_populates="cmf_links")
    cmf_line: Mapped["CmfLine"] = relationship(back_populates="insurance_line_links")


__all__ = ["CmfLine", "CmfLineKind", "InsuranceLine", "InsuranceLineCmfCode"]
