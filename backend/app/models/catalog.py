"""``insurance_subtype`` — the closed ramo/subtipo catalogue (v12). PLATFORM table.

The team fixed the catalogue (``docs/v12/catalogo_ramos.csv``: 18 ramos, 53 + 1
subtipos). Ramos are global ``insurance_line`` rows carrying a ``code``;
subtipos hang off them here. Brokers can no longer create ramos — the product
must be able to trace which ramos are processed most, which a free-text,
per-broker list makes impossible.

``cmf_codes`` stays JSON: a short list of CMF generales ramo codes the subtipo
reports under, read whole and never filtered in SQL. The Spanish ``name`` is
display DATA seeded from the CSV, not an identifier.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import PlacementRoute, SuggestedEntry, sql_enum
from app.models.types import JSONType

if TYPE_CHECKING:
    from app.models.insurance_line import InsuranceLine


class InsuranceSubtype(Base, TimestampMixin):
    """One subtipo of a global ramo, e.g. ``PRO-02`` Todo Riesgo. No broker_id."""

    __tablename__ = "insurance_subtype"
    __table_args__ = (
        Index("ix_insurance_subtype_line_order", "insurance_line_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    insurance_line_id: Mapped[int] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Team code, e.g. "PRO-02". Globally unique.
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # CSV ``entrada_sugerida``: mercado -> market, portal -> portal.
    suggested_entry: Mapped[SuggestedEntry] = mapped_column(
        sql_enum(SuggestedEntry), nullable=False
    )
    # market -> traditional, portal -> digital.
    suggested_route: Mapped[PlacementRoute] = mapped_column(
        sql_enum(PlacementRoute), nullable=False
    )
    # CMF generales ramo codes (strings), e.g. ["1", "3"]. [] for life (not seeded).
    cmf_codes: Mapped[list[Any] | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    insurance_line: Mapped["InsuranceLine"] = relationship(back_populates="subtypes")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InsuranceSubtype code={self.code!r} name={self.name!r}>"


__all__ = ["InsuranceSubtype"]
