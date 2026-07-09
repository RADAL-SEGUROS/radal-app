"""ramo — configurable insurance line per corredora."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.corredora import Corredora


class Ramo(Base):
    __tablename__ = "ramo"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    requiere_inspeccion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    campos_min: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    reglas_presuscripcion: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=dict, nullable=True
    )
    campos_comparador: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, default=dict, nullable=True
    )

    corredora: Mapped["Corredora"] = relationship(back_populates="ramos")
