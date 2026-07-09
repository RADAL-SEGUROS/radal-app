"""Dashboard router — the single no-scroll payload for the corredora colaborador.

Every query is tenant-scoped to the authenticated user's ``corredora_id`` and, where the
contract calls for it, personalised to the logged-in usuario. Money is UF numeric.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.cliente import Cliente, ClienteEstadoEnum
from app.models.colaboracion import Actividad
from app.models.cotizacion import Cotizacion, CotizacionEstadoEnum
from app.models.inspeccion import Inspeccion, InspeccionEstadoEnum
from app.models.poliza import Poliza, PolizaEstadoEnum
from app.models.renovacion import Renovacion, RenovacionEstadoEnum
from app.models.siniestro import Siniestro, SiniestroEstadoEnum
from app.models.usuario import Usuario
from app.schemas.dashboard import (
    ActividadRecienteItem,
    DashboardKpis,
    DashboardResponse,
    PrincipalClienteItem,
    ProximaRenovacionItem,
    RequiereAtencionItem,
    Saludo,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Terminal states that no longer count as "active" work.
_SINIESTRO_TERMINAL = {SiniestroEstadoEnum.liquidado, SiniestroEstadoEnum.cerrado}
_INSPECCION_TERMINAL = {InspeccionEstadoEnum.validada, InspeccionEstadoEnum.cerrada}

_DIAS_ES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


def _renovacion_codigo(ren_id: int) -> str:
    """Stable display code for a renovación (no dedicated column in the model)."""
    return f"REN-{ren_id:03d}"


def _dias_restantes(fecha: date | None, today: date) -> int | None:
    if fecha is None:
        return None
    return (fecha - today).days


def _to_float(value) -> float:
    return float(value) if value is not None else 0.0


def _build_saludo(user: Usuario, now: datetime) -> Saludo:
    hour = now.hour
    if 6 <= hour < 12:
        periodo = "manana"
    elif 12 <= hour < 20:
        periodo = "tarde"
    else:
        periodo = "noche"
    nombre = (user.nombre or "").split(" ")[0] if user.nombre else ""
    return Saludo(
        periodo=periodo,
        dia_semana=_DIAS_ES[now.weekday()],
        nombre=nombre,
    )


def _build_kpis(db: Session, corredora_id: int) -> DashboardKpis:
    polizas_vigentes = db.execute(
        select(func.count(Poliza.id)).where(
            Poliza.corredora_id == corredora_id,
            Poliza.estado == PolizaEstadoEnum.vigente,
        )
    ).scalar_one()

    clientes_activos = db.execute(
        select(func.count(Cliente.id)).where(
            Cliente.corredora_id == corredora_id,
            Cliente.estado == ClienteEstadoEnum.activo,
        )
    ).scalar_one()

    renovaciones_activas = db.execute(
        select(func.count(Renovacion.id)).where(
            Renovacion.corredora_id == corredora_id
        )
    ).scalar_one()

    # Pipeline ponderado: primas at stake in the renewal + quotation pipeline.
    prima_renovaciones = db.execute(
        select(func.coalesce(func.sum(Renovacion.prima_defender), 0)).where(
            Renovacion.corredora_id == corredora_id
        )
    ).scalar_one()

    return DashboardKpis(
        polizas_vigentes=int(polizas_vigentes or 0),
        clientes_activos=int(clientes_activos or 0),
        renovaciones_activas=int(renovaciones_activas or 0),
        pipeline_ponderado_uf=_to_float(prima_renovaciones),
    )


def _build_requiere_atencion(
    db: Session, corredora_id: int, user: Usuario, today: date
) -> list[RequiereAtencionItem]:
    """Top-3 pendientes personalised to the logged-in colaborador.

    Prioritises the user's own renovaciones/cotizaciones by due date, then falls back
    to tenant-wide open siniestros/inspecciones so the panel is never empty.
    """
    candidates: list[tuple[float, RequiereAtencionItem]] = []

    # Renovaciones assigned to the user, soonest expiry first.
    renovaciones = db.execute(
        select(Renovacion)
        .where(
            Renovacion.corredora_id == corredora_id,
            Renovacion.ejecutivo_id == user.id,
        )
        .order_by(Renovacion.fecha_vencimiento.asc())
        .limit(5)
    ).scalars().all()
    for ren in renovaciones:
        dias = _dias_restantes(ren.fecha_vencimiento, today)
        cliente_nombre = ren.cliente.nombre if ren.cliente else "Cliente"
        detalle = (
            f"Vence en {dias} días" if dias is not None and dias >= 0
            else "Vencida" if dias is not None else "Sin fecha"
        )
        candidates.append(
            (
                float(dias) if dias is not None else 9999.0,
                RequiereAtencionItem(
                    tipo="renovacion",
                    id=ren.id,
                    titulo=f"{_renovacion_codigo(ren.id)} {cliente_nombre}",
                    detalle=detalle,
                    url=f"/renovaciones/{ren.id}",
                ),
            )
        )

    # Cotizaciones pendientes for the tenant, soonest deadline first.
    cotizaciones = db.execute(
        select(Cotizacion)
        .where(
            Cotizacion.corredora_id == corredora_id,
            Cotizacion.estado == CotizacionEstadoEnum.pendiente,
        )
        .order_by(Cotizacion.fecha_vence.asc().nulls_last())
        .limit(5)
    ).scalars().all()
    for cot in cotizaciones:
        dias = _dias_restantes(cot.fecha_vence, today)
        cliente_nombre = cot.cliente.nombre if cot.cliente else "Cliente"
        detalle = (
            f"Responde en {dias} días" if dias is not None and dias >= 0
            else "Vencida" if dias is not None else f"Prioridad {cot.prioridad.value}"
        )
        candidates.append(
            (
                float(dias) if dias is not None else 9999.0,
                RequiereAtencionItem(
                    tipo="cotizacion",
                    id=cot.id,
                    titulo=f"Cotización {cliente_nombre}",
                    detalle=detalle,
                    url=f"/cotizaciones/{cot.id}",
                ),
            )
        )

    # Open siniestros (fallback / high urgency).
    siniestros = db.execute(
        select(Siniestro)
        .where(
            Siniestro.corredora_id == corredora_id,
            Siniestro.estado.notin_(_SINIESTRO_TERMINAL),
        )
        .order_by(Siniestro.fecha_evento.desc())
        .limit(3)
    ).scalars().all()
    for sin in siniestros:
        cliente_nombre = sin.cliente.nombre if sin.cliente else "Cliente"
        candidates.append(
            (
                1000.0,
                RequiereAtencionItem(
                    tipo="siniestro",
                    id=sin.id,
                    titulo=f"Siniestro {sin.tipo} — {cliente_nombre}",
                    detalle=f"Estado: {sin.estado.value}",
                    url=f"/siniestros/{sin.id}",
                ),
            )
        )

    # Open inspecciones (fallback).
    inspecciones = db.execute(
        select(Inspeccion)
        .where(
            Inspeccion.corredora_id == corredora_id,
            Inspeccion.estado.notin_(_INSPECCION_TERMINAL),
        )
        .order_by(Inspeccion.id.desc())
        .limit(3)
    ).scalars().all()
    for insp in inspecciones:
        activo_nombre = insp.activo.nombre if insp.activo else "Activo"
        candidates.append(
            (
                2000.0,
                RequiereAtencionItem(
                    tipo="inspeccion",
                    id=insp.id,
                    titulo=f"Inspección {activo_nombre}",
                    detalle=f"Estado: {insp.estado.value}",
                    url=f"/inspecciones/{insp.id}",
                ),
            )
        )

    candidates.sort(key=lambda c: c[0])
    return [item for _, item in candidates[:3]]


def _build_principales_clientes(
    db: Session, corredora_id: int
) -> list[PrincipalClienteItem]:
    """Top clients by total vigente prima."""
    prima_sum = func.coalesce(func.sum(Poliza.prima), 0)
    vigentes_count = func.count(Poliza.id)

    rows = db.execute(
        select(
            Cliente.id,
            Cliente.nombre,
            Cliente.sector,
            Cliente.estado,
            prima_sum.label("prima_total"),
            vigentes_count.label("polizas_vigentes"),
        )
        .join(
            Poliza,
            (Poliza.cliente_id == Cliente.id)
            & (Poliza.estado == PolizaEstadoEnum.vigente),
            isouter=True,
        )
        .where(Cliente.corredora_id == corredora_id)
        .group_by(Cliente.id, Cliente.nombre, Cliente.sector, Cliente.estado)
        .order_by(prima_sum.desc(), Cliente.nombre.asc())
        .limit(5)
    ).all()

    return [
        PrincipalClienteItem(
            id=row.id,
            nombre=row.nombre,
            sector=row.sector,
            prima_total_uf=_to_float(row.prima_total),
            polizas_vigentes=int(row.polizas_vigentes or 0),
            estado=row.estado.value if hasattr(row.estado, "value") else str(row.estado),
        )
        for row in rows
    ]


def _build_actividad_reciente(
    db: Session, corredora_id: int, limit: int
) -> list[ActividadRecienteItem]:
    rows = db.execute(
        select(Actividad)
        .where(Actividad.corredora_id == corredora_id)
        .order_by(Actividad.created_at.desc(), Actividad.id.desc())
        .limit(limit)
    ).scalars().all()

    items: list[ActividadRecienteItem] = []
    for act in rows:
        usuario_nombre = act.usuario.nombre if act.usuario else None
        rol = None
        if act.usuario and act.usuario.rol is not None:
            rol = (
                act.usuario.rol.value
                if hasattr(act.usuario.rol, "value")
                else str(act.usuario.rol)
            )
        items.append(
            ActividadRecienteItem(
                id=act.id,
                accion=act.accion,
                descripcion=act.descripcion,
                usuario=usuario_nombre,
                rol=rol,
                created_at=act.created_at,
            )
        )
    return items


def _build_proximas_renovaciones(
    db: Session, corredora_id: int, today: date
) -> list[ProximaRenovacionItem]:
    rows = db.execute(
        select(Renovacion)
        .where(Renovacion.corredora_id == corredora_id)
        .order_by(Renovacion.fecha_vencimiento.asc())
        .limit(5)
    ).scalars().all()

    items: list[ProximaRenovacionItem] = []
    for ren in rows:
        dias = _dias_restantes(ren.fecha_vencimiento, today)
        items.append(
            ProximaRenovacionItem(
                id=ren.id,
                codigo=_renovacion_codigo(ren.id),
                cliente=ren.cliente.nombre if ren.cliente else "Cliente",
                fecha_vencimiento=ren.fecha_vencimiento,
                dias_restantes=dias if dias is not None else 0,
                prima_defender_uf=_to_float(ren.prima_defender),
                estado=ren.estado.value if hasattr(ren.estado, "value") else str(ren.estado),
            )
        )
    return items


def _build_dashboard(
    db: Session, user: Usuario, actividad_limit: int
) -> DashboardResponse:
    corredora_id = user.corredora_id
    now = datetime.now(timezone.utc)
    today = now.date()

    return DashboardResponse(
        saludo=_build_saludo(user, now),
        kpis=_build_kpis(db, corredora_id),
        requiere_atencion=_build_requiere_atencion(db, corredora_id, user, today),
        principales_clientes=_build_principales_clientes(db, corredora_id),
        actividad_reciente=_build_actividad_reciente(db, corredora_id, actividad_limit),
        proximas_renovaciones=_build_proximas_renovaciones(db, corredora_id, today),
    )


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    limit: int = Query(
        10, ge=1, le=10, description="máximo de items de actividad reciente"
    ),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> DashboardResponse:
    """The single no-scroll dashboard payload for the logged-in colaborador."""
    return _build_dashboard(db, current_user, limit)


@router.get("/summary", response_model=DashboardResponse)
def get_dashboard_summary(
    limit: int = Query(
        10, ge=1, le=10, description="máximo de items de actividad reciente"
    ),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> DashboardResponse:
    """Alias of the dashboard payload under /dashboard/summary."""
    return _build_dashboard(db, current_user, limit)
