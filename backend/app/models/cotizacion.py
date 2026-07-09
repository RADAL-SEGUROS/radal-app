"""cotizacion — quote request."""
from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum as SQLEnum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.cliente import Cliente
    from app.models.activo import Activo
    from app.models.ramo import Ramo
    from app.models.oferta import Oferta


class PrioridadEnum(str, enum.Enum):
    baja = "baja"
    media = "media"
    alta = "alta"


class CotizacionEstadoEnum(str, enum.Enum):
    pendiente = "pendiente"
    respondida = "respondida"


class Cotizacion(Base):
    __tablename__ = "cotizacion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id"), nullable=False, index=True
    )
    activo_id: Mapped[int | None] = mapped_column(
        ForeignKey("activo.id"), nullable=True, index=True
    )
    ramo_id: Mapped[int] = mapped_column(ForeignKey("ramo.id"), nullable=False, index=True)
    bien_asegurar: Mapped[str] = mapped_column(String, nullable=False)
    valor_declarado: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    fecha_envio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_vence: Mapped[date | None] = mapped_column(Date, nullable=True)
    prioridad: Mapped[PrioridadEnum] = mapped_column(
        SQLEnum(PrioridadEnum, native_enum=False, length=8, validate_strings=True),
        default=PrioridadEnum.media,
        nullable=False,
    )
    estado: Mapped[CotizacionEstadoEnum] = mapped_column(
        SQLEnum(CotizacionEstadoEnum, native_enum=False, length=16, validate_strings=True),
        default=CotizacionEstadoEnum.pendiente,
        nullable=False,
    )

    cliente: Mapped["Cliente"] = relationship("Cliente")
    activo: Mapped["Activo | None"] = relationship("Activo")
    ramo: Mapped["Ramo"] = relationship("Ramo")
    ofertas: Mapped[list["Oferta"]] = relationship(
        back_populates="cotizacion", cascade="all, delete-orphan"
    )
