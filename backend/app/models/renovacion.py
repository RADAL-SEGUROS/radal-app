"""renovacion — renewal in progress."""
from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Enum as SQLEnum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.poliza import Poliza
    from app.models.cliente import Cliente
    from app.models.ramo import Ramo
    from app.models.aseguradora import Aseguradora
    from app.models.usuario import Usuario


class RenovacionEstadoEnum(str, enum.Enum):
    por_iniciar = "por_iniciar"
    cotizando = "cotizando"
    negociando = "negociando"


class Renovacion(Base):
    __tablename__ = "renovacion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    poliza_id: Mapped[int] = mapped_column(
        ForeignKey("poliza.id"), nullable=False, index=True
    )
    cliente_id: Mapped[int] = mapped_column(
        ForeignKey("cliente.id"), nullable=False, index=True
    )
    ramo_id: Mapped[int] = mapped_column(ForeignKey("ramo.id"), nullable=False, index=True)
    aseguradora_id: Mapped[int] = mapped_column(
        ForeignKey("aseguradora.id"), nullable=False, index=True
    )
    aplica_coaseguro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prima_defender: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    comision_pct: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)
    ejecutivo_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id"), nullable=True, index=True
    )
    estado: Mapped[RenovacionEstadoEnum] = mapped_column(
        SQLEnum(RenovacionEstadoEnum, native_enum=False, length=16, validate_strings=True),
        default=RenovacionEstadoEnum.por_iniciar,
        nullable=False,
    )
    estado_negociacion_texto: Mapped[str | None] = mapped_column(String, nullable=True)

    poliza: Mapped["Poliza"] = relationship("Poliza")
    cliente: Mapped["Cliente"] = relationship("Cliente")
    ramo: Mapped["Ramo"] = relationship("Ramo")
    aseguradora: Mapped["Aseguradora"] = relationship("Aseguradora")
    ejecutivo: Mapped["Usuario | None"] = relationship("Usuario")
