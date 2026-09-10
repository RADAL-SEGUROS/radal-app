"""``/case-files`` — the expediente: the folder the broker actually thinks in.

``placement`` stays the operating record for one new-business cycle; the case
file is the narrative container around it — a stage on the journey machine
(``app.services.case_files``), a document tree grouped by SECTION, notes, packs
and post-sale children.

House rules, as everywhere: every query filters on the authenticated broker,
a foreign row is **404** (never 403), a rule violation is **422**, and the stage
NEVER moves through the generic PATCH — only through ``POST /{id}/transition``,
which writes a ``case_file_stage_event`` plus an ``activity`` row and keeps the
placement in step inside the same transaction.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, is_partial, require_permission
from app.api.routers.clients import count_total, record_activity
from app.core.case_scope import scope_case_query
from app.models.account_client import AccountClient, AccountClientRole
from app.models.account_group import AccountGroup
from app.models.activity import Activity, Note
from app.models.case_file import CaseFile, CaseFileStageEvent, CasePack
from app.models.client import Client
from app.models.document import Document
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    CaseOrigin,
    CaseSection,
    CaseStage,
    EntityType,
)
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.placement import Placement, PlacementStatus
from app.models.policy import Policy
from app.models.quote import QuoteRequest
from app.models.user import User
from app.schemas.case_file import (
    CaseClientAttach,
    CaseDocumentGroups,
    CaseDocumentRead,
    CaseDocumentSection,
    CaseFileCreate,
    CaseFileDetail,
    CaseFileListItem,
    CaseFilePage,
    CaseFileRead,
    CaseFileRef,
    CaseFileSummary,
    CaseFileTimeline,
    CaseFileTransition,
    CaseFileTransitions,
    CaseFileUpdate,
    CaseFileVersionCreate,
    CaseHistoryResponse,
    CasePriorContext,
    CaseRenewBody,
    CaseReperiodBody,
    ClaimSummary,
    OriginChainEntry,
    PendingAction,
    PendingActionsResponse,
    SectionCount,
    StageEventRead,
    TimelineEntry,
    TransitionOptionRead,
)
from app.services import case_files as machine

router = APIRouter(prefix="/case-files", tags=["case-files"])

#: The post-sale sub-funnel of a policy lives under ``/policies`` but belongs to
#: this module (it is a case-file view, not a policy view).
policy_cases_router = APIRouter(prefix="/policies", tags=["case-files"])

#: Spanish labels for the sub-expedientes. The UI has its own i18n copy; the
#: server needs one for the generated PDFs and for API consumers without it.
SECTION_LABELS_ES: dict[str, str] = {
    CaseSection.ROOT_PROSPECT.value: "Raíz del expediente",
    CaseSection.SUBMISSION.value: "1. Cotización (corredor a compañías)",
    CaseSection.INSURER_QUOTES.value: "2. Cotizaciones (compañías a corredor)",
    CaseSection.BROKER_PROPOSAL.value: "3. Propuesta",
    CaseSection.POLICY_FILE.value: "4. Póliza",
    CaseSection.COLLECTION.value: "Cobranza",
    CaseSection.ENDORSEMENT.value: "Endoso",
    CaseSection.CLAIM.value: "Siniestro",
    CaseSection.RENEWAL.value: "Renovación",
}


# =============================================================================
# Helpers
# =============================================================================

def _inspector_scoped(db: Session, user: User) -> bool:
    """True when this role sees only a NARROWED slice of the tenant's cases.

    Kept as a named predicate (it reads better at call sites than the raw
    grant lookup); the narrowing itself lives in ``app.core.case_scope``.
    """
    from app.core.permissions import resolve_role

    return is_partial(resolve_role(user), "CaseFiles", "View")


def _visible(db: Session, broker_id: int, user: User):
    """The base SELECT, narrowed for a ``partial`` View grant.

    The narrowing is ONE mechanism — ``app.core.case_scope.CASE_VIEW_SCOPE`` —
    shared with :func:`get_case_or_404`, so a role never sees through the packs
    or documents routers a case its own list would hide (spec v3 §6).
    """
    stmt = select(CaseFile).where(CaseFile.broker_id == broker_id)
    return scope_case_query(stmt, broker_id=broker_id, user=user)


def _get_case(db: Session, case_id: int, broker_id: int, user: User) -> CaseFile:
    case = db.scalars(
        _visible(db, broker_id, user).where(CaseFile.id == case_id)
    ).first()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case file not found"
        )
    return case


def get_case_or_404(
    db: Session, case_id: int, broker_id: int, user: User | None = None
) -> CaseFile:
    """Tenant-scoped fetch shared with the packs and documents routers.

    Pass ``user`` to apply the SAME narrowing ``_visible()`` applies: without it
    a role whose ``CaseFiles.View`` grant is ``partial`` could reach a case's
    packs or documents through those routers even though the case never appears
    in its own list (spec v3 §6). It stays optional so the internal callers that
    have already resolved the case (parent / origin lookups) need not repeat it.
    """
    base = select(CaseFile).where(CaseFile.broker_id == broker_id)
    if user is not None:
        base = scope_case_query(base, broker_id=broker_id, user=user)
    case = db.scalars(base.where(CaseFile.id == case_id)).first()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case file not found"
        )
    return case


def _resolve_client(db: Session, client_id: int, broker_id: int) -> Client:
    client = db.scalars(
        select(Client).where(Client.id == client_id, Client.broker_id == broker_id)
    ).first()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return client


def _resolve_placement(db: Session, placement_id: int, broker_id: int) -> Placement:
    placement = db.scalars(
        select(Placement).where(
            Placement.id == placement_id, Placement.broker_id == broker_id
        )
    ).first()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Placement not found"
        )
    return placement


def _resolve_policy(db: Session, policy_id: int, broker_id: int) -> Policy:
    policy = db.scalars(
        select(Policy).where(Policy.id == policy_id, Policy.broker_id == broker_id)
    ).first()
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    return policy


def _resolve_group(db: Session, group_id: int, broker_id: int) -> AccountGroup:
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


def _group_name(db: Session, group_id: int | None) -> str | None:
    if group_id is None:
        return None
    group = db.get(AccountGroup, group_id)
    return group.name if group is not None else None


def _folder_exists_error(existing: CaseFile) -> HTTPException:
    """Rule 5 — no hybrid states: one OPEN folder per (group, line, vigencia)."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "folder_exists",
            "case_file_id": existing.id,
            "reference": existing.reference,
            "detail": (
                f"An open folder for this group, line and vigencia already exists "
                f"(case file {existing.id})"
            ),
        },
    )


def _client_not_in_group_error(client: Client, case: CaseFile) -> HTTPException:
    """Rule 6 — members of an account must belong to the folder's group."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "client_not_in_group",
            "client_id": client.id,
            "account_group_id": case.account_group_id,
            "detail": (
                f"Client {client.id} does not belong to account group "
                f"{case.account_group_id}"
            ),
        },
    )


def _attach_member(
    db: Session,
    *,
    case: CaseFile,
    client: Client,
    role: AccountClientRole,
    is_primary: bool = False,
) -> AccountClient | None:
    """Idempotent ``account_client`` upsert. 422 when the client is elsewhere."""
    if case.account_group_id is not None and client.account_group_id != case.account_group_id:
        raise _client_not_in_group_error(client, case)
    existing = db.scalars(
        select(AccountClient).where(
            AccountClient.case_file_id == case.id,
            AccountClient.client_id == client.id,
        )
    ).first()
    if existing is not None:
        existing.role = role
        if is_primary:
            existing.is_primary = True
        return existing
    row = AccountClient(
        broker_id=case.broker_id,
        case_file_id=case.id,
        client_id=client.id,
        role=role,
        is_primary=is_primary,
    )
    db.add(row)
    return row


def _period_fields(db: Session, case: CaseFile) -> dict[str, object]:
    """The three router-filled group/period fields of ``CaseFileRead``."""
    return {
        "account_group_name": _group_name(db, case.account_group_id),
        "period_locked": machine.is_period_locked(db, case),
        "client_ids": machine.account_member_client_ids(db, case),
    }


def _identity(db: Session, case: CaseFile) -> dict[str, object]:
    client = db.get(Client, case.client_id)
    insured = db.get(Insured, client.insured_id) if client else None
    line = (
        db.get(InsuranceLine, case.insurance_line_id)
        if case.insurance_line_id is not None
        else None
    )
    return {
        "client_legal_name": insured.legal_name if insured else None,
        "client_rut": insured.rut if insured else None,
        "insurance_line_name": line.name if line else None,
    }


def _documents_count(db: Session, case_ids: list[int]) -> dict[int, int]:
    if not case_ids:
        return {}
    rows = db.execute(
        select(Document.case_file_id, func.count(Document.id))
        .where(Document.case_file_id.in_(case_ids))
        .group_by(Document.case_file_id)
    ).all()
    return {int(key): int(value) for key, value in rows if key is not None}


def _read(db: Session, case: CaseFile, *, documents_count: int = 0) -> CaseFileRead:
    return CaseFileRead.model_validate(case).model_copy(
        update={
            **_identity(db, case),
            **_period_fields(db, case),
            "documents_count": documents_count,
        }
    )


def _detail(db: Session, case: CaseFile) -> CaseFileDetail:
    payload = CaseFileDetail.model_validate(case).model_copy(
        update={**_identity(db, case), **_period_fields(db, case)}
    )

    section_rows = db.execute(
        select(Document.section, func.count(Document.id))
        .where(Document.case_file_id == case.id)
        .group_by(Document.section)
    ).all()
    payload.documents_by_section = [
        SectionCount(section=section, count=int(count)) for section, count in section_rows
    ]
    payload.documents_count = sum(item.count for item in payload.documents_by_section)

    placement = (
        db.get(Placement, case.placement_id) if case.placement_id is not None else None
    )
    if placement is not None:
        payload.placement_status = placement.status.value
        payload.period = placement.period or (
            f"{placement.period_start} → {placement.period_end}"
            if placement.period_start and placement.period_end
            else None
        )
    policy = db.get(Policy, case.policy_id) if case.policy_id is not None else None
    if policy is not None:
        payload.policy_number = policy.policy_number

    children = db.scalars(
        select(CaseFile)
        .where(CaseFile.parent_case_file_id == case.id)
        .order_by(CaseFile.kind.asc(), CaseFile.sequence_no.asc(), CaseFile.version.asc())
    ).all()
    payload.children = [CaseFileRef.model_validate(row) for row in children]

    versions = db.scalars(
        select(CaseFile)
        .where(
            or_(
                CaseFile.supersedes_case_file_id == case.id,
                CaseFile.id == case.supersedes_case_file_id,
            )
        )
        .order_by(CaseFile.version.asc())
    ).all()
    payload.versions = [CaseFileRef.model_validate(row) for row in versions]

    latest = db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case.id)
        .order_by(CaseFileStageEvent.occurred_at.desc(), CaseFileStageEvent.id.desc())
    ).first()
    payload.latest_stage_event = (
        StageEventRead.model_validate(latest) if latest is not None else None
    )

    payload.proposals_count = len(machine.case_proposals(db, case))
    quote_clauses = [QuoteRequest.case_file_id == case.id]
    if case.placement_id is not None:
        quote_clauses.append(QuoteRequest.placement_id == case.placement_id)
    payload.quote_requests_count = int(
        db.scalar(
            select(func.count(QuoteRequest.id)).where(
                QuoteRequest.broker_id == case.broker_id, or_(*quote_clauses)
            )
        )
        or 0
    )
    payload.packs_count = int(
        db.scalar(select(func.count(CasePack.id)).where(CasePack.case_file_id == case.id))
        or 0
    )
    payload.notes_count = int(
        db.scalar(
            select(func.count(Note.id)).where(
                Note.broker_id == case.broker_id,
                Note.entity_type == EntityType.CASE_FILE,
                Note.entity_id == case.id,
            )
        )
        or 0
    )
    return payload


def _default_title(db: Session, case_kind: CaseFileKind, client: Client) -> str:
    insured = db.get(Insured, client.insured_id)
    name = insured.legal_name if insured else f"Cliente {client.id}"
    return f"{name} · {case_kind.value}"


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/summary", response_model=CaseFileSummary)
def get_case_files_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> CaseFileSummary:
    """The pipeline board: counts by kind, stage and status in one round trip."""
    base = _visible(db, broker_id, current_user).subquery()

    def _group(column) -> dict[str, int]:
        rows = db.execute(select(column, func.count()).select_from(base).group_by(column)).all()
        return {str(key): int(value) for key, value in rows}

    by_kind = _group(base.c.kind)
    by_stage = _group(base.c.stage)
    by_status = _group(base.c.status)
    total = sum(by_kind.values())
    overdue = int(
        db.scalar(
            select(func.count())
            .select_from(base)
            .where(
                base.c.due_at.is_not(None),
                base.c.due_at < datetime.now(timezone.utc),
                base.c.status == CaseFileStatus.OPEN,
            )
        )
        or 0
    )
    return CaseFileSummary(
        total=total,
        open=by_status.get(CaseFileStatus.OPEN.value, 0),
        by_kind={k.value: by_kind.get(k.value, 0) for k in CaseFileKind},
        by_stage={s.value: by_stage.get(s.value, 0) for s in CaseStage},
        by_status={s.value: by_status.get(s.value, 0) for s in CaseFileStatus},
        overdue=overdue,
    )


@router.get("", response_model=CaseFilePage)
def list_case_files(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
    kind: list[CaseFileKind] | None = Query(default=None),
    stage: list[CaseStage] | None = Query(default=None),
    status_filter: list[CaseFileStatus] | None = Query(default=None, alias="status"),
    client_id: int | None = Query(default=None, gt=0),
    policy_id: int | None = Query(default=None, gt=0),
    parent_id: int | None = Query(default=None, gt=0),
    insurance_line_id: int | None = Query(default=None, gt=0),
    owner_user_id: int | None = Query(default=None, gt=0),
    account_group_id: int | None = Query(default=None, gt=0),
    period_label: str | None = Query(default=None, max_length=32),
    origin: list[CaseOrigin] | None = Query(default=None),
    q: str | None = Query(default=None, description="reference, title or insured"),
    sort: Literal["opened_at", "due_at", "stage", "reference"] = "opened_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
) -> CaseFilePage:
    """List the broker's expedientes, filtered and paginated."""
    stmt = _visible(db, broker_id, current_user)
    if kind:
        stmt = stmt.where(CaseFile.kind.in_(kind))
    if stage:
        stmt = stmt.where(CaseFile.stage.in_(stage))
    if status_filter:
        stmt = stmt.where(CaseFile.status.in_(status_filter))
    if client_id is not None:
        stmt = stmt.where(CaseFile.client_id == client_id)
    if policy_id is not None:
        stmt = stmt.where(CaseFile.policy_id == policy_id)
    if parent_id is not None:
        stmt = stmt.where(CaseFile.parent_case_file_id == parent_id)
    if insurance_line_id is not None:
        stmt = stmt.where(CaseFile.insurance_line_id == insurance_line_id)
    if owner_user_id is not None:
        stmt = stmt.where(CaseFile.owner_user_id == owner_user_id)
    if account_group_id is not None:
        stmt = stmt.where(CaseFile.account_group_id == account_group_id)
    if period_label:
        stmt = stmt.where(CaseFile.period_label == period_label)
    if origin:
        stmt = stmt.where(CaseFile.origin.in_(origin))
    if q:
        needle = f"%{q.strip()}%"
        stmt = (
            stmt.join(Client, Client.id == CaseFile.client_id)
            .join(Insured, Insured.id == Client.insured_id)
            .where(
                or_(
                    CaseFile.reference.ilike(needle),
                    CaseFile.title.ilike(needle),
                    Insured.legal_name.ilike(needle),
                    Insured.rut.ilike(needle),
                )
            )
        )

    total = count_total(db, stmt)
    column = {
        "opened_at": CaseFile.opened_at,
        "due_at": CaseFile.due_at,
        "stage": CaseFile.stage,
        "reference": CaseFile.reference,
    }[sort]
    stmt = stmt.order_by(
        column.asc() if order == "asc" else column.desc(), CaseFile.id.desc()
    )
    rows = list(
        db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    )
    counts = _documents_count(db, [row.id for row in rows])
    return CaseFilePage(
        items=[
            CaseFileListItem.model_validate(
                _read(db, row, documents_count=counts.get(row.id, 0))
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)) if total else 0,
    )


@router.post("", response_model=CaseFileDetail, status_code=status.HTTP_201_CREATED)
def create_case_file(
    payload: CaseFileCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Create")),
) -> CaseFileDetail:
    """Open an expediente. The server owns reference, sequence_no and version."""
    placement = None
    policy = None
    if payload.placement_id is not None:
        placement = _resolve_placement(db, payload.placement_id, broker_id)
    if payload.policy_id is not None:
        policy = _resolve_policy(db, payload.policy_id, broker_id)

    client_id = payload.client_id
    if client_id is None:
        if placement is not None:
            client_id = placement.client_id
        elif policy is not None:
            client_id = policy.client_id
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_id is required unless a placement or policy is supplied",
        )
    client = _resolve_client(db, client_id, broker_id)

    # A folder wraps N placements, but a placement is wrapped by <= 1 account
    # folder (spec v3 §2.5). The authority is ``placement.case_file_id``; the
    # legacy ``case_file.placement_id`` back-pointer is checked too, because the
    # importer writes it before the placement row's own FK.
    if payload.kind in machine.ACCOUNT_KINDS and placement is not None:
        existing = db.scalars(
            select(CaseFile).where(
                CaseFile.broker_id == broker_id,
                CaseFile.kind.in_(machine.ACCOUNT_KINDS),
                or_(
                    CaseFile.placement_id == placement.id,
                    CaseFile.id == placement.case_file_id,
                ),
            )
        ).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Placement {placement.id} already has account case file "
                    f"{existing.id}; a placement is wrapped by one account case"
                ),
            )

    # A renewal folder is a SIBLING IN TIME of the prior vigencia, never a child
    # of a policy — so it is accepted without ``policy_id`` when the source
    # folder is named (spec v3 §4.3, relaxing the post-sale rule).
    origin_case = None
    if payload.origin_case_file_id is not None:
        origin_case = get_case_or_404(db, payload.origin_case_file_id, broker_id)
    needs_policy = (
        payload.kind is not CaseFileKind.ACCOUNT
        and not (payload.kind is CaseFileKind.RENEWAL and origin_case is not None)
    )
    if needs_policy and policy is None and payload.policy_id is None:
        # Post-sale cases hang off a policy; without one the sub-funnel is a lie.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A '{payload.kind.value}' case file requires a policy_id",
        )

    parent = None
    if payload.parent_case_file_id is not None:
        parent = get_case_or_404(db, payload.parent_case_file_id, broker_id)
    elif payload.kind is not CaseFileKind.ACCOUNT and policy is not None:
        # "Post-sale case files hang off the resulting policy AND back-reference
        # the account case" (§2.1) — the sub-funnel needs that link, so default it
        # from the policy instead of leaving an orphan.
        if policy.case_file_id is not None:
            parent = db.get(CaseFile, policy.case_file_id)
            if parent is not None and parent.broker_id != broker_id:
                parent = None
        if parent is None and policy.placement_id is not None:
            parent = db.scalars(
                select(CaseFile)
                .where(
                    CaseFile.broker_id == broker_id,
                    CaseFile.kind == CaseFileKind.ACCOUNT,
                    CaseFile.placement_id == policy.placement_id,
                )
                .order_by(CaseFile.id)
                .limit(1)
            ).first()

    line_id = payload.insurance_line_id
    if line_id is None:
        if placement is not None:
            line_id = placement.insurance_line_id
        elif policy is not None:
            line_id = policy.insurance_line_id

    # --- Group & vigencia (spec v3 §4.3) ------------------------------------
    if payload.account_group_id is not None:
        group = _resolve_group(db, payload.account_group_id, broker_id)
        account_group_id = group.id
    else:
        account_group_id = client.account_group_id

    period_start = payload.period_start
    period_end = payload.period_end
    if period_start is None and placement is not None:
        period_start = placement.period_start
    if period_end is None and placement is not None:
        period_end = placement.period_end

    # Post-sale children inherit group + vigencia from their policy's account,
    # so the tree never joins through ``policy`` for its counts (spec §2.3).
    inherit_from = parent if payload.kind not in machine.ACCOUNT_KINDS else origin_case
    if inherit_from is not None:
        account_group_id = account_group_id or inherit_from.account_group_id
        period_start = period_start or inherit_from.period_start
        period_end = period_end or inherit_from.period_end

    if payload.kind is CaseFileKind.ACCOUNT and (
        period_start is None or period_end is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "period_start and period_end are required for an account case "
                "file: the vigencia IS the folder"
            ),
        )

    period_label = payload.period_label or machine.period_label_for(
        period_start, period_end
    )
    if payload.kind in machine.ACCOUNT_KINDS:
        clash = machine.find_open_folder(
            db,
            broker_id=broker_id,
            account_group_id=account_group_id,
            insurance_line_id=line_id,
            period_start=period_start,
            period_end=period_end,
        )
        if clash is not None:
            raise _folder_exists_error(clash)

    origin = CaseOrigin.RENEWAL if (
        payload.kind is CaseFileKind.RENEWAL and origin_case is not None
    ) else CaseOrigin.NEW

    stage = payload.stage or machine.initial_stage_for(payload.kind)
    if stage not in _kind_stages(payload.kind):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Stage '{stage.value}' does not belong to the '{payload.kind.value}' "
                "chain"
            ),
        )

    sequence_no = machine.next_sequence_no(
        db, broker_id=broker_id,
        policy_id=policy.id if policy else payload.policy_id,
        kind=payload.kind,
    )
    if payload.kind in machine.ACCOUNT_KINDS and (
        payload.kind is CaseFileKind.ACCOUNT or origin_case is not None
    ):
        reference = machine.build_reference(
            db,
            broker_id=broker_id,
            year=period_start.year if period_start is not None else None,
        )
    else:
        reference = machine.build_post_sale_reference(
            db, broker_id=broker_id, kind=payload.kind,
            sequence_no=sequence_no, parent=parent,
        )

    case = CaseFile(
        broker_id=broker_id,
        client_id=client.id,
        placement_id=placement.id if placement else None,
        policy_id=policy.id if policy else payload.policy_id,
        parent_case_file_id=parent.id if parent else None,
        insurance_line_id=line_id,
        kind=payload.kind,
        stage=stage,
        status=payload.status,
        reference=reference,
        title=payload.title or _default_title(db, payload.kind, client),
        sequence_no=sequence_no,
        version=1,
        owner_user_id=payload.owner_user_id or current_user.id,
        due_at=payload.due_at,
        summary=payload.summary,
        meta=payload.meta,
        account_group_id=account_group_id,
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        origin=origin,
        origin_case_file_id=origin_case.id if origin_case is not None else None,
    )
    db.add(case)
    db.flush()

    if placement is not None and placement.case_file_id is None:
        placement.case_file_id = case.id

    # Membership (rule 6): the contratante is always the primary member; the
    # extra RUTs must already belong to the folder's group.
    if payload.kind in machine.ACCOUNT_KINDS:
        _attach_member(
            db,
            case=case,
            client=client,
            role=AccountClientRole.POLICYHOLDER,
            is_primary=True,
        )
        for extra_id in payload.client_ids or []:
            if extra_id == client.id:
                continue
            _attach_member(
                db,
                case=case,
                client=_resolve_client(db, extra_id, broker_id),
                role=AccountClientRole.INSURED,
            )

    machine.record_stage_event(
        db, case=case, from_stage=None, to_stage=case.stage, user=current_user,
        note="Expediente abierto",
    )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.created",
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        description=f"Expediente {case.reference} · {case.title}",
        meta={"kind": case.kind.value, "stage": case.stage.value},
    )
    db.commit()
    db.refresh(case)
    return _detail(db, case)


def _kind_stages(kind: CaseFileKind) -> set[CaseStage]:
    stages = set(machine.CASE_STAGE_FLOW.get(kind, ()))
    stages.update(machine.ALLOWED_CASE_TRANSITIONS.get(kind, {}).keys())
    stages.add(CaseStage.CLOSED)
    return stages


@router.get("/{case_id}", response_model=CaseFileDetail)
def get_case_file(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> CaseFileDetail:
    """One expediente with its refs, per-section counts, children and timeline tip."""
    return _detail(db, _get_case(db, case_id, broker_id, current_user))


@router.patch("/{case_id}", response_model=CaseFileDetail)
def update_case_file(
    case_id: int,
    payload: CaseFileUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Edit")),
) -> CaseFileDetail:
    """Patch the human-editable fields. The stage moves only via /transition."""
    case = _get_case(db, case_id, broker_id, current_user)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _detail(db, case)

    if changes.get("policy_id") is not None:
        _resolve_policy(db, changes["policy_id"], broker_id)
    if changes.get("parent_case_file_id") is not None:
        if changes["parent_case_file_id"] == case.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A case file cannot be its own parent",
            )
        get_case_or_404(db, changes["parent_case_file_id"], broker_id)

    if "insurance_line_id" in changes and case.kind in machine.ACCOUNT_KINDS:
        # ``insurance_line_id`` is one of the five columns of rule 5's
        # uniqueness key, so moving it is the one PATCH that can land a folder
        # on top of another open folder. The line must also be one the broker
        # can actually place business in (its own, or a global one).
        new_line_id = changes["insurance_line_id"]
        if new_line_id is not None and new_line_id != case.insurance_line_id:
            clash = machine.find_open_folder(
                db,
                broker_id=broker_id,
                account_group_id=case.account_group_id,
                insurance_line_id=new_line_id,
                period_start=case.period_start,
                period_end=case.period_end,
                exclude_id=case.id,
            )
            if clash is not None:
                raise _folder_exists_error(clash)

    for field, value in changes.items():
        setattr(case, field, value)
    if changes.get("status") in (
        CaseFileStatus.CLOSED,
        CaseFileStatus.CANCELLED,
        CaseFileStatus.LOST,
        CaseFileStatus.WON,
    ) and case.closed_at is None:
        case.closed_at = datetime.now(timezone.utc)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.updated",
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        description=f"Expediente actualizado ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(case)
    return _detail(db, case)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_case_file(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Delete")),
) -> Response:
    """Delete an empty expediente. Anything with files or children is closed, not erased."""
    case = get_case_or_404(db, case_id, broker_id, current_user)

    documents = db.scalar(
        select(func.count(Document.id)).where(Document.case_file_id == case.id)
    )
    children = db.scalar(
        select(func.count(CaseFile.id)).where(CaseFile.parent_case_file_id == case.id)
    )
    if documents or children:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Case file {case.id} has {int(documents or 0)} document(s) and "
                f"{int(children or 0)} child case(s); close it instead of deleting it"
            ),
        )

    # Detach the back-pointers a SET NULL FK would only fix on the DB side.
    for placement in db.scalars(
        select(Placement).where(Placement.case_file_id == case.id)
    ).all():
        placement.case_file_id = None

    db.delete(case)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.deleted",
        entity_type=EntityType.CASE_FILE,
        entity_id=case_id,
        description="Expediente eliminado",
        meta={},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{case_id}/transitions", response_model=CaseFileTransitions)
def get_case_file_transitions(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> CaseFileTransitions:
    """Which stages this case may move to right now, and why not otherwise.

    The journey strip drives its buttons off this list, so a control is never
    rendered live for a move the server would refuse.
    """
    case = _get_case(db, case_id, broker_id, current_user)
    options = machine.transition_options(db, case)
    return CaseFileTransitions(
        id=case.id,
        kind=case.kind,
        stage=case.stage,
        options=[
            TransitionOptionRead(
                to_stage=option.to_stage, allowed=option.allowed, reason=option.reason
            )
            for option in options
        ],
        is_terminal=not machine.allowed_stages(case),
    )


def _forward_next_stage(case: CaseFile) -> CaseStage | None:
    """The immediate next stage on the case's chain, or ``None`` at the end."""
    chain = machine.CASE_STAGE_FLOW.get(case.kind, ())
    if case.stage not in chain:
        return None
    index = chain.index(case.stage)
    return chain[index + 1] if index + 1 < len(chain) else None


@router.get("/{case_id}/pending-actions", response_model=PendingActionsResponse)
def get_case_file_pending_actions(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> PendingActionsResponse:
    """The typed list of actionable gaps that feed the overview Journey.

    Each item is ``{code, severity, tab, reason, count}`` with an English ``code``
    token. Derived from: an incomplete antecedentes record, unconfirmed
    proposals, the first BLOCKED forward transition (the real guard reason), a
    comparison awaiting alignment, and a missing propuesta at the right stage.
    Read-only; every query is tenant-scoped through ``_get_case``.
    """
    from app.models.broker_proposal import BrokerProposal
    from app.models.comparison import Comparison, ComparisonStatus
    from app.models.enums import RecordExpedienteStatus
    from app.models.record_expediente import RecordExpediente

    case = _get_case(db, case_id, broker_id, current_user)
    actions: list[PendingAction] = []

    account_kinds = machine.ACCOUNT_KINDS
    is_account = case.kind in account_kinds

    # (1) Record / antecedentes gap — a missing or not-yet-registered expediente.
    if is_account:
        record = db.scalars(
            select(RecordExpediente).where(
                RecordExpediente.broker_id == broker_id,
                RecordExpediente.case_file_id == case.id,
            )
        ).first()
        if record is None:
            actions.append(
                PendingAction(
                    code="antecedentes_missing",
                    severity="warning",
                    tab="record",
                    reason="La cuenta aún no tiene un expediente de antecedentes.",
                )
            )
        elif record.status != RecordExpedienteStatus.REGISTERED:
            actions.append(
                PendingAction(
                    code="antecedentes_unregistered",
                    severity="warning",
                    tab="record",
                    reason="El expediente de antecedentes está en borrador, falta registrarlo.",
                )
            )

    # (2) Unconfirmed proposals (suggest -> confirm -> commit not yet done).
    unconfirmed = sum(
        1 for p in machine.case_proposals(db, case) if not p.is_confirmed
    )
    if unconfirmed:
        actions.append(
            PendingAction(
                code="proposals_unconfirmed",
                severity="warning",
                tab="comparison",
                reason="Hay cotizaciones sin confirmar (suggest → confirmar → commit).",
                count=unconfirmed,
            )
        )

    # (3) A comparison awaiting alignment (has columns but is not ALIGNED).
    comparison = db.scalars(
        select(Comparison)
        .where(
            Comparison.broker_id == broker_id,
            Comparison.case_file_id == case.id,
            Comparison.status != ComparisonStatus.SUPERSEDED,
        )
        .order_by(Comparison.id.desc())
    ).first()
    if comparison is not None and comparison.status != ComparisonStatus.ALIGNED:
        if comparison.entries:
            actions.append(
                PendingAction(
                    code="comparison_unaligned",
                    severity="warning",
                    tab="comparison",
                    reason="La comparación tiene columnas sin alinear.",
                )
            )

    # (4) A missing propuesta once the account has reached the decision point.
    if is_account and case.stage in machine.CASE_STAGE_FLOW.get(case.kind, ()):
        chain = machine.CASE_STAGE_FLOW[case.kind]
        decision_idx = (
            chain.index(CaseStage.INSURED_DECISION)
            if CaseStage.INSURED_DECISION in chain
            else None
        )
        if decision_idx is not None and chain.index(case.stage) >= decision_idx:
            has_bp = db.scalars(
                select(BrokerProposal.id).where(
                    BrokerProposal.broker_id == broker_id,
                    BrokerProposal.case_file_id == case.id,
                )
            ).first()
            if has_bp is None:
                actions.append(
                    PendingAction(
                        code="propuesta_missing",
                        severity="warning",
                        tab="proposal",
                        reason="La cuenta está en decisión pero no tiene una propuesta emitida.",
                    )
                )

    # (5) The first BLOCKED forward transition — the real guard reason.
    next_stage = _forward_next_stage(case)
    if next_stage is not None:
        reason = machine.guard_reason(db, case, next_stage)
        if reason:
            actions.append(
                PendingAction(
                    code="stage_blocked",
                    severity="blocker",
                    tab="journey",
                    reason=reason,
                )
            )

    return PendingActionsResponse(case_file_id=case.id, actions=actions)


@router.post("/{case_id}/transition", response_model=CaseFileDetail)
def transition_case_file(
    case_id: int,
    payload: CaseFileTransition,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> CaseFileDetail:
    """Move the case along the journey machine — 422 with the specific reason."""
    case = _get_case(db, case_id, broker_id, current_user)
    try:
        machine.apply_transition(
            db, case=case, to_stage=payload.to_stage, user=current_user, note=payload.note
        )
    except machine.CaseTransitionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(case)
    return _detail(db, case)


@router.get("/{case_id}/timeline", response_model=CaseFileTimeline)
def get_case_file_timeline(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
    limit: int = Query(default=200, ge=1, le=500),
) -> CaseFileTimeline:
    """Stage events + activity + notes, merged and ordered — the bitácora."""
    case = _get_case(db, case_id, broker_id, current_user)
    entries: list[TimelineEntry] = []

    for event in db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case.id)
        .order_by(CaseFileStageEvent.occurred_at.asc())
        .limit(limit)
    ).all():
        entries.append(
            TimelineEntry(
                kind="stage",
                occurred_at=event.occurred_at,
                title=event.to_stage.value,
                from_stage=event.from_stage.value if event.from_stage else None,
                to_stage=event.to_stage.value,
                detail=event.note,
                user_id=event.user_id,
                meta=event.meta,
            )
        )

    for activity in db.scalars(
        select(Activity)
        .where(
            Activity.broker_id == broker_id,
            Activity.entity_type == EntityType.CASE_FILE,
            Activity.entity_id == case.id,
        )
        .order_by(Activity.occurred_at.asc())
        .limit(limit)
    ).all():
        entries.append(
            TimelineEntry(
                kind="activity",
                occurred_at=activity.occurred_at,
                title=activity.action,
                detail=activity.description,
                user_id=activity.user_id,
                meta=activity.meta,
            )
        )

    for note in db.scalars(
        select(Note)
        .where(
            Note.broker_id == broker_id,
            Note.entity_type == EntityType.CASE_FILE,
            Note.entity_id == case.id,
        )
        .order_by(Note.id.asc())
        .limit(limit)
    ).all():
        entries.append(
            TimelineEntry(
                kind="note",
                occurred_at=note.created_at or case.opened_at,
                title="internal" if note.is_internal else "shared",
                detail=note.body,
                user_id=note.author_id,
                meta={"follow_up_on": note.follow_up_on.isoformat()}
                if note.follow_up_on
                else None,
            )
        )

    entries.sort(key=lambda item: (item.occurred_at is None, item.occurred_at))
    return CaseFileTimeline(case_file_id=case.id, entries=entries[:limit])


@router.get("/{case_id}/documents", response_model=CaseDocumentGroups)
def list_case_file_documents(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "View")),
    section: CaseSection | None = Query(default=None),
) -> CaseDocumentGroups:
    """The document tree, grouped by sub-expediente, each row with a download URL."""
    from app.api.routers.documents import build_download_payload
    from app.services.packs import category_label

    case = get_case_or_404(db, case_id, broker_id, _user)
    stmt = select(Document).where(
        Document.broker_id == broker_id, Document.case_file_id == case.id
    )
    if section is not None:
        stmt = stmt.where(Document.section == section)
    rows = list(
        db.scalars(stmt.order_by(Document.document_code.asc(), Document.id.asc())).all()
    )

    grouped: dict[str | None, list[CaseDocumentRead]] = {}
    for doc in rows:
        payload = build_download_payload(doc)
        key = doc.section.value if doc.section is not None else None
        grouped.setdefault(key, []).append(
            CaseDocumentRead(
                id=doc.id,
                original_name=doc.original_name,
                category=doc.category,
                category_label=category_label(doc.category),
                section=doc.section,
                document_code=doc.document_code,
                mime_type=doc.mime_type,
                size_bytes=doc.size_bytes,
                uploaded_by_id=doc.uploaded_by_id,
                created_at=doc.created_at,
                url=payload.url,
                expires_in_seconds=payload.expires_in_seconds,
            )
        )

    ordered = [s.value for s in CaseSection] + [None]
    sections = [
        CaseDocumentSection(
            section=CaseSection(key) if key is not None else None,
            label=SECTION_LABELS_ES.get(key or "", "Sin clasificar"),
            documents=grouped[key],
        )
        for key in ordered
        if key in grouped
    ]
    return CaseDocumentGroups(case_file_id=case.id, total=len(rows), sections=sections)


@router.post("/{case_id}/versions", response_model=CaseFileDetail, status_code=status.HTTP_201_CREATED)
def create_case_file_version(
    case_id: int,
    payload: CaseFileVersionCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Create")),
) -> CaseFileDetail:
    """Rework the case: a NEW row at ``version + 1``, superseding this one.

    Same ``sequence_no`` — E2 v2 is still E2. That is the "past → current with
    dates" sub-funnel the team asked for.
    """
    case = get_case_or_404(db, case_id, broker_id, current_user)

    already = db.scalars(
        select(CaseFile).where(
            CaseFile.broker_id == broker_id, CaseFile.supersedes_case_file_id == case.id
        )
    ).first()
    if already is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Case file {case.id} is already superseded by {already.id}",
        )

    clone = CaseFile(
        broker_id=broker_id,
        client_id=case.client_id,
        placement_id=case.placement_id,
        policy_id=case.policy_id,
        parent_case_file_id=case.parent_case_file_id,
        supersedes_case_file_id=case.id,
        insurance_line_id=case.insurance_line_id,
        kind=case.kind,
        stage=machine.initial_stage_for(case.kind),
        status=CaseFileStatus.OPEN,
        reference=machine.build_reference(db, broker_id=broker_id),
        title=payload.title or f"{case.title} (v{case.version + 1})",
        sequence_no=case.sequence_no,
        version=case.version + 1,
        owner_user_id=case.owner_user_id or current_user.id,
        due_at=case.due_at,
        summary=case.summary,
        meta=case.meta,
        # A version is the SAME folder reworked: same group, same vigencia, same
        # origin story. Dropping these would make the rework invisible in the
        # group tree (every group view filters on ``account_group_id``) while
        # the source it supersedes is closed — the account would simply vanish.
        account_group_id=case.account_group_id,
        period_start=case.period_start,
        period_end=case.period_end,
        period_label=case.period_label,
        origin=case.origin,
        origin_case_file_id=case.origin_case_file_id,
    )
    db.add(clone)
    db.flush()

    case.status = CaseFileStatus.CLOSED
    if case.closed_at is None:
        case.closed_at = datetime.now(timezone.utc)

    machine.record_stage_event(
        db, case=clone, from_stage=None, to_stage=clone.stage, user=current_user,
        note=payload.note or f"Nueva versión del expediente {case.reference}",
        meta={"supersedes_case_file_id": case.id},
    )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.versioned",
        entity_type=EntityType.CASE_FILE,
        entity_id=clone.id,
        description=f"Versión {clone.version} de {case.reference}",
        meta={"supersedes_case_file_id": case.id, "version": clone.version},
    )
    db.commit()
    db.refresh(clone)
    return _detail(db, clone)


# =============================================================================
# Groups & accounts: renew, re-period, history and membership (spec v3 §4.3)
# =============================================================================

#: A renewal may only be opened from a folder whose period is effectively over
#: or in its final validation. Anything earlier is still the CURRENT vigencia.
_RENEWABLE_STAGES: frozenset[CaseStage] = frozenset(
    {CaseStage.ACTIVE, CaseStage.MIRROR_VALIDATION, CaseStage.CLOSED}
)

#: Up to (and including) this stage a re-perioded folder keeps the stage it had:
#: nothing has left the building yet. Past it, the successor restarts at intake.
_REPERIOD_KEEP_STAGES: tuple[CaseStage, ...] = (
    CaseStage.LEAD,
    CaseStage.INTAKE,
    CaseStage.PRE_UNDERWRITING,
    CaseStage.TECHNICAL_BASIS,
    CaseStage.RENEWAL_REVIEW,
)


def _renewal_not_allowed(reason: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "renewal_not_allowed", "reason": reason, "detail": detail},
    )


def _record_folder_categories() -> set:
    """Every ANTECEDENTES category, from the server-owned navigator mapping.

    ``RECORD_FOLDERS`` (``app.schemas.navigator``) is the single definition of
    the Excel's MONTOS / SINIESTRALIDAD / INFORME / CUESTIONARIO / SLIP leaves;
    re-declaring it here would let the two drift.
    """
    from app.schemas.navigator import RECORD_FOLDERS

    return {category for folder in RECORD_FOLDERS for category in folder.categories}


def _copy_documents(
    db: Session,
    *,
    source: CaseFile,
    target: CaseFile,
    sections: list[CaseSection] | None,
    categories: set | None,
    user: User,
) -> dict[str, int]:
    """Copy documents into ``target`` as NEW rows on NEW storage keys.

    Rule 8: the ``document`` table is the only place a storage key lives, and a
    key is UNIQUE — so a "copy" is never a second row pointing at the source's
    key. The bytes are copied best-effort; when the source object is unreadable
    the new row is still created (both rows are then equally empty) and the
    caller records the tally on the stage event.
    """
    from app.api.routers.documents import store_generated_document
    from app.services.packs import _document_bytes

    stmt = select(Document).where(
        Document.broker_id == source.broker_id, Document.case_file_id == source.id
    )
    if sections is not None:
        stmt = stmt.where(Document.section.in_(sections))
    if categories is not None:
        stmt = stmt.where(Document.category.in_(sorted(categories, key=lambda c: c.value)))

    if sections is not None and not sections:
        return {"documents_copied": 0, "documents_without_bytes": 0}
    if categories is not None and not categories:
        return {"documents_copied": 0, "documents_without_bytes": 0}

    copied = 0
    without_bytes = 0
    for doc in db.scalars(stmt.order_by(Document.id.asc())).all():
        data = _document_bytes(doc)
        if data is None:
            without_bytes += 1
            data = b""
        clone = store_generated_document(
            db,
            broker_id=target.broker_id,
            entity_type=EntityType.CASE_FILE,
            entity_id=target.id,
            category=doc.category,
            data=data,
            original_name=doc.original_name,
            mime_type=doc.mime_type or "application/octet-stream",
            phase=doc.phase,
            uploaded_by_id=user.id,
        )
        clone.case_file_id = target.id
        clone.section = doc.section
        clone.document_code = doc.document_code
        copied += 1
    return {"documents_copied": copied, "documents_without_bytes": without_bytes}


def _clone_placements(
    db: Session,
    *,
    source: CaseFile,
    target: CaseFile,
    period_start,
    period_end,
    period_label: str | None,
) -> list[Placement]:
    """One new DRAFT placement per source placement, on the new vigencia.

    The placement is the per-RUT operating copy of the folder's period (§2.3);
    a renewal starts them all over rather than moving the old rows, so the prior
    vigencia stays readable exactly as it was.
    """
    clones: list[Placement] = []
    for placement in machine.case_placements(db, source):
        clone = Placement(
            broker_id=target.broker_id,
            client_id=placement.client_id,
            asset_id=placement.asset_id,
            insurance_line_id=placement.insurance_line_id,
            status=PlacementStatus.DRAFT,
            period=period_label,
            period_start=period_start,
            period_end=period_end,
            notes=placement.notes,
            case_file_id=target.id,
        )
        db.add(clone)
        clones.append(clone)
    db.flush()
    if clones and target.placement_id is None:
        target.placement_id = clones[0].id
    return clones


def _copy_members(
    db: Session,
    *,
    source: CaseFile,
    target: CaseFile,
    client_ids: list[int] | None,
) -> None:
    """Carry the account's RUTs across, or use the caller's explicit override."""
    contratante = _resolve_client(db, target.client_id, target.broker_id)
    _attach_member(
        db,
        case=target,
        client=contratante,
        role=AccountClientRole.POLICYHOLDER,
        is_primary=True,
    )
    if client_ids is None:
        rows = db.scalars(
            select(AccountClient)
            .where(AccountClient.case_file_id == source.id)
            .order_by(AccountClient.id.asc())
        ).all()
        wanted = [(row.client_id, row.role) for row in rows]
    else:
        wanted = [(cid, AccountClientRole.INSURED) for cid in client_ids]
    for client_id, role in wanted:
        if client_id == target.client_id:
            continue
        _attach_member(
            db,
            case=target,
            client=_resolve_client(db, client_id, target.broker_id),
            role=role,
        )


@router.post(
    "/{case_id}/renew", response_model=CaseFileDetail, status_code=status.HTTP_201_CREATED
)
def renew_case_file(
    case_id: int,
    payload: CaseRenewBody,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Create")),
) -> CaseFileDetail:
    """Open the next vigencia of this ramo — one transaction (rule 4).

    Renewal is managed at ramo-vigencia level, never on a policy: the new folder
    is a SIBLING IN TIME of this one (``origin=renewal``,
    ``origin_case_file_id``), with cloned placements, the same members and
    ``policy_id=NULL``. The prior period is read through
    ``GET /case-files/{id}/history`` and never constrains it.
    """
    source = _get_case(db, case_id, broker_id, current_user)
    if source.kind not in machine.ACCOUNT_KINDS:
        raise _renewal_not_allowed(
            "account_not_active",
            f"Only an account or renewal folder can be renewed, not '{source.kind.value}'",
        )
    if source.stage not in _RENEWABLE_STAGES:
        raise _renewal_not_allowed(
            "account_not_active",
            (
                f"The folder is at stage '{source.stage.value}'; a renewal opens from "
                "active, mirror_validation or closed"
            ),
        )
    already = db.scalars(
        select(CaseFile).where(
            CaseFile.broker_id == broker_id,
            CaseFile.origin_case_file_id == source.id,
            CaseFile.origin == CaseOrigin.RENEWAL,
        )
    ).first()
    if already is not None:
        raise _renewal_not_allowed(
            "already_renewed",
            f"Folder {source.id} was already renewed into case file {already.id}",
        )

    period_label = payload.period_label or machine.period_label_for(
        payload.period_start, payload.period_end
    )
    clash = machine.find_open_folder(
        db,
        broker_id=broker_id,
        account_group_id=source.account_group_id,
        insurance_line_id=source.insurance_line_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    if clash is not None:
        raise _folder_exists_error(clash)

    prior_policy_ids = [policy.id for policy in machine.case_policies(db, source)]
    target = CaseFile(
        broker_id=broker_id,
        client_id=source.client_id,
        placement_id=None,
        policy_id=None,
        parent_case_file_id=None,
        insurance_line_id=source.insurance_line_id,
        kind=CaseFileKind.RENEWAL,
        stage=CaseStage.RENEWAL_REVIEW,
        status=CaseFileStatus.OPEN,
        reference=machine.build_reference(
            db, broker_id=broker_id, year=payload.period_start.year
        ),
        title=f"{source.title} · {period_label or 'renovación'}",
        sequence_no=1,
        version=1,
        owner_user_id=source.owner_user_id or current_user.id,
        summary=source.summary,
        meta={"renewed_from_case_file_id": source.id},
        account_group_id=source.account_group_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        period_label=period_label,
        origin=CaseOrigin.RENEWAL,
        origin_case_file_id=source.id,
    )
    db.add(target)
    db.flush()

    _clone_placements(
        db,
        source=source,
        target=target,
        period_start=payload.period_start,
        period_end=payload.period_end,
        period_label=period_label,
    )
    _copy_members(db, source=source, target=target, client_ids=payload.client_ids)
    tally = _copy_documents(
        db,
        source=source,
        target=target,
        sections=list(payload.copy_sections),
        categories=None,
        user=current_user,
    )

    machine.record_stage_event(
        db,
        case=target,
        from_stage=None,
        to_stage=target.stage,
        user=current_user,
        note=f"Renovación de {source.reference}",
        meta={
            "origin_case_file_id": source.id,
            "prior_policy_ids": prior_policy_ids,
            **tally,
        },
    )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.renewed",
        entity_type=EntityType.CASE_FILE,
        entity_id=target.id,
        description=f"Renovación {target.reference} de {source.reference}",
        meta={"origin_case_file_id": source.id, "period_label": period_label},
    )
    db.commit()
    db.refresh(target)
    return _detail(db, target)


@router.post(
    "/{case_id}/reperiod",
    response_model=CaseFileDetail,
    status_code=status.HTTP_201_CREATED,
)
def reperiod_case_file(
    case_id: int,
    payload: CaseReperiodBody,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Create")),
) -> CaseFileDetail:
    """A vigencia date change opens a NEW folder and closes this one (rule 1).

    There is no in-place edit and no "extended account": the documents already
    filed quote the old dates, so the honest move is a sibling folder
    (``origin=period_change``) carrying the antecedentes forward.
    """
    source = _get_case(db, case_id, broker_id, current_user)
    if source.kind not in machine.ACCOUNT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Only an account or renewal folder has a vigencia to change, not "
                f"'{source.kind.value}'"
            ),
        )
    if source.status is not CaseFileStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Case file {source.id} is '{source.status.value}', not open",
        )

    period_label = machine.period_label_for(payload.period_start, payload.period_end)
    clash = machine.find_open_folder(
        db,
        broker_id=broker_id,
        account_group_id=source.account_group_id,
        insurance_line_id=source.insurance_line_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        exclude_id=source.id,
    )
    if clash is not None:
        raise _folder_exists_error(clash)

    stage = (
        source.stage if source.stage in _REPERIOD_KEEP_STAGES else CaseStage.INTAKE
    )
    if stage is CaseStage.RENEWAL_REVIEW:
        stage = CaseStage.INTAKE  # the successor is an ``account`` folder

    target = CaseFile(
        broker_id=broker_id,
        client_id=source.client_id,
        placement_id=None,
        policy_id=None,
        parent_case_file_id=None,
        insurance_line_id=source.insurance_line_id,
        kind=CaseFileKind.ACCOUNT,
        stage=stage,
        status=CaseFileStatus.OPEN,
        reference=machine.build_reference(
            db, broker_id=broker_id, year=payload.period_start.year
        ),
        title=source.title,
        sequence_no=1,
        version=1,
        owner_user_id=source.owner_user_id or current_user.id,
        summary=source.summary,
        meta={"reperiod_from_case_file_id": source.id, "reason": payload.reason},
        account_group_id=source.account_group_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        period_label=period_label,
        origin=CaseOrigin.PERIOD_CHANGE,
        origin_case_file_id=source.id,
    )
    db.add(target)
    db.flush()

    _clone_placements(
        db,
        source=source,
        target=target,
        period_start=payload.period_start,
        period_end=payload.period_end,
        period_label=period_label,
    )
    _copy_members(db, source=source, target=target, client_ids=None)
    tally = _copy_documents(
        db,
        source=source,
        target=target,
        sections=None,
        categories=_record_folder_categories(),
        user=current_user,
    )

    machine.record_stage_event(
        db,
        case=target,
        from_stage=None,
        to_stage=target.stage,
        user=current_user,
        note=f"Cambio de vigencia desde {source.reference}",
        meta={
            "origin_case_file_id": source.id,
            "reason": payload.reason,
            **tally,
        },
    )

    # Close the source in the same transaction — never two open folders for one
    # (group, line, vigencia).
    previous = source.stage
    source.meta = {**(source.meta or {}), "closed_reason": "period_change"}
    source.status = CaseFileStatus.CLOSED
    if source.closed_at is None:
        source.closed_at = datetime.now(timezone.utc)
    if previous is not CaseStage.CLOSED:
        source.stage = CaseStage.CLOSED
        machine.record_stage_event(
            db,
            case=source,
            from_stage=previous,
            to_stage=CaseStage.CLOSED,
            user=current_user,
            note=payload.reason,
            meta={"reason": payload.reason, "successor_case_file_id": target.id},
        )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.reperiod",
        entity_type=EntityType.CASE_FILE,
        entity_id=target.id,
        description=f"Cambio de vigencia {source.reference} → {target.reference}",
        meta={"origin_case_file_id": source.id, "reason": payload.reason},
    )
    db.commit()
    db.refresh(target)
    return _detail(db, target)


@router.get("/{case_id}/history", response_model=CaseHistoryResponse)
def get_case_file_history(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> CaseHistoryResponse:
    """The origin chain plus the immediate prior folder's read-only context.

    This is what the renewal screen reads: what was insured last time, which
    policies came out of it and what it cost in claims. It informs the new
    folder; it never constrains it.
    """
    from app.models.policy import Claim
    from app.schemas.document import DocumentRead
    from app.schemas.navigator import RECORD_FOLDERS
    from app.schemas.policy import PolicyRead

    case = _get_case(db, case_id, broker_id, current_user)

    chain: list[CaseFile] = []
    seen: set[int] = set()
    cursor: CaseFile | None = case
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        chain.append(cursor)
        if cursor.origin_case_file_id is None:
            break
        parent = db.scalars(
            select(CaseFile).where(
                CaseFile.id == cursor.origin_case_file_id,
                CaseFile.broker_id == broker_id,
            )
        ).first()
        cursor = parent
    chain.reverse()  # oldest first: new -> renewal -> renewal

    prior_row = chain[-2] if len(chain) >= 2 else None
    prior: CasePriorContext | None = None
    if prior_row is not None:
        records: dict[str, list[DocumentRead]] = {}
        for folder in RECORD_FOLDERS:
            rows = db.scalars(
                select(Document)
                .where(
                    Document.broker_id == broker_id,
                    Document.case_file_id == prior_row.id,
                    Document.category.in_(folder.categories),
                )
                .order_by(Document.id.asc())
            ).all()
            records[folder.key] = [DocumentRead.model_validate(row) for row in rows]

        policies = machine.case_policies(db, prior_row)
        policy_ids = [policy.id for policy in policies]
        claim_clauses = [Claim.case_file_id == prior_row.id]
        if policy_ids:
            claim_clauses.append(Claim.policy_id.in_(policy_ids))
        claims = list(
            db.scalars(
                select(Claim)
                .where(Claim.broker_id == broker_id, or_(*claim_clauses))
                .order_by(Claim.id.asc())
            ).all()
        )

        paid = sum(
            (claim.paid_amount_uf or claim.settled_amount_uf or 0) for claim in claims
        )
        premium = sum((policy.total_premium_uf or 0) for policy in policies)
        loss_ratio = (
            (Decimal(paid) / Decimal(premium) * 100).quantize(Decimal("0.001"))
            if premium
            else None
        )

        prior = CasePriorContext(
            case_file_id=prior_row.id,
            records=records,
            policies=[PolicyRead.model_validate(policy) for policy in policies],
            claims=[ClaimSummary.model_validate(claim) for claim in claims],
            loss_ratio_pct=loss_ratio,
        )

    return CaseHistoryResponse(
        case_file_id=case.id,
        origin_chain=[
            OriginChainEntry(
                case_file_id=row.id,
                reference=row.reference,
                period_label=row.period_label,
                origin=row.origin,
                stage=row.stage,
            )
            for row in chain
        ],
        prior=prior,
    )


@router.post(
    "/{case_id}/clients",
    response_model=CaseFileDetail,
    status_code=status.HTTP_201_CREATED,
)
def add_case_file_client(
    case_id: int,
    payload: CaseClientAttach,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Edit")),
) -> CaseFileDetail:
    """Add a RUT to the account (rule 6: an account has N RUTs)."""
    case = _get_case(db, case_id, broker_id, current_user)
    if case.kind not in machine.ACCOUNT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Only an account or renewal folder has members, not "
                f"'{case.kind.value}'"
            ),
        )
    client = _resolve_client(db, payload.client_id, broker_id)
    _attach_member(db, case=case, client=client, role=payload.role)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.client_attached",
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        description=f"RUT agregado al expediente {case.reference}",
        meta={"client_id": client.id, "role": payload.role.value},
    )
    db.commit()
    db.refresh(case)
    return _detail(db, case)


@router.delete(
    "/{case_id}/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_case_file_client(
    case_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Edit")),
) -> Response:
    """Detach a RUT. The contratante (``case_file.client_id``) cannot be removed."""
    case = _get_case(db, case_id, broker_id, current_user)
    if client_id == case.client_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "The contratante cannot be removed from its own account; change "
                "the folder's client instead"
            ),
        )
    row = db.scalars(
        select(AccountClient).where(
            AccountClient.case_file_id == case.id,
            AccountClient.client_id == client_id,
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This client is not a member of the account",
        )
    db.delete(row)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_file.client_detached",
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        description=f"RUT retirado del expediente {case.reference}",
        meta={"client_id": client_id},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# The post-sale sub-funnel of a policy
# =============================================================================

@policy_cases_router.get("/{policy_id}/case-files", response_model=list[CaseFileRef])
def list_policy_case_files(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> list[CaseFileRef]:
    """The versioned post-sale sub-funnel: kind, sequence_no, version, with dates."""
    _resolve_policy(db, policy_id, broker_id)
    rows = db.scalars(
        select(CaseFile)
        .where(CaseFile.broker_id == broker_id, CaseFile.policy_id == policy_id)
        .order_by(
            CaseFile.kind.asc(), CaseFile.sequence_no.asc(), CaseFile.version.asc()
        )
        .options(selectinload(CaseFile.stage_events))
    ).all()
    return [CaseFileRef.model_validate(row) for row in rows]


__all__ = ["router", "policy_cases_router", "get_case_or_404", "SECTION_LABELS_ES"]
