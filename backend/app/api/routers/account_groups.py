"""``/account-groups`` — the broker-private Group above the expediente (spec §4.1).

A group is the label the broker works in ("JO PASTELERÍA", not the legal
"Pacto Food SpA") and it may span several RUTs. It carries **no money and no
stage** (rule 7): everything money- or stage-shaped lives on the account folder
(``case_file(kind=account|renewal)``) and is only *counted* here, always over the
cases the caller may actually see.

Detaching is never a delete: `client.account_group_id` and
`case_file.account_group_id` are ``SET NULL``, so archiving a group leaves every
folder intact.

Three routes are gated on ``CaseFiles.View`` rather than ``Groups.View`` — the
tree, the timeline and the archive are views over case files; the group is only
the axis they are grouped by.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import count_total, paginate, record_activity
from app.models.account_client import AccountClient
from app.models.account_group import AccountGroup, AccountGroupStatus
from app.models.activity import Activity, Note
from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.client import Client
from app.models.collection import CollectionPlan
from app.models.endorsement import Endorsement
from app.models.enums import CaseFileStatus, EntityType
from app.models.insured import Insured
from app.models.policy import Claim, Policy
from app.models.user import User
from app.schemas.account_group import (
    AccountGroupAttachClient,
    AccountGroupClientRead,
    AccountGroupCreate,
    AccountGroupDetail,
    AccountGroupPage,
    AccountGroupRead,
    AccountGroupUpdate,
    ArchiveCreate,
    ArchiveResponse,
    GroupClientRef,
    GroupTimelineEntry,
    GroupTimelinePage,
)
from app.schemas.navigator import GroupTree
from app.services import navigator as navigator_service
from app.services import packs as pack_service

router = APIRouter(prefix="/account-groups", tags=["account-groups"])


# =============================================================================
# Helpers
# =============================================================================

def _get_group(db: Session, group_id: int, broker_id: int) -> AccountGroup:
    group = db.scalars(
        select(AccountGroup).where(
            AccountGroup.id == group_id, AccountGroup.broker_id == broker_id
        )
    ).first()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account group not found"
        )
    return group


def _unique_slug(db: Session, broker_id: int, name: str) -> str:
    """``slugify`` + a numeric suffix while the (broker, slug) pair is taken.

    The UNIQUE index is the real guard; this only keeps a second group named
    "La Favorita" from 409-ing on a race the user cannot understand.
    """
    base = navigator_service.slugify_group_name(name)
    slug = base
    n = 2
    while db.scalar(
        select(func.count(AccountGroup.id)).where(
            AccountGroup.broker_id == broker_id, AccountGroup.slug == slug
        )
    ):
        suffix = f"-{n}"
        slug = f"{base[: max(1, 80 - len(suffix))]}{suffix}"
        n += 1
    return slug


def _resolve_client(db: Session, client_id: int, broker_id: int) -> Client:
    client = db.scalars(
        select(Client).where(Client.id == client_id, Client.broker_id == broker_id)
    ).first()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


def _adopt_client_case_files(db: Session, *, client: Client, group: AccountGroup) -> int:
    """Back-fill ``case_file.account_group_id`` for the client's orphan folders.

    ``case_file.account_group_id`` is the DENORMALISED column every group view
    filters on (the tree, the per-group counters, the archive). Writing only
    ``client.account_group_id`` when a RUT joins a group would leave that
    client's existing expedientes permanently invisible inside the group, with
    no API left to repair them (``CaseFileUpdate`` forbids extra fields and does
    not expose the column).

    Only folders that carry NO group are adopted — a folder already filed under
    another group is never moved, so attaching a client can never steal history
    from a sibling group. Post-sale children are included: they inherit the
    account's group by construction (spec v3 §2.3).
    """
    orphans = db.scalars(
        select(CaseFile).where(
            CaseFile.broker_id == client.broker_id,
            CaseFile.client_id == client.id,
            CaseFile.account_group_id.is_(None),
        )
    ).all()
    for case in orphans:
        case.account_group_id = group.id
    return len(orphans)


def _group_aggregates(
    db: Session, broker_id: int, user: User, group_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Per-group counts over VISIBLE account folders, in one grouped query."""
    if not group_ids:
        return {}
    base = navigator_service.visible_cases(db, broker_id, user).subquery()
    rows = db.execute(
        select(
            base.c.account_group_id,
            base.c.id,
            base.c.client_id,
            base.c.status,
            base.c.period_label,
            base.c.period_start,
            base.c.period_end,
        ).where(
            base.c.account_group_id.in_(group_ids),
            base.c.kind.in_(list(navigator_service.ACCOUNT_KINDS)),
        )
    ).all()

    out: dict[int, dict[str, Any]] = {
        gid: {
            "accounts_count": 0,
            "open_count": 0,
            "latest_period_label": None,
            "latest_period_start": None,
            "primary_client_id": None,
        }
        for gid in group_ids
    }
    # "Latest" = the account with the greatest ``period_start``; a folder with no
    # period at all only wins when nothing else is there (rank 0 vs 1).
    best: dict[int, tuple[int, date]] = {}
    for gid, _case_id, client_id, case_status, label, start, end in rows:
        gid = int(gid)
        bucket = out[gid]
        bucket["accounts_count"] += 1
        if case_status == CaseFileStatus.OPEN:
            bucket["open_count"] += 1
        start = navigator_service.as_date(start)
        rank = (1, start) if start is not None else (0, date.min)
        if gid in best and rank <= best[gid]:
            continue
        best[gid] = rank
        bucket["latest_period_start"] = start
        bucket["latest_period_label"] = label or navigator_service.period_label_for(
            start, navigator_service.as_date(end)
        )
        bucket["primary_client_id"] = int(client_id) if client_id else None
    return out


def _client_refs(db: Session, client_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not client_ids:
        return {}
    rows = db.execute(
        select(
            Client.id, Insured.rut, Insured.legal_name, Insured.trade_name, Client.status
        )
        .join(Insured, Insured.id == Client.insured_id)
        .where(Client.id.in_(client_ids))
    ).all()
    return {
        int(cid): {
            "id": int(cid),
            "rut": rut,
            "legal_name": legal_name,
            "trade_name": trade_name,
            "status": client_status,
        }
        for cid, rut, legal_name, trade_name, client_status in rows
    }


def _clients_of(db: Session, broker_id: int, group_ids: list[int]) -> dict[int, list[int]]:
    if not group_ids:
        return {}
    rows = db.execute(
        select(Client.account_group_id, Client.id)
        .where(Client.broker_id == broker_id, Client.account_group_id.in_(group_ids))
        .order_by(Client.id.asc())
    ).all()
    grouped: dict[int, list[int]] = defaultdict(list)
    for gid, cid in rows:
        grouped[int(gid)].append(int(cid))
    return grouped


def _read(
    group: AccountGroup,
    *,
    aggregates: dict[str, Any],
    member_ids: list[int],
    refs: dict[int, dict[str, Any]],
) -> AccountGroupRead:
    primary_id = aggregates.get("primary_client_id")
    if primary_id is None and member_ids:
        primary_id = member_ids[0]
    primary = refs.get(int(primary_id)) if primary_id else None
    return AccountGroupRead(
        id=group.id,
        name=group.name,
        slug=group.slug,
        status=group.status,
        primary_client=GroupClientRef(**{
            k: primary[k] for k in ("id", "rut", "legal_name")
        })
        if primary
        else None,
        clients_count=len(member_ids),
        accounts_count=aggregates.get("accounts_count", 0),
        open_count=aggregates.get("open_count", 0),
        latest_period_label=aggregates.get("latest_period_label"),
        latest_period_start=aggregates.get("latest_period_start"),
        updated_at=group.updated_at,
    )


def _detail(
    db: Session, group: AccountGroup, broker_id: int, user: User
) -> AccountGroupDetail:
    aggregates = _group_aggregates(db, broker_id, user, [group.id]).get(group.id, {})
    member_ids = _clients_of(db, broker_id, [group.id]).get(group.id, [])
    refs = _client_refs(
        db,
        list({*member_ids, *( [aggregates["primary_client_id"]]
                              if aggregates.get("primary_client_id") else [] )}),
    )
    base = _read(group, aggregates=aggregates, member_ids=member_ids, refs=refs)
    return AccountGroupDetail(
        **base.model_dump(),
        notes=group.notes,
        clients=[
            AccountGroupClientRead(**refs[cid]) for cid in member_ids if cid in refs
        ],
    )


def _naive_utc(value: datetime | None) -> datetime | None:
    """One comparable instant. Both engines hand DATETIME back naive; a caller's
    ISO cursor may be aware, so normalise to naive UTC before comparing."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _instant(*candidates: Any) -> datetime | None:
    """First present value, as a naive-UTC datetime (a ``date`` at midnight)."""
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, datetime):
            return _naive_utc(value)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
    return None


# =============================================================================
# CRUD
# =============================================================================

@router.get("", response_model=AccountGroupPage)
def list_account_groups(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Groups", "View")),
    q: str | None = Query(default=None, description="name or slug"),
    status_filter: AccountGroupStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> AccountGroupPage:
    """The broker's groups, newest vigencia first inside the page."""
    stmt = select(AccountGroup).where(AccountGroup.broker_id == broker_id)
    allowed = navigator_service.visible_group_ids(db, broker_id, current_user)
    if allowed is not None:
        stmt = stmt.where(AccountGroup.id.in_(allowed or [0]))
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(AccountGroup.name.ilike(needle), AccountGroup.slug.ilike(needle))
        )
    if status_filter is not None:
        stmt = stmt.where(AccountGroup.status == status_filter)

    total = count_total(db, stmt)
    rows = list(
        db.scalars(
            stmt.order_by(AccountGroup.name.asc(), AccountGroup.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    ids = [row.id for row in rows]
    aggregates = _group_aggregates(db, broker_id, current_user, ids)
    members = _clients_of(db, broker_id, ids)
    ref_ids = {cid for values in members.values() for cid in values}
    ref_ids |= {
        int(item["primary_client_id"])
        for item in aggregates.values()
        if item.get("primary_client_id")
    }
    refs = _client_refs(db, sorted(ref_ids))
    return AccountGroupPage(
        items=[
            _read(
                row,
                aggregates=aggregates.get(row.id, {}),
                member_ids=members.get(row.id, []),
                refs=refs,
            )
            for row in rows
        ],
        **paginate(total, page, page_size),
    )


@router.post("", response_model=AccountGroupDetail, status_code=status.HTTP_201_CREATED)
def create_account_group(
    payload: AccountGroupCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Groups", "Create")),
) -> AccountGroupDetail:
    """Open a group. ``client_ids`` attaches existing clients in the same call."""
    group = AccountGroup(
        broker_id=broker_id,
        name=payload.name.strip(),
        slug=_unique_slug(db, broker_id, payload.name),
        status=AccountGroupStatus.ACTIVE,
        notes=payload.notes,
    )
    db.add(group)
    try:
        db.flush()
    except IntegrityError as exc:  # pragma: no cover - the race, not the rule
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A group with that name already exists for this broker",
        ) from exc

    for client_id in payload.client_ids or []:
        client = _resolve_client(db, client_id, broker_id)
        if client.account_group_id is not None and client.account_group_id != group.id:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "client_in_other_group",
                    "client_id": client.id,
                    "account_group_id": client.account_group_id,
                },
            )
        client.account_group_id = group.id
        _adopt_client_case_files(db, client=client, group=group)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="account_group.created",
        entity_type=EntityType.ACCOUNT_GROUP,
        entity_id=group.id,
        description=group.name,
    )
    db.commit()
    db.refresh(group)
    return _detail(db, group, broker_id, current_user)


@router.get("/{group_id}", response_model=AccountGroupDetail)
def get_account_group(
    group_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Groups", "View")),
) -> AccountGroupDetail:
    group = _get_group(db, group_id, broker_id)
    return _detail(db, group, broker_id, current_user)


@router.patch("/{group_id}", response_model=AccountGroupDetail)
def update_account_group(
    group_id: int,
    payload: AccountGroupUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Groups", "Edit")),
) -> AccountGroupDetail:
    """Rename, re-note or archive. Archiving DETACHES nothing and cascades nothing."""
    group = _get_group(db, group_id, broker_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        group.name = data["name"].strip()
        group.slug = _unique_slug(db, broker_id, data["name"])
    if "status" in data and data["status"] is not None:
        group.status = data["status"]
    if "notes" in data:
        group.notes = data["notes"]
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="account_group.updated",
        entity_type=EntityType.ACCOUNT_GROUP,
        entity_id=group.id,
        meta={key: str(value) for key, value in data.items()},
    )
    db.commit()
    db.refresh(group)
    return _detail(db, group, broker_id, current_user)


# =============================================================================
# Membership
# =============================================================================

@router.post("/{group_id}/clients", response_model=AccountGroupDetail)
def attach_client(
    group_id: int,
    payload: AccountGroupAttachClient,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Groups", "Edit")),
) -> AccountGroupDetail:
    """A broker x RUT belongs to at most one group — moving is explicit."""
    group = _get_group(db, group_id, broker_id)
    client = _resolve_client(db, payload.client_id, broker_id)
    if client.account_group_id is not None and client.account_group_id != group.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "client_in_other_group",
                "client_id": client.id,
                "account_group_id": client.account_group_id,
            },
        )
    client.account_group_id = group.id
    adopted = _adopt_client_case_files(db, client=client, group=group)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="account_group.client_attached",
        entity_type=EntityType.ACCOUNT_GROUP,
        entity_id=group.id,
        meta={"client_id": client.id, "adopted_case_files": adopted},
    )
    db.commit()
    db.refresh(group)
    return _detail(db, group, broker_id, current_user)


@router.delete("/{group_id}/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_client(
    group_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Groups", "Edit")),
) -> Response:
    """Detach a RUT. Refused while it still has an OPEN folder in this group."""
    group = _get_group(db, group_id, broker_id)
    client = _resolve_client(db, client_id, broker_id)
    if client.account_group_id != group.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client is not attached to this group",
        )

    member_cases = select(AccountClient.case_file_id).where(
        AccountClient.client_id == client.id
    )
    open_count = int(
        db.scalar(
            select(func.count(CaseFile.id)).where(
                CaseFile.broker_id == broker_id,
                CaseFile.account_group_id == group.id,
                CaseFile.status == CaseFileStatus.OPEN,
                or_(CaseFile.client_id == client.id, CaseFile.id.in_(member_cases)),
            )
        )
        or 0
    )
    if open_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "client_has_open_case_files",
                "client_id": client.id,
                "open_case_files": open_count,
            },
        )

    client.account_group_id = None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="account_group.client_detached",
        entity_type=EntityType.ACCOUNT_GROUP,
        entity_id=group.id,
        meta={"client_id": client.id},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Views over the group's case files
# =============================================================================

@router.get("/{group_id}/tree", response_model=GroupTree)
def get_group_tree(
    group_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> GroupTree:
    """The contextual rail: vigencia -> ramo -> antecedentes / pólizas / renovación."""
    try:
        return navigator_service.build_group_tree(
            db, current_user, group_id, broker_id=broker_id
        )
    except navigator_service.GroupNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account group not found"
        ) from exc


@router.get("/{group_id}/timeline", response_model=GroupTimelinePage)
def get_group_timeline(
    group_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = Query(default=None, description="cursor: occurred_at"),
) -> GroupTimelinePage:
    """Every visible case's events, merged and sorted DESC **before** truncation.

    The per-case bitácora truncates each source then sorts; a group merges seven
    sources across N folders, so truncating first would silently drop the newest
    rows of a busy folder. Pagination is a cursor (``before``), not an offset:
    a merged stream has no stable offset.
    """
    group = _get_group(db, group_id, broker_id)
    cases = list(
        db.scalars(
            navigator_service.visible_cases(db, broker_id, current_user).where(
                CaseFile.account_group_id == group.id
            )
        ).all()
    )
    case_ids = [case.id for case in cases]
    cutoff = _naive_utc(before)
    entries: list[GroupTimelineEntry] = []

    def _add(entry: GroupTimelineEntry) -> None:
        if cutoff is not None and entry.occurred_at >= cutoff:
            return
        entries.append(entry)

    if case_ids:
        window = limit * 4
        for event in db.scalars(
            select(CaseFileStageEvent)
            .where(CaseFileStageEvent.case_file_id.in_(case_ids))
            .order_by(CaseFileStageEvent.occurred_at.desc(), CaseFileStageEvent.id.desc())
            .limit(window)
        ).all():
            moment = _instant(event.occurred_at)
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="stage",
                    occurred_at=moment,
                    case_file_id=event.case_file_id,
                    title=event.to_stage.value,
                    from_stage=event.from_stage.value if event.from_stage else None,
                    to_stage=event.to_stage.value,
                    detail=event.note,
                )
            )

        for activity in db.scalars(
            select(Activity)
            .where(
                Activity.broker_id == broker_id,
                Activity.entity_type == EntityType.CASE_FILE,
                Activity.entity_id.in_(case_ids),
            )
            .order_by(Activity.occurred_at.desc(), Activity.id.desc())
            .limit(window)
        ).all():
            moment = _instant(activity.occurred_at, activity.created_at)
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="activity",
                    occurred_at=moment,
                    case_file_id=activity.entity_id,
                    title=activity.action,
                    detail=activity.description,
                )
            )

        for note in db.scalars(
            select(Note)
            .where(
                Note.broker_id == broker_id,
                Note.entity_type == EntityType.CASE_FILE,
                Note.entity_id.in_(case_ids),
            )
            .order_by(Note.id.desc())
            .limit(window)
        ).all():
            moment = _instant(note.created_at)
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="note",
                    occurred_at=moment,
                    case_file_id=note.entity_id,
                    title="internal" if note.is_internal else "shared",
                    detail=note.body,
                )
            )

        for policy in db.scalars(
            select(Policy)
            .where(Policy.broker_id == broker_id, Policy.case_file_id.in_(case_ids))
            .order_by(Policy.id.desc())
        ).all():
            moment = _instant(policy.issued_at, policy.start_date, policy.created_at)
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="policy",
                    occurred_at=moment,
                    case_file_id=policy.case_file_id,
                    policy_id=policy.id,
                    title=policy.policy_number,
                    detail_token=policy.status.value,
                )
            )

        for endorsement in db.scalars(
            select(Endorsement)
            .where(
                Endorsement.broker_id == broker_id,
                Endorsement.case_file_id.in_(case_ids),
            )
            .order_by(Endorsement.id.desc())
        ).all():
            moment = _instant(
                endorsement.issued_at, endorsement.effective_at, endorsement.created_at
            )
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="endorsement",
                    occurred_at=moment,
                    case_file_id=endorsement.case_file_id,
                    policy_id=endorsement.policy_id,
                    title=str(
                        endorsement.endorsement_number or endorsement.sequence_no
                    ),
                    detail_token=endorsement.kind.value,
                )
            )

        for claim in db.scalars(
            select(Claim)
            .where(Claim.broker_id == broker_id, Claim.case_file_id.in_(case_ids))
            .order_by(Claim.id.desc())
        ).all():
            moment = _instant(
                claim.reported_at, claim.reported_date, claim.event_date, claim.created_at
            )
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="claim",
                    occurred_at=moment,
                    case_file_id=claim.case_file_id,
                    policy_id=claim.policy_id,
                    title=str(claim.claim_number or claim.id),
                    detail_token=claim.status.value,
                )
            )

        for plan in db.scalars(
            select(CollectionPlan)
            .where(
                CollectionPlan.broker_id == broker_id,
                CollectionPlan.case_file_id.in_(case_ids),
            )
            .order_by(CollectionPlan.id.desc())
        ).all():
            moment = _instant(plan.as_of_date, plan.created_at)
            if moment is None:
                continue
            _add(
                GroupTimelineEntry(
                    kind="collection",
                    occurred_at=moment,
                    case_file_id=plan.case_file_id,
                    policy_id=plan.policy_id,
                    title=str(plan.plan_number or plan.id),
                    detail_token=plan.status.value,
                )
            )

    entries.sort(key=lambda item: item.occurred_at, reverse=True)
    page = entries[:limit]
    next_before = page[-1].occurred_at if len(page) == limit and page else None
    return GroupTimelinePage(entries=page, next_before=next_before)


@router.post(
    "/{group_id}/archives",
    response_model=ArchiveResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_group_archive(
    group_id: int,
    payload: ArchiveCreate | None = None,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
    _documents: User = Depends(require_permission("Documents", "View")),
) -> ArchiveResponse:
    """Zip the group (or one vigencia) into an ``archive_pack`` document.

    Only documents of cases the caller can see are included, and nothing streams
    through the buffered Lambda: the answer is the existing presigned
    ``DocumentDownload`` (OAC constraint 4).
    """
    from app.api.routers.documents import build_download_payload

    group = _get_group(db, group_id, broker_id)
    period_label = payload.period_label if payload else None

    stmt = navigator_service.visible_cases(db, broker_id, current_user).where(
        CaseFile.account_group_id == group.id
    )
    if period_label:
        stmt = stmt.where(CaseFile.period_label == period_label)
    cases = list(db.scalars(stmt.order_by(CaseFile.id.asc())).all())

    try:
        document = pack_service.build_group_archive(
            db,
            group=group,
            cases=cases,
            user=current_user,
            period_label=period_label,
        )
    except pack_service.PackTooLargeError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except pack_service.PackError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="account_group.archive_generated",
        entity_type=EntityType.ACCOUNT_GROUP,
        entity_id=group.id,
        meta={"document_id": document.id, "period_label": period_label},
    )
    db.commit()
    db.refresh(document)
    return ArchiveResponse(
        document_id=document.id, download=build_download_payload(document)
    )
