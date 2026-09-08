"""``/endorsements`` — proposed and issued amendments to a policy.

The money here is a set of DELTAS and it is sign-preserving: an exclusion or a
sum-insured decrease is negative, an administrative endorsement is all zero, and
``reconcile_endorsement_money`` refuses only a real arithmetic break (422).

``POST /endorsements/{id}/issue`` is the one place the deltas leave the
endorsement row: in ONE transaction it attaches the carrier's issued document,
moves the policy's insured amount and premium, and re-prices the collection plan
so ``Σ instalments == policy gross + Σ endorsement deltas`` keeps holding.

``POST /endorsements/batch`` is the **prórroga**: the same motive fanned over N
explicitly chosen policies of ONE account group, sharing a ``batch_key``. Every
delta is zero, so issuing it moves ``policy.end_date`` and leaves the ledger
exactly where it was — see ``app/services/endorsements.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.api.routers.policies import get_policy_or_404
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.document import Document
from app.models.endorsement import Endorsement
from app.models.enums import (
    EndorsementKind,
    EndorsementStatus,
    EntityType,
    InstallmentStatus,
)
from app.models.policy import Policy
from app.models.user import User
from app.schemas.endorsement import (
    EndorsementBatchCreate,
    EndorsementBatchIssue,
    EndorsementBatchItem,
    EndorsementBatchResponse,
    EndorsementCreate,
    EndorsementIssue,
    EndorsementIssueResult,
    EndorsementPage,
    EndorsementRead,
    EndorsementUpdate,
    reconcile_endorsement_money,
)
from app.services import endorsements as batch_service

router = APIRouter(prefix="/endorsements", tags=["endorsements"])

_MONEY_FIELDS = (
    "taxable_premium_delta_uf",
    "exempt_premium_delta_uf",
    "net_premium_delta_uf",
    "vat_delta_uf",
    "total_premium_delta_uf",
)

#: Statuses whose deltas are already reflected in the policy.
_APPLIED_STATUSES = (EndorsementStatus.ISSUED, EndorsementStatus.APPLIED)


# --- Helpers -------------------------------------------------------------------

def get_endorsement_or_404(db: Session, endorsement_id: int, broker_id: int) -> Endorsement:
    row = db.scalars(
        select(Endorsement).where(
            Endorsement.id == endorsement_id, Endorsement.broker_id == broker_id
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Endorsement not found"
        )
    return row


def _resolve_document(db: Session, document_id: int | None, broker_id: int) -> None:
    if document_id is None:
        return
    found = db.scalar(
        select(Document.id).where(
            Document.id == document_id, Document.broker_id == broker_id
        )
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The referenced document does not belong to this broker",
        )


def _apply_money(endorsement: Endorsement, incoming: dict) -> None:
    merged = {
        field: incoming.get(field, getattr(endorsement, field))
        for field in _MONEY_FIELDS
    }
    derived, errors = reconcile_endorsement_money(merged)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=errors
        )
    for field, value in derived.items():
        setattr(endorsement, field, value)


def _d(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


# --- Endpoints -----------------------------------------------------------------

@router.get("", response_model=EndorsementPage)
def list_endorsements(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Endorsements", "View")),
    policy_id: int | None = Query(default=None, gt=0),
    case_file_id: int | None = Query(default=None, gt=0),
    batch_key: str | None = Query(default=None, min_length=1, max_length=36),
    status_filter: list[EndorsementStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EndorsementPage:
    stmt = select(Endorsement).where(Endorsement.broker_id == broker_id)
    if policy_id is not None:
        stmt = stmt.where(Endorsement.policy_id == policy_id)
    if case_file_id is not None:
        stmt = stmt.where(Endorsement.case_file_id == case_file_id)
    if batch_key is not None:
        # The N rows of one prórroga, read back as an ordinary page.
        stmt = stmt.where(Endorsement.batch_key == batch_key)
    if status_filter:
        stmt = stmt.where(Endorsement.status.in_(status_filter))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Endorsement.policy_id.asc(), Endorsement.sequence_no.asc())
        .offset(offset)
        .limit(limit)
    ).all()
    return EndorsementPage(
        items=[EndorsementRead.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=EndorsementRead, status_code=status.HTTP_201_CREATED)
def create_endorsement(
    payload: EndorsementCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Create")),
) -> EndorsementRead:
    """Open an endorsement on a policy. Deltas may be zero or negative."""
    if payload.kind is EndorsementKind.PERIOD_EXTENSION:
        # A prórroga is a manual multi-select over N policies (rule 3): even a
        # one-policy one goes through the batch, so it always carries a
        # ``batch_key`` and the UI can render it as one movement.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "use_endorsement_batch",
                "detail": (
                    "A period extension is raised over explicitly chosen policies: "
                    "use POST /endorsements/batch"
                ),
            },
        )
    policy = get_policy_or_404(db, payload.policy_id, broker_id)
    _resolve_document(db, payload.proposal_document_id, broker_id)
    _resolve_document(db, payload.issued_document_id, broker_id)

    data = payload.model_dump(exclude_unset=False)
    sequence = data.pop("sequence_no", None)
    if sequence is None:
        highest = db.scalar(
            select(func.max(Endorsement.sequence_no)).where(
                Endorsement.broker_id == broker_id, Endorsement.policy_id == policy.id
            )
        )
        sequence = int(highest or 0) + 1

    endorsement = Endorsement(
        broker_id=broker_id, sequence_no=sequence, **data
    )
    _apply_money(endorsement, data)
    db.add(endorsement)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="endorsement.created",
        entity_type=EntityType.ENDORSEMENT,
        entity_id=endorsement.id,
        description=f"Endoso E{endorsement.sequence_no} · {endorsement.kind.value}",
        meta={"policy_id": policy.id, "status": endorsement.status.value},
    )
    db.commit()
    db.refresh(endorsement)
    return EndorsementRead.model_validate(endorsement)


# --- Prórroga: the batch (registered BEFORE /{endorsement_id}) -----------------
#
# Route order matters: ``/batch`` and ``/batch/{key}/issue`` must be declared
# above the ``/{endorsement_id}`` family or FastAPI would try to coerce
# "batch" into an int path parameter.

def _batch_or_404(db: Session, broker_id: int, batch_key: str) -> list[Endorsement]:
    members = batch_service.load_batch(db, broker_id=broker_id, batch_key=batch_key)
    if not members:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Endorsement batch not found"
        )
    return members


@router.post(
    "/batch", response_model=EndorsementBatchResponse, status_code=status.HTTP_201_CREATED
)
def create_endorsement_batch(
    payload: EndorsementBatchCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Create")),
) -> EndorsementBatchResponse:
    """One prórroga over N explicitly chosen policies of ONE account group.

    Writes one ``Endorsement`` + one ``case_file(kind=endorsement)`` per policy,
    all sharing a server-minted ``batch_key`` and all with ZERO premium deltas.
    Nothing moves on the policy until ``/batch/{batch_key}/issue``.
    """
    _resolve_document(db, payload.issued_document_id, broker_id)
    policies = [
        get_policy_or_404(db, policy_id, broker_id) for policy_id in payload.policy_ids
    ]

    try:
        batch_key, members = batch_service.create_period_extension_batch(
            db,
            broker_id=broker_id,
            user=current_user,
            policies=policies,
            new_end_date=payload.new_end_date,
            effective_at=payload.effective_at,
            issued_document_id=payload.issued_document_id,
            note=payload.note,
        )
    except batch_service.EndorsementBatchError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.as_detail()
        ) from exc

    for member in members:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="endorsement.created",
            entity_type=EntityType.ENDORSEMENT,
            entity_id=member.endorsement.id,
            description=(
                f"Prórroga E{member.endorsement.sequence_no} · póliza "
                f"{member.policy.policy_number}"
            ),
            meta={
                "policy_id": member.policy.id,
                "batch_key": batch_key,
                "new_end_date": payload.new_end_date.isoformat(),
                "case_file_id": member.case_file.id if member.case_file else None,
            },
        )
    db.commit()

    return EndorsementBatchResponse(
        batch_key=batch_key,
        kind=payload.kind,
        new_end_date=payload.new_end_date,
        items=[
            EndorsementBatchItem(
                policy_id=member.policy.id,
                endorsement_id=member.endorsement.id,
                case_file_id=member.case_file.id if member.case_file else None,
            )
            for member in members
        ],
    )


@router.post("/batch/{batch_key}/issue", response_model=EndorsementBatchResponse)
def issue_endorsement_batch(
    batch_key: str,
    payload: EndorsementBatchIssue,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Submit")),
) -> EndorsementBatchResponse:
    """Issue every member through the existing path, then move each end date.

    The whole batch is validated before anything is written, so a prórroga is
    never half-issued. Because every delta is zero the policy premium and the
    collection ledger come out exactly as they went in — only
    ``policy.end_date`` / ``period_end_at`` move (rule 3).
    """
    members = _batch_or_404(db, broker_id, batch_key)
    _resolve_document(db, payload.issued_document_id, broker_id)

    # Validate first: status, then the carrier's document, for every member.
    for endorsement in members:
        _guard_issuable(endorsement)
        _require_issued_document(
            db, endorsement, payload.issued_document_id, broker_id
        )

    items: list[EndorsementBatchItem] = []
    new_end_date = None
    for endorsement in members:
        policy = get_policy_or_404(db, endorsement.policy_id, broker_id)
        document_id = _require_issued_document(
            db, endorsement, payload.issued_document_id, broker_id
        )
        _apply_issue(
            db,
            endorsement=endorsement,
            policy=policy,
            broker_id=broker_id,
            document_id=document_id,
            issued_at=payload.issued_at,
        )
        new_end_date = batch_service.apply_period_extension(policy, endorsement)
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="endorsement.issued",
            entity_type=EntityType.ENDORSEMENT,
            entity_id=endorsement.id,
            description=(
                f"Prórroga E{endorsement.sequence_no} emitida · póliza "
                f"{policy.policy_number}"
            ),
            meta={
                "policy_id": policy.id,
                "batch_key": batch_key,
                "end_date": policy.end_date.isoformat() if policy.end_date else None,
                "note": payload.note,
            },
        )
        items.append(
            EndorsementBatchItem(
                policy_id=policy.id,
                endorsement_id=endorsement.id,
                case_file_id=endorsement.case_file_id,
                policy_end_date=policy.end_date,
            )
        )
    db.commit()

    return EndorsementBatchResponse(
        batch_key=batch_key,
        kind=EndorsementKind.PERIOD_EXTENSION,
        new_end_date=new_end_date,
        items=items,
    )


@router.get("/{endorsement_id}", response_model=EndorsementRead)
def get_endorsement(
    endorsement_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Endorsements", "View")),
) -> EndorsementRead:
    return EndorsementRead.model_validate(
        get_endorsement_or_404(db, endorsement_id, broker_id)
    )


@router.patch("/{endorsement_id}", response_model=EndorsementRead)
def update_endorsement(
    endorsement_id: int,
    payload: EndorsementUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Edit")),
) -> EndorsementRead:
    endorsement = get_endorsement_or_404(db, endorsement_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return EndorsementRead.model_validate(endorsement)
    if endorsement.status in _APPLIED_STATUSES and any(
        field in changes for field in _MONEY_FIELDS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "The deltas of an issued endorsement are already reflected in the "
                "policy; cancel it and raise a new one instead"
            ),
        )
    for field in ("proposal_document_id", "issued_document_id"):
        if field in changes:
            _resolve_document(db, changes[field], broker_id)

    _apply_money(endorsement, changes)
    confirming = changes.pop("is_confirmed", None)
    for field, value in changes.items():
        setattr(endorsement, field, value)
    if confirming is not None:
        endorsement.is_confirmed = confirming
        endorsement.confirmed_by_id = current_user.id if confirming else None
        endorsement.confirmed_at = datetime.now(timezone.utc) if confirming else None

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="endorsement.updated",
        entity_type=EntityType.ENDORSEMENT,
        entity_id=endorsement.id,
        description=f"Endoso actualizado ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(endorsement)
    return EndorsementRead.model_validate(endorsement)


@router.delete(
    "/{endorsement_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_endorsement(
    endorsement_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Delete")),
) -> Response:
    """Only a draft or rejected endorsement can be deleted; issued ones are history."""
    endorsement = get_endorsement_or_404(db, endorsement_id, broker_id)
    if endorsement.status not in (EndorsementStatus.DRAFT, EndorsementStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"An endorsement in '{endorsement.status.value}' cannot be deleted — "
                "cancel it instead"
            ),
        )
    db.delete(endorsement)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="endorsement.deleted",
        entity_type=EntityType.ENDORSEMENT,
        entity_id=endorsement_id,
        description="Endoso eliminado",
        meta={},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _guard_issuable(endorsement: Endorsement) -> None:
    """The two status refusals every issue path shares."""
    if endorsement.status in _APPLIED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Endorsement {endorsement.id} is already '{endorsement.status.value}'",
        )
    if endorsement.status == EndorsementStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A cancelled endorsement cannot be issued",
        )


def _require_issued_document(
    db: Session, endorsement: Endorsement, supplied: int | None, broker_id: int
) -> int:
    document_id = supplied or endorsement.issued_document_id
    if document_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "issued_document_id is required: an endorsement is issued by the "
                "carrier's document (08A/08B/09B/09D), never by a status flip"
            ),
        )
    _resolve_document(db, document_id, broker_id)
    return document_id


def _apply_issue(
    db: Session,
    *,
    endorsement: Endorsement,
    policy: Policy,
    broker_id: int,
    document_id: int,
    endorsement_number: str | None = None,
    issued_at=None,
    effective_at=None,
) -> tuple[CollectionPlan | None, Decimal | None]:
    """Fold one endorsement into its policy and collection plan.

    Extracted verbatim from ``POST /endorsements/{id}/issue`` so the prórroga
    batch issues **through the existing path** instead of a second copy of the
    arithmetic. The caller commits.
    """
    _apply_money(endorsement, {})

    endorsement.issued_document_id = document_id
    if endorsement_number:
        endorsement.endorsement_number = endorsement_number
    if issued_at:
        endorsement.issued_at = issued_at
    if effective_at:
        endorsement.effective_at = effective_at
    # ``applied``, not ``issued``: the carrier issued it AND Radal has folded the
    # deltas into the policy and the ledger. ``issued`` is reserved for a folio
    # recorded but not yet applied — which is exactly what the demo importer
    # writes, and what ``endorsement_delta_total`` still counts on top of the
    # policy's premium so the collection invariant never double-counts.
    endorsement.status = EndorsementStatus.APPLIED

    # --- apply the deltas to the policy ------------------------------------
    if endorsement.insured_amount_delta_uf is not None:
        policy.insured_amount_uf = _d(policy.insured_amount_uf) + _d(
            endorsement.insured_amount_delta_uf
        )
    for policy_field, delta_field in (
        ("taxable_premium_uf", "taxable_premium_delta_uf"),
        ("exempt_premium_uf", "exempt_premium_delta_uf"),
        ("net_premium_uf", "net_premium_delta_uf"),
        ("vat_uf", "vat_delta_uf"),
        ("total_premium_uf", "total_premium_delta_uf"),
    ):
        delta = getattr(endorsement, delta_field)
        if delta is None:
            continue
        current = getattr(policy, policy_field)
        if current is None and _d(delta) == 0:
            # A zero delta (an administrative endorsement, a prórroga) must not
            # invent a premium on a policy that records none — NULL is the
            # collection invariant's own escape hatch.
            continue
        setattr(policy, policy_field, _d(current) + _d(delta))

    # --- re-price the collection plan --------------------------------------
    plan = db.scalars(
        select(CollectionPlan)
        .where(
            CollectionPlan.broker_id == broker_id,
            CollectionPlan.policy_id == policy.id,
        )
        .order_by(CollectionPlan.id.asc())
    ).first()
    plan_total: Decimal | None = None
    if plan is not None:
        total_delta = _d(endorsement.total_premium_delta_uf)
        if total_delta != 0:
            plan.total_premium_uf = _d(plan.total_premium_uf) + total_delta
            # The last OPEN instalment absorbs the movement — that is what the
            # carrier's plan de pago does in the corpus. If nothing is open,
            # a new cuota carries it.
            open_rows = [
                row
                for row in sorted(plan.installments, key=lambda r: r.number)
                if row.status
                in (
                    InstallmentStatus.PENDING,
                    InstallmentStatus.DUE,
                    InstallmentStatus.OVERDUE,
                )
            ]
            if open_rows:
                target = open_rows[-1]
                target.gross_amount_uf = _d(target.gross_amount_uf) + total_delta
                target.endorsement_id = endorsement.id
                target.note = (target.note or "") + f" · endoso E{endorsement.sequence_no}"
            else:
                highest = max((row.number for row in plan.installments), default=0)
                plan.installments.append(
                    CollectionInstallment(
                        broker_id=broker_id,
                        number=highest + 1,
                        gross_amount_uf=total_delta,
                        status=InstallmentStatus.PENDING,
                        endorsement_id=endorsement.id,
                        note=f"Cuota por endoso E{endorsement.sequence_no}",
                    )
                )
                plan.installment_count = highest + 1
        plan_total = plan.total_premium_uf

    return plan, plan_total


@router.post("/{endorsement_id}/issue", response_model=EndorsementIssueResult)
def issue_endorsement(
    endorsement_id: int,
    payload: EndorsementIssue,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Submit")),
) -> EndorsementIssueResult:
    """Attach the issued document and apply the deltas — policy + collection, once."""
    endorsement = get_endorsement_or_404(db, endorsement_id, broker_id)
    _guard_issuable(endorsement)
    document_id = _require_issued_document(
        db, endorsement, payload.issued_document_id, broker_id
    )
    policy = get_policy_or_404(db, endorsement.policy_id, broker_id)

    plan, plan_total = _apply_issue(
        db,
        endorsement=endorsement,
        policy=policy,
        broker_id=broker_id,
        document_id=document_id,
        endorsement_number=payload.endorsement_number,
        issued_at=payload.issued_at,
        effective_at=payload.effective_at,
    )

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="endorsement.issued",
        entity_type=EntityType.ENDORSEMENT,
        entity_id=endorsement.id,
        description=(
            f"Endoso E{endorsement.sequence_no} emitido "
            f"({endorsement.endorsement_number or 's/folio'})"
        ),
        meta={
            "policy_id": policy.id,
            "total_premium_delta_uf": str(_d(endorsement.total_premium_delta_uf)),
            "collection_plan_id": plan.id if plan else None,
            "note": payload.note,
        },
    )
    db.commit()
    db.refresh(endorsement)
    db.refresh(policy)
    return EndorsementIssueResult(
        endorsement=EndorsementRead.model_validate(endorsement),
        policy_id=policy.id,
        policy_total_premium_uf=policy.total_premium_uf,
        policy_insured_amount_uf=policy.insured_amount_uf,
        collection_plan_id=plan.id if plan else None,
        collection_total_premium_uf=plan_total,
    )


__all__ = ["router", "get_endorsement_or_404"]
