"""``/assets`` — insurable objects (bienes asegurables), owned by a client.

Two routers are exported:

- :data:`router` — the flat resource (``/assets``), for the cross-client list
  view and for get/patch/delete by id.
- :data:`client_assets_router` — the nested form
  (``/clients/{client_id}/assets``), which is how the client detail page lists
  and creates them. Both share the same handlers and the same tenant scoping.

Storage is hybrid: the ten promoted underwriting columns are written as columns,
the type-specific tail goes to ``attributes`` JSON. A PATCH of ``attributes``
REPLACES the JSON object wholesale — it is a document, not a set of columns, so
a partial merge would make deleting a key impossible.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import (
    OPEN_PLACEMENT_STATUSES,
    count_total,
    counts_by,
    get_client_or_404,
    paginate,
    record_activity,
)
from app.models.asset import Asset, AssetStatus
from app.models.client import Client
from app.models.enums import EntityType
from app.models.inspection import Inspection
from app.models.placement import Placement
from app.models.user import User
from app.schemas.asset import (
    AssetClientSummary,
    AssetCreate,
    AssetListItem,
    AssetPage,
    AssetRead,
    AssetSummary,
    AssetUpdate,
)

router = APIRouter(prefix="/assets", tags=["assets"])
client_assets_router = APIRouter(prefix="/clients/{client_id}/assets", tags=["assets"])


# --- Helpers -----------------------------------------------------------------


def _get_asset_or_404(db: Session, asset_id: int, broker_id: int) -> Asset:
    """Fetch an asset inside the tenant, or 404 (never leak another broker's)."""
    asset = db.execute(
        select(Asset)
        .where(Asset.id == asset_id, Asset.broker_id == broker_id)
        .options(selectinload(Asset.client).selectinload(Client.insured))
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _client_summary(client: Client | None) -> AssetClientSummary | None:
    if client is None:
        return None
    insured = client.insured
    return AssetClientSummary(
        id=client.id,
        legal_name=insured.legal_name if insured else None,
        rut=insured.rut if insured else None,
    )


def _asset_counts(db: Session, asset_ids: list[int]) -> dict[int, dict[str, int]]:
    """Per-asset placement counters, in two grouped queries."""
    zero = {"placements_count": 0, "active_placements_count": 0}
    out: dict[int, dict[str, int]] = {aid: dict(zero) for aid in asset_ids}
    if not asset_ids:
        return out

    placements = counts_by(
        db,
        select(Placement.asset_id, func.count(Placement.id))
        .where(Placement.asset_id.in_(asset_ids))
        .group_by(Placement.asset_id),
    )
    active = counts_by(
        db,
        select(Placement.asset_id, func.count(Placement.id))
        .where(
            Placement.asset_id.in_(asset_ids),
            Placement.status.in_(OPEN_PLACEMENT_STATUSES),
        )
        .group_by(Placement.asset_id),
    )
    for aid in asset_ids:
        out[aid]["placements_count"] = placements.get(aid, 0)
        out[aid]["active_placements_count"] = active.get(aid, 0)
    return out


def _serialize(asset: Asset, counts: dict[str, int], *, detail: bool = False):
    model = AssetRead if detail else AssetListItem
    return model.model_validate(asset).model_copy(
        update={**counts, "client": _client_summary(asset.client)}
    )


def _detail_response(db: Session, asset: Asset) -> AssetRead:
    counts = _asset_counts(db, [asset.id])[asset.id]
    counts["inspections_count"] = db.execute(
        select(func.count(Inspection.id)).where(Inspection.asset_id == asset.id)
    ).scalar_one()
    return _serialize(asset, counts, detail=True)


def _list_assets(
    db: Session,
    broker_id: int,
    *,
    client_id: int | None,
    q: str | None,
    asset_type: str | None,
    status_filter: list[AssetStatus] | None,
    commune: str | None,
    region: str | None,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> AssetPage:
    """The single implementation behind both the flat and the nested list."""
    stmt = select(Asset).where(Asset.broker_id == broker_id)

    if client_id is not None:
        stmt = stmt.where(Asset.client_id == client_id)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Asset.name.ilike(needle),
                Asset.address.ilike(needle),
                Asset.commune.ilike(needle),
                Asset.activity.ilike(needle),
            )
        )
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if status_filter:
        stmt = stmt.where(Asset.status.in_(status_filter))
    if commune:
        stmt = stmt.where(Asset.commune == commune)
    if region:
        stmt = stmt.where(Asset.region == region)

    total = count_total(db, stmt)

    sort_column = {
        "name": Asset.name,
        "created_at": Asset.created_at,
        "asset_type": Asset.asset_type,
        "built_area_m2": Asset.built_area_m2,
    }[sort]
    stmt = stmt.order_by(
        sort_column.asc() if order == "asc" else sort_column.desc(), Asset.id.asc()
    )

    rows = (
        db.execute(
            stmt.options(selectinload(Asset.client).selectinload(Client.insured))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    counts = _asset_counts(db, [row.id for row in rows])
    return AssetPage(
        items=[_serialize(row, counts[row.id]) for row in rows],
        **paginate(total, page, page_size),
    )


def _create_asset(
    db: Session, broker_id: int, current_user: User, client_id: int, payload: AssetCreate
) -> AssetRead:
    """Create an asset under a client that belongs to this broker."""
    client = get_client_or_404(db, client_id, broker_id)

    data = payload.model_dump(exclude={"client_id"})
    asset = Asset(broker_id=broker_id, client_id=client.id, **data)
    db.add(asset)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="asset.created",
        entity_type=EntityType.ASSET,
        entity_id=asset.id,
        description=f"Bien asegurable {asset.name} creado",
        meta={"client_id": client.id, "asset_type": asset.asset_type},
    )
    db.commit()
    db.refresh(asset)
    return _detail_response(db, asset)


# --- Flat resource -----------------------------------------------------------


@router.get("/summary", response_model=AssetSummary)
def get_assets_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Assets", "View")),
    client_id: int | None = None,
) -> AssetSummary:
    """Counters the assets list header needs, optionally for one client."""
    scope = [Asset.broker_id == broker_id]
    if client_id is not None:
        scope.append(Asset.client_id == client_id)

    by_status_rows = db.execute(
        select(Asset.status, func.count(Asset.id)).where(*scope).group_by(Asset.status)
    ).all()
    by_type_rows = db.execute(
        select(Asset.asset_type, func.count(Asset.id))
        .where(*scope)
        .group_by(Asset.asset_type)
    ).all()

    by_status = {str(key): value for key, value in by_status_rows}
    without_placements = db.execute(
        select(func.count(Asset.id)).where(
            *scope,
            ~Asset.id.in_(select(Placement.asset_id).where(Placement.broker_id == broker_id)),
        )
    ).scalar_one()

    return AssetSummary(
        total=sum(by_status.values()),
        by_status={s.value: by_status.get(s.value, 0) for s in AssetStatus},
        by_type={str(key): value for key, value in by_type_rows},
        without_placements=without_placements,
    )


@router.get("", response_model=AssetPage)
def list_assets(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Assets", "View")),
    client_id: int | None = None,
    q: str | None = Query(None, description="Search name, address, commune or activity"),
    asset_type: str | None = None,
    status_filter: list[AssetStatus] | None = Query(None, alias="status"),
    commune: str | None = None,
    region: str | None = None,
    sort: Literal["name", "created_at", "asset_type", "built_area_m2"] = "name",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> AssetPage:
    """List the broker's assets across every client."""
    return _list_assets(
        db,
        broker_id,
        client_id=client_id,
        q=q,
        asset_type=asset_type,
        status_filter=status_filter,
        commune=commune,
        region=region,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
def create_asset(
    payload: AssetCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Assets", "Create")),
) -> AssetRead:
    """Create an asset. ``client_id`` is required on this flat route."""
    if payload.client_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_id is required",
        )
    return _create_asset(db, broker_id, current_user, payload.client_id, payload)


@router.get("/{asset_id}", response_model=AssetRead)
def get_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Assets", "View")),
) -> AssetRead:
    """One asset, with its promoted columns, its JSON tail and its counters."""
    asset = _get_asset_or_404(db, asset_id, broker_id)
    return _detail_response(db, asset)


@router.patch("/{asset_id}", response_model=AssetRead)
def update_asset(
    asset_id: int,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Assets", "Edit")),
) -> AssetRead:
    """Patch an asset. ``attributes`` is replaced wholesale when supplied."""
    asset = _get_asset_or_404(db, asset_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _detail_response(db, asset)

    for field, value in changes.items():
        setattr(asset, field, value)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="asset.updated",
        entity_type=EntityType.ASSET,
        entity_id=asset.id,
        description=f"Bien asegurable {asset.name} actualizado",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(asset)
    return _detail_response(db, asset)


@router.delete(
    "/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Assets", "Delete")),
) -> Response:
    """Delete an asset. Refused (409) while it carries placements."""
    asset = _get_asset_or_404(db, asset_id, broker_id)

    placements = db.execute(
        select(func.count(Placement.id)).where(Placement.asset_id == asset.id)
    ).scalar_one()
    if placements:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Asset has {placements} placements — set status=inactive instead",
        )

    name = asset.name
    db.delete(asset)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="asset.deleted",
        entity_type=EntityType.ASSET,
        entity_id=asset_id,
        description=f"Bien asegurable {name} eliminado",
        meta={"name": name},
    )
    db.commit()


# --- Nested under a client ---------------------------------------------------


@client_assets_router.get("", response_model=AssetPage)
def list_client_assets(
    client_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Assets", "View")),
    q: str | None = None,
    asset_type: str | None = None,
    status_filter: list[AssetStatus] | None = Query(None, alias="status"),
    sort: Literal["name", "created_at", "asset_type", "built_area_m2"] = "name",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> AssetPage:
    """List one client's assets (404 if the client is not this broker's)."""
    get_client_or_404(db, client_id, broker_id)
    return _list_assets(
        db,
        broker_id,
        client_id=client_id,
        q=q,
        asset_type=asset_type,
        status_filter=status_filter,
        commune=None,
        region=None,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )


@client_assets_router.post("", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
def create_client_asset(
    client_id: int,
    payload: AssetCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Assets", "Create")),
) -> AssetRead:
    """Create an asset under this client. A conflicting body ``client_id`` is 422."""
    if payload.client_id is not None and payload.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_id in the body does not match the path",
        )
    return _create_asset(db, broker_id, current_user, client_id, payload)


__all__ = ["router", "client_assets_router"]
