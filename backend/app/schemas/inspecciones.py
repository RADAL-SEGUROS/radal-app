"""Pydantic v2 schemas for the inspecciones module. Matches docs/api-contract.md."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.inspeccion import (
    InspeccionEstadoEnum,
    SolicitudInspeccionEstadoEnum,
)

# Lifecycle order of the inspección estados, used to build the estados timeline.
INSPECCION_ESTADOS: tuple[InspeccionEstadoEnum, ...] = (
    InspeccionEstadoEnum.solicitada,
    InspeccionEstadoEnum.asignada,
    InspeccionEstadoEnum.en_progreso,
    InspeccionEstadoEnum.enviada,
    InspeccionEstadoEnum.observada,
    InspeccionEstadoEnum.validada,
    InspeccionEstadoEnum.cerrada,
)

# Estados that count as an "abierta/activa" inspección (not validada/cerrada).
INSPECCION_ABIERTAS: frozenset[InspeccionEstadoEnum] = frozenset(
    {
        InspeccionEstadoEnum.solicitada,
        InspeccionEstadoEnum.asignada,
        InspeccionEstadoEnum.en_progreso,
        InspeccionEstadoEnum.enviada,
        InspeccionEstadoEnum.observada,
    }
)


# --- Small embedded refs ----------------------------------------------------
class RefOut(BaseModel):
    """Generic {id, nombre} reference (activo / cliente / inspector / ramo)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


# --- List item / envelope ---------------------------------------------------
class InspeccionListItem(BaseModel):
    """Row shape for GET /inspecciones."""

    id: int
    activo: RefOut
    cliente: RefOut
    inspector: RefOut
    version: int
    estado: InspeccionEstadoEnum
    solicitud_id: int | None = None


class InspeccionListResponse(BaseModel):
    """Standard list envelope for inspecciones."""

    items: list[InspeccionListItem]
    total: int
    page: int = 1
    page_size: int = 25


# --- Summary / KPI ----------------------------------------------------------
class InspeccionSummary(BaseModel):
    """KPI aggregates for the list header (GET /inspecciones/summary).

    solicitadas = solicitud_inspeccion pending (estado=solicitada) + inspecciones
    still in estado=solicitada. en_progreso counts en_progreso inspecciones.
    validadas counts validadas inspecciones. abiertas_total = inspecciones not in
    a terminal estado (validada/cerrada).
    """

    solicitadas: int
    en_progreso: int
    validadas: int
    abiertas_total: int
    # Extra breakdown the UI KPI cards use (solicitadas/pendientes, enviadas/observadas).
    pendientes: int = 0
    enviadas: int = 0
    observadas: int = 0


# --- Detail nested pieces ---------------------------------------------------
class SolicitudOut(BaseModel):
    """Solicitud de inspección context embedded in the inspección detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    motivo: str
    urgencia: str
    fecha_objetivo: date | None = None
    estado: SolicitudInspeccionEstadoEnum
    proceso_ramo_id: int | None = None
    created_by: int


class SolicitudListItem(BaseModel):
    """Row shape for GET /inspecciones/solicitudes."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    activo: RefOut
    cliente: RefOut
    motivo: str
    urgencia: str
    fecha_objetivo: date | None = None
    estado: SolicitudInspeccionEstadoEnum
    proceso_ramo_id: int | None = None
    created_by: int


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


class EstadoTimelineItem(BaseModel):
    """A single step of the inspección lifecycle timeline."""

    estado: InspeccionEstadoEnum
    alcanzado: bool
    actual: bool


class ActivoContexto(BaseModel):
    """Activo + cliente context shown on the inspección detail header."""

    activo: RefOut
    cliente: RefOut
    tipo_activo: str | None = None
    direccion: str | None = None
    atributos: dict[str, Any] | None = None
    estado: str | None = None


class ActivoResumenOut(BaseModel):
    """Flat activo summary the frontend detail reads as ``activo_resumen``."""

    id: int
    nombre: str
    tipo_activo: str | None = None
    direccion: str | None = None
    estado: str | None = None
    cliente: RefOut | None = None


class InspeccionDetail(BaseModel):
    """Full inspección detail for GET /inspecciones/{id}."""

    id: int
    activo: RefOut
    cliente: RefOut
    inspector: RefOut
    version: int
    estado: InspeccionEstadoEnum
    solicitud_id: int | None = None

    contexto: ActivoContexto
    # Contract/frontend name for the activo summary card (same data as contexto).
    activo_resumen: ActivoResumenOut | None = None
    checklist: dict[str, Any] | None = None
    solicitud: SolicitudOut | None = None

    estados_timeline: list[EstadoTimelineItem] = Field(default_factory=list)
    documentos: list[DocumentoOut] = Field(default_factory=list)
    observaciones: list[ObservacionOut] = Field(default_factory=list)


# --- Create / update payloads ----------------------------------------------
class SolicitudCreate(BaseModel):
    """Solicitar inspección. corredora_id + created_by are injected server-side."""

    activo_id: int
    proceso_ramo_id: int | None = None
    motivo: str
    urgencia: str
    fecha_objetivo: date | None = None
    estado: SolicitudInspeccionEstadoEnum = SolicitudInspeccionEstadoEnum.solicitada


class InspeccionCreate(BaseModel):
    """Create/assign an inspección. corredora_id is injected from the JWT."""

    activo_id: int
    solicitud_id: int | None = None
    inspector_id: int
    version: int = 1
    estado: InspeccionEstadoEnum = InspeccionEstadoEnum.asignada
    checklist: dict[str, Any] | None = None


class InspeccionUpdate(BaseModel):
    """Partial update: advance estado / update checklist / reassign inspector."""

    inspector_id: int | None = None
    solicitud_id: int | None = None
    version: int | None = None
    estado: InspeccionEstadoEnum | None = None
    checklist: dict[str, Any] | None = None


__all__ = [
    "INSPECCION_ESTADOS",
    "INSPECCION_ABIERTAS",
    "RefOut",
    "InspeccionListItem",
    "InspeccionListResponse",
    "InspeccionSummary",
    "SolicitudOut",
    "SolicitudListItem",
    "DocumentoOut",
    "ObservacionOut",
    "EstadoTimelineItem",
    "ActivoContexto",
    "ActivoResumenOut",
    "InspeccionDetail",
    "SolicitudCreate",
    "InspeccionCreate",
    "InspeccionUpdate",
]
