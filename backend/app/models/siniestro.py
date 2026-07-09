"""siniestro — claim across its lifecycle."""
from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum as SQLEnum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.poliza import Poliza
    from app.models.cliente import Cliente
    from app.models.activo import Activo


class SiniestroEstadoEnum(str, enum.Enum):
    reportado = "reportado"
    en_documentacion = "en_documentacion"
    en_evaluacion = "en_evaluacion"
    pre_liquidado = "pre_liquidado"
    liquidado = "liquidado"
    cerrado = "cerrado"


class Siniestro(Base):
    __tablename__ = "siniestro"

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
    activo_id: Mapped[int | None] = mapped_column(
        ForeignKey("activo.id"), nullable=True, index=True
    )
    fecha_evento: Mapped[date] = mapped_column(Date, nullable=False)
    tipo: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String, nullable=True)
    estado: Mapped[SiniestroEstadoEnum] = mapped_column(
        SQLEnum(SiniestroEstadoEnum, native_enum=False, length=20, validate_strings=True),
        default=SiniestroEstadoEnum.reportado,
        nullable=False,
    )
    monto_estimado: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)
    monto_liquidado: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)
    fecha_liquidacion: Mapped[date | None] = mapped_column(Date, nullable=True)

    poliza: Mapped["Poliza"] = relationship("Poliza")
    cliente: Mapped["Cliente"] = relationship("Cliente")
    activo: Mapped["Activo | None"] = relationship("Activo")
