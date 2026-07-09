"""Inspecciones router — CRUD + solicitudes + KPI summary + detail con contexto.

Every query is tenant-scoped to the authenticated user's corredora_id (multi-tenant).
Mounted under /api/v1/inspecciones in app.main (do not touch main.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_corredora_staff
from app.models.activo import Activo
from app.models.cliente import Cliente
from app.models.colaboracion import Documento, Observacion
from app.models.inspeccion import (
    Inspeccion,
    InspeccionEstadoEnum,
    SolicitudInspeccion,
    SolicitudInspeccionEstadoEnum,
)
from app.models.proceso_ramo import ProcesoRamo
from app.models.usuario import Usuario
from app.schemas.common import Page
from app.schemas.inspecciones import (
    INSPECCION_ABIERTAS,
    INSPECCION_ESTADOS,
    ActivoContexto,
    ActivoResumenOut,
    DocumentoOut,
    EstadoTimelineItem,
    InspeccionCreate,
    InspeccionDetail,
    InspeccionListItem,
    InspeccionSummary,
    InspeccionUpdate,
    ObservacionOut,
    RefOut,
    SolicitudCreate,
    SolicitudListItem,
    SolicitudOut,
)

router = APIRouter(prefix="/inspecciones", tags=["inspecciones"])

# Polymorphic entidad_tipo used by documento / observacion for inspecciones.
_ENTIDAD_TIPO = "inspeccion"


# --- Serializers -----------------------------------------------------------
def _activo_cliente(db: Session, corredora_id: int, activo: Activo) -> Cliente | None:
    return db.scalar(
        select(Cliente).where(
            Cliente.id == activo.cliente_id,
            Cliente.corredora_id == corredora_id,
        )
    )


def _to_list_item(db: Session, corredora_id: int, insp: Inspeccion) -> InspeccionListItem:
    cliente = _activo_cliente(db, corredora_id, insp.activo)
    return InspeccionListItem(
        id=insp.id,
        activo=RefOut.model_validate(insp.activo),
        cliente=(
            RefOut.model_validate(cliente)
            if cliente is not None
            else RefOut(id=insp.activo.cliente_id, nombre="—")
        ),
        inspector=RefOut.model_validate(insp.inspector),
        version=insp.version,
        estado=insp.estado,
        solicitud_id=insp.solicitud_id,
    )


def _build_timeline(estado: InspeccionEstadoEnum) -> list[EstadoTimelineItem]:
    idx = INSPECCION_ESTADOS.index(estado)
    return [
        EstadoTimelineItem(estado=e, alcanzado=(i <= idx), actual=(i == idx))
        for i, e in enumerate(INSPECCION_ESTADOS)
    ]


# --- Fetch helpers ---------------------------------------------------------
def _get_owned_inspeccion(
    db: Session, corredora_id: int, inspeccion_id: int
) -> Inspeccion:
    """Fetch an inspección scoped to the tenant or raise 404."""
    insp = db.scalar(
        select(Inspeccion).where(
            Inspeccion.id == inspeccion_id,
            Inspeccion.corredora_id == corredora_id,
        )
    )
    if insp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inspección no encontrada")
    return insp


def _get_owned_solicitud(
    db: Session, corredora_id: int, solicitud_id: int
) -> SolicitudInspeccion:
    sol = db.scalar(
        select(SolicitudInspeccion).where(
            SolicitudInspeccion.id == solicitud_id,
            SolicitudInspeccion.corredora_id == corredora_id,
        )
    )
    if sol is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Solicitud de inspección no encontrada"
        )
    return sol


def _require_activo(db: Session, corredora_id: int, activo_id: int) -> Activo:
    activo = db.scalar(
        select(Activo).where(
            Activo.id == activo_id, Activo.corredora_id == corredora_id
        )
    )
    if activo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Activo no encontrado")
    return activo


def _require_inspector(db: Session, corredora_id: int, inspector_id: int) -> Usuario:
    inspector = db.scalar(
        select(Usuario).where(
            Usuario.id == inspector_id, Usuario.corredora_id == corredora_id
        )
    )
    if inspector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inspector no encontrado")
    return inspector


def _require_proceso_ramo(
    db: Session, corredora_id: int, proceso_ramo_id: int | None
) -> None:
    if proceso_ramo_id is None:
        return
    if db.scalar(
        select(ProcesoRamo.id).where(
            ProcesoRamo.id == proceso_ramo_id,
            ProcesoRamo.corredora_id == corredora_id,
        )
    ) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Proceso de ramo no encontrado"
        )


# --- List inspecciones -----------------------------------------------------
@router.get("", response_model=Page[InspeccionListItem])
def list_inspecciones(
    q: str | None = Query(default=None),
    estado: InspeccionEstadoEnum | None = Query(default=None),
    inspector_id: int | None = Query(default=None),
    activo_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Page[InspeccionListItem]:
    """List inspecciones for the tenant with filters + pagination."""
    corredora_id = current_user.corredora_id

    stmt = select(Inspeccion).where(Inspeccion.corredora_id == corredora_id)

    if estado is not None:
        stmt = stmt.where(Inspeccion.estado == estado)
    if inspector_id is not None:
        stmt = stmt.where(Inspeccion.inspector_id == inspector_id)
    if activo_id is not None:
        stmt = stmt.where(Inspeccion.activo_id == activo_id)
    if q:
        term = f"%{q.strip()}%"
        stmt = (
            stmt.join(Activo, Inspeccion.activo_id == Activo.id)
            .join(Cliente, Activo.cliente_id == Cliente.id)
            .where(
                or_(
                    Activo.nombre.ilike(term),
                    Cliente.nombre.ilike(term),
                )
            )
        )

    total = db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0

    stmt = (
        stmt.order_by(Inspeccion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    inspecciones = db.scalars(stmt).unique().all()

    items = [_to_list_item(db, corredora_id, i) for i in inspecciones]
    return Page[InspeccionListItem](
        items=items, total=total, page=page, page_size=page_size
    )


# --- Summary / KPIs --------------------------------------------------------
@router.get("/summary", response_model=InspeccionSummary)
def inspecciones_summary(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> InspeccionSummary:
    """KPI aggregates: solicitadas/pendientes, en_progreso, enviadas/observadas, validadas.

    Solicitudes pendientes (estado=solicitada) feed the "solicitadas/pendientes" KPI
    together with inspecciones still in estado=solicitada.
    """
    corredora_id = current_user.corredora_id

    def _count_estado(estado: InspeccionEstadoEnum) -> int:
        return int(
            db.scalar(
                select(func.count()).where(
                    Inspeccion.corredora_id == corredora_id,
                    Inspeccion.estado == estado,
                )
            )
            or 0
        )

    en_progreso = _count_estado(InspeccionEstadoEnum.en_progreso)
    enviadas = _count_estado(InspeccionEstadoEnum.enviada)
    observadas = _count_estado(InspeccionEstadoEnum.observada)
    validadas = _count_estado(InspeccionEstadoEnum.validada)
    solicitadas_insp = _count_estado(InspeccionEstadoEnum.solicitada)

    # Solicitudes de inspección aún sin asignar (pendientes de agendar).
    pendientes_solicitudes = int(
        db.scalar(
            select(func.count()).where(
                SolicitudInspeccion.corredora_id == corredora_id,
                SolicitudInspeccion.estado == SolicitudInspeccionEstadoEnum.solicitada,
            )
        )
        or 0
    )

    abiertas_total = int(
        db.scalar(
            select(func.count()).where(
                Inspeccion.corredora_id == corredora_id,
                Inspeccion.estado.in_(list(INSPECCION_ABIERTAS)),
            )
        )
        or 0
    )

    solicitadas = solicitadas_insp + pendientes_solicitudes

    return InspeccionSummary(
        solicitadas=solicitadas,
        en_progreso=en_progreso,
        validadas=validadas,
        abiertas_total=abiertas_total,
        pendientes=pendientes_solicitudes,
        enviadas=enviadas,
        observadas=observadas,
    )


# --- Solicitudes de inspección ---------------------------------------------
@router.get("/solicitudes", response_model=Page[SolicitudListItem])
def list_solicitudes(
    estado: SolicitudInspeccionEstadoEnum | None = Query(default=None),
    activo_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Page[SolicitudListItem]:
    """List solicitudes de inspección for the tenant."""
    corredora_id = current_user.corredora_id

    stmt = select(SolicitudInspeccion).where(
        SolicitudInspeccion.corredora_id == corredora_id
    )
    if estado is not None:
        stmt = stmt.where(SolicitudInspeccion.estado == estado)
    if activo_id is not None:
        stmt = stmt.where(SolicitudInspeccion.activo_id == activo_id)

    total = db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ) or 0

    stmt = (
        stmt.order_by(SolicitudInspeccion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    solicitudes = db.scalars(stmt).unique().all()

    items = [_to_solicitud_list_item(db, corredora_id, s) for s in solicitudes]
    return Page[SolicitudListItem](
        items=items, total=total, page=page, page_size=page_size
    )


def _to_solicitud_list_item(
    db: Session, corredora_id: int, sol: SolicitudInspeccion
) -> SolicitudListItem:
    cliente = _activo_cliente(db, corredora_id, sol.activo)
    return SolicitudListItem(
        id=sol.id,
        activo=RefOut.model_validate(sol.activo),
        cliente=(
            RefOut.model_validate(cliente)
            if cliente is not None
            else RefOut(id=sol.activo.cliente_id, nombre="—")
        ),
        motivo=sol.motivo,
        urgencia=sol.urgencia,
        fecha_objetivo=sol.fecha_objetivo,
        estado=sol.estado,
        proceso_ramo_id=sol.proceso_ramo_id,
        created_by=sol.created_by,
    )


@router.post(
    "/solicitudes",
    response_model=SolicitudListItem,
    status_code=status.HTTP_201_CREATED,
)
def create_solicitud(
    body: SolicitudCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> SolicitudListItem:
    """Solicitar inspección: creates a solicitud_inspeccion (tenant-scoped).

    corredora_id and created_by are injected server-side (never from the body).
    """
    corredora_id = current_user.corredora_id

    _require_activo(db, corredora_id, body.activo_id)
    _require_proceso_ramo(db, corredora_id, body.proceso_ramo_id)

    sol = SolicitudInspeccion(
        corredora_id=corredora_id,
        activo_id=body.activo_id,
        proceso_ramo_id=body.proceso_ramo_id,
        motivo=body.motivo,
        urgencia=body.urgencia,
        fecha_objetivo=body.fecha_objetivo,
        estado=body.estado,
        created_by=current_user.id,
    )
    db.add(sol)
    db.commit()
    db.refresh(sol)
    return _to_solicitud_list_item(db, corredora_id, sol)


# --- Detail ----------------------------------------------------------------
@router.get("/{inspeccion_id}", response_model=InspeccionDetail)
def get_inspeccion(
    inspeccion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> InspeccionDetail:
    """Full inspección + activo context + checklist + estados (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    insp = _get_owned_inspeccion(db, corredora_id, inspeccion_id)

    activo = insp.activo
    cliente = _activo_cliente(db, corredora_id, activo)
    activo_ref = RefOut.model_validate(activo)
    cliente_ref = (
        RefOut.model_validate(cliente)
        if cliente is not None
        else RefOut(id=activo.cliente_id, nombre="—")
    )

    contexto = ActivoContexto(
        activo=activo_ref,
        cliente=cliente_ref,
        tipo_activo=activo.tipo_activo,
        direccion=activo.direccion,
        atributos=activo.atributos,
        estado=activo.estado.value
        if hasattr(activo.estado, "value")
        else activo.estado,
    )

    solicitud_out: SolicitudOut | None = None
    if insp.solicitud_id is not None:
        sol = db.scalar(
            select(SolicitudInspeccion).where(
                SolicitudInspeccion.id == insp.solicitud_id,
                SolicitudInspeccion.corredora_id == corredora_id,
            )
        )
        if sol is not None:
            solicitud_out = SolicitudOut.model_validate(sol)

    documentos = db.scalars(
        select(Documento)
        .where(
            Documento.corredora_id == corredora_id,
            Documento.entidad_tipo == _ENTIDAD_TIPO,
            Documento.entidad_id == insp.id,
        )
        .order_by(Documento.created_at.desc())
    ).all()

    observaciones = db.scalars(
        select(Observacion)
        .where(
            Observacion.corredora_id == corredora_id,
            Observacion.entidad_tipo == _ENTIDAD_TIPO,
            Observacion.entidad_id == insp.id,
        )
        .order_by(Observacion.created_at.desc())
    ).all()

    return InspeccionDetail(
        id=insp.id,
        activo=activo_ref,
        cliente=cliente_ref,
        inspector=RefOut.model_validate(insp.inspector),
        version=insp.version,
        estado=insp.estado,
        solicitud_id=insp.solicitud_id,
        contexto=contexto,
        activo_resumen=ActivoResumenOut(
            id=activo.id,
            nombre=activo.nombre,
            tipo_activo=activo.tipo_activo,
            direccion=activo.direccion,
            estado=activo.estado.value
            if hasattr(activo.estado, "value")
            else activo.estado,
            cliente=cliente_ref,
        ),
        checklist=insp.checklist,
        solicitud=solicitud_out,
        estados_timeline=_build_timeline(insp.estado),
        documentos=[DocumentoOut.model_validate(d) for d in documentos],
        observaciones=[ObservacionOut.model_validate(o) for o in observaciones],
    )


# --- Create / assign inspección --------------------------------------------
@router.post("", response_model=InspeccionDetail, status_code=status.HTTP_201_CREATED)
def create_inspeccion(
    body: InspeccionCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> InspeccionDetail:
    """Create/assign an inspección. corredora_id is injected from the JWT.

    When it is fulfilling a solicitud, that solicitud moves to estado=asignada.
    """
    corredora_id = current_user.corredora_id

    _require_activo(db, corredora_id, body.activo_id)
    _require_inspector(db, corredora_id, body.inspector_id)

    sol: SolicitudInspeccion | None = None
    if body.solicitud_id is not None:
        sol = _get_owned_solicitud(db, corredora_id, body.solicitud_id)

    insp = Inspeccion(
        corredora_id=corredora_id,
        activo_id=body.activo_id,
        solicitud_id=body.solicitud_id,
        inspector_id=body.inspector_id,
        version=body.version,
        estado=body.estado,
        checklist=body.checklist or {},
    )
    db.add(insp)

    # Assigning an inspección fulfils its solicitud.
    if sol is not None:
        sol.estado = SolicitudInspeccionEstadoEnum.asignada
        db.add(sol)

    db.commit()
    db.refresh(insp)
    return get_inspeccion(insp.id, db=db, current_user=current_user)


# --- Update / advance estado -----------------------------------------------
@router.patch("/{inspeccion_id}", response_model=InspeccionDetail)
def update_inspeccion(
    inspeccion_id: int,
    body: InspeccionUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> InspeccionDetail:
    """Advance estado / update checklist / reassign inspector (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    insp = _get_owned_inspeccion(db, corredora_id, inspeccion_id)

    data = body.model_dump(exclude_unset=True)

    if "inspector_id" in data and data["inspector_id"] is not None:
        _require_inspector(db, corredora_id, data["inspector_id"])
    if "solicitud_id" in data and data["solicitud_id"] is not None:
        _get_owned_solicitud(db, corredora_id, data["solicitud_id"])

    for field, value in data.items():
        setattr(insp, field, value)

    db.add(insp)
    db.commit()
    db.refresh(insp)
    return get_inspeccion(insp.id, db=db, current_user=current_user)


# --- Delete ----------------------------------------------------------------
@router.delete("/{inspeccion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inspeccion(
    inspeccion_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(require_corredora_staff),
) -> Response:
    """Delete an inspección (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    insp = _get_owned_inspeccion(db, corredora_id, inspeccion_id)
    db.delete(insp)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
