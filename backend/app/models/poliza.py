"""poliza aggregate — poliza, coaseguro_participacion, ubicacion_poliza, cobertura_item."""
from __future__ import annotations

import enum
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base

if TYPE_CHECKING:
    from app.models.cliente import Cliente
    from app.models.activo import Activo
    from app.models.ramo import Ramo
    from app.models.aseguradora import Aseguradora
    from app.models.proceso_ramo import ProcesoRamo


class TipoCoberturaEnum(str, enum.Enum):
    infravalorada = "infravalorada"
    optima = "optima"
    sobrevalorada = "sobrevalorada"


class PolizaEstadoEnum(str, enum.Enum):
    vigente = "vigente"
    no_vigente = "no_vigente"


class CoberturaItemTipoEnum(str, enum.Enum):
    cobertura = "cobertura"
    exclusion = "exclusion"


class Poliza(Base):
    __tablename__ = "poliza"

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
    proceso_ramo_id: Mapped[int | None] = mapped_column(
        ForeignKey("proceso_ramo.id"), nullable=True, index=True
    )
    numero_poliza: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ramo_id: Mapped[int] = mapped_column(ForeignKey("ramo.id"), nullable=False, index=True)
    aseguradora_id: Mapped[int] = mapped_column(
        ForeignKey("aseguradora.id"), nullable=False, index=True
    )
    tiene_coaseguro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prima: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    comision_pct: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    suma_asegurada: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    tipo_cobertura: Mapped[TipoCoberturaEnum] = mapped_column(
        SQLEnum(TipoCoberturaEnum, native_enum=False, length=16, validate_strings=True),
        default=TipoCoberturaEnum.optima,
        nullable=False,
    )
    cobertura_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=100)
    vigencia_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    vigencia_fin: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[PolizaEstadoEnum] = mapped_column(
        SQLEnum(PolizaEstadoEnum, native_enum=False, length=16, validate_strings=True),
        default=PolizaEstadoEnum.vigente,
        nullable=False,
    )
    deducible_texto: Mapped[str | None] = mapped_column(String, nullable=True)
    limite_indemnizacion: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)
    plan_pago_cuotas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_pago_metodo: Mapped[str | None] = mapped_column(String, nullable=True)
    pago_url: Mapped[str | None] = mapped_column(String, nullable=True)

    cliente: Mapped["Cliente"] = relationship(back_populates="polizas")
    activo: Mapped["Activo | None"] = relationship("Activo")
    ramo: Mapped["Ramo"] = relationship("Ramo")
    aseguradora: Mapped["Aseguradora"] = relationship("Aseguradora")
    proceso_ramo: Mapped["ProcesoRamo | None"] = relationship("ProcesoRamo")

    coaseguro_participaciones: Mapped[list["CoaseguroParticipacion"]] = relationship(
        back_populates="poliza", cascade="all, delete-orphan"
    )
    ubicaciones: Mapped[list["UbicacionPoliza"]] = relationship(
        back_populates="poliza", cascade="all, delete-orphan"
    )
    cobertura_items: Mapped[list["CoberturaItem"]] = relationship(
        back_populates="poliza", cascade="all, delete-orphan"
    )


class CoaseguroParticipacion(Base):
    __tablename__ = "coaseguro_participacion"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    poliza_id: Mapped[int] = mapped_column(
        ForeignKey("poliza.id"), nullable=False, index=True
    )
    aseguradora_id: Mapped[int] = mapped_column(
        ForeignKey("aseguradora.id"), nullable=False, index=True
    )
    es_lider: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    porcentaje: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)

    poliza: Mapped["Poliza"] = relationship(back_populates="coaseguro_participaciones")
    aseguradora: Mapped["Aseguradora"] = relationship("Aseguradora")


class UbicacionPoliza(Base):
    __tablename__ = "ubicacion_poliza"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    poliza_id: Mapped[int] = mapped_column(
        ForeignKey("poliza.id"), nullable=False, index=True
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    direccion: Mapped[str | None] = mapped_column(String, nullable=True)
    suma_asegurada: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False, default=0)
    porcentaje: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)

    poliza: Mapped["Poliza"] = relationship(back_populates="ubicaciones")


class CoberturaItem(Base):
    __tablename__ = "cobertura_item"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    poliza_id: Mapped[int] = mapped_column(
        ForeignKey("poliza.id"), nullable=False, index=True
    )
    tipo: Mapped[CoberturaItemTipoEnum] = mapped_column(
        SQLEnum(CoberturaItemTipoEnum, native_enum=False, length=16, validate_strings=True),
        nullable=False,
    )
    descripcion: Mapped[str] = mapped_column(String, nullable=False)

    poliza: Mapped["Poliza"] = relationship(back_populates="cobertura_items")
