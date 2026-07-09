"""Pydantic v2 schemas for the Cotizaciones module (api-contract §Cotizaciones)."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.cotizacion import CotizacionEstadoEnum, PrioridadEnum
from app.models.oferta import OfertaEstadoEnum

DiasColor = Literal["rojo", "ambar"]


# --- Nested reference shapes ----------------------------------------------
class ClienteRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class RamoRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class AseguradoraRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


class ActivoRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str


# --- List / item ----------------------------------------------------------
class CotizacionListItem(BaseModel):
    """Row shape for GET /cotizaciones (api-contract item shape)."""

    id: int
    cliente: ClienteRef
    ramo: RamoRef
    activo: ActivoRef | None = None
    bien_asegurar: str
    valor_declarado_uf: float
    fecha_envio: date | None = None
    fecha_vence: date | None = None
    dias_restantes: int | None = None
    dias_color: DiasColor | None = None
    prioridad: PrioridadEnum
    estado: CotizacionEstadoEnum


# --- Detail sub-shapes ----------------------------------------------------
class OfertaOut(BaseModel):
    """An insurer offer for the comparator (GET /cotizaciones/{id})."""

    id: int
    aseguradora: AseguradoraRef
    prima_uf: float
    deducible: str | None = None
    coberturas: list[Any] = Field(default_factory=list)
    exclusiones: list[Any] = Field(default_factory=list)
    vigencia: str | None = None
    estado: OfertaEstadoEnum


class CotizacionDetail(CotizacionListItem):
    """GET /cotizaciones/{id}: full record + ofertas[] for the comparator."""

    ofertas: list[OfertaOut] = Field(default_factory=list)


# --- Summary / KPIs -------------------------------------------------------
class CotizacionSummary(BaseModel):
    """GET /cotizaciones/summary — KPI aggregates."""

    en_curso: int
    valor_declarado_total_uf: float
    alta_prioridad: int
    por_vencer_l7d: int


# --- Create / update bodies -----------------------------------------------
class CotizacionCreate(BaseModel):
    """POST /cotizaciones. corredora_id is injected server-side, never accepted."""

    cliente_id: int
    ramo_id: int
    activo_id: int | None = None
    bien_asegurar: str
    valor_declarado: float = Field(default=0, ge=0)
    fecha_envio: date | None = None
    fecha_vence: date | None = None
    prioridad: PrioridadEnum = PrioridadEnum.media
    estado: CotizacionEstadoEnum = CotizacionEstadoEnum.pendiente


class CotizacionUpdate(BaseModel):
    """PATCH /cotizaciones/{id} — partial update."""

    cliente_id: int | None = None
    ramo_id: int | None = None
    activo_id: int | None = None
    bien_asegurar: str | None = None
    valor_declarado: float | None = Field(default=None, ge=0)
    fecha_envio: date | None = None
    fecha_vence: date | None = None
    prioridad: PrioridadEnum | None = None
    estado: CotizacionEstadoEnum | None = None


# --- Oferta bodies --------------------------------------------------------
class OfertaCreate(BaseModel):
    """POST /cotizaciones/{id}/ofertas. subir=borrador; enviar sets estado=enviada."""

    aseguradora_id: int
    prima: float = Field(default=0, ge=0)
    deducible: str | None = None
    coberturas: list[Any] = Field(default_factory=list)
    exclusiones: list[Any] = Field(default_factory=list)
    vigencia: str | None = None
    estado: OfertaEstadoEnum = OfertaEstadoEnum.borrador


class OfertaUpdate(BaseModel):
    """PATCH /ofertas/{oferta_id} — update offer estado / fields."""

    aseguradora_id: int | None = None
    prima: float | None = Field(default=None, ge=0)
    deducible: str | None = None
    coberturas: list[Any] | None = None
    exclusiones: list[Any] | None = None
    vigencia: str | None = None
    estado: OfertaEstadoEnum | None = None
