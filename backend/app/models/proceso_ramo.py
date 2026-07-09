"""proceso_ramo — operational folder of an activo for one ramo."""
from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum as SQLEnum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.activo import Activo
    from app.models.ramo import Ramo


class ProcesoRamoEstadoEnum(str, enum.Enum):
    creado = "creado"
    en_inspeccion = "en_inspeccion"
    en_presuscripcion = "en_presuscripcion"
    en_cotizacion = "en_cotizacion"
    en_negociacion = "en_negociacion"
    asegurado = "asegurado"
    en_siniestro = "en_siniestro"
    cerrado = "cerrado"


class ProcesoRamo(Base):
    __tablename__ = "proceso_ramo"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    activo_id: Mapped[int] = mapped_column(
        ForeignKey("activo.id"), nullable=False, index=True
    )
    ramo_id: Mapped[int] = mapped_column(ForeignKey("ramo.id"), nullable=False, index=True)
    periodo: Mapped[str | None] = mapped_column(String, nullable=True)
    vigencia_objetivo: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[ProcesoRamoEstadoEnum] = mapped_column(
        SQLEnum(ProcesoRamoEstadoEnum, native_enum=False, length=24, validate_strings=True),
        default=ProcesoRamoEstadoEnum.creado,
        nullable=False,
    )

    activo: Mapped["Activo"] = relationship(back_populates="proceso_ramos")
    ramo: Mapped["Ramo"] = relationship("Ramo")
