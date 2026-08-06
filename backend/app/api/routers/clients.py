"""``/clients`` — the broker's book of business.

A client is the broker-private CRM row that points at the CANONICAL ``insured``
(keyed by RUT). Creating one therefore takes a RUT, not an ``insured_id``:

1. validate mod-11 and normalize to ``BODY-DV``;
2. find-or-create the canonical insured for that RUT;
3. create the broker's own ``client`` row linked to it;
4. write an ``activity`` audit row.

**The broker is never blocked** — there is no access code and no approval step
(v2-architecture.md §4.1). Confidentiality comes from tenant scoping alone:
every query below filters on the authenticated user's ``broker_id``.

This module also exposes :func:`record_activity`, the audit helper the rest of
the clients/assets/placements slice uses.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.models.activity import Activity
from app.models.asset import Asset
from app.models.client import Client, ClientStatus
from app.models.enums import EntityType
from app.models.insured import Insured
from app.models.placement import Placement, PlacementStatus
from app.models.policy import Policy
from app.models.user import User
from app.schemas.client import (
    ClientCreate,
    ClientListItem,
    ClientPage,
    ClientRead,
    ClientSummary,
    ClientUpdate,
)

router = APIRouter(prefix="/clients", tags=["clients"])

# Placement statuses that count as "live work" on a client.
OPEN_PLACEMENT_STATUSES = (
    PlacementStatus.INSPECTION,
    PlacementStatus.PRE_UNDERWRITING,
    PlacementStatus.QUOTING,
    PlacementStatus.NEGOTIATING,
    PlacementStatus.AWARDED,
    PlacementStatus.ACTIVE,
)


# --- Shared helpers (also used by assets.py / placements.py) -----------------


def record_activity(
    db: Session,
    *,
    broker_id: int,
    user: User | None,
    action: str,
    entity_type: EntityType,
    entity_id: int | None,
    description: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Activity:
    """Append one audit row. Added to the session; the caller commits."""
    activity = Activity(
        broker_id=broker_id,
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        meta=meta,
    )
    db.add(activity)
    return activity


def paginate(total: int, page: int, page_size: int) -> dict[str, int]:
    """The page envelope every list endpoint in this slice returns."""
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)) if total else 0,
    }


def count_total(db: Session, stmt: Select) -> int:
    """Count the rows a filtered SELECT would return, without its ORDER BY.

    Wrapping the statement in a subquery keeps this correct for any set of
    joins/filters and portable across SQLite and MySQL.
    """
    return db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()


def counts_by(db: Session, stmt: Select) -> dict[int, int]:
    """Run a ``(key, count)`` SELECT and return it as a dict."""
    return {key: value for key, value in db.execute(stmt).all()}


def get_client_or_404(db: Session, client_id: int, broker_id: int) -> Client:
    """Fetch a client inside the tenant, or 404.

    A client belonging to another broker is reported as 404, never 403: the
    existence of another tenant's row must not leak.
    """
    client = db.execute(
        select(Client)
        .where(Client.id == client_id, Client.broker_id == broker_id)
        .options(selectinload(Client.insured), selectinload(Client.account_manager))
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


def _validate_account_manager(db: Session, user_id: int | None, broker_id: int) -> None:
    """An account manager must be an active user of the SAME broker."""
    if user_id is None:
        return
    manager = db.execute(
        select(User).where(User.id == user_id, User.broker_id == broker_id)
    ).scalar_one_or_none()
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="account_manager_id does not belong to this broker",
        )


def _client_counts(db: Session, client_ids: list[int]) -> dict[int, dict[str, int]]:
    """Per-client asset/placement counters, in three grouped queries."""
    zero = {"assets_count": 0, "placements_count": 0, "active_placements_count": 0}
    out: dict[int, dict[str, int]] = {cid: dict(zero) for cid in client_ids}
    if not client_ids:
        return out

    assets = counts_by(
        db,
        select(Asset.client_id, func.count(Asset.id))
        .where(Asset.client_id.in_(client_ids))
        .group_by(Asset.client_id),
    )
    placements = counts_by(
        db,
        select(Placement.client_id, func.count(Placement.id))
        .where(Placement.client_id.in_(client_ids))
        .group_by(Placement.client_id),
    )
    active = counts_by(
        db,
        select(Placement.client_id, func.count(Placement.id))
        .where(
            Placement.client_id.in_(client_ids),
            Placement.status.in_(OPEN_PLACEMENT_STATUSES),
        )
        .group_by(Placement.client_id),
    )
    for cid in client_ids:
        out[cid]["assets_count"] = assets.get(cid, 0)
        out[cid]["placements_count"] = placements.get(cid, 0)
        out[cid]["active_placements_count"] = active.get(cid, 0)
    return out


def _serialize(client: Client, counts: dict[str, int], *, detail: bool = False):
    model = ClientRead if detail else ClientListItem
    return model.model_validate(client).model_copy(update=counts)


def _detail_response(db: Session, client: Client) -> ClientRead:
    counts = _client_counts(db, [client.id])[client.id]
    counts["policies_count"] = db.execute(
        select(func.count(Policy.id)).where(Policy.client_id == client.id)
    ).scalar_one()
    return _serialize(client, counts, detail=True)


# --- Endpoints ---------------------------------------------------------------


@router.get("/summary", response_model=ClientSummary)
def get_clients_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Clients", "View")),
) -> ClientSummary:
    """Counters the clients list header needs, in one round trip."""
    by_status_rows = db.execute(
        select(Client.status, func.count(Client.id))
        .where(Client.broker_id == broker_id)
        .group_by(Client.status)
    ).all()
    by_status = {str(key): value for key, value in by_status_rows}

    total = sum(by_status.values())
    with_active = db.execute(
        select(func.count(func.distinct(Placement.client_id))).where(
            Placement.broker_id == broker_id,
            Placement.status.in_(OPEN_PLACEMENT_STATUSES),
        )
    ).scalar_one()
    unassigned = db.execute(
        select(func.count(Client.id)).where(
            Client.broker_id == broker_id, Client.account_manager_id.is_(None)
        )
    ).scalar_one()

    return ClientSummary(
        total=total,
        by_status={s.value: by_status.get(s.value, 0) for s in ClientStatus},
        with_active_placements=with_active,
        unassigned=unassigned,
    )


@router.get("", response_model=ClientPage)
def list_clients(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Clients", "View")),
    q: str | None = Query(None, description="Search legal/trade name or RUT"),
    status_filter: list[ClientStatus] | None = Query(None, alias="status"),
    account_manager_id: int | None = None,
    sector: str | None = None,
    source: str | None = None,
    sort: Literal["legal_name", "created_at", "status", "since"] = "legal_name",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> ClientPage:
    """List the broker's clients, filtered and paginated."""
    stmt = (
        select(Client)
        .join(Insured, Insured.id == Client.insured_id)
        .where(Client.broker_id == broker_id)
    )

    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Insured.legal_name.ilike(needle),
                Insured.trade_name.ilike(needle),
                Insured.rut.ilike(needle),
                Client.contact_name.ilike(needle),
            )
        )
    if status_filter:
        stmt = stmt.where(Client.status.in_(status_filter))
    if account_manager_id is not None:
        stmt = stmt.where(Client.account_manager_id == account_manager_id)
    if sector:
        stmt = stmt.where(Client.sector == sector)
    if source:
        stmt = stmt.where(Client.source == source)

    total = count_total(db, stmt)

    sort_column = {
        "legal_name": Insured.legal_name,
        "created_at": Client.created_at,
        "status": Client.status,
        "since": Client.since,
    }[sort]
    stmt = stmt.order_by(
        sort_column.asc() if order == "asc" else sort_column.desc(), Client.id.asc()
    )

    rows = (
        db.execute(
            stmt.options(selectinload(Client.insured), selectinload(Client.account_manager))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    counts = _client_counts(db, [row.id for row in rows])
    return ClientPage(
        items=[_serialize(row, counts[row.id]) for row in rows],
        **paginate(total, page, page_size),
    )


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
def create_client(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Clients", "Create")),
) -> ClientRead:
    """Create a client from a RUT, finding-or-creating the canonical insured.

    The RUT is already mod-11 validated and normalized by the schema. When the
    insured already exists, only its EMPTY fields are enriched from the payload:
    that row is shared with other brokers and must not be overwritten.
    """
    _validate_account_manager(db, payload.account_manager_id, broker_id)

    insured = db.execute(
        select(Insured).where(Insured.rut == payload.rut)
    ).scalar_one_or_none()
    insured_created = False

    if insured is None:
        if not payload.legal_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="legal_name is required: this RUT is not registered yet",
            )
        insured = Insured(
            rut=payload.rut,
            person_type=payload.person_type,
            legal_name=payload.legal_name,
            trade_name=payload.trade_name,
            tax_activity=payload.tax_activity,
            contact_name=payload.insured_contact_name,
            email=str(payload.insured_email) if payload.insured_email else None,
            phone=payload.insured_phone,
            address=payload.insured_address,
            commune=payload.insured_commune,
            region=payload.insured_region,
        )
        db.add(insured)
        db.flush()
        insured_created = True
    else:
        # Non-destructive enrichment of the canonical row.
        enrichable = {
            "trade_name": payload.trade_name,
            "tax_activity": payload.tax_activity,
            "contact_name": payload.insured_contact_name,
            "email": str(payload.insured_email) if payload.insured_email else None,
            "phone": payload.insured_phone,
            "address": payload.insured_address,
            "commune": payload.insured_commune,
            "region": payload.insured_region,
        }
        for field, value in enrichable.items():
            if value and getattr(insured, field, None) in (None, ""):
                setattr(insured, field, value)

        existing = db.execute(
            select(Client).where(
                Client.broker_id == broker_id, Client.insured_id == insured.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This RUT is already a client of yours (client_id={existing.id})",
            )

    client = Client(
        broker_id=broker_id,
        insured_id=insured.id,
        status=payload.status,
        account_manager_id=payload.account_manager_id,
        source=payload.source,
        sector=payload.sector,
        since=payload.since,
        contact_name=payload.contact_name,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        contact_phone=payload.contact_phone,
        internal_notes=payload.internal_notes,
    )
    db.add(client)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="client.created",
        entity_type=EntityType.CLIENT,
        entity_id=client.id,
        description=f"Cliente {insured.legal_name} ({insured.rut}) creado",
        meta={
            "rut": insured.rut,
            "insured_id": insured.id,
            "insured_created": insured_created,
            "status": client.status.value,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against a concurrent create for the same (broker, RUT).
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This RUT is already a client of yours",
        )
    db.refresh(client)
    return _detail_response(db, client)


@router.get("/{client_id}", response_model=ClientRead)
def get_client(
    client_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Clients", "View")),
) -> ClientRead:
    """One client, with its canonical insured and its counters."""
    client = get_client_or_404(db, client_id, broker_id)
    return _detail_response(db, client)


@router.patch("/{client_id}", response_model=ClientRead)
def update_client(
    client_id: int,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Clients", "Edit")),
) -> ClientRead:
    """Patch the broker-private side of a client (never the canonical insured)."""
    client = get_client_or_404(db, client_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _detail_response(db, client)

    if "account_manager_id" in changes:
        _validate_account_manager(db, changes["account_manager_id"], broker_id)
    if changes.get("contact_email") is not None:
        changes["contact_email"] = str(changes["contact_email"])

    for field, value in changes.items():
        setattr(client, field, value)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="client.updated",
        entity_type=EntityType.CLIENT,
        entity_id=client.id,
        description=f"Cliente actualizado ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(client)
    return _detail_response(db, client)


@router.delete(
    "/{client_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Clients", "Delete")),
) -> Response:
    """Delete a client.

    Refused (409) while operational history exists — placements or policies.
    Archive it instead (``status = archived``); a broker's audit trail is not
    something a DELETE should silently take with it.
    """
    client = get_client_or_404(db, client_id, broker_id)

    placements = db.execute(
        select(func.count(Placement.id)).where(Placement.client_id == client.id)
    ).scalar_one()
    policies = db.execute(
        select(func.count(Policy.id)).where(Policy.client_id == client.id)
    ).scalar_one()
    if placements or policies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Client has operational history "
                f"({placements} placements, {policies} policies) — archive it instead"
            ),
        )

    rut = client.insured.rut if client.insured else None
    db.delete(client)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="client.deleted",
        entity_type=EntityType.CLIENT,
        entity_id=client_id,
        description=f"Cliente eliminado ({rut})",
        meta={"rut": rut},
    )
    db.commit()


__all__ = [
    "router",
    "record_activity",
    "paginate",
    "count_total",
    "counts_by",
    "get_client_or_404",
    "OPEN_PLACEMENT_STATUSES",
]
