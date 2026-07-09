"""oferta — an insurer's offer against a cotizacion / proceso_ramo."""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum as SQLEnum, ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.cotizacion import Cotizacion
    from app.models.aseguradora import Aseguradora


class OfertaEstadoEnum(str, enum.Enum):
    borrador = "borrador"
    enviada = "enviada"
    ajustada = "ajustada"
    aceptada = "aceptada"
    rechazada = "rechazada"


class Oferta(Base):
    __tablename__ = "oferta"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    cotizacion_id: Mapped[int | None] = mapped_column(
        ForeignKey("cotizacion.id"), nullable=True, index=True
    )
    proceso_ramo_id: Mapped[int | None] = mapped_column(
        ForeignKey("proceso_ramo.id"), nullable=True, index=True
    )
    aseguradora_id: Mapped[int] = mapped_column(
        ForeignKey("aseguradora.id"), nullable=False, index=True
    )
    prima: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    deducible: Mapped[str | None] = mapped_column(String, nullable=True)
    coberturas: Mapped[list[Any] | None] = mapped_column(JSON, default=list, nullable=True)
    exclusiones: Mapped[list[Any] | None] = mapped_column(JSON, default=list, nullable=True)
    vigencia: Mapped[str | None] = mapped_column(String, nullable=True)
    estado: Mapped[OfertaEstadoEnum] = mapped_column(
        SQLEnum(OfertaEstadoEnum, native_enum=False, length=16, validate_strings=True),
        default=OfertaEstadoEnum.borrador,
        nullable=False,
    )

    cotizacion: Mapped["Cotizacion | None"] = relationship(back_populates="ofertas")
    aseguradora: Mapped["Aseguradora"] = relationship("Aseguradora")
