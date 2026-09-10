"""``/inspection-requests`` and ``/inspections`` — the inspection workflow.

Two routers live here because they are one workflow: a request is raised against
an asset (usually because ``insurance_line.requires_inspection``), an inspector
is assigned, and the resulting report is written — possibly several times, since
inspections are VERSIONED per asset.

Everything is tenant-scoped: every query filters ``broker_id`` from the JWT, and
every FK supplied by the client is proved to live inside the same broker before
it is written. A row belonging to another broker answers 404, never 403 — its
existence is not the caller's business.

Structure follows docs/v2-data-modeling-decisions.md §7: scores are flat
columns, the checklist is versioned JSON, boundaries (colindancias) are child
rows with their own CRUD.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db
from app.core.permissions import require_permission
from app.services.analytics import (
    ScopeFilters,
    build_entity_summary,
    group_by_query,
    scope_filters,
    scope_predicates,
)
from app.models import (
    Asset,
    Document,
    Inspection,
    InspectionBoundary,
    InspectionRequest,
    InspectionRequestStatus,
    InspectionStatus,
    InsuranceLine,
    Placement,
    User,
)
from app.models.enums import UserType
from app.schemas.analytics import EntitySummary
from app.schemas.inspection import (
    InspectionAssign,
    InspectionBoundaryCreate,
    InspectionBoundaryRead,
    InspectionBoundaryUpdate,
    InspectionChecklistUpdate,
    InspectionCreate,
    InspectionListResponse,
    InspectionRead,
    InspectionRequestCreate,
    InspectionRequestListResponse,
    InspectionRequestRead,
    InspectionRequestUpdate,
    InspectionStatusUpdate,
    InspectionUpdate,
    InspectionVersionCreate,
)

requests_router = APIRouter(prefix="/inspection-requests", tags=["inspections"])
router = APIRouter(prefix="/inspections", tags=["inspections"])

#: An issued or archived report is frozen; reopen it with a new version instead.
_FROZEN_STATUSES = (InspectionStatus.ISSUED, InspectionStatus.ARCHIVED)

#: Legal report transitions. Enforced so the UI never offers a dead control.
_STATUS_TRANSITIONS: dict[InspectionStatus, tuple[InspectionStatus, ...]] = {
    InspectionStatus.DRAFT: (InspectionStatus.IN_REVIEW, InspectionStatus.ARCHIVED),
    InspectionStatus.IN_REVIEW: (
        InspectionStatus.DRAFT,
        InspectionStatus.ISSUED,
        InspectionStatus.ARCHIVED,
    ),
    InspectionStatus.ISSUED: (InspectionStatus.ARCHIVED,),
    InspectionStatus.ARCHIVED: (),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# Tenant-scoped FK validation
# =============================================================================

def _require_asset(db: Session, asset_id: int, broker_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.broker_id != broker_id:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return asset


def _require_placement(db: Session, placement_id: int, broker_id: int) -> Placement:
    placement = db.get(Placement, placement_id)
    if placement is None or placement.broker_id != broker_id:
        raise HTTPException(
            status_code=404, detail=f"Placement {placement_id} not found"
        )
    return placement


def _require_insurance_line(db: Session, line_id: int, broker_id: int) -> InsuranceLine:
    line = db.get(InsuranceLine, line_id)
    # broker_id NULL = the global catalog row, usable by every broker.
    if line is None or (line.broker_id is not None and line.broker_id != broker_id):
        raise HTTPException(
            status_code=404, detail=f"Insurance line {line_id} not found"
        )
    return line


def _require_broker_user(db: Session, user_id: int, broker_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.broker_id != broker_id:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    if user.user_type is not UserType.BROKER:
        raise HTTPException(
            status_code=422,
            detail=f"User {user_id} is not a broker user and cannot be assigned",
        )
    return user


def _require_document(db: Session, document_id: int, broker_id: int) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.broker_id != broker_id:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return doc


def _require_inspection_request(
    db: Session, request_id: int, broker_id: int
) -> InspectionRequest:
    req = db.get(InspectionRequest, request_id)
    if req is None or req.broker_id != broker_id:
        raise HTTPException(
            status_code=404, detail=f"Inspection request {request_id} not found"
        )
    return req


def _get_inspection(db: Session, inspection_id: int, broker_id: int) -> Inspection:
    inspection = db.scalar(
        select(Inspection)
        .options(selectinload(Inspection.boundaries))
        .where(Inspection.id == inspection_id, Inspection.broker_id == broker_id)
    )
    if inspection is None:
        raise HTTPException(status_code=404, detail="Inspection not found")
    return inspection


def _validate_inspection_fks(
    db: Session, data: dict, broker_id: int, *, asset_id: int | None
) -> None:
    """Prove every optional FK in a create/update payload belongs to the broker."""
    if data.get("inspection_request_id") is not None:
        req = _require_inspection_request(db, data["inspection_request_id"], broker_id)
        if asset_id is not None and req.asset_id != asset_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Inspection request {req.id} targets asset {req.asset_id}, "
                    f"not asset {asset_id}"
                ),
            )
    if data.get("inspector_id") is not None:
        _require_broker_user(db, data["inspector_id"], broker_id)
    if data.get("report_document_id") is not None:
        _require_document(db, data["report_document_id"], broker_id)


# =============================================================================
# /inspection-requests
# =============================================================================

@requests_router.post(
    "", response_model=InspectionRequestRead, status_code=status.HTTP_201_CREATED
)
def create_inspection_request(
    payload: InspectionRequestCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Inspections", "Create")),
) -> InspectionRequest:
    """Raise a request to inspect an asset."""
    asset = _require_asset(db, payload.asset_id, broker_id)

    if payload.placement_id is not None:
        placement = _require_placement(db, payload.placement_id, broker_id)
        if placement.asset_id != asset.id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Placement {placement.id} covers asset {placement.asset_id}, "
                    f"not asset {asset.id}"
                ),
            )
    if payload.insurance_line_id is not None:
        _require_insurance_line(db, payload.insurance_line_id, broker_id)

    req = InspectionRequest(
        broker_id=broker_id,
        requested_by_id=current_user.id,
        requested_at=_utcnow(),
        **payload.model_dump(),
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@requests_router.get("", response_model=InspectionRequestListResponse)
def list_inspection_requests(
    asset_id: int | None = Query(default=None, gt=0),
    placement_id: int | None = Query(default=None, gt=0),
    request_status: InspectionRequestStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "View")),
) -> InspectionRequestListResponse:
    filters = [InspectionRequest.broker_id == broker_id]
    if asset_id is not None:
        filters.append(InspectionRequest.asset_id == asset_id)
    if placement_id is not None:
        filters.append(InspectionRequest.placement_id == placement_id)
    if request_status is not None:
        filters.append(InspectionRequest.status == request_status)

    total = db.scalar(select(func.count(InspectionRequest.id)).where(*filters)) or 0
    rows = db.scalars(
        select(InspectionRequest)
        .where(*filters)
        .order_by(InspectionRequest.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return InspectionRequestListResponse(
        total=int(total),
        items=[InspectionRequestRead.model_validate(r) for r in rows],
    )


@requests_router.get("/{request_id}", response_model=InspectionRequestRead)
def get_inspection_request(
    request_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "View")),
) -> InspectionRequest:
    return _require_inspection_request(db, request_id, broker_id)


@requests_router.patch("/{request_id}", response_model=InspectionRequestRead)
def update_inspection_request(
    request_id: int,
    payload: InspectionRequestUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> InspectionRequest:
    req = _require_inspection_request(db, request_id, broker_id)
    data = payload.model_dump(exclude_unset=True)

    if data.get("placement_id") is not None:
        placement = _require_placement(db, data["placement_id"], broker_id)
        if placement.asset_id != req.asset_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Placement {placement.id} covers asset {placement.asset_id}, "
                    f"not asset {req.asset_id}"
                ),
            )
    if data.get("insurance_line_id") is not None:
        _require_insurance_line(db, data["insurance_line_id"], broker_id)

    for field, value in data.items():
        setattr(req, field, value)
    db.commit()
    db.refresh(req)
    return req


@requests_router.post("/{request_id}/cancel", response_model=InspectionRequestRead)
def cancel_inspection_request(
    request_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> InspectionRequest:
    """Cancel a request that has not already been completed."""
    req = _require_inspection_request(db, request_id, broker_id)
    if req.status is InspectionRequestStatus.COMPLETED:
        raise HTTPException(
            status_code=409, detail="A completed inspection request cannot be cancelled"
        )
    req.status = InspectionRequestStatus.CANCELLED
    db.commit()
    db.refresh(req)
    return req


@requests_router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inspection_request(
    request_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Delete")),
) -> Response:
    """Delete a request. Refused once reports hang off it — cancel it instead."""
    req = _require_inspection_request(db, request_id, broker_id)
    linked = db.scalar(
        select(func.count(Inspection.id)).where(
            Inspection.inspection_request_id == req.id
        )
    )
    if linked:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Inspection request {req.id} has {int(linked)} inspection(s); "
                "cancel it instead of deleting"
            ),
        )
    db.delete(req)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# /inspections
# =============================================================================

def _next_version(db: Session, asset_id: int, broker_id: int) -> int:
    current = db.scalar(
        select(func.max(Inspection.version)).where(
            Inspection.asset_id == asset_id, Inspection.broker_id == broker_id
        )
    )
    return int(current or 0) + 1


@router.post("", response_model=InspectionRead, status_code=status.HTTP_201_CREATED)
def create_inspection(
    payload: InspectionCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Create")),
) -> Inspection:
    """Create an inspection report (version auto-assigned per asset)."""
    _require_asset(db, payload.asset_id, broker_id)
    data = payload.model_dump()
    boundaries = data.pop("boundaries", []) or []
    _validate_inspection_fks(db, data, broker_id, asset_id=payload.asset_id)

    version = data.pop("version", None) or _next_version(db, payload.asset_id, broker_id)
    if data.get("checklist") is not None and data.get("checklist_version") is None:
        data["checklist_version"] = 1

    inspection = Inspection(broker_id=broker_id, version=version, **data)
    for order, boundary in enumerate(boundaries):
        inspection.boundaries.append(
            InspectionBoundary(**{**boundary, "sort_order": boundary.get("sort_order") or order})
        )
    db.add(inspection)
    db.commit()
    db.refresh(inspection)
    return inspection


# NOTE: registered before ``/{inspection_id}`` so the literal path wins.
@router.get("/summary", response_model=EntitySummary)
def get_inspections_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "View")),
    scope: ScopeFilters = Depends(scope_filters),
    group_by: str | None = group_by_query(),
) -> EntitySummary:
    """The inspecciones board: status split.

    Scope: ``account_group_id`` resolves through the inspection's case file OR
    its asset's client; the date window sits on ``visit_date`` (the visit is the
    event — ``report_date`` is downstream paperwork). ``group_by`` accepts
    ``status | risk_classification | account_group | month``.
    """
    scoped = [
        Inspection.broker_id == broker_id,
        *scope_predicates("inspections", scope, broker_id),
    ]
    return build_entity_summary(
        db, "inspections", broker_id=broker_id, scoped=scoped, group_by=group_by
    )


@router.get("", response_model=InspectionListResponse)
def list_inspections(
    asset_id: int | None = Query(default=None, gt=0),
    inspection_request_id: int | None = Query(default=None, gt=0),
    scope: ScopeFilters = Depends(scope_filters),
    inspector_id: int | None = Query(default=None, gt=0),
    inspection_status: InspectionStatus | None = Query(default=None, alias="status"),
    min_overall_score: float | None = Query(default=None, ge=0, le=100),
    max_overall_score: float | None = Query(default=None, ge=0, le=100),
    latest_only: bool = Query(
        default=False,
        description="Return only the highest version per asset.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "View")),
) -> InspectionListResponse:
    """List the broker's inspections.

    Scope params: ``account_group_id`` (the grupo — via the inspection's
    expediente or the asset's client), ``case_file_id`` (the grupo-cuenta) and
    ``date_from`` / ``date_to`` on ``visit_date``.
    """
    filters = [
        Inspection.broker_id == broker_id,
        *scope_predicates("inspections", scope, broker_id),
    ]
    if asset_id is not None:
        filters.append(Inspection.asset_id == asset_id)
    if inspection_request_id is not None:
        filters.append(Inspection.inspection_request_id == inspection_request_id)
    if inspector_id is not None:
        filters.append(Inspection.inspector_id == inspector_id)
    if inspection_status is not None:
        filters.append(Inspection.status == inspection_status)
    if min_overall_score is not None:
        filters.append(Inspection.overall_score >= min_overall_score)
    if max_overall_score is not None:
        filters.append(Inspection.overall_score <= max_overall_score)

    if latest_only:
        # Portable (no window functions): join against max(version) per asset.
        latest = (
            select(
                Inspection.asset_id.label("asset_id"),
                func.max(Inspection.version).label("version"),
            )
            .where(Inspection.broker_id == broker_id)
            .group_by(Inspection.asset_id)
            .subquery()
        )
        filters.append(
            Inspection.id.in_(
                select(Inspection.id).join(
                    latest,
                    (Inspection.asset_id == latest.c.asset_id)
                    & (Inspection.version == latest.c.version),
                )
            )
        )

    total = db.scalar(select(func.count(Inspection.id)).where(*filters)) or 0
    rows = db.scalars(
        select(Inspection)
        .options(selectinload(Inspection.boundaries))
        .where(*filters)
        .order_by(Inspection.asset_id.desc(), Inspection.version.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return InspectionListResponse(
        total=int(total), items=[InspectionRead.model_validate(r) for r in rows]
    )


@router.get("/{inspection_id}", response_model=InspectionRead)
def get_inspection(
    inspection_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "View")),
) -> Inspection:
    return _get_inspection(db, inspection_id, broker_id)


@router.patch("/{inspection_id}", response_model=InspectionRead)
def update_inspection(
    inspection_id: int,
    payload: InspectionUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> Inspection:
    """Patch report fields, scores and checklist. Issued reports are frozen."""
    inspection = _get_inspection(db, inspection_id, broker_id)
    data = payload.model_dump(exclude_unset=True)

    if inspection.status in _FROZEN_STATUSES and set(data) - {"status"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Inspection {inspection.id} is {inspection.status.value} and is "
                "read-only; create a new version to amend it"
            ),
        )
    if "status" in data and data["status"] is not None:
        _assert_transition(inspection.status, data["status"])

    _validate_inspection_fks(db, data, broker_id, asset_id=inspection.asset_id)

    for field, value in data.items():
        setattr(inspection, field, value)
    db.commit()
    db.refresh(inspection)
    return inspection


@router.post("/{inspection_id}/assign", response_model=InspectionRead)
def assign_inspection(
    inspection_id: int,
    payload: InspectionAssign,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> Inspection:
    """Assign (or clear) the inspector, and move the request to in_progress."""
    inspection = _get_inspection(db, inspection_id, broker_id)
    if inspection.status in _FROZEN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Inspection {inspection.id} is {inspection.status.value} and is read-only",
        )
    if payload.inspector_id is not None:
        _require_broker_user(db, payload.inspector_id, broker_id)
    inspection.inspector_id = payload.inspector_id

    if inspection.inspection_request_id is not None and payload.inspector_id is not None:
        req = db.get(InspectionRequest, inspection.inspection_request_id)
        if req is not None and req.status in (
            InspectionRequestStatus.PENDING,
            InspectionRequestStatus.SCHEDULED,
        ):
            req.status = InspectionRequestStatus.IN_PROGRESS

    db.commit()
    db.refresh(inspection)
    return inspection


def _assert_transition(current: InspectionStatus, target: InspectionStatus) -> None:
    if target is current:
        return
    if target not in _STATUS_TRANSITIONS.get(current, ()):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move an inspection from {current.value} to {target.value}",
        )


@router.post("/{inspection_id}/status", response_model=InspectionRead)
def set_inspection_status(
    inspection_id: int,
    payload: InspectionStatusUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Submit")),
) -> Inspection:
    """Advance the report through draft -> in_review -> issued -> archived."""
    inspection = _get_inspection(db, inspection_id, broker_id)
    _assert_transition(inspection.status, payload.status)

    if payload.status is InspectionStatus.ISSUED:
        if inspection.report_document_id is None:
            raise HTTPException(
                status_code=422,
                detail="An issued inspection needs report_document_id (upload the report first)",
            )
        if inspection.report_date is None:
            inspection.report_date = _utcnow().date()

    inspection.status = payload.status

    if (
        payload.status is InspectionStatus.ISSUED
        and inspection.inspection_request_id is not None
    ):
        req = db.get(InspectionRequest, inspection.inspection_request_id)
        if req is not None and req.status is not InspectionRequestStatus.CANCELLED:
            req.status = InspectionRequestStatus.COMPLETED

    db.commit()
    db.refresh(inspection)
    return inspection


@router.put("/{inspection_id}/checklist", response_model=InspectionRead)
def replace_checklist(
    inspection_id: int,
    payload: InspectionChecklistUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> Inspection:
    """Replace the versioned checklist JSON wholesale."""
    inspection = _get_inspection(db, inspection_id, broker_id)
    if inspection.status in _FROZEN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Inspection {inspection.id} is {inspection.status.value} and is read-only",
        )
    inspection.checklist = payload.checklist
    inspection.checklist_version = payload.checklist_version or (
        (inspection.checklist_version or 0) + 1
    )
    db.commit()
    db.refresh(inspection)
    return inspection


@router.post(
    "/{inspection_id}/versions",
    response_model=InspectionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_inspection_version(
    inspection_id: int,
    payload: InspectionVersionCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Create")),
) -> Inspection:
    """Fork an existing report into the next version for the same asset.

    This is how an issued report is amended: the original stays immutable and
    auditable, the copy starts in ``draft``.
    """
    source = _get_inspection(db, inspection_id, broker_id)
    if payload.inspector_id is not None:
        _require_broker_user(db, payload.inspector_id, broker_id)

    fork = Inspection(
        broker_id=broker_id,
        asset_id=source.asset_id,
        inspection_request_id=source.inspection_request_id,
        inspector_id=(
            payload.inspector_id if payload.inspector_id is not None else source.inspector_id
        ),
        version=_next_version(db, source.asset_id, broker_id),
        status=InspectionStatus.DRAFT,
        visit_date=payload.visit_date,
        folio=source.folio,
        findings_summary=source.findings_summary,
        checklist=dict(source.checklist) if (payload.copy_checklist and source.checklist) else None,
        checklist_version=source.checklist_version if payload.copy_checklist else None,
    )
    if payload.copy_scores:
        for field in (
            "technical_score",
            "commercial_score",
            "location_score",
            "loss_estimate_score",
            "overall_score",
            "pml_pct",
            "eml_pct",
            "risk_classification",
        ):
            setattr(fork, field, getattr(source, field))
    if payload.copy_boundaries:
        for boundary in source.boundaries:
            fork.boundaries.append(
                InspectionBoundary(
                    orientation=boundary.orientation,
                    description=boundary.description,
                    distance=boundary.distance,
                    aggravating=boundary.aggravating,
                    is_aggravating=boundary.is_aggravating,
                    sort_order=boundary.sort_order,
                )
            )

    db.add(fork)
    db.commit()
    db.refresh(fork)
    return fork


@router.delete("/{inspection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inspection(
    inspection_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Delete")),
) -> Response:
    """Delete a report (boundaries cascade). Issued reports must be archived."""
    inspection = _get_inspection(db, inspection_id, broker_id)
    if inspection.status is InspectionStatus.ISSUED:
        raise HTTPException(
            status_code=409,
            detail="An issued inspection cannot be deleted; archive it instead",
        )
    db.delete(inspection)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# /inspections/{id}/boundaries — child rows
# =============================================================================

@router.get("/{inspection_id}/boundaries", response_model=list[InspectionBoundaryRead])
def list_boundaries(
    inspection_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "View")),
) -> list[InspectionBoundary]:
    return _get_inspection(db, inspection_id, broker_id).boundaries


@router.post(
    "/{inspection_id}/boundaries",
    response_model=InspectionBoundaryRead,
    status_code=status.HTTP_201_CREATED,
)
def add_boundary(
    inspection_id: int,
    payload: InspectionBoundaryCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> InspectionBoundary:
    """Add one neighbouring property (colindancia) to the report."""
    inspection = _get_inspection(db, inspection_id, broker_id)
    if inspection.status in _FROZEN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Inspection {inspection.id} is {inspection.status.value} and is read-only",
        )
    data = payload.model_dump()
    if not data.get("sort_order"):
        data["sort_order"] = len(inspection.boundaries)
    boundary = InspectionBoundary(inspection_id=inspection.id, **data)
    db.add(boundary)
    db.commit()
    db.refresh(boundary)
    return boundary


def _get_boundary(
    db: Session, inspection_id: int, boundary_id: int, broker_id: int
) -> InspectionBoundary:
    _get_inspection(db, inspection_id, broker_id)  # proves tenancy
    boundary = db.get(InspectionBoundary, boundary_id)
    if boundary is None or boundary.inspection_id != inspection_id:
        raise HTTPException(status_code=404, detail="Boundary not found")
    return boundary


@router.patch(
    "/{inspection_id}/boundaries/{boundary_id}", response_model=InspectionBoundaryRead
)
def update_boundary(
    inspection_id: int,
    boundary_id: int,
    payload: InspectionBoundaryUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> InspectionBoundary:
    boundary = _get_boundary(db, inspection_id, boundary_id, broker_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(boundary, field, value)
    db.commit()
    db.refresh(boundary)
    return boundary


@router.delete(
    "/{inspection_id}/boundaries/{boundary_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_boundary(
    inspection_id: int,
    boundary_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Inspections", "Edit")),
) -> Response:
    boundary = _get_boundary(db, inspection_id, boundary_id, broker_id)
    db.delete(boundary)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router", "requests_router"]
