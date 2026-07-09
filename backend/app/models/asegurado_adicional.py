"""asegurado_adicional — third party with insurable interest."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.cliente import Cliente


class AseguradoAdicional(Base):
    __tablename__ = "asegurado_adicional"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id"), nullable=False, index=True
    )
    poliza_id: Mapped[int | None] = mapped_column(
        ForeignKey("poliza.id"), nullable=True, index=True
    )
    rut: Mapped[str] = mapped_column(String, nullable=False)
    entidad: Mapped[str] = mapped_column(String, nullable=False)
    tipo_seguro: Mapped[str | None] = mapped_column(String, nullable=True)
    relacion_bien: Mapped[str | None] = mapped_column(String, nullable=True)

    cliente: Mapped["Cliente"] = relationship(back_populates="asegurados_adicionales")
