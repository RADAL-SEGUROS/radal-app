"""``/claims`` — losses, the adjuster's per-partida table, and the closing ruling.

``claim_item`` is a child table because the adjuster's reports are 1:N and every
column is summed (notified → determined → damage → deductible → indemnity),
which is precisely what the final report reconciles. ``POST /claims/{id}/close``
is the only way a claim reaches its final ruling and loss ratio.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.api.routers.policies import get_policy_or_404
from app.models.client import Client
from app.models.enums import ClaimRuling, EntityType
from app.models.insured import Insured
from app.models.policy import Claim, ClaimItem, ClaimStatus, Policy
from app.models.user import User
from app.schemas.claim import (
    ClaimClose,
    ClaimCreate,
    ClaimItemRead,
    ClaimItemsRead,
    ClaimItemsReplace,
    ClaimItemTotals,
    ClaimPage,
    ClaimRead,
    ClaimUpdate,
)

router = APIRouter(prefix="/claims", tags=["claims"])


def _d(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def get_claim_or_404(db: Session, claim_id: int, broker_id: int) -> Claim:
    claim = db.scalars(
        select(Claim)
        .where(Claim.id == claim_id, Claim.broker_id == broker_id)
        .options(selectinload(Claim.items))
    ).first()
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    return claim


def _read(db: Session, claim: Claim) -> ClaimRead:
    payload = ClaimRead.model_validate(claim)
    payload.items = [
        ClaimItemRead.model_validate(row)
        for row in sorted(claim.items, key=lambda r: (r.sort_order, r.id))
    ]
    if claim.policy_id is not None:
        policy = db.get(Policy, claim.policy_id)
        payload.policy_number = policy.policy_number if policy else None
    return payload


def _totals(items) -> ClaimItemTotals:
    return ClaimItemTotals(
        notified_uf=sum((_d(i.notified_uf) for i in items), Decimal("0")),
        determined_uf=sum((_d(i.determined_uf) for i in items), Decimal("0")),
        damage_uf=sum((_d(i.damage_uf) for i in items), Decimal("0")),
        deductible_uf=sum((_d(i.deductible_uf) for i in items), Decimal("0")),
        indemnity_uf=sum((_d(i.indemnity_uf) for i in items), Decimal("0")),
    )


# --- Endpoints -------------------------------------------------------------------

@router.get("", response_model=ClaimPage)
def list_claims(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Claims", "View")),
    policy_id: int | None = Query(default=None, gt=0),
    client_id: int | None = Query(default=None, gt=0),
    case_file_id: int | None = Query(default=None, gt=0),
    status_filter: list[ClaimStatus] | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="claim number or insured"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ClaimPage:
    stmt = select(Claim).where(Claim.broker_id == broker_id)
    if policy_id is not None:
        stmt = stmt.where(Claim.policy_id == policy_id)
    if client_id is not None:
        stmt = stmt.where(Claim.client_id == client_id)
    if case_file_id is not None:
        stmt = stmt.where(Claim.case_file_id == case_file_id)
    if status_filter:
        stmt = stmt.where(Claim.status.in_(status_filter))
    if q:
        needle = f"%{q.strip()}%"
        stmt = (
            stmt.join(Client, Client.id == Claim.client_id)
            .join(Insured, Insured.id == Client.insured_id)
            .where(
                or_(
                    Claim.claim_number.ilike(needle),
                    Insured.legal_name.ilike(needle),
                )
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Claim.id.desc())
        .options(selectinload(Claim.items))
        .offset(offset)
        .limit(limit)
    ).all()
    return ClaimPage(
        items=[_read(db, row) for row in rows], total=int(total), limit=limit, offset=offset
    )


@router.post("", response_model=ClaimRead, status_code=status.HTTP_201_CREATED)
def create_claim(
    payload: ClaimCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Claims", "Create")),
) -> ClaimRead:
    """Report a loss. ``client_id`` is derived from the policy when it is omitted."""
    policy = (
        get_policy_or_404(db, payload.policy_id, broker_id)
        if payload.policy_id is not None
        else None
    )
    client_id = payload.client_id or (policy.client_id if policy else None)
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_id is required unless a policy is supplied",
        )
    client = db.scalars(
        select(Client).where(Client.id == client_id, Client.broker_id == broker_id)
    ).first()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    data = payload.model_dump(exclude_unset=False)
    items = data.pop("items", []) or []
    data.pop("client_id", None)

    claim = Claim(broker_id=broker_id, client_id=client.id, **data)
    claim.items = [
        ClaimItem(broker_id=broker_id, **item.model_dump(exclude_unset=False))
        for item in payload.items
    ]
    # Keep the date/datetime pair in step — the hourly franchise is contractual.
    if claim.occurred_at is not None and claim.event_date is None:
        claim.event_date = claim.occurred_at.date()
    if claim.reported_at is not None and claim.reported_date is None:
        claim.reported_date = claim.reported_at.date()
    db.add(claim)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="claim.created",
        entity_type=EntityType.CLAIM,
        entity_id=claim.id,
        description=f"Siniestro {claim.claim_number or claim.id}",
        meta={"policy_id": claim.policy_id, "items": len(items)},
    )
    db.commit()
    db.refresh(claim)
    return _read(db, claim)


@router.get("/{claim_id}", response_model=ClaimRead)
def get_claim(
    claim_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Claims", "View")),
) -> ClaimRead:
    return _read(db, get_claim_or_404(db, claim_id, broker_id))


@router.patch("/{claim_id}", response_model=ClaimRead)
def update_claim(
    claim_id: int,
    payload: ClaimUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Claims", "Edit")),
) -> ClaimRead:
    claim = get_claim_or_404(db, claim_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("policy_id") is not None:
        get_policy_or_404(db, changes["policy_id"], broker_id)
    for field, value in changes.items():
        setattr(claim, field, value)
    if claim.occurred_at is not None and "event_date" not in changes:
        claim.event_date = claim.occurred_at.date()
    if claim.reported_at is not None and "reported_date" not in changes:
        claim.reported_date = claim.reported_at.date()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="claim.updated",
        entity_type=EntityType.CLAIM,
        entity_id=claim.id,
        description=f"Siniestro actualizado ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(claim)
    return _read(db, claim)


@router.get("/{claim_id}/items", response_model=ClaimItemsRead)
def list_claim_items(
    claim_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Claims", "View")),
) -> ClaimItemsRead:
    """The adjuster's table, with the column totals the final report reconciles."""
    claim = get_claim_or_404(db, claim_id, broker_id)
    items = sorted(claim.items, key=lambda r: (r.sort_order, r.id))
    return ClaimItemsRead(
        claim_id=claim.id,
        items=[ClaimItemRead.model_validate(row) for row in items],
        totals=_totals(items),
    )


@router.put("/{claim_id}/items", response_model=ClaimItemsRead)
def replace_claim_items(
    claim_id: int,
    payload: ClaimItemsReplace,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Claims", "Edit")),
) -> ClaimItemsRead:
    """Full replace — the shape the pre-informe and the informe final arrive in."""
    claim = get_claim_or_404(db, claim_id, broker_id)
    claim.items.clear()
    db.flush()
    claim.items = [
        ClaimItem(broker_id=broker_id, **item.model_dump(exclude_unset=False))
        for item in payload.items
    ]
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="claim.items_replaced",
        entity_type=EntityType.CLAIM,
        entity_id=claim.id,
        description=f"Partidas del siniestro reemplazadas ({len(payload.items)})",
        meta={"items": len(payload.items)},
    )
    db.commit()
    db.refresh(claim)
    items = sorted(claim.items, key=lambda r: (r.sort_order, r.id))
    return ClaimItemsRead(
        claim_id=claim.id,
        items=[ClaimItemRead.model_validate(row) for row in items],
        totals=_totals(items),
    )


@router.post("/{claim_id}/close", response_model=ClaimRead)
def close_claim(
    claim_id: int,
    payload: ClaimClose,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Claims", "Approve")),
) -> ClaimRead:
    """The adjuster's final ruling + the loss ratio. A pending ruling cannot close."""
    claim = get_claim_or_404(db, claim_id, broker_id)
    if payload.coverage_ruling is ClaimRuling.PENDING:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A claim cannot be closed while the coverage ruling is 'pending'",
        )
    if claim.status == ClaimStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Claim {claim.id} is already closed",
        )

    claim.coverage_ruling = payload.coverage_ruling
    for field in (
        "settled_amount_uf",
        "paid_amount_uf",
        "deductible_uf",
        "recovery_uf",
        "loss_ratio_pct",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(claim, field, value)

    items = list(claim.items)
    if items:
        totals = _totals(items)
        if claim.settled_amount_uf is None:
            claim.settled_amount_uf = totals.indemnity_uf
        if claim.deductible_uf is None:
            claim.deductible_uf = totals.deductible_uf
    if claim.cost_uf is None:
        claim.cost_uf = _d(claim.paid_amount_uf) - _d(claim.recovery_uf)

    claim.status = (
        ClaimStatus.REJECTED
        if payload.coverage_ruling is ClaimRuling.REJECTED
        else ClaimStatus.CLOSED
    )

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="claim.closed",
        entity_type=EntityType.CLAIM,
        entity_id=claim.id,
        description=f"Siniestro cerrado · {payload.coverage_ruling.value}",
        meta={
            "coverage_ruling": payload.coverage_ruling.value,
            "paid_amount_uf": str(_d(claim.paid_amount_uf)),
            "note": payload.note,
        },
    )
    db.commit()
    db.refresh(claim)
    return _read(db, claim)


__all__ = ["router", "get_claim_or_404"]
