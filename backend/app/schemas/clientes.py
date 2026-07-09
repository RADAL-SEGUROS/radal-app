"""Pydantic v2 schemas for the clientes module (list, detail, CRUD, KPIs).

Money fields are UF numeric; percentages 0-100; dates ISO; timestamps ISO 8601 UTC.
Response models mirror docs/api-contract.md § Clientes.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.cliente import ClienteEstadoEnum


# --- Nested references -----------------------------------------------------
class EjecutivoRef(BaseModel):
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


# --- List item + KPIs ------------------------------------------------------
class ClienteListItem(BaseModel):
    """One row of GET /clientes, with per-row aggregates."""

    id: int
    nombre: str
    rut: str
    sector: str | None = None
    estado: ClienteEstadoEnum
    contacto_principal: str | None = None
    telefono: str | None = None
    email: str | None = None
    ejecutivo: EjecutivoRef | None = None
    polizas_vigentes: int = 0
    prima_total_uf: float = 0.0
    fecha_alta: date | None = None


class ClientesKpis(BaseModel):
    """Header aggregates for the clientes list."""

    total: int = 0
    activos: int = 0
    onboarding: int = 0
    prospectos: int = 0
    prima_total_cartera_uf: float = 0.0


class ClientesListResponse(BaseModel):
    """List envelope + KPI aggregates for the clientes screen."""

    items: list[ClienteListItem]
    total: int
    page: int = 1
    page_size: int = 25
    kpis: ClientesKpis


# --- Detail: cabecera ------------------------------------------------------
class ClienteCabecera(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    rut: str
    sector: str | None = None
    estado: ClienteEstadoEnum
    fecha_alta: date | None = None
    created_at: datetime


# --- Detail: the 5 cuadros -------------------------------------------------
class CuadroContacto(BaseModel):
    contacto_principal: str | None = None
    email: str | None = None


class CuadroTelefono(BaseModel):
    telefono: str | None = None


class CuadroPrima(BaseModel):
    prima_total_uf: float = 0.0
    n_polizas: int = 0


class CuadroAseguradoras(BaseModel):
    aseguradoras_asociadas: list[AseguradoraRef] = Field(default_factory=list)


class CuadroEjecutivo(BaseModel):
    ejecutivo: EjecutivoRef | None = None


class ClienteCuadros(BaseModel):
    contacto: CuadroContacto
    telefono: CuadroTelefono
    prima: CuadroPrima
    aseguradoras: CuadroAseguradoras
    ejecutivo: CuadroEjecutivo


# --- Detail: pólizas vigentes ----------------------------------------------
class PolizaVigenteItem(BaseModel):
    id: int
    numero_poliza: str
    ramo: RamoRef | None = None
    aseguradora: AseguradoraRef | None = None
    tiene_coaseguro: bool = False
    prima_uf: float = 0.0
    suma_asegurada_uf: float = 0.0
    tipo_cobertura: str
    cobertura_pct: float = 0.0
    cobertura_estado: str
    vigencia_inicio: date
    vigencia_fin: date
    estado: str


# --- Detail: asegurados adicionales ----------------------------------------
class AseguradoAdicionalItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    entidad: str
    tipo_seguro: str | None = None
    relacion_bien: str | None = None
    poliza_id: int | None = None


# --- Detail: cotizaciones --------------------------------------------------
class CotizacionItem(BaseModel):
    id: int
    ramo: RamoRef | None = None
    bien_asegurar: str
    valor_declarado_uf: float = 0.0
    fecha_envio: date | None = None
    fecha_vence: date | None = None
    prioridad: str
    estado: str


# --- Detail: documentos ----------------------------------------------------
class DocumentoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tipo: str
    url: str
    version: int
    autor_id: int
    created_at: datetime


# --- Detail: siniestros ----------------------------------------------------
class SiniestroItem(BaseModel):
    id: int
    poliza_id: int
    tipo: str
    fecha_evento: date
    estado: str
    monto_estimado_uf: float | None = None
    monto_liquidado_uf: float | None = None
    fecha_liquidacion: date | None = None


# --- Detail: historial de actividad ----------------------------------------
class ActividadItem(BaseModel):
    id: int
    accion: str
    descripcion: str | None = None
    usuario: str | None = None
    rol: str | None = None
    created_at: datetime


# --- Detail: kpis rollup ---------------------------------------------------
class ClienteDetailKpis(BaseModel):
    """Cartera rollups for the cliente detail header (per docs/api-contract.md)."""

    polizas_vigentes: int = 0
    prima_total_uf: float = 0.0
    suma_asegurada_uf: float = 0.0
    activos_count: int = 0
    siniestros_abiertos: int = 0


# --- Detail bundle ---------------------------------------------------------
class ClienteDetail(BaseModel):
    """GET /clientes/{id}: full NIVEL-1 detail bundle.

    Emits the contract-flat fields the frontend consumes (top-level cliente
    fields + ``kpis`` + ``polizas``/``asegurados_adicionales_items``/``actividad``)
    alongside the original ``cabecera``/``cuadros`` presentation blocks so both
    shapes are served without a breaking change.
    """

    # Flat top-level fields (mirror ClienteListItem; the frontend reads these).
    id: int
    nombre: str
    rut: str
    sector: str | None = None
    estado: ClienteEstadoEnum
    contacto_principal: str | None = None
    telefono: str | None = None
    email: str | None = None
    ejecutivo: EjecutivoRef | None = None
    prima_total_uf: float = 0.0
    fecha_alta: date | None = None
    kpis: ClienteDetailKpis = Field(default_factory=ClienteDetailKpis)

    # Presentation blocks (original shape, retained).
    cabecera: ClienteCabecera
    cuadros: ClienteCuadros

    # Nested bundles. ``polizas``/``asegurados_adicionales_items``/``actividad``
    # are the contract names the frontend reads; the *_vigentes/historial_*
    # aliases are retained for backward compatibility.
    polizas_vigentes: list[PolizaVigenteItem] = Field(default_factory=list)
    polizas: list[PolizaVigenteItem] = Field(default_factory=list)
    asegurados_adicionales: list[AseguradoAdicionalItem] = Field(default_factory=list)
    asegurados_adicionales_items: list[AseguradoAdicionalItem] = Field(
        default_factory=list
    )
    cotizaciones: list[CotizacionItem] = Field(default_factory=list)
    documentos: list[DocumentoItem] = Field(default_factory=list)
    siniestros: list[SiniestroItem] = Field(default_factory=list)
    historial_actividad: list[ActividadItem] = Field(default_factory=list)
    actividad: list[ActividadItem] = Field(default_factory=list)


# --- Write bodies ----------------------------------------------------------
class ClienteCreate(BaseModel):
    nombre: str
    rut: str
    sector: str | None = None
    estado: ClienteEstadoEnum = ClienteEstadoEnum.prospecto
    contacto_principal: str | None = None
    telefono: str | None = None
    email: EmailStr | None = None
    ejecutivo_id: int | None = None
    fecha_alta: date | None = None


class ClienteUpdate(BaseModel):
    """PATCH: all fields optional (partial update)."""

    nombre: str | None = None
    rut: str | None = None
    sector: str | None = None
    estado: ClienteEstadoEnum | None = None
    contacto_principal: str | None = None
    telefono: str | None = None
    email: EmailStr | None = None
    ejecutivo_id: int | None = None
    fecha_alta: date | None = None


__all__ = [
    "EjecutivoRef",
    "RamoRef",
    "AseguradoraRef",
    "ClienteListItem",
    "ClientesKpis",
    "ClientesListResponse",
    "ClienteCabecera",
    "ClienteCuadros",
    "ClienteDetailKpis",
    "ClienteDetail",
    "PolizaVigenteItem",
    "AseguradoAdicionalItem",
    "CotizacionItem",
    "DocumentoItem",
    "SiniestroItem",
    "ActividadItem",
    "ClienteCreate",
    "ClienteUpdate",
]
