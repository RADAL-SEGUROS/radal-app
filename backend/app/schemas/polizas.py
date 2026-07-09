"""Pydantic v2 schemas for the pólizas module. Matches docs/api-contract.md."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.poliza import (
    CoberturaItemTipoEnum,
    PolizaEstadoEnum,
    TipoCoberturaEnum,
)


# --- Small embedded refs ----------------------------------------------------
class RefOut(BaseModel):
    """Generic {id, nombre} reference used for cliente/ramo/aseguradora."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


# --- Coverage estado helper -------------------------------------------------
def cobertura_estado_for(cobertura_pct: float | None) -> str:
    """Derive coverage analyzer estado from cobertura_pct.

    <95 -> infracobertura, 95–105 -> optima, >105 -> sobrecobertura.
    """
    pct = float(cobertura_pct or 0)
    if pct < 95:
        return "infracobertura"
    if pct <= 105:
        return "optima"
    return "sobrecobertura"


# --- List item --------------------------------------------------------------
class PolizaListItem(BaseModel):
    """Row shape for GET /polizas."""

    id: int
    numero_poliza: str
    cliente: RefOut
    ramo: RefOut
    aseguradora: RefOut
    tiene_coaseguro: bool
    prima_uf: float
    comision_pct: float
    suma_asegurada_uf: float
    tipo_cobertura: TipoCoberturaEnum
    cobertura_pct: float
    cobertura_estado: str
    vigencia_inicio: date
    vigencia_fin: date
    estado: PolizaEstadoEnum


class PolizaListResponse(BaseModel):
    """Standard list envelope for pólizas."""

    items: list[PolizaListItem]
    total: int
    page: int = 1
    page_size: int = 25


# --- Summary / KPI ----------------------------------------------------------
class PolizaSummary(BaseModel):
    """KPIs for the list header (GET /polizas/summary)."""

    polizas_vigentes: int
    prima_total_uf: float
    suma_asegurada_total_uf: float
    con_coaseguro: int


# --- Nested detail pieces ---------------------------------------------------
class CoaseguroParticipacionOut(BaseModel):
    id: int
    aseguradora: RefOut
    es_lider: bool
    porcentaje: float


class UbicacionOut(BaseModel):
    id: int
    nombre: str
    direccion: str | None = None
    suma_asegurada_uf: float
    porcentaje: float


class PlanPago(BaseModel):
    cuotas: int | None = None
    metodo: str | None = None


class DocumentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tipo: str
    url: str
    version: int
    autor_id: int
    compartido_con: list[Any] | None = None
    created_at: datetime


class ObservacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    autor_id: int
    texto: str
    created_at: datetime


class PolizaDetail(BaseModel):
    """Full póliza detail for GET /polizas/{id}."""

    id: int
    numero_poliza: str
    cliente: RefOut
    ramo: RefOut
    aseguradora: RefOut
    tiene_coaseguro: bool
    prima_uf: float
    comision_pct: float
    suma_asegurada_uf: float
    tipo_cobertura: TipoCoberturaEnum
    cobertura_pct: float
    cobertura_estado: str
    vigencia_inicio: date
    vigencia_fin: date
    estado: PolizaEstadoEnum
    activo_id: int | None = None
    proceso_ramo_id: int | None = None

    coaseguro_participaciones: list[CoaseguroParticipacionOut] = Field(default_factory=list)
    ubicaciones: list[UbicacionOut] = Field(default_factory=list)
    coberturas: list[str] = Field(default_factory=list)
    exclusiones: list[str] = Field(default_factory=list)
    deducible_texto: str | None = None
    limite_indemnizacion_uf: float | None = None
    plan_pago: PlanPago
    pago_url: str | None = None
    documentos: list[DocumentoOut] = Field(default_factory=list)
    observaciones: list[ObservacionOut] = Field(default_factory=list)


# --- Create / update payloads ----------------------------------------------
class CoaseguroParticipacionIn(BaseModel):
    aseguradora_id: int
    es_lider: bool = False
    porcentaje: float = 0


class UbicacionIn(BaseModel):
    nombre: str
    direccion: str | None = None
    suma_asegurada: float = 0
    porcentaje: float = 0


class PolizaCreate(BaseModel):
    cliente_id: int
    numero_poliza: str
    ramo_id: int
    aseguradora_id: int
    activo_id: int | None = None
    proceso_ramo_id: int | None = None
    tiene_coaseguro: bool = False
    prima: float = 0
    comision_pct: float = 0
    suma_asegurada: float = 0
    tipo_cobertura: TipoCoberturaEnum = TipoCoberturaEnum.optima
    cobertura_pct: float = 100
    vigencia_inicio: date
    vigencia_fin: date
    estado: PolizaEstadoEnum = PolizaEstadoEnum.vigente
    deducible_texto: str | None = None
    limite_indemnizacion: float | None = None
    plan_pago_cuotas: int | None = None
    plan_pago_metodo: str | None = None
    pago_url: str | None = None

    coaseguro_participaciones: list[CoaseguroParticipacionIn] = Field(default_factory=list)
    ubicaciones: list[UbicacionIn] = Field(default_factory=list)
    coberturas: list[str] = Field(default_factory=list)
    exclusiones: list[str] = Field(default_factory=list)


class PolizaUpdate(BaseModel):
    """Partial update. Nested collections replace the existing set when provided."""

    cliente_id: int | None = None
    numero_poliza: str | None = None
    ramo_id: int | None = None
    aseguradora_id: int | None = None
    activo_id: int | None = None
    proceso_ramo_id: int | None = None
    tiene_coaseguro: bool | None = None
    prima: float | None = None
    comision_pct: float | None = None
    suma_asegurada: float | None = None
    tipo_cobertura: TipoCoberturaEnum | None = None
    cobertura_pct: float | None = None
    vigencia_inicio: date | None = None
    vigencia_fin: date | None = None
    estado: PolizaEstadoEnum | None = None
    deducible_texto: str | None = None
    limite_indemnizacion: float | None = None
    plan_pago_cuotas: int | None = None
    plan_pago_metodo: str | None = None
    pago_url: str | None = None

    coaseguro_participaciones: list[CoaseguroParticipacionIn] | None = None
    ubicaciones: list[UbicacionIn] | None = None
    coberturas: list[str] | None = None
    exclusiones: list[str] | None = None


__all__ = [
    "RefOut",
    "cobertura_estado_for",
    "PolizaListItem",
    "PolizaListResponse",
    "PolizaSummary",
    "CoaseguroParticipacionOut",
    "UbicacionOut",
    "PlanPago",
    "DocumentoOut",
    "ObservacionOut",
    "PolizaDetail",
    "CoaseguroParticipacionIn",
    "UbicacionIn",
    "PolizaCreate",
    "PolizaUpdate",
]
