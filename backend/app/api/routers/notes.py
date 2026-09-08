"""``/notes`` and ``/activities`` — human commentary and the audit trail.

The ``note`` table has existed since the rebuild with no endpoints at all, and
the frontend's ``ActivityPanel`` has been fabricating its timeline. These two
resources replace that with real data.

Tenancy is enforced exactly as ``documents.py`` does it: ``entity_type`` +
``entity_id`` are resolved through the same ``resolve_entity`` table (extended
with the five new ``EntityType`` members), so a note can never be attached to —
or read from — another tenant's row. The permission gate is the MODULE that owns
the target entity: reading a note about a policy needs ``Policies.View``, writing
one needs ``Policies.Comment``.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_current_user, get_db
from app.api.routers.clients import record_activity
from app.api.routers.documents import resolve_entity
from app.core.permissions import has_permission, resolve_role
from app.models.activity import Activity, Note
from app.models.enums import EntityType
from app.models.user import User
from app.schemas.note import (
    ActivityListResponse,
    ActivityRead,
    NoteCreate,
    NoteListResponse,
    NoteRead,
    NoteUpdate,
)

router = APIRouter(prefix="/notes", tags=["notes"])
activities_router = APIRouter(prefix="/activities", tags=["notes"])

#: Which RBAC module owns each attachable entity. Reading/commenting a note is
#: gated by the module that owns the thing the note is about.
MODULE_BY_ENTITY: dict[EntityType, str] = {
    EntityType.BROKER: "Settings",
    EntityType.USER: "Users",
    EntityType.CLIENT: "Clients",
    EntityType.INSURED: "Clients",
    EntityType.INSURER: "Insurers",
    EntityType.ASSET: "Assets",
    EntityType.INSURANCE_LINE: "Settings",
    EntityType.PLACEMENT: "Placements",
    EntityType.QUOTE_REQUEST: "Quotes",
    EntityType.PROPOSAL: "Proposals",
    EntityType.INSPECTION_REQUEST: "Inspections",
    EntityType.INSPECTION: "Inspections",
    EntityType.POLICY: "Policies",
    EntityType.CLAIM: "Claims",
    EntityType.OFFERING: "Offerings",
    EntityType.CASE_FILE: "CaseFiles",
    EntityType.SALES_LEAD: "Leads",
    EntityType.ENDORSEMENT: "Endorsements",
    EntityType.COLLECTION_PLAN: "Collections",
    EntityType.WARRANTY: "Policies",
}


def _module_for(entity_type: EntityType) -> str:
    module = MODULE_BY_ENTITY.get(entity_type)
    if module is None:  # pragma: no cover - EntityType is exhaustively mapped
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Notes cannot be attached to '{entity_type.value}'",
        )
    return module


def _require(user: User, entity_type: EntityType, action: str) -> None:
    module = _module_for(entity_type)
    if not has_permission(resolve_role(user), module, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not permitted to {action} on {module}",
        )


def _resolve_target(
    db: Session, entity_type: EntityType, entity_id: int, broker_id: int
) -> None:
    """404 when the target is absent or belongs to another tenant."""
    if entity_type in (
        EntityType.CASE_FILE,
        EntityType.SALES_LEAD,
        EntityType.ENDORSEMENT,
        EntityType.COLLECTION_PLAN,
        EntityType.WARRANTY,
    ):
        from app.models.case_file import CaseFile
        from app.models.collection import CollectionPlan
        from app.models.endorsement import Endorsement
        from app.models.sales_lead import SalesLead
        from app.models.warranty import Warranty

        model = {
            EntityType.CASE_FILE: CaseFile,
            EntityType.SALES_LEAD: SalesLead,
            EntityType.ENDORSEMENT: Endorsement,
            EntityType.COLLECTION_PLAN: CollectionPlan,
            EntityType.WARRANTY: Warranty,
        }[entity_type]
        row = db.get(model, entity_id)
        if row is None or row.broker_id != broker_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{entity_type.value} {entity_id} not found",
            )
        return
    resolve_entity(db, entity_type, entity_id, broker_id)


def _read(db: Session, note: Note) -> NoteRead:
    author = db.get(User, note.author_id) if note.author_id is not None else None
    return NoteRead.model_validate(note).model_copy(
        update={"author_name": author.full_name if author else None}
    )


# =============================================================================
# Notes
# =============================================================================

@router.get("", response_model=NoteListResponse)
def list_notes(
    entity_type: EntityType = Query(...),
    entity_id: int = Query(..., gt=0),
    is_internal: bool | None = Query(default=None),
    follow_up_before: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NoteListResponse:
    """Notes on one entity, newest last. Gated by the target module's View."""
    _require(current_user, entity_type, "View")
    _resolve_target(db, entity_type, entity_id, broker_id)

    stmt = select(Note).where(
        Note.broker_id == broker_id,
        Note.entity_type == entity_type,
        Note.entity_id == entity_id,
    )
    if is_internal is not None:
        stmt = stmt.where(Note.is_internal.is_(is_internal))
    if follow_up_before is not None:
        stmt = stmt.where(
            Note.follow_up_on.is_not(None), Note.follow_up_on <= follow_up_before
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Note.id.asc()).offset(offset).limit(limit)).all()
    return NoteListResponse(
        items=[_read(db, row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def create_note(
    payload: NoteCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    """Attach a note, optionally with the single follow-up date the team asked for."""
    _require(current_user, payload.entity_type, "Comment")
    _resolve_target(db, payload.entity_type, payload.entity_id, broker_id)

    note = Note(
        broker_id=broker_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        author_id=current_user.id,
        body=payload.body,
        is_internal=payload.is_internal,
        phase=payload.phase,
        follow_up_on=payload.follow_up_on,
    )
    db.add(note)
    db.flush()

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="note.created",
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        description="Nota interna" if note.is_internal else "Nota",
        meta={"note_id": note.id, "follow_up_on": (
            note.follow_up_on.isoformat() if note.follow_up_on else None
        )},
    )
    db.commit()
    db.refresh(note)
    return _read(db, note)


def _get_note(db: Session, note_id: int, broker_id: int) -> Note:
    note = db.scalars(
        select(Note).where(Note.id == note_id, Note.broker_id == broker_id)
    ).first()
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    return note


def _may_write(db: Session, note: Note, user: User) -> bool:
    """The author, or someone with Manage on the target module."""
    if note.author_id == user.id:
        return True
    return has_permission(resolve_role(user), _module_for(note.entity_type), "Manage")


@router.get("/{note_id}", response_model=NoteRead)
def get_note(
    note_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    note = _get_note(db, note_id, broker_id)
    _require(current_user, note.entity_type, "View")
    return _read(db, note)


@router.patch("/{note_id}", response_model=NoteRead)
def update_note(
    note_id: int,
    payload: NoteUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NoteRead:
    """Edit your own note (or anyone's, with Manage on the target module)."""
    note = _get_note(db, note_id, broker_id)
    _require(current_user, note.entity_type, "Comment")
    if not _may_write(db, note, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author can edit this note",
        )
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return _read(db, note)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> Response:
    note = _get_note(db, note_id, broker_id)
    _require(current_user, note.entity_type, "Comment")
    if not _may_write(db, note, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author can delete this note",
        )
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Activities
# =============================================================================

@activities_router.get("", response_model=ActivityListResponse)
def list_activities(
    entity_type: EntityType | None = Query(default=None),
    entity_id: int | None = Query(default=None, gt=0),
    action: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> ActivityListResponse:
    """The real audit trail — what ``ActivityPanel.tsx`` used to fabricate."""
    if not has_permission(resolve_role(current_user), "Dashboard", "View"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not permitted to View on Dashboard",
        )
    if entity_id is not None and entity_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entity_id requires entity_type",
        )
    if entity_type is not None:
        _require(current_user, entity_type, "View")
        if entity_id is not None:
            _resolve_target(db, entity_type, entity_id, broker_id)

    stmt = select(Activity).where(Activity.broker_id == broker_id)
    if entity_type is not None:
        stmt = stmt.where(Activity.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(Activity.entity_id == entity_id)
    if action:
        stmt = stmt.where(Activity.action == action)
    if since is not None:
        stmt = stmt.where(Activity.occurred_at >= since)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    names: dict[int, str | None] = {}
    for row in rows:
        if row.user_id is not None and row.user_id not in names:
            user = db.get(User, row.user_id)
            names[row.user_id] = user.full_name if user else None
    return ActivityListResponse(
        items=[
            ActivityRead.model_validate(row).model_copy(
                update={"user_name": names.get(row.user_id) if row.user_id else None}
            )
            for row in rows
        ],
        total=int(total),
        limit=limit,
        offset=offset,
    )


__all__ = ["router", "activities_router", "MODULE_BY_ENTITY"]
