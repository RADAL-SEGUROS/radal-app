"""Pydantic v2 schemas for the dashboard summary payload.

Mirrors docs/api-contract.md `GET /dashboard`. All money is UF numeric; dates ISO
`YYYY-MM-DD`; timestamps ISO 8601 UTC. Every field is tenant-scoped upstream.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Saludo(BaseModel):
    """Time-of-day greeting for the logged-in colaborador."""

    periodo: Literal["manana", "tarde", "noche"]
    dia_semana: str
    nombre: str


class DashboardKpis(BaseModel):
    polizas_vigentes: int = 0
    clientes_activos: int = 0
    renovaciones_activas: int = 0
    pipeline_ponderado_uf: float = 0.0


class RequiereAtencionItem(BaseModel):
    tipo: Literal["renovacion", "cotizacion", "siniestro", "inspeccion"]
    id: int
    titulo: str
    detalle: str
    url: str


class PrincipalClienteItem(BaseModel):
    id: int
    nombre: str
    sector: str | None = None
    prima_total_uf: float = 0.0
    polizas_vigentes: int = 0
    estado: str


class ActividadRecienteItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    accion: str
    descripcion: str | None = None
    usuario: str | None = None
    rol: str | None = None
    created_at: datetime


class ProximaRenovacionItem(BaseModel):
    id: int
    codigo: str
    cliente: str
    fecha_vencimiento: date
    dias_restantes: int
    prima_defender_uf: float = 0.0
    estado: str


class DashboardResponse(BaseModel):
    """The single no-scroll dashboard payload."""

    saludo: Saludo
    kpis: DashboardKpis
    requiere_atencion: list[RequiereAtencionItem]
    principales_clientes: list[PrincipalClienteItem]
    actividad_reciente: list[ActividadRecienteItem]
    proximas_renovaciones: list[ProximaRenovacionItem]
