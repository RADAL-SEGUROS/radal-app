"""Cotizaciones router — CRUD + KPI summary + detail with comparador de ofertas.

Every query is tenant-scoped to the authenticated user's corredora_id (multi-tenant).
Mounted under /api/v1/cotizaciones in app.main (do not touch main.py).
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_corredora_staff
from app.models.activo import Activo
from app.models.aseguradora import Aseguradora
from app.models.cliente import Cliente
from app.models.cotizacion import (
    Cotizacion,
    CotizacionEstadoEnum,
    PrioridadEnum,
)
from app.models.oferta import Oferta
from app.models.ramo import Ramo
from app.models.usuario import Usuario
from app.schemas.common import Page
from app.schemas.cotizaciones import (
    ActivoRef,
    AseguradoraRef,
    ClienteRef,
    CotizacionCreate,
    CotizacionDetail,
    CotizacionListItem,
    CotizacionSummary,
    CotizacionUpdate,
    OfertaCreate,
    OfertaOut,
    RamoRef,
)

router = APIRouter(prefix="/cotizaciones", tags=["cotizaciones"])

# Cotizaciones "en curso" = still awaiting an insurer response.
_EN_CURSO_ESTADO = CotizacionEstadoEnum.pendiente


def _dias_restantes(fecha_vence: date | None) -> int | None:
    if fecha_vence is None:
        return None
    return (fecha_vence - date.today()).days


def _dias_color(dias_restantes: int | None) -> str | None:
    """rojo si <= 4 días else ámbar (días-restantes rule for cotizaciones)."""
    if dias_restantes is None:
        return None
    return "rojo" if dias_restantes <= 4 else "ambar"


def _to_list_item(cot: Cotizacion) -> CotizacionListItem:
    dias = _dias_restantes(cot.fecha_vence)
    return CotizacionListItem(
        id=cot.id,
        cliente=ClienteRef.model_validate(cot.cliente),
        ramo=RamoRef.model_validate(cot.ramo),
        activo=ActivoRef.model_validate(cot.activo) if cot.activo is not None else None,
        bien_asegurar=cot.bien_asegurar,
        valor_declarado_uf=float(cot.valor_declarado or 0),
        fecha_envio=cot.fecha_envio,
        fecha_vence=cot.fecha_vence,
        dias_restantes=dias,
        dias_color=_dias_color(dias),
        prioridad=cot.prioridad,
        estado=cot.estado,
    )


def _to_oferta_out(oferta: Oferta) -> OfertaOut:
    return OfertaOut(
        id=oferta.id,
        aseguradora=AseguradoraRef.model_validate(oferta.aseguradora),
        prima_uf=float(oferta.prima or 0),
        deducible=oferta.deducible,
        coberturas=list(oferta.coberturas or []),
        exclusiones=list(oferta.exclusiones or []),
        vigencia=oferta.vigencia,
        estado=oferta.estado,
    )


def _get_owned(db: Session, corredora_id: int, cotizacion_id: int) -> Cotizacion:
    """Fetch a cotización scoped to the tenant or raise 404."""
    cot = db.scalar(
        select(Cotizacion).where(
            Cotizacion.id == cotizacion_id,
            Cotizacion.corredora_id == corredora_id,
        )
    )
    if cot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cotización no encontrada")
    return cot


# --- List ------------------------------------------------------------------
@router.get("", response_model=Page[CotizacionListItem])
def list_cotizaciones(
    q: str | None = Query(default=None),
    estado: CotizacionEstadoEnum | None = Query(default=None),
    prioridad: PrioridadEnum | None = Query(default=None),
    ramo_id: int | None = Query(default=None),
    cliente_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Page[CotizacionListItem]:
    """List cotizaciones for the tenant with filters + pagination."""
    corredora_id = current_user.corredora_id

    stmt = select(Cotizacion).where(Cotizacion.corredora_id == corredora_id)

    if estado is not None:
        stmt = stmt.where(Cotizacion.estado == estado)
    if prioridad is not None:
        stmt = stmt.where(Cotizacion.prioridad == prioridad)
    if ramo_id is not None:
        stmt = stmt.where(Cotizacion.ramo_id == ramo_id)
    if cliente_id is not None:
        stmt = stmt.where(Cotizacion.cliente_id == cliente_id)
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.join(Cliente, Cotizacion.cliente_id == Cliente.id).where(
            or_(
                Cliente.nombre.ilike(term),
                Cotizacion.bien_asegurar.ilike(term),
            )
        )

    total = db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0

    stmt = (
        stmt.order_by(Cotizacion.fecha_vence.asc().nulls_last(), Cotizacion.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    cotizaciones = db.scalars(stmt).unique().all()

    items = [_to_list_item(c) for c in cotizaciones]
    return Page[CotizacionListItem](
        items=items, total=total, page=page, page_size=page_size
    )


# --- Summary / KPIs --------------------------------------------------------
@router.get("/summary", response_model=CotizacionSummary)
def cotizaciones_summary(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> CotizacionSummary:
    """KPI aggregates: en_curso, valor_declarado_total, alta_prioridad, por_vencer_l7d."""
    corredora_id = current_user.corredora_id
    base = select(Cotizacion).where(Cotizacion.corredora_id == corredora_id)

    en_curso = db.scalar(
        select(func.count()).select_from(
            base.where(Cotizacion.estado == _EN_CURSO_ESTADO).subquery()
        )
    ) or 0

    valor_declarado_total = db.scalar(
        select(func.coalesce(func.sum(Cotizacion.valor_declarado), 0)).where(
            Cotizacion.corredora_id == corredora_id,
            Cotizacion.estado == _EN_CURSO_ESTADO,
        )
    ) or 0

    alta_prioridad = db.scalar(
        select(func.count()).select_from(
            base.where(
                Cotizacion.estado == _EN_CURSO_ESTADO,
                Cotizacion.prioridad == PrioridadEnum.alta,
            ).subquery()
        )
    ) or 0

    # "por_vencer_l7d" = dias_restantes < 7 (fecha_vence within the next 7 days).
    hoy = date.today()
    limite_l7d = date.fromordinal(hoy.toordinal() + 7)
    por_vencer_l7d = db.scalar(
        select(func.count()).select_from(
            base.where(
                Cotizacion.estado == _EN_CURSO_ESTADO,
                Cotizacion.fecha_vence.is_not(None),
                Cotizacion.fecha_vence < limite_l7d,
            ).subquery()
        )
    ) or 0

    return CotizacionSummary(
        en_curso=int(en_curso),
        valor_declarado_total_uf=float(valor_declarado_total),
        alta_prioridad=int(alta_prioridad),
        por_vencer_l7d=int(por_vencer_l7d),
    )


# --- Detail ----------------------------------------------------------------
@router.get("/{cotizacion_id}", response_model=CotizacionDetail)
def get_cotizacion(
    cotizacion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> CotizacionDetail:
    """Full cotización + ofertas[] for the comparador (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    cot = _get_owned(db, corredora_id, cotizacion_id)

    base = _to_list_item(cot).model_dump()

    ofertas_rows = db.scalars(
        select(Oferta)
        .where(
            Oferta.corredora_id == corredora_id,
            Oferta.cotizacion_id == cot.id,
        )
        .order_by(Oferta.prima.asc(), Oferta.id.asc())
    ).all()

    return CotizacionDetail(
        **base,
        ofertas=[_to_oferta_out(o) for o in ofertas_rows],
    )


# --- Create ----------------------------------------------------------------
@router.post("", response_model=CotizacionDetail, status_code=status.HTTP_201_CREATED)
def create_cotizacion(
    body: CotizacionCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> CotizacionDetail:
    """Create a cotización. corredora_id is injected from the JWT (never from the body)."""
    corredora_id = current_user.corredora_id

    _validate_refs(db, corredora_id, body.cliente_id, body.ramo_id, body.activo_id)

    cot = Cotizacion(
        corredora_id=corredora_id,
        cliente_id=body.cliente_id,
        ramo_id=body.ramo_id,
        activo_id=body.activo_id,
        bien_asegurar=body.bien_asegurar,
        valor_declarado=body.valor_declarado,
        fecha_envio=body.fecha_envio,
        fecha_vence=body.fecha_vence,
        prioridad=body.prioridad,
        estado=body.estado,
    )
    db.add(cot)
    db.commit()
    db.refresh(cot)
    return get_cotizacion(cot.id, db=db, current_user=current_user)


# --- Update ----------------------------------------------------------------
@router.patch("/{cotizacion_id}", response_model=CotizacionDetail)
def update_cotizacion(
    cotizacion_id: int,
    body: CotizacionUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> CotizacionDetail:
    """Partial update of a cotización (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    cot = _get_owned(db, corredora_id, cotizacion_id)

    data = body.model_dump(exclude_unset=True)

    _validate_refs(
        db,
        corredora_id,
        data.get("cliente_id"),
        data.get("ramo_id"),
        data.get("activo_id"),
    )

    for field, value in data.items():
        setattr(cot, field, value)

    db.add(cot)
    db.commit()
    db.refresh(cot)
    return get_cotizacion(cot.id, db=db, current_user=current_user)


# --- Delete ----------------------------------------------------------------
@router.delete("/{cotizacion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cotizacion(
    cotizacion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> Response:
    """Delete a cotización and its ofertas (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    cot = _get_owned(db, corredora_id, cotizacion_id)
    db.delete(cot)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Attach oferta ---------------------------------------------------------
@router.post(
    "/{cotizacion_id}/ofertas",
    response_model=OfertaOut,
    status_code=status.HTTP_201_CREATED,
)
def create_oferta(
    cotizacion_id: int,
    body: OfertaCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> OfertaOut:
    """Attach an oferta to a cotización (subir=borrador; enviar sets estado=enviada).

    When the first oferta lands the cotización moves to estado=respondida.
    """
    corredora_id = current_user.corredora_id
    cot = _get_owned(db, corredora_id, cotizacion_id)

    if db.scalar(
        select(Aseguradora.id).where(
            Aseguradora.id == body.aseguradora_id,
            Aseguradora.corredora_id == corredora_id,
        )
    ) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Aseguradora no encontrada"
        )

    oferta = Oferta(
        corredora_id=corredora_id,
        cotizacion_id=cot.id,
        aseguradora_id=body.aseguradora_id,
        prima=body.prima,
        deducible=body.deducible,
        coberturas=body.coberturas,
        exclusiones=body.exclusiones,
        vigencia=body.vigencia,
        estado=body.estado,
    )
    db.add(oferta)

    # A landed offer means the quote request has been answered.
    cot.estado = CotizacionEstadoEnum.respondida
    db.add(cot)

    db.commit()
    db.refresh(oferta)
    return _to_oferta_out(oferta)


# --- FK validation helper --------------------------------------------------
def _validate_refs(
    db: Session,
    corredora_id: int,
    cliente_id: int | None,
    ramo_id: int | None,
    activo_id: int | None,
) -> None:
    """Ensure referenced rows exist within the tenant; raise 404 otherwise."""
    if cliente_id is not None and db.scalar(
        select(Cliente.id).where(
            Cliente.id == cliente_id, Cliente.corredora_id == corredora_id
        )
    ) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    if ramo_id is not None and db.scalar(
        select(Ramo.id).where(Ramo.id == ramo_id, Ramo.corredora_id == corredora_id)
    ) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ramo no encontrado")
    if activo_id is not None and db.scalar(
        select(Activo.id).where(
            Activo.id == activo_id, Activo.corredora_id == corredora_id
        )
    ) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Activo no encontrado")
