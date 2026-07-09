"""Renovaciones router — CRUD + KPI summary + detail with resumen de póliza vigente.

Every query is tenant-scoped to the authenticated user's corredora_id (multi-tenant).
Mounted under /api/v1/renovaciones in app.main (do not touch main.py).
"""
from __future__ import annotations

from datetime import date, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_corredora_staff
from app.models.aseguradora import Aseguradora
from app.models.cliente import Cliente
from app.models.colaboracion import Documento, Observacion
from app.models.poliza import (
    CoberturaItem,
    CoberturaItemTipoEnum,
    Poliza,
)
from app.models.ramo import Ramo
from app.models.renovacion import Renovacion, RenovacionEstadoEnum
from app.models.usuario import Usuario
from app.schemas.common import Page
from app.schemas.renovaciones import (
    AseguradoraRef,
    ClienteRef,
    CoaseguroParticipacionOut,
    DocumentoOut,
    EjecutivoRef,
    ObservacionOut,
    PlanPago,
    PolizaRef,
    PolizaVigenteResumen,
    RamoRef,
    RenovacionCreate,
    RenovacionDetail,
    RenovacionListItem,
    RenovacionSummary,
    RenovacionUpdate,
)

router = APIRouter(prefix="/renovaciones", tags=["renovaciones"])

# Renovaciones "activas" = not started or in progress; all current states are active.
_ACTIVE_ESTADOS = (
    RenovacionEstadoEnum.por_iniciar,
    RenovacionEstadoEnum.cotizando,
    RenovacionEstadoEnum.negociando,
)


def _codigo(ren: Renovacion) -> str:
    """Derive the REN-NNN code from the id (seed convention REN-001..)."""
    return f"REN-{ren.id:03d}"


def _dias_restantes(fecha_vencimiento: date) -> int:
    return (fecha_vencimiento - date.today()).days


def _dias_color(dias_restantes: int) -> str:
    """ámbar if <= 60 días else gris (COVERAGE/días-restantes rule for renovaciones)."""
    return "ambar" if dias_restantes <= 60 else "gris"


def _cobertura_estado(cobertura_pct: float | None) -> str:
    """<95 infracobertura, 95-105 óptima, >105 sobrecobertura."""
    pct = float(cobertura_pct or 0)
    if pct < 95:
        return "infracobertura"
    if pct <= 105:
        return "optima"
    return "sobrecobertura"


def _to_list_item(ren: Renovacion) -> RenovacionListItem:
    dias = _dias_restantes(ren.fecha_vencimiento)
    return RenovacionListItem(
        id=ren.id,
        codigo=_codigo(ren),
        cliente=ClienteRef.model_validate(ren.cliente),
        poliza=PolizaRef.model_validate(ren.poliza),
        ramo=RamoRef.model_validate(ren.ramo),
        aseguradora=AseguradoraRef.model_validate(ren.aseguradora),
        aplica_coaseguro=bool(ren.aplica_coaseguro),
        prima_defender_uf=float(ren.prima_defender or 0),
        comision_pct=float(ren.comision_pct or 0),
        fecha_vencimiento=ren.fecha_vencimiento,
        dias_restantes=dias,
        dias_color=_dias_color(dias),
        estado=ren.estado,
        estado_negociacion_texto=ren.estado_negociacion_texto,
    )


def _get_owned(db: Session, corredora_id: int, renovacion_id: int) -> Renovacion:
    """Fetch a renovacion scoped to the tenant or raise 404."""
    ren = db.scalar(
        select(Renovacion).where(
            Renovacion.id == renovacion_id,
            Renovacion.corredora_id == corredora_id,
        )
    )
    if ren is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Renovación no encontrada")
    return ren


def _poliza_vigente_resumen(poliza: Poliza) -> PolizaVigenteResumen:
    """Build the resumen de póliza vigente: coaseguro, ubicaciones, coberturas/exclusiones."""
    coberturas = [
        ci.descripcion
        for ci in poliza.cobertura_items
        if ci.tipo == CoberturaItemTipoEnum.cobertura
    ]
    exclusiones = [
        ci.descripcion
        for ci in poliza.cobertura_items
        if ci.tipo == CoberturaItemTipoEnum.exclusion
    ]
    return PolizaVigenteResumen(
        id=poliza.id,
        numero_poliza=poliza.numero_poliza,
        ramo=RamoRef.model_validate(poliza.ramo),
        aseguradora=AseguradoraRef.model_validate(poliza.aseguradora),
        tiene_coaseguro=bool(poliza.tiene_coaseguro),
        prima_uf=float(poliza.prima or 0),
        comision_pct=float(poliza.comision_pct or 0),
        suma_asegurada_uf=float(poliza.suma_asegurada or 0),
        tipo_cobertura=poliza.tipo_cobertura.value
        if hasattr(poliza.tipo_cobertura, "value")
        else str(poliza.tipo_cobertura),
        cobertura_pct=float(poliza.cobertura_pct or 0),
        cobertura_estado=_cobertura_estado(poliza.cobertura_pct),
        vigencia_inicio=poliza.vigencia_inicio,
        vigencia_fin=poliza.vigencia_fin,
        estado=poliza.estado.value if hasattr(poliza.estado, "value") else str(poliza.estado),
        deducible_texto=poliza.deducible_texto,
        limite_indemnizacion_uf=(
            float(poliza.limite_indemnizacion)
            if poliza.limite_indemnizacion is not None
            else None
        ),
        plan_pago=PlanPago(
            cuotas=poliza.plan_pago_cuotas, metodo=poliza.plan_pago_metodo
        ),
        pago_url=poliza.pago_url,
        coaseguro_participaciones=[
            CoaseguroParticipacionOut(
                aseguradora=AseguradoraRef.model_validate(cp.aseguradora),
                es_lider=bool(cp.es_lider),
                porcentaje=float(cp.porcentaje or 0),
            )
            for cp in poliza.coaseguro_participaciones
        ],
        ubicaciones=[
            {
                "nombre": ub.nombre,
                "direccion": ub.direccion,
                "suma_asegurada_uf": float(ub.suma_asegurada or 0),
                "porcentaje": float(ub.porcentaje or 0),
            }
            for ub in poliza.ubicaciones
        ],
        coberturas=coberturas,
        exclusiones=exclusiones,
    )


# --- List ------------------------------------------------------------------
@router.get("", response_model=Page[RenovacionListItem])
def list_renovaciones(
    q: str | None = Query(default=None),
    estado: RenovacionEstadoEnum | None = Query(default=None),
    ejecutivo_id: int | None = Query(default=None),
    aseguradora_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Page[RenovacionListItem]:
    """List renovaciones for the tenant with filters + pagination."""
    corredora_id = current_user.corredora_id

    stmt = select(Renovacion).where(Renovacion.corredora_id == corredora_id)

    if estado is not None:
        stmt = stmt.where(Renovacion.estado == estado)
    if ejecutivo_id is not None:
        stmt = stmt.where(Renovacion.ejecutivo_id == ejecutivo_id)
    if aseguradora_id is not None:
        stmt = stmt.where(Renovacion.aseguradora_id == aseguradora_id)
    if q:
        term = f"%{q.strip()}%"
        stmt = (
            stmt.join(Cliente, Renovacion.cliente_id == Cliente.id)
            .join(Poliza, Renovacion.poliza_id == Poliza.id)
            .where(
                or_(
                    Cliente.nombre.ilike(term),
                    Poliza.numero_poliza.ilike(term),
                    Renovacion.estado_negociacion_texto.ilike(term),
                )
            )
        )

    total = db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0

    stmt = (
        stmt.order_by(Renovacion.fecha_vencimiento.asc(), Renovacion.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    renovaciones = db.scalars(stmt).unique().all()

    items = [_to_list_item(r) for r in renovaciones]
    return Page[RenovacionListItem](
        items=items, total=total, page=page, page_size=page_size
    )


# --- Summary / KPIs --------------------------------------------------------
@router.get("/summary", response_model=RenovacionSummary)
def renovaciones_summary(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> RenovacionSummary:
    """KPI aggregates: activas, en_negociacion, prima_en_juego, por_vencer_30d."""
    corredora_id = current_user.corredora_id
    base = select(Renovacion).where(Renovacion.corredora_id == corredora_id)

    renovaciones_activas = db.scalar(
        select(func.count()).select_from(
            base.where(Renovacion.estado.in_(_ACTIVE_ESTADOS)).subquery()
        )
    ) or 0

    en_negociacion = db.scalar(
        select(func.count()).select_from(
            base.where(Renovacion.estado == RenovacionEstadoEnum.negociando).subquery()
        )
    ) or 0

    prima_en_juego = db.scalar(
        select(func.coalesce(func.sum(Renovacion.prima_defender), 0)).where(
            Renovacion.corredora_id == corredora_id,
            Renovacion.estado.in_(_ACTIVE_ESTADOS),
        )
    ) or 0

    hoy = date.today()
    limite_30d = date.fromordinal(hoy.toordinal() + 30)
    por_vencer_30d = db.scalar(
        select(func.count()).select_from(
            base.where(
                Renovacion.estado.in_(_ACTIVE_ESTADOS),
                Renovacion.fecha_vencimiento >= hoy,
                Renovacion.fecha_vencimiento <= limite_30d,
            ).subquery()
        )
    ) or 0

    return RenovacionSummary(
        renovaciones_activas=int(renovaciones_activas),
        en_negociacion=int(en_negociacion),
        prima_en_juego_uf=float(prima_en_juego),
        por_vencer_30d=int(por_vencer_30d),
    )


# --- Detail ----------------------------------------------------------------
@router.get("/{renovacion_id}", response_model=RenovacionDetail)
def get_renovacion(
    renovacion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> RenovacionDetail:
    """Full renovación + resumen de póliza vigente + documentos/observaciones."""
    corredora_id = current_user.corredora_id
    ren = _get_owned(db, corredora_id, renovacion_id)

    base = _to_list_item(ren).model_dump()

    # Póliza vigente ligada (tenant-scoped resumen).
    poliza = db.scalar(
        select(Poliza).where(
            Poliza.id == ren.poliza_id, Poliza.corredora_id == corredora_id
        )
    )
    poliza_vigente = _poliza_vigente_resumen(poliza) if poliza is not None else None

    # Ejecutivo asignado.
    ejecutivo = None
    if ren.ejecutivo is not None:
        ejecutivo = EjecutivoRef.model_validate(ren.ejecutivo)

    # Colaboración polimórfica (entidad_tipo="renovacion").
    documentos = db.scalars(
        select(Documento).where(
            Documento.corredora_id == corredora_id,
            Documento.entidad_tipo == "renovacion",
            Documento.entidad_id == ren.id,
        )
    ).all()
    observaciones_rows = db.scalars(
        select(Observacion)
        .where(
            Observacion.corredora_id == corredora_id,
            Observacion.entidad_tipo == "renovacion",
            Observacion.entidad_id == ren.id,
        )
        .order_by(Observacion.created_at.desc())
    ).all()

    observaciones = [
        ObservacionOut(
            id=o.id,
            texto=o.texto,
            autor=o.autor.nombre if o.autor is not None else None,
            created_at=o.created_at.astimezone(timezone.utc).isoformat()
            if o.created_at is not None
            else None,
        )
        for o in observaciones_rows
    ]

    return RenovacionDetail(
        **base,
        ejecutivo=ejecutivo,
        poliza_vigente=poliza_vigente,
        poliza_resumen=poliza_vigente,
        documentos=[DocumentoOut.model_validate(d) for d in documentos],
        observaciones=observaciones,
    )


# --- Create ----------------------------------------------------------------
@router.post("", response_model=RenovacionDetail, status_code=status.HTTP_201_CREATED)
def create_renovacion(
    body: RenovacionCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> RenovacionDetail:
    """Create a renovación. corredora_id is injected from the JWT (never from the body)."""
    corredora_id = current_user.corredora_id

    _validate_refs(db, corredora_id, body.poliza_id, body.cliente_id, body.ramo_id,
                   body.aseguradora_id, body.ejecutivo_id)

    ren = Renovacion(
        corredora_id=corredora_id,
        poliza_id=body.poliza_id,
        cliente_id=body.cliente_id,
        ramo_id=body.ramo_id,
        aseguradora_id=body.aseguradora_id,
        aplica_coaseguro=body.aplica_coaseguro,
        prima_defender=body.prima_defender,
        comision_pct=body.comision_pct,
        fecha_vencimiento=body.fecha_vencimiento,
        ejecutivo_id=body.ejecutivo_id,
        estado=body.estado,
        estado_negociacion_texto=body.estado_negociacion_texto,
    )
    db.add(ren)
    db.commit()
    db.refresh(ren)
    return get_renovacion(ren.id, db=db, current_user=current_user)


# --- Update ----------------------------------------------------------------
@router.patch("/{renovacion_id}", response_model=RenovacionDetail)
def update_renovacion(
    renovacion_id: int,
    body: RenovacionUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> RenovacionDetail:
    """Partial update of a renovación (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    ren = _get_owned(db, corredora_id, renovacion_id)

    data = body.model_dump(exclude_unset=True)

    _validate_refs(
        db,
        corredora_id,
        data.get("poliza_id"),
        data.get("cliente_id"),
        data.get("ramo_id"),
        data.get("aseguradora_id"),
        data.get("ejecutivo_id"),
    )

    for field, value in data.items():
        setattr(ren, field, value)

    db.add(ren)
    db.commit()
    db.refresh(ren)
    return get_renovacion(ren.id, db=db, current_user=current_user)


# --- Delete ----------------------------------------------------------------
@router.delete("/{renovacion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_renovacion(
    renovacion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> Response:
    """Delete a renovación (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    ren = _get_owned(db, corredora_id, renovacion_id)
    db.delete(ren)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- FK validation helper --------------------------------------------------
def _validate_refs(
    db: Session,
    corredora_id: int,
    poliza_id: int | None,
    cliente_id: int | None,
    ramo_id: int | None,
    aseguradora_id: int | None,
    ejecutivo_id: int | None,
) -> None:
    """Ensure referenced rows exist within the tenant; raise 404 otherwise."""
    if poliza_id is not None and db.scalar(
        select(Poliza.id).where(
            Poliza.id == poliza_id, Poliza.corredora_id == corredora_id
        )
    ) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Póliza no encontrada")
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
    if aseguradora_id is not None and db.scalar(
        select(Aseguradora.id).where(
            Aseguradora.id == aseguradora_id,
            Aseguradora.corredora_id == corredora_id,
        )
    ) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Aseguradora no encontrada"
        )
    if ejecutivo_id is not None and db.scalar(
        select(Usuario.id).where(
            Usuario.id == ejecutivo_id, Usuario.corredora_id == corredora_id
        )
    ) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ejecutivo no encontrado")
