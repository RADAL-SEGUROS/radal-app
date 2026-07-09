"""Siniestros router — CRUD + KPI aggregates + nested detail. /api/v1/siniestros.

Every query is tenant-scoped to the authenticated user's corredora_id.
Built strictly to docs/api-contract.md (Siniestros section).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    get_current_corredora_id,
    get_current_user,
    require_corredora_staff,
)
from app.db.session import get_db
from app.models.activo import Activo
from app.models.cliente import Cliente
from app.models.colaboracion import Actividad, Documento, Observacion
from app.models.poliza import Poliza
from app.models.siniestro import Siniestro, SiniestroEstadoEnum
from app.models.usuario import Usuario
from app.schemas.siniestros import (
    SINIESTRO_ABIERTOS,
    SINIESTRO_ESTADOS,
    ActividadOut,
    DocumentoOut,
    EstadoTimelineItem,
    ObservacionOut,
    PolizaRef,
    RefOut,
    SiniestroContexto,
    SiniestroCreate,
    SiniestroDetail,
    SiniestroListItem,
    SiniestroListResponse,
    SiniestroSummary,
    SiniestroUpdate,
)

router = APIRouter(prefix="/siniestros", tags=["siniestros"])

_ENTIDAD_TIPO = "siniestro"


# --- helpers ----------------------------------------------------------------
def _get_owned_siniestro(db: Session, siniestro_id: int, corredora_id: int) -> Siniestro:
    """Fetch a siniestro scoped to the tenant or raise 404."""
    siniestro = db.get(Siniestro, siniestro_id)
    if siniestro is None or siniestro.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Siniestro no encontrado")
    return siniestro


def _rol_value(usuario: Usuario | None) -> str | None:
    if usuario is None:
        return None
    return usuario.rol.value if hasattr(usuario.rol, "value") else str(usuario.rol)


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _cliente_ref(cliente: Cliente | None) -> RefOut | None:
    if cliente is None:
        return None
    return RefOut(id=cliente.id, nombre=cliente.nombre)


def _activo_ref(activo: Activo | None) -> RefOut | None:
    if activo is None:
        return None
    return RefOut(id=activo.id, nombre=activo.nombre)


def _poliza_ref(poliza: Poliza | None) -> PolizaRef | None:
    if poliza is None:
        return None
    return PolizaRef(id=poliza.id, numero_poliza=poliza.numero_poliza)


def _list_item(siniestro: Siniestro) -> SiniestroListItem:
    return SiniestroListItem(
        id=siniestro.id,
        cliente=_cliente_ref(siniestro.cliente),
        poliza=_poliza_ref(siniestro.poliza),
        tipo=siniestro.tipo,
        fecha_evento=siniestro.fecha_evento,
        estado=siniestro.estado,
        monto_estimado_uf=_num(siniestro.monto_estimado),
        monto_liquidado_uf=_num(siniestro.monto_liquidado),
        fecha_liquidacion=siniestro.fecha_liquidacion,
    )


def _estados_timeline(estado: SiniestroEstadoEnum) -> list[EstadoTimelineItem]:
    """Build the lifecycle timeline, marking reached and current estado."""
    try:
        current_idx = SINIESTRO_ESTADOS.index(estado)
    except ValueError:
        current_idx = -1
    return [
        EstadoTimelineItem(
            estado=step,
            alcanzado=current_idx >= 0 and idx <= current_idx,
            actual=idx == current_idx,
        )
        for idx, step in enumerate(SINIESTRO_ESTADOS)
    ]


def _validate_refs(
    db: Session,
    corredora_id: int,
    poliza_id: int | None,
    cliente_id: int | None,
    activo_id: int | None,
) -> None:
    """Ensure referenced poliza/cliente/activo belong to the tenant."""
    if poliza_id is not None:
        poliza = db.get(Poliza, poliza_id)
        if poliza is None or poliza.corredora_id != corredora_id:
            raise HTTPException(status_code=404, detail="Póliza no encontrada")
    if cliente_id is not None:
        cliente = db.get(Cliente, cliente_id)
        if cliente is None or cliente.corredora_id != corredora_id:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
    if activo_id is not None:
        activo = db.get(Activo, activo_id)
        if activo is None or activo.corredora_id != corredora_id:
            raise HTTPException(status_code=404, detail="Activo no encontrado")


def _build_contexto(db: Session, siniestro: Siniestro) -> SiniestroContexto:
    """Bundle póliza + cliente context for the detail header."""
    poliza = siniestro.poliza
    ramo = getattr(poliza, "ramo", None) if poliza is not None else None
    aseguradora = getattr(poliza, "aseguradora", None) if poliza is not None else None
    return SiniestroContexto(
        cliente=_cliente_ref(siniestro.cliente),
        poliza=_poliza_ref(poliza),
        ramo=RefOut(id=ramo.id, nombre=ramo.nombre) if ramo is not None else None,
        aseguradora=(
            RefOut(id=aseguradora.id, nombre=aseguradora.nombre)
            if aseguradora is not None
            else None
        ),
        prima_uf=_num(poliza.prima) if poliza is not None else None,
        suma_asegurada_uf=_num(poliza.suma_asegurada) if poliza is not None else None,
        vigencia_inicio=poliza.vigencia_inicio if poliza is not None else None,
        vigencia_fin=poliza.vigencia_fin if poliza is not None else None,
        poliza_estado=(
            poliza.estado.value
            if poliza is not None and hasattr(poliza.estado, "value")
            else (str(poliza.estado) if poliza is not None else None)
        ),
    )


def _build_detail(db: Session, siniestro: Siniestro, corredora_id: int) -> SiniestroDetail:
    documentos = db.scalars(
        select(Documento)
        .where(
            Documento.corredora_id == corredora_id,
            Documento.entidad_tipo == _ENTIDAD_TIPO,
            Documento.entidad_id == siniestro.id,
        )
        .order_by(Documento.created_at.desc())
    ).all()

    observaciones = db.scalars(
        select(Observacion)
        .where(
            Observacion.corredora_id == corredora_id,
            Observacion.entidad_tipo == _ENTIDAD_TIPO,
            Observacion.entidad_id == siniestro.id,
        )
        .order_by(Observacion.created_at.desc())
    ).all()

    actividad_rows = db.scalars(
        select(Actividad)
        .where(
            Actividad.corredora_id == corredora_id,
            Actividad.entidad_tipo == _ENTIDAD_TIPO,
            Actividad.entidad_id == siniestro.id,
        )
        .order_by(Actividad.created_at.desc())
        .limit(50)
    ).all()

    actor_ids = {a.usuario_id for a in actividad_rows if a.usuario_id is not None}
    actores: dict[int, Usuario] = {}
    if actor_ids:
        for u in db.scalars(select(Usuario).where(Usuario.id.in_(actor_ids))).all():
            actores[u.id] = u

    actividad = [
        ActividadOut(
            id=a.id,
            accion=a.accion,
            descripcion=a.descripcion,
            usuario=actores[a.usuario_id].nombre if a.usuario_id in actores else None,
            rol=_rol_value(actores.get(a.usuario_id)),
            created_at=a.created_at,
        )
        for a in actividad_rows
    ]

    return SiniestroDetail(
        id=siniestro.id,
        tipo=siniestro.tipo,
        descripcion=siniestro.descripcion,
        fecha_evento=siniestro.fecha_evento,
        estado=siniestro.estado,
        monto_estimado_uf=_num(siniestro.monto_estimado),
        monto_liquidado_uf=_num(siniestro.monto_liquidado),
        fecha_liquidacion=siniestro.fecha_liquidacion,
        cliente=_cliente_ref(siniestro.cliente),
        poliza=_poliza_ref(siniestro.poliza),
        activo=_activo_ref(siniestro.activo),
        contexto=_build_contexto(db, siniestro),
        estados_timeline=_estados_timeline(siniestro.estado),
        documentos=[DocumentoOut.model_validate(d) for d in documentos],
        observaciones=[ObservacionOut.model_validate(o) for o in observaciones],
        actividad=actividad,
    )


# --- endpoints --------------------------------------------------------------
@router.get("", response_model=SiniestroListResponse)
def list_siniestros(
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
    q: str | None = Query(default=None),
    estado: SiniestroEstadoEnum | None = Query(default=None),
    cliente_id: int | None = Query(default=None),
    poliza_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> SiniestroListResponse:
    """List siniestros for the tenant with filters + pagination."""
    filters = [Siniestro.corredora_id == corredora_id]
    if estado is not None:
        filters.append(Siniestro.estado == estado)
    if cliente_id is not None:
        filters.append(Siniestro.cliente_id == cliente_id)
    if poliza_id is not None:
        filters.append(Siniestro.poliza_id == poliza_id)
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                Siniestro.tipo.ilike(like),
                Siniestro.descripcion.ilike(like),
                Siniestro.cliente.has(Cliente.nombre.ilike(like)),
                Siniestro.poliza.has(Poliza.numero_poliza.ilike(like)),
            )
        )

    total = db.execute(
        select(func.count()).select_from(Siniestro).where(*filters)
    ).scalar_one()

    rows = db.scalars(
        select(Siniestro)
        .where(*filters)
        .options(
            selectinload(Siniestro.cliente),
            selectinload(Siniestro.poliza),
        )
        .order_by(Siniestro.fecha_evento.desc(), Siniestro.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return SiniestroListResponse(
        items=[_list_item(s) for s in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/summary", response_model=SiniestroSummary)
def siniestros_summary(
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> SiniestroSummary:
    """KPI aggregates for the list header (GET /siniestros/summary)."""
    tenant = (Siniestro.corredora_id == corredora_id,)

    abiertos = db.execute(
        select(func.count())
        .select_from(Siniestro)
        .where(*tenant, Siniestro.estado.in_(list(SINIESTRO_ABIERTOS)))
    ).scalar_one()

    en_evaluacion = db.execute(
        select(func.count())
        .select_from(Siniestro)
        .where(*tenant, Siniestro.estado == SiniestroEstadoEnum.en_evaluacion)
    ).scalar_one()

    monto_estimado_total = db.execute(
        select(func.coalesce(func.sum(Siniestro.monto_estimado), 0)).where(
            *tenant, Siniestro.estado.in_(list(SINIESTRO_ABIERTOS))
        )
    ).scalar_one()

    monto_liquidado_total = db.execute(
        select(func.coalesce(func.sum(Siniestro.monto_liquidado), 0)).where(*tenant)
    ).scalar_one()

    return SiniestroSummary(
        abiertos=int(abiertos),
        en_evaluacion=int(en_evaluacion),
        monto_estimado_total_uf=float(monto_estimado_total or 0),
        monto_liquidado_total_uf=float(monto_liquidado_total or 0),
    )


@router.get("/{siniestro_id}", response_model=SiniestroDetail)
def get_siniestro(
    siniestro_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> SiniestroDetail:
    """Full siniestro detail: póliza/cliente context, estados timeline, documentos."""
    siniestro = _get_owned_siniestro(db, siniestro_id, corredora_id)
    return _build_detail(db, siniestro, corredora_id)


@router.post("", response_model=SiniestroDetail, status_code=status.HTTP_201_CREATED)
def create_siniestro(
    payload: SiniestroCreate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    current_user: Usuario = Depends(require_corredora_staff),
) -> SiniestroDetail:
    """Reportar siniestro; corredora_id is injected from the JWT, never the body."""
    _validate_refs(
        db, corredora_id, payload.poliza_id, payload.cliente_id, payload.activo_id
    )

    siniestro = Siniestro(corredora_id=corredora_id, **payload.model_dump())
    db.add(siniestro)
    db.flush()

    db.add(
        Actividad(
            corredora_id=corredora_id,
            usuario_id=current_user.id,
            accion="Siniestro reportado",
            entidad_tipo=_ENTIDAD_TIPO,
            entidad_id=siniestro.id,
            descripcion=f"{siniestro.tipo} — {siniestro.fecha_evento.isoformat()}",
        )
    )

    db.commit()
    db.refresh(siniestro)
    return _build_detail(db, siniestro, corredora_id)


@router.patch("/{siniestro_id}", response_model=SiniestroDetail)
def update_siniestro(
    siniestro_id: int,
    payload: SiniestroUpdate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    current_user: Usuario = Depends(require_corredora_staff),
) -> SiniestroDetail:
    """Partial update of a siniestro; logs an estado change to the actividad feed."""
    siniestro = _get_owned_siniestro(db, siniestro_id, corredora_id)

    data = payload.model_dump(exclude_unset=True)
    _validate_refs(
        db,
        corredora_id,
        data.get("poliza_id"),
        data.get("cliente_id"),
        data.get("activo_id"),
    )

    previous_estado = siniestro.estado
    for field, value in data.items():
        setattr(siniestro, field, value)

    new_estado = siniestro.estado
    if "estado" in data and new_estado != previous_estado:
        prev = (
            previous_estado.value
            if hasattr(previous_estado, "value")
            else str(previous_estado)
        )
        curr = new_estado.value if hasattr(new_estado, "value") else str(new_estado)
        db.add(
            Actividad(
                corredora_id=corredora_id,
                usuario_id=current_user.id,
                accion="Siniestro actualizado",
                entidad_tipo=_ENTIDAD_TIPO,
                entidad_id=siniestro.id,
                descripcion=f"Estado {prev} → {curr}",
            )
        )

    db.commit()
    db.refresh(siniestro)
    return _build_detail(db, siniestro, corredora_id)


@router.delete("/{siniestro_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_siniestro(
    siniestro_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_corredora_staff),
) -> Response:
    """Delete a siniestro (hard delete; siniestro has no soft-delete estado)."""
    siniestro = _get_owned_siniestro(db, siniestro_id, corredora_id)
    db.delete(siniestro)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
