"""Pydantic v2 schemas for the Renovaciones module (api-contract §Renovaciones)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.renovacion import RenovacionEstadoEnum

DiasColor = Literal["ambar", "gris"]
CoberturaEstado = Literal["infracobertura", "optima", "sobrecobertura"]


# --- Nested reference shapes ----------------------------------------------
class ClienteRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class PolizaRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_poliza: str


class RamoRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class AseguradoraRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class EjecutivoRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


# --- List / item ----------------------------------------------------------
class RenovacionListItem(BaseModel):
    """Row shape for GET /renovaciones (api-contract item shape)."""

    id: int
    codigo: str
    cliente: ClienteRef
    poliza: PolizaRef
    ramo: RamoRef
    aseguradora: AseguradoraRef
    aplica_coaseguro: bool
    prima_defender_uf: float
    comision_pct: float
    fecha_vencimiento: date
    dias_restantes: int
    dias_color: DiasColor
    estado: RenovacionEstadoEnum
    estado_negociacion_texto: str | None = None


# --- Detail sub-shapes (resumen de póliza vigente) ------------------------
class CoaseguroParticipacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    aseguradora: AseguradoraRef
    es_lider: bool
    porcentaje: float


class UbicacionOut(BaseModel):
    nombre: str
    direccion: str | None = None
    suma_asegurada_uf: float
    porcentaje: float


class PlanPago(BaseModel):
    cuotas: int | None = None
    metodo: str | None = None


class PolizaVigenteResumen(BaseModel):
    """Resumen de la póliza vigente ligada a la renovación."""

    id: int
    numero_poliza: str
    ramo: RamoRef
    aseguradora: AseguradoraRef
    tiene_coaseguro: bool
    prima_uf: float
    comision_pct: float
    suma_asegurada_uf: float
    tipo_cobertura: str
    cobertura_pct: float
    cobertura_estado: CoberturaEstado
    vigencia_inicio: date
    vigencia_fin: date
    estado: str
    deducible_texto: str | None = None
    limite_indemnizacion_uf: float | None = None
    plan_pago: PlanPago
    pago_url: str | None = None
    coaseguro_participaciones: list[CoaseguroParticipacionOut] = Field(default_factory=list)
    ubicaciones: list[UbicacionOut] = Field(default_factory=list)
    coberturas: list[str] = Field(default_factory=list)
    exclusiones: list[str] = Field(default_factory=list)


class DocumentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tipo: str
    url: str
    version: int


class ObservacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    texto: str
    autor: str | None = None
    created_at: str | None = None


class RenovacionDetail(RenovacionListItem):
    """GET /renovaciones/{id}: full record + resumen de póliza vigente + colaboración."""

    ejecutivo: EjecutivoRef | None = None
    poliza_vigente: PolizaVigenteResumen | None = None
    # Contract/frontend name for the linked-póliza summary (same payload).
    poliza_resumen: PolizaVigenteResumen | None = None
    documentos: list[DocumentoOut] = Field(default_factory=list)
    observaciones: list[ObservacionOut] = Field(default_factory=list)


# --- Summary / KPIs -------------------------------------------------------
class RenovacionSummary(BaseModel):
    """GET /renovaciones/summary — KPI aggregates."""

    renovaciones_activas: int
    en_negociacion: int
    prima_en_juego_uf: float
    por_vencer_30d: int


# --- Create / update bodies -----------------------------------------------
class RenovacionCreate(BaseModel):
    """POST /renovaciones. corredora_id is injected server-side, never accepted."""

    poliza_id: int
    cliente_id: int
    ramo_id: int
    aseguradora_id: int
    aplica_coaseguro: bool = False
    prima_defender: float = Field(default=0, ge=0)
    comision_pct: float = Field(default=0, ge=0, le=100)
    fecha_vencimiento: date
    ejecutivo_id: int | None = None
    estado: RenovacionEstadoEnum = RenovacionEstadoEnum.por_iniciar
    estado_negociacion_texto: str | None = None


class RenovacionUpdate(BaseModel):
    """PATCH /renovaciones/{id} — partial update."""

    poliza_id: int | None = None
    cliente_id: int | None = None
    ramo_id: int | None = None
    aseguradora_id: int | None = None
    aplica_coaseguro: bool | None = None
    prima_defender: float | None = Field(default=None, ge=0)
    comision_pct: float | None = Field(default=None, ge=0, le=100)
    fecha_vencimiento: date | None = None
    ejecutivo_id: int | None = None
    estado: RenovacionEstadoEnum | None = None
    estado_negociacion_texto: str | None = None
