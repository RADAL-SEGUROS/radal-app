"""``/collections`` — payment plans, their instalment ledger and arrears.

The invariant every plan in the corpus obeys::

    Σ installment.gross_amount_uf == policy gross premium
                                     + Σ endorsement.total_premium_delta_uf

is checked with the existing ``MONEY_TOLERANCE`` on every write that touches the
ledger. A mismatch is a **422** carrying the expected/received/difference triple
— never a silent correction, because in the demo files the difference is exactly
where a missing endorsement hides.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.api.routers.policies import get_policy_or_404
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.endorsement import Endorsement
from app.models.enums import (
    CollectionPlanStatus,
    EndorsementStatus,
    EntityType,
    InstallmentStatus,
)
from app.models.user import User
from app.schemas.collection import (
    CollectionAlert,
    CollectionPlanCreate,
    CollectionPlanPage,
    CollectionPlanRead,
    CollectionPlanUpdate,
    CollectionStatusRead,
    InstallmentCreate,
    InstallmentRead,
    InstallmentReplace,
    InstallmentUpdate,
    check_installment_total,
)

router = APIRouter(prefix="/collections", tags=["collections"])

#: Instalments that no longer owe anything.
SETTLED_STATUSES = (
    InstallmentStatus.PAID,
    InstallmentStatus.PAID_LATE,
    InstallmentStatus.CREDITED,
    InstallmentStatus.CANCELLED,
)
PAID_STATUSES = (InstallmentStatus.PAID, InstallmentStatus.PAID_LATE)


def _d(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


# --- Helpers -------------------------------------------------------------------

def get_plan_or_404(db: Session, plan_id: int, broker_id: int) -> CollectionPlan:
    plan = db.scalars(
        select(CollectionPlan)
        .where(CollectionPlan.id == plan_id, CollectionPlan.broker_id == broker_id)
        .options(selectinload(CollectionPlan.installments))
    ).first()
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Collection plan not found"
        )
    return plan


def endorsement_delta_total(db: Session, *, broker_id: int, policy_id: int) -> Decimal:
    """Σ of the deltas that are real but NOT yet folded into the policy premium.

    ``issued`` means the carrier's folio exists; ``applied`` means Radal has
    already moved ``policy.total_premium_uf`` by that delta (``POST
    /endorsements/{id}/issue`` does both in one transaction). Only the former is
    added on top of the policy's premium — otherwise the corpus invariant would
    count the same movement twice.
    """
    total = db.scalar(
        select(func.coalesce(func.sum(Endorsement.total_premium_delta_uf), 0)).where(
            Endorsement.broker_id == broker_id,
            Endorsement.policy_id == policy_id,
            Endorsement.status == EndorsementStatus.ISSUED,
        )
    )
    return _d(total)


def _validate_total(
    db: Session, *, broker_id: int, policy_id: int, installments: list
) -> None:
    """422 unless Σ instalments == policy gross + Σ endorsement deltas."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    total = sum((_d(getattr(row, "gross_amount_uf", None)) for row in installments), Decimal("0"))
    error = check_installment_total(
        installments_total=total,
        policy_gross_uf=policy.total_premium_uf,
        endorsement_delta_uf=endorsement_delta_total(
            db, broker_id=broker_id, policy_id=policy_id
        ),
    )
    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=[error]
        )


def _read(plan: CollectionPlan) -> CollectionPlanRead:
    payload = CollectionPlanRead.model_validate(plan)
    payload.installments = [
        InstallmentRead.model_validate(row)
        for row in sorted(plan.installments, key=lambda r: r.number)
    ]
    return payload


def _rows_from(payload: list[InstallmentCreate], broker_id: int) -> list[CollectionInstallment]:
    seen: set[int] = set()
    rows: list[CollectionInstallment] = []
    for item in payload:
        if item.number in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Duplicate instalment number {item.number}",
            )
        seen.add(item.number)
        rows.append(
            CollectionInstallment(broker_id=broker_id, **item.model_dump(exclude_unset=False))
        )
    return rows


# --- Plans ---------------------------------------------------------------------

@router.get("", response_model=CollectionPlanPage)
def list_collection_plans(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
    policy_id: int | None = Query(default=None, gt=0),
    case_file_id: int | None = Query(default=None, gt=0),
    status_filter: list[CollectionPlanStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CollectionPlanPage:
    stmt = select(CollectionPlan).where(CollectionPlan.broker_id == broker_id)
    if policy_id is not None:
        stmt = stmt.where(CollectionPlan.policy_id == policy_id)
    if case_file_id is not None:
        stmt = stmt.where(CollectionPlan.case_file_id == case_file_id)
    if status_filter:
        stmt = stmt.where(CollectionPlan.status.in_(status_filter))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(CollectionPlan.id.desc())
        .options(selectinload(CollectionPlan.installments))
        .offset(offset)
        .limit(limit)
    ).all()
    return CollectionPlanPage(
        items=[_read(row) for row in rows], total=int(total), limit=limit, offset=offset
    )


@router.post("", response_model=CollectionPlanRead, status_code=status.HTTP_201_CREATED)
def create_collection_plan(
    payload: CollectionPlanCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Create")),
) -> CollectionPlanRead:
    """Register the plan de pago. The ledger's Σ is validated before it is written."""
    policy = get_policy_or_404(db, payload.policy_id, broker_id)

    data = payload.model_dump(exclude_unset=False)
    installments = data.pop("installments", []) or []
    rows = _rows_from(payload.installments, broker_id)
    if rows:
        _validate_total(db, broker_id=broker_id, policy_id=policy.id, installments=rows)

    plan = CollectionPlan(broker_id=broker_id, **data)
    plan.installments = rows
    if plan.installment_count is None and rows:
        plan.installment_count = len(rows)
    if plan.total_premium_uf is None and rows:
        plan.total_premium_uf = sum(
            (_d(row.gross_amount_uf) for row in rows), Decimal("0")
        )
    db.add(plan)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.created",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Plan de pago {plan.plan_number or plan.id} · {len(rows)} cuota(s)",
        meta={"policy_id": policy.id, "installments": len(rows)},
    )
    db.commit()
    db.refresh(plan)
    return _read(plan)


@router.get("/{plan_id}", response_model=CollectionPlanRead)
def get_collection_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> CollectionPlanRead:
    return _read(get_plan_or_404(db, plan_id, broker_id))


@router.patch("/{plan_id}", response_model=CollectionPlanRead)
def update_collection_plan(
    plan_id: int,
    payload: CollectionPlanUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> CollectionPlanRead:
    plan = get_plan_or_404(db, plan_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(plan, field, value)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.updated",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Plan de pago actualizado ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(plan)
    return _read(plan)


# --- Instalments -----------------------------------------------------------------

@router.get("/{plan_id}/installments", response_model=list[InstallmentRead])
def list_installments(
    plan_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> list[InstallmentRead]:
    plan = get_plan_or_404(db, plan_id, broker_id)
    return [
        InstallmentRead.model_validate(row)
        for row in sorted(plan.installments, key=lambda r: r.number)
    ]


@router.put("/{plan_id}/installments", response_model=CollectionPlanRead)
def replace_installments(
    plan_id: int,
    payload: InstallmentReplace,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> CollectionPlanRead:
    """Full replace of the ledger. The Σ is validated BEFORE anything is written."""
    plan = get_plan_or_404(db, plan_id, broker_id)
    rows = _rows_from(payload.installments, broker_id)
    _validate_total(db, broker_id=broker_id, policy_id=plan.policy_id, installments=rows)

    plan.installments.clear()
    db.flush()
    plan.installments = rows
    plan.installment_count = len(rows)
    plan.total_premium_uf = sum((_d(row.gross_amount_uf) for row in rows), Decimal("0"))

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_plan.installments_replaced",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Ledger reemplazado ({len(rows)} cuotas)",
        meta={"installments": len(rows)},
    )
    db.commit()
    db.refresh(plan)
    return _read(plan)


@router.patch("/{plan_id}/installments/{number}", response_model=InstallmentRead)
def update_installment(
    plan_id: int,
    number: int,
    payload: InstallmentUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Collections", "Edit")),
) -> InstallmentRead:
    """Mark one cuota paid (or late). ``days_late`` is derived when it is not given."""
    plan = get_plan_or_404(db, plan_id, broker_id)
    row = next((item for item in plan.installments if item.number == number), None)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Instalment {number} not found"
        )

    changes = payload.model_dump(exclude_unset=True)
    if "gross_amount_uf" in changes and changes["gross_amount_uf"] is not None:
        projected = [
            item for item in plan.installments if item.number != number
        ]

        class _Shim:  # noqa: D401 - a value carrier for the Σ check
            gross_amount_uf = changes["gross_amount_uf"]

        _validate_total(
            db,
            broker_id=broker_id,
            policy_id=plan.policy_id,
            installments=[*projected, _Shim()],
        )

    for field, value in changes.items():
        setattr(row, field, value)

    if row.paid_on is not None and row.due_date is not None and "days_late" not in changes:
        row.days_late = max(0, (row.paid_on - row.due_date).days)
    if row.paid_on is not None and "status" not in changes:
        row.status = (
            InstallmentStatus.PAID_LATE
            if (row.days_late or 0) > 0
            else InstallmentStatus.PAID
        )

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="collection_installment.updated",
        entity_type=EntityType.COLLECTION_PLAN,
        entity_id=plan.id,
        description=f"Cuota {number}: {row.status.value}",
        meta={"number": number, "fields": sorted(changes)},
    )
    db.commit()
    db.refresh(row)
    return InstallmentRead.model_validate(row)


# --- The derived dashboard --------------------------------------------------------

@router.get("/{plan_id}/status", response_model=CollectionStatusRead)
def get_collection_status(
    plan_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> CollectionStatusRead:
    """Outstanding, overdue, compliance % and the alerts the tracker raises."""
    plan = get_plan_or_404(db, plan_id, broker_id)
    policy = get_policy_or_404(db, plan.policy_id, broker_id)
    today = plan.as_of_date or date.today()

    rows = sorted(plan.installments, key=lambda r: r.number)
    scheduled = sum((_d(row.gross_amount_uf) for row in rows), Decimal("0"))
    paid = sum(
        (_d(row.gross_amount_uf) for row in rows if row.status in PAID_STATUSES),
        Decimal("0"),
    )
    outstanding = sum(
        (_d(row.gross_amount_uf) for row in rows if row.status not in SETTLED_STATUSES),
        Decimal("0"),
    )
    overdue_rows = [
        row
        for row in rows
        if row.status not in SETTLED_STATUSES
        and row.due_date is not None
        and row.due_date < today
    ]
    overdue = sum((_d(row.gross_amount_uf) for row in overdue_rows), Decimal("0"))
    paid_count = sum(1 for row in rows if row.status in PAID_STATUSES)
    compliance = (
        (Decimal(paid_count) * 100 / Decimal(len(rows))).quantize(Decimal("0.01"))
        if rows
        else Decimal("0")
    )
    next_due = next(
        (
            row.due_date
            for row in rows
            if row.status not in SETTLED_STATUSES and row.due_date is not None
        ),
        None,
    )

    expected = None
    balances = True
    if policy.total_premium_uf is not None:
        expected = _d(policy.total_premium_uf) + endorsement_delta_total(
            db, broker_id=broker_id, policy_id=policy.id
        )
        balances = (
            check_installment_total(
                installments_total=scheduled,
                policy_gross_uf=policy.total_premium_uf,
                endorsement_delta_uf=expected - _d(policy.total_premium_uf),
            )
            is None
        )

    alerts: list[CollectionAlert] = []
    if overdue_rows:
        alerts.append(
            CollectionAlert(
                code="installments_overdue",
                severity="high",
                detail=f"{len(overdue_rows)} cuota(s) vencida(s) por {overdue} UF",
            )
        )
    if plan.status in (CollectionPlanStatus.SUSPENDED, CollectionPlanStatus.TERMINATED):
        alerts.append(
            CollectionAlert(
                code=f"plan_{plan.status.value}",
                severity="high",
                detail="La cobertura está afectada por el estado del plan (Art. 528)",
            )
        )
    if plan.days_without_cover:
        alerts.append(
            CollectionAlert(
                code="days_without_cover",
                severity="high",
                detail=f"{plan.days_without_cover} día(s) sin cobertura",
            )
        )
    if not balances:
        alerts.append(
            CollectionAlert(
                code="ledger_out_of_balance",
                severity="medium",
                detail=(
                    "La suma de las cuotas no coincide con la prima de la póliza más "
                    "los endosos emitidos"
                ),
            )
        )

    return CollectionStatusRead(
        collection_plan_id=plan.id,
        policy_id=policy.id,
        status=plan.status,
        as_of_date=plan.as_of_date,
        installment_count=len(rows),
        total_scheduled_uf=scheduled,
        paid_uf=paid,
        outstanding_uf=outstanding,
        overdue_uf=overdue,
        overdue_count=len(overdue_rows),
        paid_count=paid_count,
        compliance_pct=compliance,
        next_due_date=next_due,
        expected_total_uf=expected,
        balances=balances,
        alerts=alerts,
    )


__all__ = ["router", "get_plan_or_404", "endorsement_delta_total"]
