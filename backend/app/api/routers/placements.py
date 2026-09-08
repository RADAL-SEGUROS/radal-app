"""``/placements`` — the operating folder: asset x insurance line x period.

The placement carries the workflow status machine. Status is **never** written
by the generic PATCH; it moves only through ``POST /placements/{id}/transition``,
validated against :data:`ALLOWED_TRANSITIONS`. ``GET /placements/{id}/transitions``
returns exactly which moves are legal right now, so the UI can enable/disable
its controls instead of rendering a button that silently fails.

Every transition writes an ``activity`` row (``placement.transitioned``) — the
status history of a folder is auditable from the activity trail alone.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import (
    OPEN_PLACEMENT_STATUSES,
    count_total,
    counts_by,
    paginate,
    record_activity,
)
from app.models.asset import Asset
from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.document import Document
from app.models.enums import EntityType
from app.models.inspection import InspectionRequest
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.placement import Placement, PlacementStatus
from app.models.policy import Policy
from app.models.proposal import Proposal
from app.models.quote import QuoteRequest
from app.models.user import User
from app.schemas.placement import (
    PlacementClientSummary,
    PlacementCreate,
    PlacementListItem,
    PlacementPage,
    PlacementRead,
    PlacementRefSummary,
    PlacementSummary,
    PlacementTransition,
    PlacementTransitionOptions,
    PlacementUpdate,
)

router = APIRouter(prefix="/placements", tags=["placements"])

# The status machine. Forward moves follow the canonical path; one step back is
# allowed so an operator can correct a mistake, and `closed` is reachable from
# anywhere (a deal can die at any point). `closed` itself is terminal.
ALLOWED_TRANSITIONS: dict[PlacementStatus, tuple[PlacementStatus, ...]] = {
    PlacementStatus.DRAFT: (
        PlacementStatus.INSPECTION,
        PlacementStatus.PRE_UNDERWRITING,
        PlacementStatus.QUOTING,
        PlacementStatus.CLOSED,
    ),
    PlacementStatus.INSPECTION: (
        PlacementStatus.PRE_UNDERWRITING,
        PlacementStatus.QUOTING,
        PlacementStatus.DRAFT,
        PlacementStatus.CLOSED,
    ),
    PlacementStatus.PRE_UNDERWRITING: (
        PlacementStatus.QUOTING,
        PlacementStatus.INSPECTION,
        PlacementStatus.CLOSED,
    ),
    PlacementStatus.QUOTING: (
        PlacementStatus.NEGOTIATING,
        PlacementStatus.AWARDED,
        PlacementStatus.PRE_UNDERWRITING,
        PlacementStatus.CLOSED,
    ),
    PlacementStatus.NEGOTIATING: (
        PlacementStatus.AWARDED,
        PlacementStatus.QUOTING,
        PlacementStatus.CLOSED,
    ),
    PlacementStatus.AWARDED: (
        PlacementStatus.ACTIVE,
        PlacementStatus.NEGOTIATING,
        PlacementStatus.CLOSED,
    ),
    PlacementStatus.ACTIVE: (PlacementStatus.CLOSED,),
    PlacementStatus.CLOSED: (),
}

IN_MARKET_STATUSES = (PlacementStatus.QUOTING, PlacementStatus.NEGOTIATING)


# --- Helpers -----------------------------------------------------------------


def _get_placement_or_404(db: Session, placement_id: int, broker_id: int) -> Placement:
    """Fetch a placement inside the tenant, or 404."""
    placement = db.execute(
        select(Placement)
        .where(Placement.id == placement_id, Placement.broker_id == broker_id)
        .options(
            selectinload(Placement.client).selectinload(Client.insured),
            selectinload(Placement.asset),
            selectinload(Placement.insurance_line),
        )
    ).scalar_one_or_none()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Placement not found"
        )
    return placement


def _resolve_asset(db: Session, asset_id: int, broker_id: int) -> Asset:
    asset = db.execute(
        select(Asset).where(Asset.id == asset_id, Asset.broker_id == broker_id)
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


def _resolve_insurance_line(db: Session, line_id: int, broker_id: int) -> InsuranceLine:
    """A broker may use its own lines or the global (broker_id NULL) catalogue."""
    line = db.execute(
        select(InsuranceLine).where(
            InsuranceLine.id == line_id,
            or_(InsuranceLine.broker_id == broker_id, InsuranceLine.broker_id.is_(None)),
        )
    ).scalar_one_or_none()
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Insurance line not found"
        )
    if not line.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Insurance line is not active",
        )
    return line


def _validate_brief_document(db: Session, document_id: int | None, broker_id: int) -> None:
    """The technical brief must be one of this broker's documents (rule 8: FK only)."""
    if document_id is None:
        return
    exists = db.execute(
        select(Document.id).where(
            Document.id == document_id, Document.broker_id == broker_id
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="brief_document_id does not belong to this broker",
        )


#: The period columns the folder owns. Editing any of them touches the vigencia.
_PERIOD_FIELDS = ("period", "period_start", "period_end")


def _wrapping_account(db: Session, placement: Placement) -> CaseFile | None:
    """The account folder this placement belongs to, if any.

    ``placement.case_file_id`` is the authority (spec v3 §2.5); the legacy
    ``case_file.placement_id`` back-pointer is honoured too, because the
    importer writes it first.
    """
    from app.services.case_files import ACCOUNT_KINDS

    clauses = [CaseFile.placement_id == placement.id]
    if placement.case_file_id is not None:
        clauses.append(CaseFile.id == placement.case_file_id)
    return db.scalars(
        select(CaseFile)
        .where(
            CaseFile.broker_id == placement.broker_id,
            CaseFile.kind.in_(ACCOUNT_KINDS),
            or_(*clauses),
        )
        .order_by(CaseFile.id.asc())
        .limit(1)
    ).first()


def _guard_period_edit(db: Session, placement: Placement, changes: dict) -> CaseFile | None:
    """Rule 1: a vigencia date change creates a NEW folder, never an edit.

    While the wrapping account has no stage event beyond ``intake`` the period
    is still a draft and the edit is allowed — and it SYNCS to the folder, so
    the two never disagree. Afterwards the only door is
    ``POST /case-files/{id}/reperiod``.
    """
    touched = [field for field in _PERIOD_FIELDS if field in changes]
    if not touched:
        return None
    case = _wrapping_account(db, placement)
    if case is None:
        return None

    from app.services.case_files import is_period_locked

    if is_period_locked(db, case):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "period_locked",
                "case_file_id": case.id,
                "detail": (
                    "The validity period is a folder; use "
                    "POST /case-files/{id}/reperiod"
                ),
            },
        )
    return case


def _sync_period_to_folder(db: Session, case: CaseFile | None, placement: Placement) -> None:
    """Carry an allowed period edit up to the folder it belongs to.

    Rule 5 still applies on the way up: ``period_start``/``period_end`` are two
    of the five columns of the folder uniqueness key, so this — the one period
    mutation the spec deliberately leaves open — is also the one path that could
    slide a folder onto a sibling's key. Refuse with the same ``folder_exists``
    error the create / renew / reperiod doors raise.
    """
    if case is None:
        return
    from app.services.case_files import find_open_folder, period_label_for

    clash = find_open_folder(
        db,
        broker_id=case.broker_id,
        account_group_id=case.account_group_id,
        insurance_line_id=case.insurance_line_id,
        period_start=placement.period_start,
        period_end=placement.period_end,
        exclude_id=case.id,
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "folder_exists",
                "case_file_id": clash.id,
                "reference": clash.reference,
                "detail": (
                    "An open folder for this group, line and vigencia already "
                    f"exists (case file {clash.id})"
                ),
            },
        )

    case.period_start = placement.period_start
    case.period_end = placement.period_end
    case.period_label = period_label_for(placement.period_start, placement.period_end)


def _placement_counts(db: Session, placement_ids: list[int]) -> dict[int, dict[str, int]]:
    """Per-placement child counters, in three grouped queries."""
    zero = {
        "quote_requests_count": 0,
        "proposals_count": 0,
        "inspection_requests_count": 0,
    }
    out: dict[int, dict[str, int]] = {pid: dict(zero) for pid in placement_ids}
    if not placement_ids:
        return out

    quotes = counts_by(
        db,
        select(QuoteRequest.placement_id, func.count(QuoteRequest.id))
        .where(QuoteRequest.placement_id.in_(placement_ids))
        .group_by(QuoteRequest.placement_id),
    )
    proposals = counts_by(
        db,
        select(QuoteRequest.placement_id, func.count(Proposal.id))
        .join(Proposal, Proposal.quote_request_id == QuoteRequest.id)
        .where(QuoteRequest.placement_id.in_(placement_ids))
        .group_by(QuoteRequest.placement_id),
    )
    inspections = counts_by(
        db,
        select(InspectionRequest.placement_id, func.count(InspectionRequest.id))
        .where(InspectionRequest.placement_id.in_(placement_ids))
        .group_by(InspectionRequest.placement_id),
    )
    for pid in placement_ids:
        out[pid]["quote_requests_count"] = quotes.get(pid, 0)
        out[pid]["proposals_count"] = proposals.get(pid, 0)
        out[pid]["inspection_requests_count"] = inspections.get(pid, 0)
    return out


def _days_to_period_end(placement: Placement) -> int | None:
    if placement.period_end is None:
        return None
    return (placement.period_end - date.today()).days


def _refs(placement: Placement) -> dict[str, object]:
    client = placement.client
    insured = client.insured if client else None
    return {
        "client": PlacementClientSummary(
            id=client.id,
            legal_name=insured.legal_name if insured else None,
            rut=insured.rut if insured else None,
        )
        if client
        else None,
        "asset": PlacementRefSummary(id=placement.asset.id, name=placement.asset.name)
        if placement.asset
        else None,
        "insurance_line": PlacementRefSummary(
            id=placement.insurance_line.id, name=placement.insurance_line.name
        )
        if placement.insurance_line
        else None,
    }


def _serialize(placement: Placement, counts: dict[str, int], *, detail: bool = False):
    model = PlacementRead if detail else PlacementListItem
    update = {
        **counts,
        **_refs(placement),
        "days_to_period_end": _days_to_period_end(placement),
    }
    if detail:
        update["allowed_transitions"] = list(ALLOWED_TRANSITIONS[placement.status])
    return model.model_validate(placement).model_copy(update=update)


def _detail_response(db: Session, placement: Placement) -> PlacementRead:
    counts = _placement_counts(db, [placement.id])[placement.id]
    counts["policies_count"] = db.execute(
        select(func.count(Policy.id)).where(Policy.placement_id == placement.id)
    ).scalar_one()
    return _serialize(placement, counts, detail=True)


# --- Endpoints ---------------------------------------------------------------


@router.get("/summary", response_model=PlacementSummary)
def get_placements_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Placements", "View")),
    client_id: int | None = None,
) -> PlacementSummary:
    """Every counter the placements list view needs, in one round trip."""
    scope = [Placement.broker_id == broker_id]
    if client_id is not None:
        scope.append(Placement.client_id == client_id)

    by_status_rows = db.execute(
        select(Placement.status, func.count(Placement.id))
        .where(*scope)
        .group_by(Placement.status)
    ).all()
    by_status = {str(key): value for key, value in by_status_rows}

    by_line_rows = db.execute(
        select(InsuranceLine.name, func.count(Placement.id))
        .join(InsuranceLine, InsuranceLine.id == Placement.insurance_line_id)
        .where(*scope)
        .group_by(InsuranceLine.name)
    ).all()

    horizon = date.today() + timedelta(days=60)
    expiring = db.execute(
        select(func.count(Placement.id)).where(
            *scope,
            Placement.period_end.is_not(None),
            Placement.period_end <= horizon,
            Placement.period_end >= date.today(),
            Placement.status != PlacementStatus.CLOSED,
        )
    ).scalar_one()

    return PlacementSummary(
        total=sum(by_status.values()),
        by_status={s.value: by_status.get(s.value, 0) for s in PlacementStatus},
        by_insurance_line={str(name): count for name, count in by_line_rows},
        open=sum(
            count
            for key, count in by_status.items()
            if key != PlacementStatus.CLOSED.value
        ),
        in_market=sum(by_status.get(s.value, 0) for s in IN_MARKET_STATUSES),
        awaiting_inspection=by_status.get(PlacementStatus.INSPECTION.value, 0),
        expiring_within_60_days=expiring,
    )


@router.get("", response_model=PlacementPage)
def list_placements(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Placements", "View")),
    client_id: int | None = None,
    asset_id: int | None = None,
    insurance_line_id: int | None = None,
    account_group_id: int | None = Query(None, gt=0),
    status_filter: list[PlacementStatus] | None = Query(None, alias="status"),
    period: str | None = None,
    open_only: bool = Query(False, description="Exclude closed placements"),
    q: str | None = Query(None, description="Search asset name, period or client"),
    sort: Literal["created_at", "period_end", "status", "period"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> PlacementPage:
    """List the broker's placements, filtered and paginated."""
    stmt = select(Placement).where(Placement.broker_id == broker_id)

    if client_id is not None:
        stmt = stmt.where(Placement.client_id == client_id)
    if asset_id is not None:
        stmt = stmt.where(Placement.asset_id == asset_id)
    if insurance_line_id is not None:
        stmt = stmt.where(Placement.insurance_line_id == insurance_line_id)
    if account_group_id is not None:
        # A placement belongs to a group through its folder, or — for the ones
        # that have no folder yet (an honest empty state, spec §3.2) — through
        # its client.
        stmt = stmt.where(
            or_(
                Placement.case_file_id.in_(
                    select(CaseFile.id).where(
                        CaseFile.broker_id == broker_id,
                        CaseFile.account_group_id == account_group_id,
                    )
                ),
                Placement.client_id.in_(
                    select(Client.id).where(
                        Client.broker_id == broker_id,
                        Client.account_group_id == account_group_id,
                    )
                ),
            )
        )
    if status_filter:
        stmt = stmt.where(Placement.status.in_(status_filter))
    if open_only:
        stmt = stmt.where(Placement.status != PlacementStatus.CLOSED)
    if period:
        stmt = stmt.where(Placement.period == period)
    if q:
        needle = f"%{q.strip()}%"
        stmt = (
            stmt.join(Asset, Asset.id == Placement.asset_id)
            .join(Client, Client.id == Placement.client_id)
            .join(Insured, Insured.id == Client.insured_id)
            .where(
                or_(
                    Asset.name.ilike(needle),
                    Placement.period.ilike(needle),
                    Insured.legal_name.ilike(needle),
                    Insured.rut.ilike(needle),
                )
            )
        )

    total = count_total(db, stmt)

    sort_column = {
        "created_at": Placement.created_at,
        "period_end": Placement.period_end,
        "status": Placement.status,
        "period": Placement.period,
    }[sort]
    stmt = stmt.order_by(
        sort_column.asc() if order == "asc" else sort_column.desc(), Placement.id.desc()
    )

    rows = (
        db.execute(
            stmt.options(
                selectinload(Placement.client).selectinload(Client.insured),
                selectinload(Placement.asset),
                selectinload(Placement.insurance_line),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    counts = _placement_counts(db, [row.id for row in rows])
    return PlacementPage(
        items=[_serialize(row, counts[row.id]) for row in rows],
        **paginate(total, page, page_size),
    )


@router.post("", response_model=PlacementRead, status_code=status.HTTP_201_CREATED)
def create_placement(
    payload: PlacementCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Placements", "Create")),
) -> PlacementRead:
    """Open a placement on an asset. ``client_id`` is derived from the asset."""
    asset = _resolve_asset(db, payload.asset_id, broker_id)
    line = _resolve_insurance_line(db, payload.insurance_line_id, broker_id)
    _validate_brief_document(db, payload.brief_document_id, broker_id)

    placement = Placement(
        broker_id=broker_id,
        client_id=asset.client_id,
        asset_id=asset.id,
        insurance_line_id=line.id,
        period=payload.period,
        period_start=payload.period_start,
        period_end=payload.period_end,
        status=payload.status,
        notes=payload.notes,
        brief_document_id=payload.brief_document_id,
    )
    db.add(placement)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="placement.created",
        entity_type=EntityType.PLACEMENT,
        entity_id=placement.id,
        description=f"Colocación {line.name} · {asset.name} ({placement.period or 's/p'})",
        meta={
            "asset_id": asset.id,
            "client_id": asset.client_id,
            "insurance_line_id": line.id,
            "status": placement.status.value,
        },
    )
    db.commit()
    db.refresh(placement)
    return _detail_response(db, placement)


@router.get("/{placement_id}", response_model=PlacementRead)
def get_placement(
    placement_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Placements", "View")),
) -> PlacementRead:
    """One placement, with its refs, counters and legal next statuses."""
    placement = _get_placement_or_404(db, placement_id, broker_id)
    return _detail_response(db, placement)


@router.get("/{placement_id}/transitions", response_model=PlacementTransitionOptions)
def get_placement_transitions(
    placement_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Placements", "View")),
) -> PlacementTransitionOptions:
    """The statuses this placement may move to right now.

    The UI drives its buttons off this list — anything not in it is rendered
    disabled rather than as a control that fails on click.
    """
    placement = _get_placement_or_404(db, placement_id, broker_id)
    allowed = list(ALLOWED_TRANSITIONS[placement.status])
    return PlacementTransitionOptions(
        id=placement.id,
        status=placement.status,
        allowed=allowed,
        is_terminal=not allowed,
    )


@router.post("/{placement_id}/transition", response_model=PlacementRead)
def transition_placement(
    placement_id: int,
    payload: PlacementTransition,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Placements", "Edit")),
) -> PlacementRead:
    """Move a placement along the status machine.

    409 when the move is not legal from the current status (the resource state
    conflicts with the request), 200 with no change when it is a no-op.
    """
    placement = _get_placement_or_404(db, placement_id, broker_id)
    previous = placement.status

    if payload.status == previous:
        return _detail_response(db, placement)

    allowed = ALLOWED_TRANSITIONS[previous]
    if payload.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot move a placement from '{previous.value}' to "
                f"'{payload.status.value}'. Allowed: "
                f"{', '.join(s.value for s in allowed) or 'none (terminal)'}"
            ),
        )

    placement.status = payload.status
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="placement.transitioned",
        entity_type=EntityType.PLACEMENT,
        entity_id=placement.id,
        description=f"Estado {previous.value} → {payload.status.value}",
        meta={
            "from": previous.value,
            "to": payload.status.value,
            "note": payload.note,
        },
    )
    db.commit()
    db.refresh(placement)
    return _detail_response(db, placement)


@router.patch("/{placement_id}", response_model=PlacementRead)
def update_placement(
    placement_id: int,
    payload: PlacementUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Placements", "Edit")),
) -> PlacementRead:
    """Patch a placement's period, line, brief or notes (never its status)."""
    placement = _get_placement_or_404(db, placement_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _detail_response(db, placement)

    if changes.get("insurance_line_id") is not None:
        _resolve_insurance_line(db, changes["insurance_line_id"], broker_id)
    if "brief_document_id" in changes:
        _validate_brief_document(db, changes["brief_document_id"], broker_id)

    start = changes.get("period_start", placement.period_start)
    end = changes.get("period_end", placement.period_end)
    if start and end and end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_end must not be earlier than period_start",
        )

    folder = _guard_period_edit(db, placement, changes)

    for field, value in changes.items():
        setattr(placement, field, value)

    if any(field in changes for field in _PERIOD_FIELDS):
        _sync_period_to_folder(db, folder, placement)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="placement.updated",
        entity_type=EntityType.PLACEMENT,
        entity_id=placement.id,
        description=f"Colocación actualizada ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(placement)
    return _detail_response(db, placement)


@router.delete(
    "/{placement_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
def delete_placement(
    placement_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Placements", "Delete")),
) -> Response:
    """Delete a placement.

    Only a ``draft`` with no quote requests, inspection requests or policies can
    be deleted; anything further along is closed, not erased.
    """
    placement = _get_placement_or_404(db, placement_id, broker_id)

    if placement.status != PlacementStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a draft placement can be deleted — close it instead",
        )

    counts = _placement_counts(db, [placement.id])[placement.id]
    policies = db.execute(
        select(func.count(Policy.id)).where(Policy.placement_id == placement.id)
    ).scalar_one()
    if any(counts.values()) or policies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Placement has quote requests, inspections or policies attached",
        )

    db.delete(placement)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="placement.deleted",
        entity_type=EntityType.PLACEMENT,
        entity_id=placement_id,
        description="Colocación eliminada",
        meta={"status": PlacementStatus.DRAFT.value},
    )
    db.commit()


__all__ = ["router", "ALLOWED_TRANSITIONS", "OPEN_PLACEMENT_STATUSES"]
