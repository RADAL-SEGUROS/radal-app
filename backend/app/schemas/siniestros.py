"""Pydantic v2 schemas for the siniestros module. Matches docs/api-contract.md."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.siniestro import SiniestroEstadoEnum

# Lifecycle order of the siniestro estados, used to build the estados timeline.
SINIESTRO_ESTADOS: tuple[SiniestroEstadoEnum, ...] = (
    SiniestroEstadoEnum.reportado,
    SiniestroEstadoEnum.en_documentacion,
    SiniestroEstadoEnum.en_evaluacion,
    SiniestroEstadoEnum.pre_liquidado,
    SiniestroEstadoEnum.liquidado,
    SiniestroEstadoEnum.cerrado,
)

# Estados that count as an "abierto/activo" claim (not liquidado/cerrado).
SINIESTRO_ABIERTOS: frozenset[SiniestroEstadoEnum] = frozenset(
    {
        SiniestroEstadoEnum.reportado,
        SiniestroEstadoEnum.en_documentacion,
        SiniestroEstadoEnum.en_evaluacion,
        SiniestroEstadoEnum.pre_liquidado,
    }
)


# --- Small embedded refs ----------------------------------------------------
class RefOut(BaseModel):
    """Generic {id, nombre} reference (cliente / activo)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class PolizaRef(BaseModel):
    """{id, numero_poliza} reference to a póliza."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    numero_poliza: str


# --- List item / envelope ---------------------------------------------------
class SiniestroListItem(BaseModel):
    """Row shape for GET /siniestros."""

    id: int
    cliente: RefOut
    poliza: PolizaRef
    tipo: str
    fecha_evento: date
    estado: SiniestroEstadoEnum
    monto_estimado_uf: float | None = None
    monto_liquidado_uf: float | None = None
    fecha_liquidacion: date | None = None


class SiniestroListResponse(BaseModel):
    """Standard list envelope for siniestros."""

    items: list[SiniestroListItem]
    total: int
    page: int = 1
    page_size: int = 25


# --- Summary / KPI ----------------------------------------------------------
class SiniestroSummary(BaseModel):
    """KPI aggregates for the list header (GET /siniestros/summary)."""

    abiertos: int
    en_evaluacion: int
    monto_estimado_total_uf: float
    monto_liquidado_total_uf: float


# --- Detail nested pieces ---------------------------------------------------
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


class ActividadOut(BaseModel):
    id: int
    accion: str
    descripcion: str | None = None
    usuario: str | None = None
    rol: str | None = None
    created_at: datetime


class EstadoTimelineItem(BaseModel):
    """A single step of the siniestro lifecycle timeline."""

    estado: SiniestroEstadoEnum
    alcanzado: bool
    actual: bool


class SiniestroContexto(BaseModel):
    """Póliza + cliente context shown on the siniestro detail header."""

    cliente: RefOut
    poliza: PolizaRef
    ramo: RefOut | None = None
    aseguradora: RefOut | None = None
    prima_uf: float | None = None
    suma_asegurada_uf: float | None = None
    vigencia_inicio: date | None = None
    vigencia_fin: date | None = None
    poliza_estado: str | None = None


class SiniestroDetail(BaseModel):
    """Full siniestro detail for GET /siniestros/{id}."""

    id: int
    tipo: str
    descripcion: str | None = None
    fecha_evento: date
    estado: SiniestroEstadoEnum
    monto_estimado_uf: float | None = None
    monto_liquidado_uf: float | None = None
    fecha_liquidacion: date | None = None

    cliente: RefOut
    poliza: PolizaRef
    activo: RefOut | None = None
    contexto: SiniestroContexto

    estados_timeline: list[EstadoTimelineItem] = Field(default_factory=list)
    documentos: list[DocumentoOut] = Field(default_factory=list)
    observaciones: list[ObservacionOut] = Field(default_factory=list)
    actividad: list[ActividadOut] = Field(default_factory=list)


# --- Create / update payloads ----------------------------------------------
class SiniestroCreate(BaseModel):
    """Reportar siniestro. corredora_id is injected from the JWT, never the body."""

    poliza_id: int
    cliente_id: int
    activo_id: int | None = None
    fecha_evento: date
    tipo: str
    descripcion: str | None = None
    estado: SiniestroEstadoEnum = SiniestroEstadoEnum.reportado
    monto_estimado: float | None = None
    monto_liquidado: float | None = None
    fecha_liquidacion: date | None = None


class SiniestroUpdate(BaseModel):
    """Partial update of a siniestro."""

    poliza_id: int | None = None
    cliente_id: int | None = None
    activo_id: int | None = None
    fecha_evento: date | None = None
    tipo: str | None = None
    descripcion: str | None = None
    estado: SiniestroEstadoEnum | None = None
    monto_estimado: float | None = None
    monto_liquidado: float | None = None
    fecha_liquidacion: date | None = None


__all__ = [
    "SINIESTRO_ESTADOS",
    "SINIESTRO_ABIERTOS",
    "RefOut",
    "PolizaRef",
    "SiniestroListItem",
    "SiniestroListResponse",
    "SiniestroSummary",
    "DocumentoOut",
    "ObservacionOut",
    "ActividadOut",
    "EstadoTimelineItem",
    "SiniestroContexto",
    "SiniestroDetail",
    "SiniestroCreate",
    "SiniestroUpdate",
]
