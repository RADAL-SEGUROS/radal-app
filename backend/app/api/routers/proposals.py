"""``/proposals`` — the unit of work: CRUD, coverage children, confirm, accept/reject.

Three rules are enforced here and answered with precise errors:

* rule 3 — a proposal CANNOT exist without its source document
  (``source_document_id`` NOT NULL, and the document must belong to the broker);
* rule 3 — its insurer must carry both ``rut`` and ``cmf_code``;
* rule 6 — the Chilean premium arithmetic (net / vat / total / comprehensive
  rate). Missing members are derived, supplied ones are validated, and a broken
  invariant is a ``422`` naming the exact field, rule, expectation and delta.

Accepting a proposal is one transaction: the winner becomes ``accepted``, every
sibling becomes ``rejected``, the quote request closes and the placement moves to
``awarded``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.models.document import Document
from app.models.insurer import Insurer
from app.models.placement import Placement, PlacementStatus
from app.models.proposal import Proposal, ProposalCoverage, ProposalOrigin, ProposalStatus
from app.models.quote import QuoteRequest, QuoteRequestStatus
from app.models.user import User
from app.schemas.proposal import (
    ProposalConfirm,
    ProposalCoverageCreate,
    ProposalCoverageRead,
    ProposalCoverageUpdate,
    ProposalCreate,
    ProposalDecisionResult,
    ProposalPage,
    ProposalRead,
    ProposalReject,
    ProposalUpdate,
    reconcile_money,
)

router = APIRouter(prefix="/proposals", tags=["proposals"])

# The columns reconcile_money() reasons about, in the order it reports them.
MONEY_FIELDS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
)

# Which columns each derived figure is computed FROM. Used to recompute a
# derived value when a partial update changes one of its inputs.
_DERIVED_INPUTS: dict[str, tuple[str, ...]] = {
    "net_premium_uf": ("taxable_premium_uf", "exempt_premium_uf"),
    "vat_uf": ("taxable_premium_uf",),
    "total_premium_uf": (
        "taxable_premium_uf",
        "exempt_premium_uf",
        "net_premium_uf",
        "vat_uf",
    ),
    "comprehensive_rate_permille": ("taxable_rate_permille", "exempt_rate_permille"),
}

# A proposal in one of these states is out of the running.
_CLOSED_STATUSES = (
    ProposalStatus.REJECTED,
    ProposalStatus.WITHDRAWN,
    ProposalStatus.EXPIRED,
)


# --- Internal helpers --------------------------------------------------------

def _proposal_query():
    return select(Proposal).options(
        selectinload(Proposal.coverages), selectinload(Proposal.insurer)
    )


def _get_proposal(db: Session, broker_id: int, proposal_id: int) -> Proposal:
    proposal = db.scalars(
        _proposal_query().where(
            Proposal.id == proposal_id, Proposal.broker_id == broker_id
        )
    ).first()
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal {proposal_id} not found",
        )
    return proposal


def _get_quote(db: Session, broker_id: int, quote_request_id: int) -> QuoteRequest:
    quote = db.scalars(
        select(QuoteRequest).where(
            QuoteRequest.id == quote_request_id, QuoteRequest.broker_id == broker_id
        )
    ).first()
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quote request {quote_request_id} not found",
        )
    return quote


def _require_valid_insurer(db: Session, insurer_id: int) -> Insurer:
    """The insurer must exist AND carry rut + cmf_code (rule 3).

    ``insurer`` is canonical/cross-broker, so it is looked up without a tenant
    filter — brokers reach it only through their own proposals.
    """
    insurer = db.get(Insurer, insurer_id)
    if insurer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Insurer {insurer_id} not found",
        )
    missing = [
        field
        for field in ("rut", "cmf_code")
        if not (getattr(insurer, field, None) or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "insurer_identity_incomplete",
                "message": (
                    f"Insurer {insurer_id} ('{insurer.legal_name}') is missing "
                    f"{' and '.join(missing)}. A proposal requires an insurer "
                    "identified by both rut and cmf_code."
                ),
                "field": "insurer_id",
                "insurer_id": insurer_id,
                "missing": missing,
            },
        )
    return insurer


def _require_source_document(db: Session, broker_id: int, document_id: int) -> Document:
    """The mandatory source file (rule 3), scoped to the broker."""
    document = db.scalars(
        select(Document).where(
            Document.id == document_id, Document.broker_id == broker_id
        )
    ).first()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "source_document_not_found",
                "message": (
                    f"Document {document_id} does not exist in this workspace. A "
                    "proposal cannot exist without its source document."
                ),
                "field": "source_document_id",
                "source_document_id": document_id,
            },
        )
    return document


def _apply_money(db: Session, proposal: Proposal, incoming: dict[str, Any]) -> None:
    """Merge, derive and validate the premium block; 422 on a broken invariant.

    A stored DERIVED figure whose inputs are being changed in this same request
    is treated as stale and recomputed, so patching just ``taxable_premium_uf``
    works instead of demanding the caller resend net/vat/total. A conflict the
    caller states EXPLICITLY (both taxable and a vat that contradicts it) is
    still a ``422`` — that is a real disagreement, not staleness.
    """
    merged: dict[str, Any] = {
        field: incoming[field] if field in incoming else getattr(proposal, field, None)
        for field in MONEY_FIELDS
    }
    for derived_field, inputs in _DERIVED_INPUTS.items():
        if derived_field not in incoming and any(name in incoming for name in inputs):
            merged[derived_field] = None
    derived, errors = reconcile_money(merged)
    if errors:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "money_invariant_violated",
                "message": (
                    "The premium figures are inconsistent. Chilean rules: "
                    "net = taxable + exempt; vat = 0.19 * taxable (not on net); "
                    "total = net + vat; comprehensive_rate = taxable_rate + exempt_rate."
                ),
                "errors": errors,
            },
        )
    for field in MONEY_FIELDS:
        if field in incoming:
            setattr(proposal, field, incoming[field])
    for field, value in derived.items():
        setattr(proposal, field, value)


def _json_safe(value: Any) -> Any:
    """Make a validated term JSON-column safe.

    Decimals are kept NUMERIC (int when integral, float otherwise) rather than
    stringified: a deductible percentage is a number to the UI and to any future
    SQL/JSON query, and these are small display-precision values, not money
    columns.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _dump_deductibles(deductibles: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialise the validated per-peril terms back to a plain JSON dict."""
    if deductibles is None:
        return None
    return {
        peril: _json_safe(
            term.model_dump(exclude_none=True) if hasattr(term, "model_dump") else term
        )
        for peril, term in deductibles.items()
    }


def _replace_coverages(proposal: Proposal, items: list[ProposalCoverageCreate]) -> None:
    proposal.coverages.clear()
    for index, item in enumerate(items):
        proposal.coverages.append(
            ProposalCoverage(
                kind=item.kind,
                text=item.text,
                normalized_code=item.normalized_code,
                sort_order=item.sort_order if item.sort_order else index,
            )
        )


def _get_coverage(db: Session, proposal: Proposal, coverage_id: int) -> ProposalCoverage:
    coverage = db.scalars(
        select(ProposalCoverage).where(
            ProposalCoverage.id == coverage_id,
            ProposalCoverage.proposal_id == proposal.id,
        )
    ).first()
    if coverage is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coverage {coverage_id} not found on proposal {proposal.id}",
        )
    return coverage


# --- CRUD --------------------------------------------------------------------

@router.get("", response_model=ProposalPage)
def list_proposals(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
    quote_request_id: int | None = None,
    placement_id: int | None = None,
    insurer_id: int | None = None,
    proposal_status: ProposalStatus | None = Query(default=None, alias="status"),
    origin: ProposalOrigin | None = None,
    is_confirmed: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ProposalPage:
    """List the broker's proposals, newest first."""
    filters = [Proposal.broker_id == broker_id]
    if quote_request_id is not None:
        filters.append(Proposal.quote_request_id == quote_request_id)
    if insurer_id is not None:
        filters.append(Proposal.insurer_id == insurer_id)
    if proposal_status is not None:
        filters.append(Proposal.status == proposal_status)
    if origin is not None:
        filters.append(Proposal.origin == origin)
    if is_confirmed is not None:
        filters.append(Proposal.is_confirmed == is_confirmed)

    base = _proposal_query().where(*filters)
    counter = select(func.count(Proposal.id)).where(*filters)
    if placement_id is not None:
        join_filter = QuoteRequest.placement_id == placement_id
        base = base.join(QuoteRequest, QuoteRequest.id == Proposal.quote_request_id).where(
            join_filter
        )
        counter = counter.join(
            QuoteRequest, QuoteRequest.id == Proposal.quote_request_id
        ).where(join_filter)

    total = int(db.scalar(counter) or 0)
    rows = list(
        db.scalars(base.order_by(Proposal.id.desc()).limit(limit).offset(offset)).all()
    )
    return ProposalPage(
        items=[ProposalRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ProposalRead, status_code=status.HTTP_201_CREATED)
def create_proposal(
    payload: ProposalCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Create")),
) -> ProposalRead:
    """Register one insurer's offer against a quote request.

    ``source_document_id`` and an insurer with rut + cmf_code are mandatory.
    When ``origin`` is not stated it is derived from ``insurer.is_native``.
    """
    _get_quote(db, broker_id, payload.quote_request_id)
    insurer = _require_valid_insurer(db, payload.insurer_id)
    _require_source_document(db, broker_id, payload.source_document_id)

    origin = (
        payload.origin
        if "origin" in payload.model_fields_set
        else (ProposalOrigin.NATIVE if insurer.is_native else ProposalOrigin.EXTERNAL)
    )

    proposal = Proposal(
        broker_id=broker_id,
        quote_request_id=payload.quote_request_id,
        insurer_id=payload.insurer_id,
        source_document_id=payload.source_document_id,
        origin=origin,
        modality=payload.modality,
        activity_classification=payload.activity_classification,
        commission_pct=payload.commission_pct,
        validity_business_days=payload.validity_business_days,
        coverage_start=payload.coverage_start,
        coverage_end=payload.coverage_end,
        received_at=payload.received_at,
        deductibles=_dump_deductibles(payload.deductibles),
        warranties=payload.warranties,
        notes=payload.notes,
        status=payload.status,
        extraction_id=payload.extraction_id,
        extraction_confidence=payload.extraction_confidence,
    )
    _apply_money(db, proposal, payload.model_dump(include=set(MONEY_FIELDS)))
    _replace_coverages(proposal, payload.coverages)

    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


@router.get("/{proposal_id}", response_model=ProposalRead)
def get_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
) -> ProposalRead:
    return ProposalRead.model_validate(_get_proposal(db, broker_id, proposal_id))


@router.patch("/{proposal_id}", response_model=ProposalRead)
def update_proposal(
    proposal_id: int,
    payload: ProposalUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> ProposalRead:
    """Partial update. Sending ``coverages`` replaces the whole set."""
    proposal = _get_proposal(db, broker_id, proposal_id)
    data = payload.model_dump(exclude_unset=True)
    new_coverages = data.pop("coverages", None)
    money = {field: data.pop(field) for field in MONEY_FIELDS if field in data}

    if "insurer_id" in data and data["insurer_id"] is not None:
        _require_valid_insurer(db, data["insurer_id"])
    if "source_document_id" in data:
        if data["source_document_id"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "source_document_required",
                    "message": "A proposal cannot exist without its source document.",
                    "field": "source_document_id",
                },
            )
        _require_source_document(db, broker_id, data["source_document_id"])
    if "deductibles" in data:
        data["deductibles"] = _dump_deductibles(payload.deductibles)

    for field, value in data.items():
        setattr(proposal, field, value)
    _apply_money(db, proposal, money)
    if new_coverages is not None:
        _replace_coverages(
            proposal, [ProposalCoverageCreate(**item) for item in new_coverages]
        )

    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


@router.delete("/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Delete")),
) -> Response:
    """Delete a proposal. Refused once it has been accepted or turned into a policy."""
    proposal = _get_proposal(db, broker_id, proposal_id)
    if proposal.status == ProposalStatus.ACCEPTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_accepted",
                "message": (
                    "An accepted proposal cannot be deleted; accept a different "
                    "proposal or reject this one first."
                ),
            },
        )
    if proposal.policy is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_has_policy",
                "message": f"Proposal {proposal_id} is bound to a policy.",
            },
        )
    db.delete(proposal)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Coverages / exclusions --------------------------------------------------

@router.get("/{proposal_id}/coverages", response_model=list[ProposalCoverageRead])
def list_coverages(
    proposal_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
    kind: str | None = Query(default=None, pattern="^(coverage|exclusion)$"),
) -> list[ProposalCoverage]:
    proposal = _get_proposal(db, broker_id, proposal_id)
    items = list(proposal.coverages)
    if kind is not None:
        items = [item for item in items if str(item.kind) == kind]
    return items


@router.post(
    "/{proposal_id}/coverages",
    response_model=ProposalRead,
    status_code=status.HTTP_201_CREATED,
)
def add_coverage(
    proposal_id: int,
    payload: ProposalCoverageCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> ProposalRead:
    """Append one coverage or exclusion line."""
    proposal = _get_proposal(db, broker_id, proposal_id)
    proposal.coverages.append(
        ProposalCoverage(
            kind=payload.kind,
            text=payload.text,
            normalized_code=payload.normalized_code,
            sort_order=payload.sort_order if payload.sort_order else len(proposal.coverages),
        )
    )
    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


@router.put("/{proposal_id}/coverages", response_model=ProposalRead)
def replace_coverages(
    proposal_id: int,
    payload: list[ProposalCoverageCreate] = Body(...),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> ProposalRead:
    """Replace every coverage/exclusion of the proposal in one call."""
    proposal = _get_proposal(db, broker_id, proposal_id)
    _replace_coverages(proposal, payload)
    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


@router.patch("/{proposal_id}/coverages/{coverage_id}", response_model=ProposalRead)
def update_coverage(
    proposal_id: int,
    coverage_id: int,
    payload: ProposalCoverageUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> ProposalRead:
    proposal = _get_proposal(db, broker_id, proposal_id)
    coverage = _get_coverage(db, proposal, coverage_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(coverage, field, value)
    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


@router.delete("/{proposal_id}/coverages/{coverage_id}", response_model=ProposalRead)
def delete_coverage(
    proposal_id: int,
    coverage_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> ProposalRead:
    proposal = _get_proposal(db, broker_id, proposal_id)
    coverage = _get_coverage(db, proposal, coverage_id)
    proposal.coverages.remove(coverage)
    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


# --- Confirm / accept / reject ----------------------------------------------

@router.post("/{proposal_id}/confirm", response_model=ProposalRead)
def confirm_proposal(
    proposal_id: int,
    payload: ProposalConfirm | None = Body(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Approve")),
) -> ProposalRead:
    """Human confirmation of an AI pre-filled proposal (suggest -> confirm -> commit)."""
    payload = payload or ProposalConfirm()
    proposal = _get_proposal(db, broker_id, proposal_id)
    proposal.is_confirmed = payload.is_confirmed
    if payload.is_confirmed:
        proposal.confirmed_by_id = current_user.id
        proposal.confirmed_at = datetime.now(timezone.utc)
    else:
        proposal.confirmed_by_id = None
        proposal.confirmed_at = None
    db.commit()
    db.refresh(proposal)
    return ProposalRead.model_validate(proposal)


@router.post("/{proposal_id}/accept", response_model=ProposalDecisionResult)
def accept_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Approve")),
) -> ProposalDecisionResult:
    """Award the quote to this proposal — one transaction, four effects.

    The winner becomes ``accepted``; every still-open sibling of the same quote
    request becomes ``rejected``; the quote request closes; the placement moves
    to ``awarded``. A proposal that was AI pre-filled must be confirmed first.
    """
    proposal = _get_proposal(db, broker_id, proposal_id)
    if proposal.status in _CLOSED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_not_acceptable",
                "message": f"A proposal in status '{proposal.status}' cannot be accepted.",
                "status": str(proposal.status),
            },
        )
    if proposal.extraction_id is not None and not proposal.is_confirmed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_not_confirmed",
                "message": (
                    "This proposal was pre-filled by AI and has not been confirmed. "
                    "Confirm it (POST /proposals/{id}/confirm) before accepting."
                ),
            },
        )

    quote = _get_quote(db, broker_id, proposal.quote_request_id)
    placement = db.scalars(
        select(Placement).where(
            Placement.id == quote.placement_id, Placement.broker_id == broker_id
        )
    ).first()
    if placement is None:  # pragma: no cover - FK guarantees it exists
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Placement {quote.placement_id} not found",
        )

    siblings = list(
        db.scalars(
            select(Proposal).where(
                Proposal.quote_request_id == quote.id,
                Proposal.broker_id == broker_id,
                Proposal.id != proposal.id,
            )
        ).all()
    )
    rejected_ids: list[int] = []
    for sibling in siblings:
        if sibling.status in _CLOSED_STATUSES:
            continue
        sibling.status = ProposalStatus.REJECTED
        rejected_ids.append(sibling.id)

    proposal.status = ProposalStatus.ACCEPTED
    quote.status = QuoteRequestStatus.CLOSED
    placement.status = PlacementStatus.AWARDED

    # One commit: award, rejections and both status moves land together or not at all.
    db.commit()
    db.refresh(proposal)

    return ProposalDecisionResult(
        proposal_id=proposal.id,
        proposal_status=proposal.status,
        rejected_proposal_ids=rejected_ids,
        quote_request_id=quote.id,
        quote_request_status=quote.status,
        placement_id=placement.id,
        placement_status=str(placement.status),
    )


@router.post("/{proposal_id}/reject", response_model=ProposalDecisionResult)
def reject_proposal(
    proposal_id: int,
    payload: ProposalReject | None = Body(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Approve")),
) -> ProposalDecisionResult:
    """Reject one proposal.

    Rejecting the proposal that currently holds the award reverses the award:
    the quote request reopens (``receiving``) and the placement returns to
    ``negotiating``, so the broker is never left with a closed quote and no winner.
    """
    payload = payload or ProposalReject()
    proposal = _get_proposal(db, broker_id, proposal_id)
    quote = _get_quote(db, broker_id, proposal.quote_request_id)
    placement = db.scalars(
        select(Placement).where(
            Placement.id == quote.placement_id, Placement.broker_id == broker_id
        )
    ).first()
    if placement is None:  # pragma: no cover - FK guarantees it exists
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Placement {quote.placement_id} not found",
        )

    was_awarded = proposal.status == ProposalStatus.ACCEPTED
    proposal.status = ProposalStatus.REJECTED
    if payload.reason:
        existing = (proposal.notes or "").rstrip()
        proposal.notes = f"{existing}\nRejected: {payload.reason}".strip()

    if was_awarded:
        if quote.status == QuoteRequestStatus.CLOSED:
            quote.status = QuoteRequestStatus.RECEIVING
        if placement.status == PlacementStatus.AWARDED:
            placement.status = PlacementStatus.NEGOTIATING

    db.commit()
    db.refresh(proposal)

    return ProposalDecisionResult(
        proposal_id=proposal.id,
        proposal_status=proposal.status,
        rejected_proposal_ids=[proposal.id],
        quote_request_id=quote.id,
        quote_request_status=quote.status,
        placement_id=placement.id,
        placement_status=str(placement.status),
    )


__all__ = ["router"]
