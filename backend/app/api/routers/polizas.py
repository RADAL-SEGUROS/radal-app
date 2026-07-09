"""Pólizas router — CRUD + KPI aggregates + nested detail. /api/v1/polizas.

Every query is tenant-scoped to the authenticated user's corredora_id.
Built strictly to docs/api-contract.md (Pólizas section).
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
from app.models.aseguradora import Aseguradora
from app.models.cliente import Cliente
from app.models.colaboracion import Documento, Observacion
from app.models.poliza import (
    CoaseguroParticipacion,
    CoberturaItem,
    CoberturaItemTipoEnum,
    Poliza,
    PolizaEstadoEnum,
    UbicacionPoliza,
)
from app.models.ramo import Ramo
from app.models.usuario import Usuario
from app.schemas.polizas import (
    CoaseguroParticipacionOut,
    DocumentoOut,
    ObservacionOut,
    PlanPago,
    PolizaCreate,
    PolizaDetail,
    PolizaListItem,
    PolizaListResponse,
    PolizaSummary,
    PolizaUpdate,
    RefOut,
    UbicacionOut,
    cobertura_estado_for,
)

router = APIRouter(prefix="/polizas", tags=["polizas"])

_ENTIDAD_TIPO = "poliza"


# --- helpers ----------------------------------------------------------------
def _get_owned_poliza(db: Session, poliza_id: int, corredora_id: int) -> Poliza:
    """Fetch a póliza scoped to the tenant or raise 404."""
    poliza = db.get(Poliza, poliza_id)
    if poliza is None or poliza.corredora_id != corredora_id:
        raise HTTPException(status_code=404, detail="Póliza no encontrada")
    return poliza


def _ref(obj) -> RefOut | None:
    if obj is None:
        return None
    return RefOut(id=obj.id, nombre=obj.nombre)


def _list_item(poliza: Poliza) -> PolizaListItem:
    return PolizaListItem(
        id=poliza.id,
        numero_poliza=poliza.numero_poliza,
        cliente=_ref(poliza.cliente),
        ramo=_ref(poliza.ramo),
        aseguradora=_ref(poliza.aseguradora),
        tiene_coaseguro=poliza.tiene_coaseguro,
        prima_uf=float(poliza.prima or 0),
        comision_pct=float(poliza.comision_pct or 0),
        suma_asegurada_uf=float(poliza.suma_asegurada or 0),
        tipo_cobertura=poliza.tipo_cobertura,
        cobertura_pct=float(poliza.cobertura_pct or 0),
        cobertura_estado=cobertura_estado_for(poliza.cobertura_pct),
        vigencia_inicio=poliza.vigencia_inicio,
        vigencia_fin=poliza.vigencia_fin,
        estado=poliza.estado,
    )


def _sync_children(db: Session, poliza: Poliza, payload) -> None:
    """Replace nested coaseguro/ubicaciones/coberturas/exclusiones when provided."""
    data = payload if isinstance(payload, dict) else payload.model_dump(exclude_unset=True)

    if "coaseguro_participaciones" in data and data["coaseguro_participaciones"] is not None:
        poliza.coaseguro_participaciones.clear()
        for cp in data["coaseguro_participaciones"]:
            poliza.coaseguro_participaciones.append(
                CoaseguroParticipacion(
                    aseguradora_id=cp["aseguradora_id"],
                    es_lider=cp.get("es_lider", False),
                    porcentaje=cp.get("porcentaje", 0),
                )
            )

    if "ubicaciones" in data and data["ubicaciones"] is not None:
        poliza.ubicaciones.clear()
        for ub in data["ubicaciones"]:
            poliza.ubicaciones.append(
                UbicacionPoliza(
                    nombre=ub["nombre"],
                    direccion=ub.get("direccion"),
                    suma_asegurada=ub.get("suma_asegurada", 0),
                    porcentaje=ub.get("porcentaje", 0),
                )
            )

    coberturas_given = "coberturas" in data and data["coberturas"] is not None
    exclusiones_given = "exclusiones" in data and data["exclusiones"] is not None
    if coberturas_given or exclusiones_given:
        # Rebuild the affected tipo(s) only.
        kept = [
            ci
            for ci in list(poliza.cobertura_items)
            if not (
                (coberturas_given and ci.tipo == CoberturaItemTipoEnum.cobertura)
                or (exclusiones_given and ci.tipo == CoberturaItemTipoEnum.exclusion)
            )
        ]
        poliza.cobertura_items.clear()
        for ci in kept:
            poliza.cobertura_items.append(ci)
        if coberturas_given:
            for desc in data["coberturas"]:
                poliza.cobertura_items.append(
                    CoberturaItem(tipo=CoberturaItemTipoEnum.cobertura, descripcion=desc)
                )
        if exclusiones_given:
            for desc in data["exclusiones"]:
                poliza.cobertura_items.append(
                    CoberturaItem(tipo=CoberturaItemTipoEnum.exclusion, descripcion=desc)
                )


def _build_detail(db: Session, poliza: Poliza, corredora_id: int) -> PolizaDetail:
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

    documentos = db.execute(
        select(Documento)
        .where(
            Documento.corredora_id == corredora_id,
            Documento.entidad_tipo == _ENTIDAD_TIPO,
            Documento.entidad_id == poliza.id,
        )
        .order_by(Documento.created_at.desc())
    ).scalars().all()

    observaciones = db.execute(
        select(Observacion)
        .where(
            Observacion.corredora_id == corredora_id,
            Observacion.entidad_tipo == _ENTIDAD_TIPO,
            Observacion.entidad_id == poliza.id,
        )
        .order_by(Observacion.created_at.desc())
    ).scalars().all()

    return PolizaDetail(
        id=poliza.id,
        numero_poliza=poliza.numero_poliza,
        cliente=_ref(poliza.cliente),
        ramo=_ref(poliza.ramo),
        aseguradora=_ref(poliza.aseguradora),
        tiene_coaseguro=poliza.tiene_coaseguro,
        prima_uf=float(poliza.prima or 0),
        comision_pct=float(poliza.comision_pct or 0),
        suma_asegurada_uf=float(poliza.suma_asegurada or 0),
        tipo_cobertura=poliza.tipo_cobertura,
        cobertura_pct=float(poliza.cobertura_pct or 0),
        cobertura_estado=cobertura_estado_for(poliza.cobertura_pct),
        vigencia_inicio=poliza.vigencia_inicio,
        vigencia_fin=poliza.vigencia_fin,
        estado=poliza.estado,
        activo_id=poliza.activo_id,
        proceso_ramo_id=poliza.proceso_ramo_id,
        coaseguro_participaciones=[
            CoaseguroParticipacionOut(
                id=cp.id,
                aseguradora=_ref(cp.aseguradora),
                es_lider=cp.es_lider,
                porcentaje=float(cp.porcentaje or 0),
            )
            for cp in poliza.coaseguro_participaciones
        ],
        ubicaciones=[
            UbicacionOut(
                id=ub.id,
                nombre=ub.nombre,
                direccion=ub.direccion,
                suma_asegurada_uf=float(ub.suma_asegurada or 0),
                porcentaje=float(ub.porcentaje or 0),
            )
            for ub in poliza.ubicaciones
        ],
        coberturas=coberturas,
        exclusiones=exclusiones,
        deducible_texto=poliza.deducible_texto,
        limite_indemnizacion_uf=(
            float(poliza.limite_indemnizacion)
            if poliza.limite_indemnizacion is not None
            else None
        ),
        plan_pago=PlanPago(cuotas=poliza.plan_pago_cuotas, metodo=poliza.plan_pago_metodo),
        pago_url=poliza.pago_url,
        documentos=[DocumentoOut.model_validate(d) for d in documentos],
        observaciones=[ObservacionOut.model_validate(o) for o in observaciones],
    )


# --- endpoints --------------------------------------------------------------
@router.get("", response_model=PolizaListResponse)
def list_polizas(
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
    q: str | None = Query(default=None),
    estado: PolizaEstadoEnum | None = Query(default=None),
    cliente_id: int | None = Query(default=None),
    ramo_id: int | None = Query(default=None),
    aseguradora_id: int | None = Query(default=None),
    tiene_coaseguro: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> PolizaListResponse:
    """List pólizas for the tenant with filters + pagination."""
    filters = [Poliza.corredora_id == corredora_id]
    if estado is not None:
        filters.append(Poliza.estado == estado)
    if cliente_id is not None:
        filters.append(Poliza.cliente_id == cliente_id)
    if ramo_id is not None:
        filters.append(Poliza.ramo_id == ramo_id)
    if aseguradora_id is not None:
        filters.append(Poliza.aseguradora_id == aseguradora_id)
    if tiene_coaseguro is not None:
        filters.append(Poliza.tiene_coaseguro == tiene_coaseguro)
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                Poliza.numero_poliza.ilike(like),
                Poliza.cliente.has(Cliente.nombre.ilike(like)),
            )
        )

    total = db.execute(
        select(func.count()).select_from(Poliza).where(*filters)
    ).scalar_one()

    rows = db.execute(
        select(Poliza)
        .where(*filters)
        .options(
            selectinload(Poliza.cliente),
            selectinload(Poliza.ramo),
            selectinload(Poliza.aseguradora),
        )
        .order_by(Poliza.vigencia_fin.desc(), Poliza.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return PolizaListResponse(
        items=[_list_item(p) for p in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/summary", response_model=PolizaSummary)
def polizas_summary(
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> PolizaSummary:
    """KPI aggregates for the list header."""
    vigentes_filter = (
        Poliza.corredora_id == corredora_id,
        Poliza.estado == PolizaEstadoEnum.vigente,
    )

    polizas_vigentes = db.execute(
        select(func.count()).select_from(Poliza).where(*vigentes_filter)
    ).scalar_one()

    prima_total = db.execute(
        select(func.coalesce(func.sum(Poliza.prima), 0)).where(*vigentes_filter)
    ).scalar_one()

    suma_asegurada_total = db.execute(
        select(func.coalesce(func.sum(Poliza.suma_asegurada), 0)).where(*vigentes_filter)
    ).scalar_one()

    con_coaseguro = db.execute(
        select(func.count())
        .select_from(Poliza)
        .where(*vigentes_filter, Poliza.tiene_coaseguro.is_(True))
    ).scalar_one()

    return PolizaSummary(
        polizas_vigentes=int(polizas_vigentes),
        prima_total_uf=float(prima_total or 0),
        suma_asegurada_total_uf=float(suma_asegurada_total or 0),
        con_coaseguro=int(con_coaseguro),
    )


@router.get("/{poliza_id}", response_model=PolizaDetail)
def get_poliza(
    poliza_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(get_current_user),
) -> PolizaDetail:
    """Full póliza detail bundling coaseguro, ubicaciones, coberturas, condiciones."""
    poliza = _get_owned_poliza(db, poliza_id, corredora_id)
    return _build_detail(db, poliza, corredora_id)


@router.post("", response_model=PolizaDetail, status_code=status.HTTP_201_CREATED)
def create_poliza(
    payload: PolizaCreate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_corredora_staff),
) -> PolizaDetail:
    """Create a póliza plus its nested collections, tenant-scoped."""
    _validate_refs(db, corredora_id, payload.cliente_id, payload.ramo_id, payload.aseguradora_id)

    data = payload.model_dump()
    child_keys = ("coaseguro_participaciones", "ubicaciones", "coberturas", "exclusiones")
    for key in child_keys:
        data.pop(key, None)

    poliza = Poliza(corredora_id=corredora_id, **data)
    _sync_children(db, poliza, payload)

    db.add(poliza)
    db.commit()
    db.refresh(poliza)
    return _build_detail(db, poliza, corredora_id)


@router.patch("/{poliza_id}", response_model=PolizaDetail)
def update_poliza(
    poliza_id: int,
    payload: PolizaUpdate,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_corredora_staff),
) -> PolizaDetail:
    """Partial update; nested collections are replaced when present in the body."""
    poliza = _get_owned_poliza(db, poliza_id, corredora_id)

    data = payload.model_dump(exclude_unset=True)
    _validate_refs(
        db,
        corredora_id,
        data.get("cliente_id"),
        data.get("ramo_id"),
        data.get("aseguradora_id"),
    )

    child_keys = ("coaseguro_participaciones", "ubicaciones", "coberturas", "exclusiones")
    scalar_data = {k: v for k, v in data.items() if k not in child_keys}
    for field, value in scalar_data.items():
        setattr(poliza, field, value)

    _sync_children(db, poliza, {k: data[k] for k in child_keys if k in data})

    db.commit()
    db.refresh(poliza)
    return _build_detail(db, poliza, corredora_id)


@router.delete("/{poliza_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_poliza(
    poliza_id: int,
    db: Session = Depends(get_db),
    corredora_id: int = Depends(get_current_corredora_id),
    _: Usuario = Depends(require_corredora_staff),
) -> Response:
    """Delete a póliza (cascades to its nested children)."""
    poliza = _get_owned_poliza(db, poliza_id, corredora_id)
    db.delete(poliza)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _validate_refs(
    db: Session,
    corredora_id: int,
    cliente_id: int | None,
    ramo_id: int | None,
    aseguradora_id: int | None,
) -> None:
    """Ensure referenced cliente/ramo/aseguradora belong to the tenant."""
    if cliente_id is not None:
        cliente = db.get(Cliente, cliente_id)
        if cliente is None or cliente.corredora_id != corredora_id:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
    if ramo_id is not None:
        ramo = db.get(Ramo, ramo_id)
        if ramo is None or ramo.corredora_id != corredora_id:
            raise HTTPException(status_code=404, detail="Ramo no encontrado")
    if aseguradora_id is not None:
        aseguradora = db.get(Aseguradora, aseguradora_id)
        if aseguradora is None or aseguradora.corredora_id != corredora_id:
            raise HTTPException(status_code=404, detail="Aseguradora no encontrada")
