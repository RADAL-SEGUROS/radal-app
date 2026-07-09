"""solicitud_inspeccion + inspeccion."""
from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, Enum as SQLEnum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.activo import Activo
    from app.models.usuario import Usuario


class SolicitudInspeccionEstadoEnum(str, enum.Enum):
    solicitada = "solicitada"
    asignada = "asignada"


class InspeccionEstadoEnum(str, enum.Enum):
    solicitada = "solicitada"
    asignada = "asignada"
    en_progreso = "en_progreso"
    enviada = "enviada"
    observada = "observada"
    validada = "validada"
    cerrada = "cerrada"


class SolicitudInspeccion(Base):
    __tablename__ = "solicitud_inspeccion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    activo_id: Mapped[int] = mapped_column(
        ForeignKey("activo.id"), nullable=False, index=True
    )
    proceso_ramo_id: Mapped[int | None] = mapped_column(
        ForeignKey("proceso_ramo.id"), nullable=True, index=True
    )
    motivo: Mapped[str] = mapped_column(String, nullable=False)
    urgencia: Mapped[str] = mapped_column(String, nullable=False)
    fecha_objetivo: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[SolicitudInspeccionEstadoEnum] = mapped_column(
        SQLEnum(
            SolicitudInspeccionEstadoEnum,
            native_enum=False,
            length=16,
            validate_strings=True,
        ),
        default=SolicitudInspeccionEstadoEnum.solicitada,
        nullable=False,
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("usuario.id"), nullable=False, index=True
    )

    activo: Mapped["Activo"] = relationship("Activo")
    creador: Mapped["Usuario"] = relationship("Usuario")
    inspecciones: Mapped[list["Inspeccion"]] = relationship(back_populates="solicitud")


class Inspeccion(Base):
    __tablename__ = "inspeccion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    corredora_id: Mapped[int] = mapped_column(
        ForeignKey("corredora.id"), nullable=False, index=True
    )
    activo_id: Mapped[int] = mapped_column(
        ForeignKey("activo.id"), nullable=False, index=True
    )
    solicitud_id: Mapped[int | None] = mapped_column(
        ForeignKey("solicitud_inspeccion.id"), nullable=True, index=True
    )
    inspector_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    estado: Mapped[InspeccionEstadoEnum] = mapped_column(
        SQLEnum(InspeccionEstadoEnum, native_enum=False, length=16, validate_strings=True),
        default=InspeccionEstadoEnum.solicitada,
        nullable=False,
    )
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)

    activo: Mapped["Activo"] = relationship("Activo")
    solicitud: Mapped["SolicitudInspeccion | None"] = relationship(
        back_populates="inspecciones"
    )
    inspector: Mapped["Usuario"] = relationship("Usuario")
