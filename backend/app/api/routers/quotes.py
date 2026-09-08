"""``/quotes`` — quote requests, their line items, and the proposal comparator.

Every query is scoped to the authenticated user's ``broker_id`` (rule 2); a row
belonging to another broker is indistinguishable from a missing row (``404``),
never a ``403`` that would leak its existence.

The invariant ``declared_value_uf == SUM(line_items.value_uf)`` is enforced on
every write path. Building a quote up item by item stays ergonomic: the nested
line-item endpoints re-sync the declared value by default
(``?sync_declared_value=false`` to opt out and get the strict ``422`` instead).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.models.enums import CoverageKind, Priority
from app.models.insurer import Insurer
from app.models.placement import Placement, PlacementStatus
from app.models.proposal import Proposal, ProposalStatus
from app.models.quote import QuoteLineItem, QuoteRequest, QuoteRequestStatus
from app.models.user import User
from app.schemas.proposal import (
    ComparisonColumn,
    ComparisonHighlights,
    ComparisonQuote,
    CoverageCell,
    CoverageRow,
    DeductibleCell,
    DeductibleRow,
    InsurerRef,
    ProposalComparison,
    coverage_key,
)
from app.schemas.quote import (
    QuoteLineItemCreate,
    QuoteLineItemRead,
    QuoteLineItemUpdate,
    QuoteRequestCreate,
    QuoteRequestPage,
    QuoteRequestRead,
    QuoteRequestSend,
    QuoteRequestSummary,
    QuoteRequestUpdate,
    check_declared_value,
    line_items_total,
)

router = APIRouter(prefix="/quotes", tags=["quotes"])


# --- Internal helpers --------------------------------------------------------

def _quote_query():
    return select(QuoteRequest).options(
        selectinload(QuoteRequest.line_items),
        selectinload(QuoteRequest.placement),
    )


def _get_quote(db: Session, broker_id: int, quote_id: int) -> QuoteRequest:
    """Fetch one quote request inside the tenant, or 404."""
    quote = db.scalars(
        _quote_query().where(
            QuoteRequest.id == quote_id, QuoteRequest.broker_id == broker_id
        )
    ).first()
    if quote is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quote request {quote_id} not found",
        )
    return quote


def _get_placement(db: Session, broker_id: int, placement_id: int) -> Placement:
    placement = db.scalars(
        select(Placement).where(
            Placement.id == placement_id, Placement.broker_id == broker_id
        )
    ).first()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Placement {placement_id} not found",
        )
    return placement


def _proposal_count(db: Session, quote_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(Proposal.id)).where(Proposal.quote_request_id == quote_id)
        )
        or 0
    )


def _read(quote: QuoteRequest, proposal_count: int) -> QuoteRequestRead:
    payload = QuoteRequestRead.model_validate(quote)
    payload.line_items_total_uf = line_items_total(
        [item.value_uf for item in quote.line_items]
    )
    payload.proposal_count = proposal_count
    return payload


def _enforce_declared_value(db: Session, quote: QuoteRequest, *, explicit: bool) -> None:
    """Apply the declared-value invariant.

    ``explicit`` means the caller stated a ``declared_value_uf`` on this request:
    then it must match the line items or we answer ``422``. When the caller said
    nothing we derive the declared value from the items instead of nagging.
    """
    items = list(quote.line_items)
    total = line_items_total([item.value_uf for item in items])
    if not explicit and items:
        quote.declared_value_uf = total
        return
    error = check_declared_value(quote.declared_value_uf, total, len(items))
    if error is not None:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error)


def _replace_line_items(quote: QuoteRequest, items: list[QuoteLineItemCreate]) -> None:
    quote.line_items.clear()
    for index, item in enumerate(items):
        quote.line_items.append(
            QuoteLineItem(
                name=item.name,
                value_uf=item.value_uf,
                detail=item.detail,
                sort_order=item.sort_order if item.sort_order else index,
            )
        )


def _get_line_item(db: Session, quote: QuoteRequest, item_id: int) -> QuoteLineItem:
    item = db.scalars(
        select(QuoteLineItem).where(
            QuoteLineItem.id == item_id, QuoteLineItem.quote_request_id == quote.id
        )
    ).first()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Line item {item_id} not found on quote request {quote.id}",
        )
    return item


# --- Quote requests ----------------------------------------------------------

# A quote request in one of these states is still being worked; ``closed`` and
# ``cancelled`` are terminal (mirrors the send/accept transitions above).
_OPEN_QUOTE_STATUSES = (
    QuoteRequestStatus.DRAFT,
    QuoteRequestStatus.SENT,
    QuoteRequestStatus.RECEIVING,
)


# NOTE: registered before ``/{quote_id}`` so the literal path wins the match.
@router.get("/summary", response_model=QuoteRequestSummary)
def get_quotes_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "View")),
) -> QuoteRequestSummary:
    """The quotes board: status split plus the sent / overdue clocks."""
    rows = db.execute(
        select(QuoteRequest.status, func.count(QuoteRequest.id))
        .where(QuoteRequest.broker_id == broker_id)
        .group_by(QuoteRequest.status)
    ).all()
    by_status = {str(key): int(value) for key, value in rows}

    now = datetime.now(timezone.utc)
    sent_last_30d = int(
        db.scalar(
            select(func.count(QuoteRequest.id)).where(
                QuoteRequest.broker_id == broker_id,
                QuoteRequest.sent_at.is_not(None),
                QuoteRequest.sent_at >= now - timedelta(days=30),
            )
        )
        or 0
    )
    overdue = int(
        db.scalar(
            select(func.count(QuoteRequest.id)).where(
                QuoteRequest.broker_id == broker_id,
                QuoteRequest.due_at.is_not(None),
                QuoteRequest.due_at < now,
                QuoteRequest.status.in_(_OPEN_QUOTE_STATUSES),
            )
        )
        or 0
    )
    return QuoteRequestSummary(
        total=sum(by_status.values()),
        by_status={s.value: by_status.get(s.value, 0) for s in QuoteRequestStatus},
        sent_last_30d=sent_last_30d,
        overdue=overdue,
    )


@router.get("", response_model=QuoteRequestPage)
def list_quote_requests(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "View")),
    placement_id: int | None = None,
    client_id: int | None = None,
    case_file_id: int | None = Query(
        default=None, description="Only quote requests filed under this expediente"
    ),
    quote_status: QuoteRequestStatus | None = Query(default=None, alias="status"),
    priority: Priority | None = None,
    search: str | None = Query(default=None, min_length=1, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> QuoteRequestPage:
    """List the broker's quote requests, newest first."""
    filters = [QuoteRequest.broker_id == broker_id]
    if placement_id is not None:
        filters.append(QuoteRequest.placement_id == placement_id)
    if case_file_id is not None:
        # ``broker_id`` is already in ``filters``; a foreign case file just
        # yields an empty page rather than confirming that it exists.
        filters.append(QuoteRequest.case_file_id == case_file_id)
    if quote_status is not None:
        filters.append(QuoteRequest.status == quote_status)
    if priority is not None:
        filters.append(QuoteRequest.priority == priority)
    if search:
        filters.append(QuoteRequest.insured_object.ilike(f"%{search}%"))

    base = _quote_query().where(*filters)
    counter = select(func.count(QuoteRequest.id)).where(*filters)
    if client_id is not None:
        base = base.join(Placement, Placement.id == QuoteRequest.placement_id).where(
            Placement.client_id == client_id
        )
        counter = counter.join(Placement, Placement.id == QuoteRequest.placement_id).where(
            Placement.client_id == client_id
        )

    total = int(db.scalar(counter) or 0)
    rows = list(
        db.scalars(base.order_by(QuoteRequest.id.desc()).limit(limit).offset(offset)).all()
    )
    counts = _proposal_counts_for(db, [row.id for row in rows])
    return QuoteRequestPage(
        items=[_read(row, counts.get(row.id, 0)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _proposal_counts_for(db: Session, quote_ids: list[int]) -> dict[int, int]:
    if not quote_ids:
        return {}
    rows = db.execute(
        select(Proposal.quote_request_id, func.count(Proposal.id))
        .where(Proposal.quote_request_id.in_(quote_ids))
        .group_by(Proposal.quote_request_id)
    ).all()
    return {int(quote_id): int(count) for quote_id, count in rows}


@router.post("", response_model=QuoteRequestRead, status_code=status.HTTP_201_CREATED)
def create_quote_request(
    payload: QuoteRequestCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Quotes", "Create")),
) -> QuoteRequestRead:
    """Create a quote request on one of the broker's placements."""
    _get_placement(db, broker_id, payload.placement_id)

    quote = QuoteRequest(
        broker_id=broker_id,
        placement_id=payload.placement_id,
        insured_object=payload.insured_object,
        declared_value_uf=payload.declared_value_uf,
        currency=payload.currency,
        requested_coverages=payload.requested_coverages,
        desired_start=payload.desired_start,
        desired_end=payload.desired_end,
        due_at=payload.due_at,
        priority=payload.priority,
        status=payload.status,
        recipient_insurer_ids=payload.recipient_insurer_ids,
        created_by_id=current_user.id,
    )
    _replace_line_items(quote, payload.line_items)
    _enforce_declared_value(
        db, quote, explicit="declared_value_uf" in payload.model_fields_set
    )

    db.add(quote)
    db.commit()
    db.refresh(quote)
    return _read(quote, 0)


@router.get("/{quote_id}", response_model=QuoteRequestRead)
def get_quote_request(
    quote_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "View")),
) -> QuoteRequestRead:
    quote = _get_quote(db, broker_id, quote_id)
    return _read(quote, _proposal_count(db, quote.id))


@router.patch("/{quote_id}", response_model=QuoteRequestRead)
def update_quote_request(
    quote_id: int,
    payload: QuoteRequestUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Edit")),
) -> QuoteRequestRead:
    """Partial update. Sending ``line_items`` replaces the whole set."""
    quote = _get_quote(db, broker_id, quote_id)
    data = payload.model_dump(exclude_unset=True)
    new_items = data.pop("line_items", None)

    for field, value in data.items():
        setattr(quote, field, value)
    if new_items is not None:
        _replace_line_items(quote, [QuoteLineItemCreate(**item) for item in new_items])

    _enforce_declared_value(
        db, quote, explicit="declared_value_uf" in payload.model_fields_set
    )
    db.commit()
    db.refresh(quote)
    return _read(quote, _proposal_count(db, quote.id))


@router.delete("/{quote_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quote_request(
    quote_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Delete")),
) -> Response:
    """Delete a quote request. Refused while proposals hang off it."""
    quote = _get_quote(db, broker_id, quote_id)
    count = _proposal_count(db, quote.id)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "quote_has_proposals",
                "message": (
                    f"Quote request {quote_id} has {count} proposal(s); delete or "
                    "withdraw them first."
                ),
                "proposal_count": count,
            },
        )
    db.delete(quote)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{quote_id}/send", response_model=QuoteRequestRead)
def send_quote_request(
    quote_id: int,
    payload: QuoteRequestSend,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Submit")),
) -> QuoteRequestRead:
    """Mark the request as sent to a set of insurers and move the placement to quoting.

    Only records the send-time snapshot — actually delivering the email/portal
    message is a later pass.
    """
    quote = _get_quote(db, broker_id, quote_id)
    if quote.status not in (QuoteRequestStatus.DRAFT, QuoteRequestStatus.SENT):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "quote_not_sendable",
                "message": f"A quote request in status '{quote.status}' cannot be sent.",
                "status": str(quote.status),
            },
        )

    known = set(
        db.scalars(
            select(Insurer.id).where(Insurer.id.in_(payload.recipient_insurer_ids))
        ).all()
    )
    missing = [i for i in payload.recipient_insurer_ids if i not in known]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unknown_insurers",
                "message": "One or more recipient insurers do not exist.",
                "field": "recipient_insurer_ids",
                "unknown_insurer_ids": missing,
            },
        )

    quote.recipient_insurer_ids = list(payload.recipient_insurer_ids)
    quote.status = QuoteRequestStatus.SENT
    quote.sent_at = datetime.now(timezone.utc)
    if payload.due_at is not None:
        quote.due_at = payload.due_at

    placement = _get_placement(db, broker_id, quote.placement_id)
    if placement.status in (
        PlacementStatus.DRAFT,
        PlacementStatus.INSPECTION,
        PlacementStatus.PRE_UNDERWRITING,
    ):
        placement.status = PlacementStatus.QUOTING

    db.commit()
    db.refresh(quote)
    return _read(quote, _proposal_count(db, quote.id))


# --- Line items --------------------------------------------------------------

@router.get("/{quote_id}/line-items", response_model=list[QuoteLineItemRead])
def list_line_items(
    quote_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "View")),
) -> list[QuoteLineItem]:
    quote = _get_quote(db, broker_id, quote_id)
    return list(quote.line_items)


@router.post(
    "/{quote_id}/line-items",
    response_model=QuoteRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def add_line_item(
    quote_id: int,
    payload: QuoteLineItemCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Edit")),
    sync_declared_value: bool = Query(
        default=True,
        description=(
            "Re-derive declared_value_uf from the resulting line items. "
            "Set false to keep the declared value fixed and get a 422 on mismatch."
        ),
    ),
) -> QuoteRequestRead:
    """Append one line item; the whole quote request is returned re-totalled."""
    quote = _get_quote(db, broker_id, quote_id)
    quote.line_items.append(
        QuoteLineItem(
            name=payload.name,
            value_uf=payload.value_uf,
            detail=payload.detail,
            sort_order=payload.sort_order if payload.sort_order else len(quote.line_items),
        )
    )
    _enforce_declared_value(db, quote, explicit=not sync_declared_value)
    db.commit()
    db.refresh(quote)
    return _read(quote, _proposal_count(db, quote.id))


@router.put("/{quote_id}/line-items", response_model=QuoteRequestRead)
def replace_line_items(
    quote_id: int,
    payload: list[QuoteLineItemCreate] = Body(...),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Edit")),
    sync_declared_value: bool = Query(default=True),
) -> QuoteRequestRead:
    """Replace the entire line-item set in one call."""
    quote = _get_quote(db, broker_id, quote_id)
    _replace_line_items(quote, payload)
    _enforce_declared_value(db, quote, explicit=not sync_declared_value)
    db.commit()
    db.refresh(quote)
    return _read(quote, _proposal_count(db, quote.id))


@router.patch("/{quote_id}/line-items/{item_id}", response_model=QuoteRequestRead)
def update_line_item(
    quote_id: int,
    item_id: int,
    payload: QuoteLineItemUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Edit")),
    sync_declared_value: bool = Query(default=True),
) -> QuoteRequestRead:
    quote = _get_quote(db, broker_id, quote_id)
    item = _get_line_item(db, quote, item_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    _enforce_declared_value(db, quote, explicit=not sync_declared_value)
    db.commit()
    db.refresh(quote)
    return _read(quote, _proposal_count(db, quote.id))


@router.delete("/{quote_id}/line-items/{item_id}", response_model=QuoteRequestRead)
def delete_line_item(
    quote_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Quotes", "Edit")),
    sync_declared_value: bool = Query(default=True),
) -> QuoteRequestRead:
    quote = _get_quote(db, broker_id, quote_id)
    item = _get_line_item(db, quote, item_id)
    quote.line_items.remove(item)
    _enforce_declared_value(db, quote, explicit=not sync_declared_value)
    db.commit()
    db.refresh(quote)
    return _read(quote, _proposal_count(db, quote.id))


# --- Comparison --------------------------------------------------------------

@router.get("/{quote_id}/comparison", response_model=ProposalComparison)
def compare_proposals(
    quote_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
    include_rejected: bool = Query(
        default=False, description="Include rejected/withdrawn/expired proposals."
    ),
) -> ProposalComparison:
    """Align every proposal of a quote request side by side.

    Returns three grids the UI can render directly:
    ``columns`` (one per proposal: premiums, rates, commission, validity),
    ``deductibles`` (one row per peril seen anywhere, so a peril a competitor
    priced and this one ignored is visible as a hole), and ``coverages`` /
    ``exclusions`` (rows aligned on ``normalized_code``, falling back to a
    normalised slug of the text — that is the "who covers what" matrix).
    """
    quote = _get_quote(db, broker_id, quote_id)

    filters = [
        Proposal.quote_request_id == quote.id,
        Proposal.broker_id == broker_id,
    ]
    if not include_rejected:
        filters.append(
            Proposal.status.in_(
                [
                    ProposalStatus.DRAFT,
                    ProposalStatus.SUBMITTED,
                    ProposalStatus.ACCEPTED,
                ]
            )
        )
    proposals = list(
        db.scalars(
            select(Proposal)
            .options(selectinload(Proposal.coverages), selectinload(Proposal.insurer))
            .where(*filters)
            .order_by(Proposal.id)
        ).all()
    )

    columns: list[ComparisonColumn] = []
    for proposal in proposals:
        coverages = list(proposal.coverages)
        columns.append(
            ComparisonColumn(
                proposal_id=proposal.id,
                insurer=InsurerRef.model_validate(proposal.insurer),
                origin=proposal.origin,
                status=proposal.status,
                is_confirmed=proposal.is_confirmed,
                source_document_id=proposal.source_document_id,
                extraction_confidence=proposal.extraction_confidence,
                modality=proposal.modality,
                activity_classification=proposal.activity_classification,
                taxable_premium_uf=proposal.taxable_premium_uf,
                exempt_premium_uf=proposal.exempt_premium_uf,
                net_premium_uf=proposal.net_premium_uf,
                vat_uf=proposal.vat_uf,
                total_premium_uf=proposal.total_premium_uf,
                taxable_rate_permille=proposal.taxable_rate_permille,
                exempt_rate_permille=proposal.exempt_rate_permille,
                comprehensive_rate_permille=proposal.comprehensive_rate_permille,
                commission_pct=proposal.commission_pct,
                validity_business_days=proposal.validity_business_days,
                coverage_start=proposal.coverage_start,
                coverage_end=proposal.coverage_end,
                received_at=proposal.received_at,
                warranties=proposal.warranties,
                coverage_count=sum(1 for c in coverages if c.kind == CoverageKind.COVERAGE),
                exclusion_count=sum(1 for c in coverages if c.kind == CoverageKind.EXCLUSION),
            )
        )

    perils = _union_perils(proposals)
    deductible_rows = [
        DeductibleRow(
            peril=peril,
            cells=[
                DeductibleCell(
                    proposal_id=p.id, term=(p.deductibles or {}).get(peril)
                )
                for p in proposals
            ],
        )
        for peril in perils
    ]

    coverage_rows = _coverage_matrix(proposals, CoverageKind.COVERAGE)
    exclusion_rows = _coverage_matrix(proposals, CoverageKind.EXCLUSION)

    return ProposalComparison(
        quote=ComparisonQuote(
            id=quote.id,
            placement_id=quote.placement_id,
            insured_object=quote.insured_object,
            declared_value_uf=quote.declared_value_uf,
            currency=quote.currency,
            status=quote.status,
            desired_start=quote.desired_start,
            desired_end=quote.desired_end,
            due_at=quote.due_at,
            line_items_total_uf=line_items_total(
                [item.value_uf for item in quote.line_items]
            ),
        ),
        proposal_count=len(proposals),
        columns=columns,
        perils=perils,
        deductibles=deductible_rows,
        coverages=coverage_rows,
        exclusions=exclusion_rows,
        highlights=_highlights(proposals),
    )


def _union_perils(proposals: list[Proposal]) -> list[str]:
    """Every peril mentioned by any proposal, in first-seen order."""
    seen: list[str] = []
    for proposal in proposals:
        for peril in (proposal.deductibles or {}):
            if peril not in seen:
                seen.append(peril)
    return seen


def _coverage_matrix(proposals: list[Proposal], kind: CoverageKind) -> list[CoverageRow]:
    """Align coverage (or exclusion) items across proposals into grid rows."""
    order: list[str] = []
    labels: dict[str, str] = {}
    codes: dict[str, str | None] = {}
    per_proposal: dict[int, dict[str, str]] = {p.id: {} for p in proposals}

    for proposal in proposals:
        for item in proposal.coverages:
            if item.kind != kind:
                continue
            key = coverage_key(item.normalized_code, item.text)
            if key not in labels:
                order.append(key)
                labels[key] = item.text
                codes[key] = item.normalized_code
            per_proposal[proposal.id][key] = item.text

    rows: list[CoverageRow] = []
    for key in order:
        rows.append(
            CoverageRow(
                key=key,
                label=labels[key],
                kind=kind,
                normalized_code=codes[key],
                cells=[
                    CoverageCell(
                        proposal_id=p.id,
                        included=key in per_proposal[p.id],
                        text=per_proposal[p.id].get(key),
                    )
                    for p in proposals
                ],
            )
        )
    return rows


def _highlights(proposals: list[Proposal]) -> ComparisonHighlights:
    """Cheap, explainable "best of" markers — no scoring model, just extremes."""

    def _best(attr: str, *, largest: bool) -> int | None:
        candidates: list[tuple[Decimal, int]] = [
            (Decimal(getattr(p, attr)), p.id)
            for p in proposals
            if getattr(p, attr) is not None
        ]
        if not candidates:
            return None
        chosen = max(candidates) if largest else min(candidates)
        return chosen[1]

    return ComparisonHighlights(
        lowest_total_premium_proposal_id=_best("total_premium_uf", largest=False),
        lowest_comprehensive_rate_proposal_id=_best(
            "comprehensive_rate_permille", largest=False
        ),
        highest_commission_proposal_id=_best("commission_pct", largest=True),
    )


__all__ = ["router"]
