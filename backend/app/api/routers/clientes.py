"""Clientes router — NIVEL-1 expediente CRUD, list KPIs and detail bundle.

Every query is scoped to the authenticated user's corredora_id (multi-tenant).
Builds strictly to docs/api-contract.md § Clientes and docs/data-model.md.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.activo import Activo
from app.models.asegurado_adicional import AseguradoAdicional
from app.models.aseguradora import Aseguradora
from app.models.cliente import Cliente, ClienteEstadoEnum
from app.models.colaboracion import Actividad, Documento
from app.models.cotizacion import Cotizacion
from app.models.poliza import CoaseguroParticipacion, Poliza, PolizaEstadoEnum
from app.models.ramo import Ramo
from app.models.siniestro import Siniestro
from app.models.usuario import Usuario
from app.schemas.clientes import (
    ActividadItem,
    AseguradoAdicionalItem,
    AseguradoraRef,
    ClienteCabecera,
    ClienteCreate,
    ClienteCuadros,
    ClienteDetail,
    ClienteDetailKpis,
    ClienteListItem,
    ClientesKpis,
    ClientesListResponse,
    ClienteUpdate,
    CotizacionItem,
    CuadroAseguradoras,
    CuadroContacto,
    CuadroEjecutivo,
    CuadroPrima,
    CuadroTelefono,
    DocumentoItem,
    EjecutivoRef,
    PolizaVigenteItem,
    RamoRef,
    SiniestroItem,
)

router = APIRouter(prefix="/clientes", tags=["clientes"])


# --- Helpers ---------------------------------------------------------------
def _cobertura_estado(pct: float | None) -> str:
    """Coverage analyzer: <95 infracobertura, 95-105 optima, >105 sobrecobertura."""
    value = float(pct or 0)
    if value < 95:
        return "infracobertura"
    if value <= 105:
        return "optima"
    return "sobrecobertura"


def _get_cliente_or_404(db: Session, corredora_id: int, cliente_id: int) -> Cliente:
    cliente = db.scalar(
        select(Cliente).where(
            Cliente.id == cliente_id, Cliente.corredora_id == corredora_id
        )
    )
    if cliente is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado"
        )
    return cliente


def _ejecutivo_ref(db: Session, corredora_id: int, ejecutivo_id: int | None) -> EjecutivoRef | None:
    if ejecutivo_id is None:
        return None
    usuario = db.scalar(
        select(Usuario).where(
            Usuario.id == ejecutivo_id, Usuario.corredora_id == corredora_id
        )
    )
    if usuario is None:
        return None
    return EjecutivoRef(id=usuario.id, nombre=usuario.nombre)


# --- List with KPI aggregates ----------------------------------------------
@router.get("", response_model=ClientesListResponse)
def list_clientes(
    q: str | None = Query(default=None),
    estado: ClienteEstadoEnum | None = Query(default=None),
    sector: str | None = Query(default=None),
    ejecutivo_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> ClientesListResponse:
    """Paginated clientes list (tenant-scoped) plus cartera KPI aggregates."""
    corredora_id = current_user.corredora_id

    filters = [Cliente.corredora_id == corredora_id]
    if q:
        like = f"%{q}%"
        filters.append(or_(Cliente.nombre.ilike(like), Cliente.rut.ilike(like)))
    if estado is not None:
        filters.append(Cliente.estado == estado)
    if sector:
        filters.append(Cliente.sector == sector)
    if ejecutivo_id is not None:
        filters.append(Cliente.ejecutivo_id == ejecutivo_id)

    total = db.scalar(select(func.count()).select_from(Cliente).where(*filters)) or 0

    rows = db.scalars(
        select(Cliente)
        .where(*filters)
        .order_by(Cliente.nombre.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    # Per-row aggregates: vigentes count + prima total (only vigente pólizas).
    ejecutivos: dict[int, EjecutivoRef] = {}
    items: list[ClienteListItem] = []
    for c in rows:
        agg = db.execute(
            select(
                func.count(Poliza.id),
                func.coalesce(func.sum(Poliza.prima), 0),
            ).where(
                Poliza.corredora_id == corredora_id,
                Poliza.cliente_id == c.id,
                Poliza.estado == PolizaEstadoEnum.vigente,
            )
        ).one()
        n_vigentes, prima_total = int(agg[0] or 0), float(agg[1] or 0)

        ejecutivo_ref: EjecutivoRef | None = None
        if c.ejecutivo_id is not None:
            if c.ejecutivo_id not in ejecutivos:
                usuario = db.get(Usuario, c.ejecutivo_id)
                if usuario is not None and usuario.corredora_id == corredora_id:
                    ejecutivos[c.ejecutivo_id] = EjecutivoRef(
                        id=usuario.id, nombre=usuario.nombre
                    )
            ejecutivo_ref = ejecutivos.get(c.ejecutivo_id)

        items.append(
            ClienteListItem(
                id=c.id,
                nombre=c.nombre,
                rut=c.rut,
                sector=c.sector,
                estado=c.estado,
                contacto_principal=c.contacto_principal,
                telefono=c.telefono,
                email=c.email,
                ejecutivo=ejecutivo_ref,
                polizas_vigentes=n_vigentes,
                prima_total_uf=prima_total,
                fecha_alta=c.fecha_alta,
            )
        )

    # Cartera KPIs across the whole tenant (not just the page).
    estado_counts = db.execute(
        select(Cliente.estado, func.count(Cliente.id))
        .where(Cliente.corredora_id == corredora_id)
        .group_by(Cliente.estado)
    ).all()
    counts_by_estado = {estado_val: int(n) for estado_val, n in estado_counts}
    total_clientes = sum(counts_by_estado.values())

    prima_cartera = db.scalar(
        select(func.coalesce(func.sum(Poliza.prima), 0)).where(
            Poliza.corredora_id == corredora_id,
            Poliza.estado == PolizaEstadoEnum.vigente,
        )
    ) or 0

    kpis = ClientesKpis(
        total=total_clientes,
        activos=counts_by_estado.get(ClienteEstadoEnum.activo, 0),
        onboarding=counts_by_estado.get(ClienteEstadoEnum.onboarding, 0),
        prospectos=counts_by_estado.get(ClienteEstadoEnum.prospecto, 0),
        prima_total_cartera_uf=float(prima_cartera),
    )

    return ClientesListResponse(
        items=items, total=total, page=page, page_size=page_size, kpis=kpis
    )


# --- Detail bundle ---------------------------------------------------------
@router.get("/{cliente_id}", response_model=ClienteDetail)
def get_cliente(
    cliente_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> ClienteDetail:
    """NIVEL-1 detail: cabecera, 5 cuadros, and nested operational bundles."""
    corredora_id = current_user.corredora_id
    cliente = _get_cliente_or_404(db, corredora_id, cliente_id)

    # Vigente pólizas for this cliente.
    polizas = db.scalars(
        select(Poliza)
        .where(
            Poliza.corredora_id == corredora_id,
            Poliza.cliente_id == cliente_id,
            Poliza.estado == PolizaEstadoEnum.vigente,
        )
        .order_by(Poliza.vigencia_fin.asc())
    ).all()

    # Ramo / aseguradora reference lookups (batched, tenant-scoped).
    ramo_ids = {p.ramo_id for p in polizas if p.ramo_id is not None}
    ramos: dict[int, RamoRef] = {}
    if ramo_ids:
        for r in db.scalars(
            select(Ramo).where(
                Ramo.corredora_id == corredora_id, Ramo.id.in_(ramo_ids)
            )
        ).all():
            ramos[r.id] = RamoRef(id=r.id, nombre=r.nombre)

    # Aseguradoras associated to the cliente: líderes + coaseguro participants.
    poliza_ids = [p.id for p in polizas]
    aseguradora_ids: set[int] = {p.aseguradora_id for p in polizas if p.aseguradora_id}
    if poliza_ids:
        for a_id in db.scalars(
            select(CoaseguroParticipacion.aseguradora_id).where(
                CoaseguroParticipacion.poliza_id.in_(poliza_ids)
            )
        ).all():
            if a_id is not None:
                aseguradora_ids.add(a_id)

    aseguradoras: dict[int, AseguradoraRef] = {}
    if aseguradora_ids:
        for a in db.scalars(
            select(Aseguradora).where(
                Aseguradora.corredora_id == corredora_id,
                Aseguradora.id.in_(aseguradora_ids),
            )
        ).all():
            aseguradoras[a.id] = AseguradoraRef(id=a.id, nombre=a.nombre)

    polizas_vigentes = [
        PolizaVigenteItem(
            id=p.id,
            numero_poliza=p.numero_poliza,
            ramo=ramos.get(p.ramo_id),
            aseguradora=aseguradoras.get(p.aseguradora_id),
            tiene_coaseguro=bool(p.tiene_coaseguro),
            prima_uf=float(p.prima or 0),
            suma_asegurada_uf=float(p.suma_asegurada or 0),
            tipo_cobertura=p.tipo_cobertura.value
            if hasattr(p.tipo_cobertura, "value")
            else str(p.tipo_cobertura),
            cobertura_pct=float(p.cobertura_pct or 0),
            cobertura_estado=_cobertura_estado(p.cobertura_pct),
            vigencia_inicio=p.vigencia_inicio,
            vigencia_fin=p.vigencia_fin,
            estado=p.estado.value if hasattr(p.estado, "value") else str(p.estado),
        )
        for p in polizas
    ]

    prima_total = sum(float(p.prima or 0) for p in polizas)

    # Cuadro 5: ejecutivo.
    ejecutivo_ref = _ejecutivo_ref(db, corredora_id, cliente.ejecutivo_id)

    cuadros = ClienteCuadros(
        contacto=CuadroContacto(
            contacto_principal=cliente.contacto_principal, email=cliente.email
        ),
        telefono=CuadroTelefono(telefono=cliente.telefono),
        prima=CuadroPrima(prima_total_uf=prima_total, n_polizas=len(polizas)),
        aseguradoras=CuadroAseguradoras(
            aseguradoras_asociadas=sorted(
                aseguradoras.values(), key=lambda a: a.nombre
            )
        ),
        ejecutivo=CuadroEjecutivo(ejecutivo=ejecutivo_ref),
    )

    # Asegurados adicionales.
    asegurados = db.scalars(
        select(AseguradoAdicional)
        .where(
            AseguradoAdicional.corredora_id == corredora_id,
            AseguradoAdicional.cliente_id == cliente_id,
        )
        .order_by(AseguradoAdicional.id.asc())
    ).all()
    asegurados_adicionales = [
        AseguradoAdicionalItem(
            id=a.id,
            rut=a.rut,
            entidad=a.entidad,
            tipo_seguro=a.tipo_seguro,
            relacion_bien=a.relacion_bien,
            poliza_id=a.poliza_id,
        )
        for a in asegurados
    ]

    # Cotizaciones.
    cotizaciones_rows = db.scalars(
        select(Cotizacion)
        .where(
            Cotizacion.corredora_id == corredora_id,
            Cotizacion.cliente_id == cliente_id,
        )
        .order_by(Cotizacion.id.desc())
    ).all()
    cotiz_ramo_ids = {c.ramo_id for c in cotizaciones_rows if c.ramo_id is not None}
    missing = cotiz_ramo_ids - set(ramos.keys())
    if missing:
        for r in db.scalars(
            select(Ramo).where(
                Ramo.corredora_id == corredora_id, Ramo.id.in_(missing)
            )
        ).all():
            ramos[r.id] = RamoRef(id=r.id, nombre=r.nombre)
    cotizaciones = [
        CotizacionItem(
            id=c.id,
            ramo=ramos.get(c.ramo_id),
            bien_asegurar=c.bien_asegurar,
            valor_declarado_uf=float(c.valor_declarado or 0),
            fecha_envio=c.fecha_envio,
            fecha_vence=c.fecha_vence,
            prioridad=c.prioridad.value
            if hasattr(c.prioridad, "value")
            else str(c.prioridad),
            estado=c.estado.value if hasattr(c.estado, "value") else str(c.estado),
        )
        for c in cotizaciones_rows
    ]

    # Documentos attached to this cliente (polymorphic).
    documentos_rows = db.scalars(
        select(Documento)
        .where(
            Documento.corredora_id == corredora_id,
            Documento.entidad_tipo == "cliente",
            Documento.entidad_id == cliente_id,
        )
        .order_by(Documento.created_at.desc())
    ).all()
    documentos = [
        DocumentoItem(
            id=d.id,
            nombre=d.nombre,
            tipo=d.tipo,
            url=d.url,
            version=d.version,
            autor_id=d.autor_id,
            created_at=d.created_at,
        )
        for d in documentos_rows
    ]

    # Siniestros (summary).
    siniestros_rows = db.scalars(
        select(Siniestro)
        .where(
            Siniestro.corredora_id == corredora_id,
            Siniestro.cliente_id == cliente_id,
        )
        .order_by(Siniestro.fecha_evento.desc())
    ).all()
    siniestros = [
        SiniestroItem(
            id=s.id,
            poliza_id=s.poliza_id,
            tipo=s.tipo,
            fecha_evento=s.fecha_evento,
            estado=s.estado.value if hasattr(s.estado, "value") else str(s.estado),
            monto_estimado_uf=float(s.monto_estimado)
            if s.monto_estimado is not None
            else None,
            monto_liquidado_uf=float(s.monto_liquidado)
            if s.monto_liquidado is not None
            else None,
            fecha_liquidacion=s.fecha_liquidacion,
        )
        for s in siniestros_rows
    ]

    # Historial de actividad for this cliente.
    actividad_rows = db.scalars(
        select(Actividad)
        .where(
            Actividad.corredora_id == corredora_id,
            Actividad.entidad_tipo == "cliente",
            Actividad.entidad_id == cliente_id,
        )
        .order_by(Actividad.created_at.desc())
        .limit(50)
    ).all()
    actor_ids = {a.usuario_id for a in actividad_rows if a.usuario_id is not None}
    actores: dict[int, Usuario] = {}
    if actor_ids:
        for u in db.scalars(
            select(Usuario).where(Usuario.id.in_(actor_ids))
        ).all():
            actores[u.id] = u
    historial_actividad = [
        ActividadItem(
            id=a.id,
            accion=a.accion,
            descripcion=a.descripcion,
            usuario=actores[a.usuario_id].nombre
            if a.usuario_id in actores
            else None,
            rol=(
                actores[a.usuario_id].rol.value
                if a.usuario_id in actores
                and hasattr(actores[a.usuario_id].rol, "value")
                else (str(actores[a.usuario_id].rol) if a.usuario_id in actores else None)
            ),
            created_at=a.created_at,
        )
        for a in actividad_rows
    ]

    # Cartera rollups for the detail header (contract § GET /clientes/{id}.kpis).
    suma_asegurada = sum(float(p.suma_asegurada or 0) for p in polizas)
    activos_count = db.scalar(
        select(func.count(Activo.id)).where(
            Activo.corredora_id == corredora_id,
            Activo.cliente_id == cliente_id,
        )
    ) or 0
    _TERMINAL_SINIESTRO = {"liquidado", "cerrado"}
    siniestros_abiertos = sum(
        1 for s in siniestros if str(s.estado) not in _TERMINAL_SINIESTRO
    )

    detail_kpis = ClienteDetailKpis(
        polizas_vigentes=len(polizas),
        prima_total_uf=prima_total,
        suma_asegurada_uf=suma_asegurada,
        activos_count=int(activos_count),
        siniestros_abiertos=siniestros_abiertos,
    )

    return ClienteDetail(
        # Flat contract fields (read by the frontend cliente detail page).
        id=cliente.id,
        nombre=cliente.nombre,
        rut=cliente.rut,
        sector=cliente.sector,
        estado=cliente.estado,
        contacto_principal=cliente.contacto_principal,
        telefono=cliente.telefono,
        email=cliente.email,
        ejecutivo=ejecutivo_ref,
        prima_total_uf=prima_total,
        fecha_alta=cliente.fecha_alta,
        kpis=detail_kpis,
        # Presentation blocks (retained).
        cabecera=ClienteCabecera.model_validate(cliente),
        cuadros=cuadros,
        # Nested bundles (contract names + backward-compatible aliases).
        polizas_vigentes=polizas_vigentes,
        polizas=polizas_vigentes,
        asegurados_adicionales=asegurados_adicionales,
        asegurados_adicionales_items=asegurados_adicionales,
        cotizaciones=cotizaciones,
        documentos=documentos,
        siniestros=siniestros,
        historial_actividad=historial_actividad,
        actividad=historial_actividad,
    )


# --- Create ----------------------------------------------------------------
@router.post("", response_model=ClienteDetail, status_code=status.HTTP_201_CREATED)
def create_cliente(
    body: ClienteCreate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> ClienteDetail:
    """Create a cliente; corredora_id is injected from the JWT (never the body)."""
    corredora_id = current_user.corredora_id

    if body.ejecutivo_id is not None:
        ejecutivo = db.scalar(
            select(Usuario).where(
                Usuario.id == body.ejecutivo_id,
                Usuario.corredora_id == corredora_id,
            )
        )
        if ejecutivo is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El ejecutivo indicado no pertenece a la corredora",
            )

    cliente = Cliente(
        corredora_id=corredora_id,
        nombre=body.nombre,
        rut=body.rut,
        sector=body.sector,
        estado=body.estado,
        contacto_principal=body.contacto_principal,
        telefono=body.telefono,
        email=str(body.email) if body.email is not None else None,
        ejecutivo_id=body.ejecutivo_id,
        fecha_alta=body.fecha_alta,
    )
    db.add(cliente)
    db.flush()

    db.add(
        Actividad(
            corredora_id=corredora_id,
            usuario_id=current_user.id,
            accion="Cliente creado",
            entidad_tipo="cliente",
            entidad_id=cliente.id,
            descripcion=f"Se creó el cliente {cliente.nombre}",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    return get_cliente(cliente.id, db=db, current_user=current_user)


# --- Update ----------------------------------------------------------------
@router.patch("/{cliente_id}", response_model=ClienteDetail)
def update_cliente(
    cliente_id: int,
    body: ClienteUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> ClienteDetail:
    """Partial update of a cliente (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    cliente = _get_cliente_or_404(db, corredora_id, cliente_id)

    data = body.model_dump(exclude_unset=True)

    if "ejecutivo_id" in data and data["ejecutivo_id"] is not None:
        ejecutivo = db.scalar(
            select(Usuario).where(
                Usuario.id == data["ejecutivo_id"],
                Usuario.corredora_id == corredora_id,
            )
        )
        if ejecutivo is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El ejecutivo indicado no pertenece a la corredora",
            )

    for field, value in data.items():
        if field == "email" and value is not None:
            value = str(value)
        setattr(cliente, field, value)

    db.add(
        Actividad(
            corredora_id=corredora_id,
            usuario_id=current_user.id,
            accion="Cliente actualizado",
            entidad_tipo="cliente",
            entidad_id=cliente.id,
            descripcion=f"Se actualizó el cliente {cliente.nombre}",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    return get_cliente(cliente.id, db=db, current_user=current_user)


# --- Delete (soft) ---------------------------------------------------------
@router.delete("/{cliente_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cliente(
    cliente_id: int,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Response:
    """Soft delete: sets estado=archivado (tenant-scoped)."""
    corredora_id = current_user.corredora_id
    cliente = _get_cliente_or_404(db, corredora_id, cliente_id)

    cliente.estado = ClienteEstadoEnum.archivado
    db.add(
        Actividad(
            corredora_id=corredora_id,
            usuario_id=current_user.id,
            accion="Cliente archivado",
            entidad_tipo="cliente",
            entidad_id=cliente.id,
            descripcion=f"Se archivó el cliente {cliente.nombre}",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
