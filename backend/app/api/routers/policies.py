"""``/policies`` — the issued contract, its warranties and the mirror validation.

Three things live here beyond plain CRUD:

* ``POST /policies/{id}/from-proposal`` builds the draft policy from the accepted
  proposal. That draft IS the mirror baseline — what the broker asked the carrier
  to issue.
* ``GET /policies/{id}/mirror-diff`` compares that baseline against the confirmed
  ``policy`` extraction, field by field (``app.services.mirror``), and
  ``POST .../queue`` turns the differences into ``endorsement(status=draft)``.
* ``/policies/{id}/warranties`` is the R-n / G-n / M-n tracker — rows, never JSON,
  because the whole point is answering "was R-1 met before the loss?".

Money reuses ``reconcile_money`` from ``app.schemas.proposal``: one
implementation of the Chilean premium arithmetic, not two.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.services.analytics import (
    ScopeFilters,
    group_by_query,
    grouped_or_none,
    scope_filters,
    scope_predicates,
)
from app.api.routers.clients import record_activity
from app.models.client import Client
from app.models.document import Document
from app.models.endorsement import Endorsement
from app.models.enums import EntityType
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.policy import Claim, Policy, PolicyStatus
from app.models.proposal import Proposal, ProposalStatus
from app.models.user import User
from app.models.warranty import Warranty
from app.schemas.policy import (
    MirrorDiffQueue,
    MirrorDiffQueueResult,
    MirrorDiffRead,
    MirrorDiffRowRead,
    PolicyCreate,
    PolicyFromProposal,
    PolicyPage,
    PolicyRead,
    PolicySummary,
    PolicyUpdate,
    PolicyUploadRequest,
    PolicyUploadResponse,
    WarrantyCreate,
    WarrantyRead,
    WarrantyUpdate,
)
from app.schemas.proposal import reconcile_money
from app.services import ai as ai_service
from app.services import extraction_commit
from app.services import mirror as mirror_service

router = APIRouter(prefix="/policies", tags=["policies"])

#: Warranties are reached by their own id once the policy has been resolved.
warranties_router = APIRouter(prefix="/warranties", tags=["policies"])

#: The Chilean noon convention: a policy period runs 12:00 to 12:00.
NOON = time(12, 0)


# --- Helpers -------------------------------------------------------------------

def get_policy_or_404(db: Session, policy_id: int, broker_id: int) -> Policy:
    policy = db.scalars(
        select(Policy).where(Policy.id == policy_id, Policy.broker_id == broker_id)
    ).first()
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    return policy


def _resolve_insurer(db: Session, insurer_id: int) -> Insurer:
    insurer = db.get(Insurer, insurer_id)
    if insurer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurer not found")
    if not (insurer.rut or "").strip() or not (insurer.cmf_code or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Insurer {insurer.id} is missing rut/cmf_code; identity is never "
                "matched by name"
            ),
        )
    return insurer


def _resolve_document(db: Session, document_id: int | None, broker_id: int) -> None:
    if document_id is None:
        return
    exists = db.scalar(
        select(Document.id).where(
            Document.id == document_id, Document.broker_id == broker_id
        )
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The referenced document does not belong to this broker",
        )


def _sync_period(policy: Policy) -> None:
    """Keep ``start_date``/``end_date`` and the noon datetimes in step (§2.9)."""
    if policy.period_start_at is not None and policy.start_date is None:
        policy.start_date = policy.period_start_at.date()
    if policy.period_end_at is not None and policy.end_date is None:
        policy.end_date = policy.period_end_at.date()
    if policy.start_date is not None and policy.period_start_at is None:
        policy.period_start_at = datetime.combine(policy.start_date, NOON, tzinfo=timezone.utc)
    if policy.end_date is not None and policy.period_end_at is None:
        policy.period_end_at = datetime.combine(policy.end_date, NOON, tzinfo=timezone.utc)


def _apply_money(policy: Policy, incoming: dict) -> None:
    """Validate the premium invariants against the POST-WRITE state — 422 on a break."""
    merged = {
        field: incoming.get(field, getattr(policy, field))
        for field in (
            "taxable_premium_uf",
            "exempt_premium_uf",
            "net_premium_uf",
            "vat_uf",
            "total_premium_uf",
        )
    }
    derived, errors = reconcile_money(merged)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=errors
        )
    for field, value in derived.items():
        if hasattr(policy, field):
            setattr(policy, field, value)


def _read(db: Session, policy: Policy) -> PolicyRead:
    insurer = db.get(Insurer, policy.insurer_id)
    client = db.get(Client, policy.client_id)
    insured = db.get(Insured, client.insured_id) if client else None
    counts = {
        "endorsements_count": int(
            db.scalar(
                select(func.count(Endorsement.id)).where(
                    Endorsement.policy_id == policy.id
                )
            )
            or 0
        ),
        "warranties_count": int(
            db.scalar(select(func.count(Warranty.id)).where(Warranty.policy_id == policy.id))
            or 0
        ),
        "claims_count": int(
            db.scalar(select(func.count(Claim.id)).where(Claim.policy_id == policy.id)) or 0
        ),
    }
    return PolicyRead.model_validate(policy).model_copy(
        update={
            "insurer_name": insurer.legal_name if insurer else None,
            "client_legal_name": insured.legal_name if insured else None,
            **counts,
        }
    )


# --- CRUD ----------------------------------------------------------------------

# NOTE: registered before ``/{policy_id}`` so the literal path wins the match.
@router.get("/summary", response_model=PolicySummary)
def get_policies_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
    scope: ScopeFilters = Depends(scope_filters),
    group_by: str | None = group_by_query(),
) -> PolicySummary:
    """The portfolio board: status split, live premium and the renewal clock.

    Scope: ``account_group_id`` resolves through ``policy.case_file_id ->
    case_file.account_group_id`` OR ``policy.client_id -> client.account_group_id``
    (a policy can predate its expediente). The date window sits on
    ``start_date`` — the vigencia start, the honest analytic date for a
    portfolio. ``group_by`` accepts
    ``status | insurer | insurance_line | account_group | month``.
    """
    scoped = [Policy.broker_id == broker_id, *scope_predicates("policies", scope, broker_id)]
    rows = db.execute(
        select(Policy.status, func.count(Policy.id))
        .where(*scoped)
        .group_by(Policy.status)
    ).all()
    by_status = {str(key): int(value) for key, value in rows}

    premium_sum = db.scalar(
        select(func.sum(Policy.total_premium_uf)).where(
            *scoped,
            Policy.status == PolicyStatus.ACTIVE,
            Policy.total_premium_uf.is_not(None),
        )
    )

    today = date.today()
    expiring = int(
        db.scalar(
            select(func.count(Policy.id)).where(
                *scoped,
                Policy.status == PolicyStatus.ACTIVE,
                Policy.end_date.is_not(None),
                Policy.end_date >= today,
                Policy.end_date <= today + timedelta(days=60),
            )
        )
        or 0
    )

    return PolicySummary(
        total=sum(by_status.values()),
        by_status={s.value: by_status.get(s.value, 0) for s in PolicyStatus},
        active_count=by_status.get(PolicyStatus.ACTIVE.value, 0),
        # SQLite hands aggregates back as floats; round-trip through str() keeps
        # the Decimal exact and quantized to the column's 4 decimals.
        total_premium_uf=(
            Decimal(str(premium_sum)).quantize(Decimal("0.0001"))
            if premium_sum is not None
            else Decimal("0")
        ),
        expiring_within_60_days=expiring,
        grouped=grouped_or_none(
            db, "policies", group_by=group_by, broker_id=broker_id, extra_filters=scoped
        ),
    )


@router.get("", response_model=PolicyPage)
def list_policies(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
    client_id: int | None = Query(default=None, gt=0),
    insurer_id: int | None = Query(default=None, gt=0),
    placement_id: int | None = Query(default=None, gt=0),
    scope: ScopeFilters = Depends(scope_filters),
    status_filter: list[PolicyStatus] | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="policy number or insured"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PolicyPage:
    """List the broker's policies, newest first.

    Scope params: ``account_group_id`` (grupo — via the expediente or the
    contratante), ``case_file_id`` (grupo-cuenta) and ``date_from`` / ``date_to``
    on ``start_date`` (the vigencia start). A foreign id yields an empty page.
    """
    stmt = select(Policy).where(
        Policy.broker_id == broker_id, *scope_predicates("policies", scope, broker_id)
    )
    if client_id is not None:
        stmt = stmt.where(Policy.client_id == client_id)
    if insurer_id is not None:
        stmt = stmt.where(Policy.insurer_id == insurer_id)
    if placement_id is not None:
        stmt = stmt.where(Policy.placement_id == placement_id)
    if status_filter:
        stmt = stmt.where(Policy.status.in_(status_filter))
    if q:
        needle = f"%{q.strip()}%"
        stmt = (
            stmt.join(Client, Client.id == Policy.client_id)
            .join(Insured, Insured.id == Client.insured_id)
            .where(
                or_(
                    Policy.policy_number.ilike(needle),
                    Insured.legal_name.ilike(needle),
                    Insured.rut.ilike(needle),
                )
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Policy.id.desc()).offset(offset).limit(limit)
    ).all()
    return PolicyPage(
        items=[_read(db, row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _apply_renewal_link(db: Session, policy: Policy, broker_id: int) -> None:
    """Thread ``renews_policy_id`` when the policy is issued on a renewal folder.

    A renewal folder is a sibling in time of the prior vigencia
    (``case_file.origin_case_file_id``), so the policy it produces renews the
    prior folder's policy FOR THE SAME RUT. Written only when the caller did not
    state it — an explicit body value always wins.
    """
    if policy.renews_policy_id is not None or policy.case_file_id is None:
        return
    from app.models.case_file import CaseFile
    from app.models.enums import CaseFileKind
    from app.services.case_files import case_policies

    case = db.scalars(
        select(CaseFile).where(
            CaseFile.id == policy.case_file_id, CaseFile.broker_id == broker_id
        )
    ).first()
    if case is None or case.kind is not CaseFileKind.RENEWAL:
        return
    if case.origin_case_file_id is None:
        return
    source = db.scalars(
        select(CaseFile).where(
            CaseFile.id == case.origin_case_file_id, CaseFile.broker_id == broker_id
        )
    ).first()
    if source is None:
        return
    candidates = [
        prior
        for prior in case_policies(db, source)
        if prior.id != policy.id and prior.client_id == policy.client_id
    ]
    if not candidates:
        return
    # The most recent prior policy: latest end_date, then highest id.
    candidates.sort(key=lambda p: (p.end_date or date.min, p.id))
    policy.renews_policy_id = candidates[-1].id


@router.post("", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
def create_policy(
    payload: PolicyCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> PolicyRead:
    """Register an issued policy. ``policy_number`` is unique PER BROKER."""
    _resolve_insurer(db, payload.insurer_id)
    _resolve_document(db, payload.source_document_id, broker_id)

    data = payload.model_dump(exclude_unset=False)
    client_id = data.pop("client_id", None)
    if client_id is None:
        placement_id = data.get("placement_id")
        if placement_id is not None:
            from app.models.placement import Placement

            placement = db.scalars(
                select(Placement).where(
                    Placement.id == placement_id, Placement.broker_id == broker_id
                )
            ).first()
            if placement is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Placement not found"
                )
            client_id = placement.client_id
    if client_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_id is required unless a placement is supplied",
        )
    client = db.scalars(
        select(Client).where(Client.id == client_id, Client.broker_id == broker_id)
    ).first()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    policy = Policy(broker_id=broker_id, client_id=client.id, **data)
    _apply_money(policy, data)
    _sync_period(policy)
    _apply_renewal_link(db, policy, broker_id)
    db.add(policy)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Policy number '{payload.policy_number}' already exists for this broker",
        ) from exc

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.created",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"Póliza {policy.policy_number}",
        meta={"insurer_id": policy.insurer_id, "status": policy.status.value},
    )
    db.commit()
    db.refresh(policy)
    return _read(db, policy)


@router.post("/upload", response_model=PolicyUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_policy(
    payload: PolicyUploadRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> PolicyUploadResponse:
    """Validate-then-dynamic policy ingestion (v8).

    Points at an already-filed ``document``, runs the context-aware POLICY
    extraction, checks the fixed minimal core and commits — persisting the FULL
    parse on ``policy.payload``. A file that does not look like a policy is a
    422 ``not_a_policy`` unless the broker sets ``override`` (the confirmed
    "register it anyway"). suggest -> confirm -> commit: the extraction row is
    always written first (rule 6), the entity only on this call.
    """
    from app.models.document import DocumentCategory

    # The document must be in this workspace (a foreign row is a 404, never 403).
    document = ai_service.get_document(db, payload.document_id, broker_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {payload.document_id} not found",
        )

    # Attach the account folder when the caller names one and the document has
    # none — extraction context + the committer both read ``case_file_id``.
    if payload.case_file_id is not None:
        from app.models.case_file import CaseFile

        case = db.scalars(
            select(CaseFile).where(
                CaseFile.id == payload.case_file_id, CaseFile.broker_id == broker_id
            )
        ).first()
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Case file {payload.case_file_id} not found",
            )
        if document.case_file_id is None:
            document.case_file_id = case.id
            db.commit()
    if document.case_file_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "El documento no está asociado a una cuenta — indica case_file_id "
                "para registrar la póliza en su expediente"
            ),
        )

    # SUGGEST: writes exactly one extraction row in every outcome.
    try:
        result = ai_service.extract_document(
            db,
            document_id=document.id,
            broker_id=broker_id,
            user=current_user,
            category=DocumentCategory.POLICY,
        )
    except ai_service.AIError as exc:
        from app.api.routers.ai import _ai_http_error

        raise _ai_http_error(exc) from exc

    if result.payload is None:
        # Nothing parsed at all — treat as "not a policy" (even override needs a
        # payload to commit). The failed/empty read persists on the extraction.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "not_a_policy",
                "reason": (
                    "Este archivo no parece ser una póliza: no se pudo extraer "
                    "ningún dato estructurado."
                ),
                "missing": ["corredor", "vigencia", "prima"],
                "detail": {},
            },
        )

    # COMMIT: the committer re-runs the fixed-core gate (with the same override)
    # and persists the full payload + verdict + provenance.
    try:
        commit_result = extraction_commit.commit_extraction(
            db,
            extraction=result.extraction,
            spec=result.spec,
            payload=result.payload,
            broker_id=broker_id,
            user=current_user,
            override=payload.override,
        )
    except extraction_commit.CommitError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if commit_result.entity_id is not None:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="policy.uploaded",
            entity_type=EntityType.POLICY,
            entity_id=commit_result.entity_id,
            description="Póliza registrada desde el archivo emitido",
            meta={
                "extraction_id": result.extraction.id,
                "is_core_valid": commit_result.extra.get("is_core_valid"),
                "overridden": commit_result.extra.get("overridden"),
            },
        )

    # One transaction per upload: stage the reviewed payload + audit stamp and
    # commit everything the committer flushed.
    ai_service.record_confirmation(
        db,
        extraction=result.extraction,
        payload=result.payload,
        user=current_user,
        target=result.spec.prefill_target,
        applied=commit_result.applied,
    )

    policy = get_policy_or_404(db, commit_result.entity_id, broker_id)
    return PolicyUploadResponse(
        policy=_read(db, policy),
        extraction_id=result.extraction.id,
        is_core_valid=bool(commit_result.extra.get("is_core_valid")),
        core_validation=policy.core_validation,
        overridden=bool(commit_result.extra.get("overridden")),
        warnings=[*result.warnings, *commit_result.warnings],
    )


@router.post("/from-proposal", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
def create_policy_from_proposal(
    payload: PolicyFromProposal,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> PolicyRead:
    """Build the draft policy from the accepted proposal — the mirror baseline.

    Everything the broker asked the carrier to issue is copied verbatim; the
    later ``mirror-diff`` measures the issued 08 against exactly this row.

    This is the ONE spelling of the spec's ``/policies/{id}/from-proposal``
    endpoint (there is no policy yet, so the id rides in the body) — it is the
    route the frontend calls, and registering a second path-parameter variant
    would shadow ``GET /policies/{policy_id}``-style routing for no caller.
    """
    return _build_from_proposal(db, payload, broker_id, current_user)


def _build_from_proposal(
    db: Session, payload: PolicyFromProposal, broker_id: int, current_user: User
) -> PolicyRead:
    proposal = db.scalars(
        select(Proposal)
        .where(Proposal.id == payload.proposal_id, Proposal.broker_id == broker_id)
        .options(selectinload(Proposal.quote_request))
    ).first()
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found"
        )
    if proposal.status != ProposalStatus.ACCEPTED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Only an accepted proposal becomes a policy; proposal "
                f"{proposal.id} is '{proposal.status.value}'"
            ),
        )
    existing = db.scalars(select(Policy).where(Policy.proposal_id == proposal.id)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Proposal {proposal.id} already produced policy {existing.id}",
        )

    _resolve_insurer(db, proposal.insurer_id)
    _resolve_document(db, payload.source_document_id, broker_id)

    quote = proposal.quote_request
    placement = quote.placement if quote is not None else None
    number = payload.policy_number or (
        proposal.quotation_number or f"BORRADOR-{proposal.id}"
    )

    policy = Policy(
        broker_id=broker_id,
        client_id=placement.client_id if placement else quote.broker_id,
        asset_id=placement.asset_id if placement else None,
        placement_id=placement.id if placement else None,
        proposal_id=proposal.id,
        insurer_id=proposal.insurer_id,
        insurance_line_id=placement.insurance_line_id if placement else None,
        case_file_id=payload.case_file_id or proposal.case_file_id,
        source_document_id=payload.source_document_id,
        policy_number=number,
        start_date=proposal.coverage_start,
        end_date=proposal.coverage_end,
        period_start_at=payload.period_start_at,
        period_end_at=payload.period_end_at,
        status=PolicyStatus.DRAFT,
        cover_mode=proposal.cover_mode,
        insured_amount_uf=quote.declared_value_uf if quote else None,
        taxable_premium_uf=proposal.taxable_premium_uf,
        exempt_premium_uf=proposal.exempt_premium_uf,
        net_premium_uf=proposal.net_premium_uf,
        vat_uf=proposal.vat_uf,
        total_premium_uf=proposal.total_premium_uf,
        commission_pct=proposal.commission_pct,
        average_rate_permille=proposal.comprehensive_rate_permille,
        deductibles=proposal.deductibles,
        notes=proposal.notes,
    )
    _sync_period(policy)
    _apply_renewal_link(db, policy, broker_id)
    db.add(policy)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Policy number '{number}' already exists for this broker",
        ) from exc

    # The as-issued coverage list starts as a copy of the accepted proposal's.
    from app.models.policy import CoverageItem

    for coverage in proposal.coverages:
        db.add(
            CoverageItem(
                policy_id=policy.id,
                kind=coverage.kind,
                description=coverage.text,
                sort_order=coverage.sort_order,
            )
        )

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.created_from_proposal",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"Póliza {policy.policy_number} desde la propuesta {proposal.id}",
        meta={"proposal_id": proposal.id, "insurer_id": policy.insurer_id},
    )
    db.commit()
    db.refresh(policy)
    return _read(db, policy)


@router.get("/{policy_id}", response_model=PolicyRead)
def get_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> PolicyRead:
    return _read(db, get_policy_or_404(db, policy_id, broker_id))


@router.patch("/{policy_id}", response_model=PolicyRead)
def update_policy(
    policy_id: int,
    payload: PolicyUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Edit")),
) -> PolicyRead:
    policy = get_policy_or_404(db, policy_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _read(db, policy)
    if changes.get("insurer_id") is not None:
        _resolve_insurer(db, changes["insurer_id"])
    if "source_document_id" in changes:
        _resolve_document(db, changes["source_document_id"], broker_id)

    _apply_money(policy, changes)
    for field, value in changes.items():
        setattr(policy, field, value)
    _sync_period(policy)

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.updated",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"Póliza actualizada ({', '.join(sorted(changes))})",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(policy)
    return _read(db, policy)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Delete")),
) -> Response:
    """Only a draft with nothing hanging off it can be deleted."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    if policy.status != PolicyStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only a draft policy can be deleted — cancel it instead",
        )
    for model, label in ((Endorsement, "endorsements"), (Claim, "claims")):
        found = db.scalar(
            select(func.count(model.id)).where(model.policy_id == policy.id)
        )
        if found:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Policy {policy.id} still has {int(found)} {label}",
            )
    db.delete(policy)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.deleted",
        entity_type=EntityType.POLICY,
        entity_id=policy_id,
        description="Póliza eliminada",
        meta={},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Mirror validation -----------------------------------------------------------

@router.get("/{policy_id}/mirror-diff", response_model=MirrorDiffRead)
def get_mirror_diff(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> MirrorDiffRead:
    """Field-by-field: what the 07 proposed vs what the 08 actually issued."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    sources = mirror_service.load_sources(db, policy=policy)
    rows = mirror_service.diff_payloads(sources.proposal_payload, sources.policy_payload)
    return MirrorDiffRead(
        policy_id=policy.id,
        proposal_extraction_id=sources.proposal_extraction_id,
        policy_extraction_id=sources.policy_extraction_id,
        proposal_source=sources.proposal_source,
        policy_source=sources.policy_source,
        missing_sources=sources.missing or [],
        rows=[MirrorDiffRowRead(**row.as_dict()) for row in rows],
    )


@router.post("/{policy_id}/mirror-diff/queue", response_model=MirrorDiffQueueResult)
def queue_mirror_diff(
    policy_id: int,
    payload: MirrorDiffQueue,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Endorsements", "Create")),
) -> MirrorDiffQueueResult:
    """Turn selected diff rows into ``endorsement(status=draft)`` — suggestions."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    sources = mirror_service.load_sources(db, policy=policy)
    rows = mirror_service.diff_payloads(sources.proposal_payload, sources.policy_payload)
    if payload.paths:
        wanted = set(payload.paths)
        rows = [row for row in rows if row.path in wanted]
        missing = wanted - {row.path for row in rows}
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No current diff row for: {', '.join(sorted(missing))}",
            )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The mirror validation found nothing to queue",
        )

    created = mirror_service.queue_endorsements(
        db, policy=policy, rows=rows, user_id=current_user.id,
        case_file_id=payload.case_file_id,
    )
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.mirror_diff_queued",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"{len(created)} endoso(s) en borrador desde la validación espejo",
        meta={"paths": [row.path for row in rows]},
    )
    db.commit()
    return MirrorDiffQueueResult(
        policy_id=policy.id,
        queued=len(created),
        endorsement_ids=[row.id for row in created],
    )


# --- Warranties -------------------------------------------------------------------

@router.get("/{policy_id}/warranties", response_model=list[WarrantyRead])
def list_warranties(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> list[WarrantyRead]:
    """The R-n / G-n / M-n tracker of one policy, in the source's own order."""
    get_policy_or_404(db, policy_id, broker_id)
    rows = db.scalars(
        select(Warranty)
        .where(Warranty.broker_id == broker_id, Warranty.policy_id == policy_id)
        .order_by(Warranty.sort_order.asc(), Warranty.id.asc())
    ).all()
    return [WarrantyRead.model_validate(row) for row in rows]


@router.post(
    "/{policy_id}/warranties", response_model=WarrantyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_warranty(
    policy_id: int,
    payload: WarrantyCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Edit")),
) -> WarrantyRead:
    policy = get_policy_or_404(db, policy_id, broker_id)
    _resolve_document(db, payload.evidence_document_id, broker_id)
    warranty = Warranty(
        broker_id=broker_id, policy_id=policy.id, **payload.model_dump(exclude_unset=False)
    )
    db.add(warranty)
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="warranty.created",
        entity_type=EntityType.WARRANTY,
        entity_id=warranty.id,
        description=f"Garantía {warranty.code or warranty.title or warranty.id}",
        meta={"policy_id": policy.id, "status": warranty.status.value},
    )
    db.commit()
    db.refresh(warranty)
    return WarrantyRead.model_validate(warranty)


@warranties_router.patch("/{warranty_id}", response_model=WarrantyRead)
def update_warranty(
    warranty_id: int,
    payload: WarrantyUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Edit")),
) -> WarrantyRead:
    """Mark an obligation met, breached or waived — the coverage-deciding column."""
    warranty = db.scalars(
        select(Warranty).where(
            Warranty.id == warranty_id, Warranty.broker_id == broker_id
        )
    ).first()
    if warranty is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Warranty not found"
        )
    changes = payload.model_dump(exclude_unset=True)
    if "evidence_document_id" in changes:
        _resolve_document(db, changes["evidence_document_id"], broker_id)
    for field, value in changes.items():
        setattr(warranty, field, value)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="warranty.updated",
        entity_type=EntityType.WARRANTY,
        entity_id=warranty.id,
        description=f"Garantía {warranty.code or warranty.id} actualizada",
        meta={"fields": sorted(changes)},
    )
    db.commit()
    db.refresh(warranty)
    return WarrantyRead.model_validate(warranty)


@warranties_router.get("/{warranty_id}", response_model=WarrantyRead)
def get_warranty(
    warranty_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> WarrantyRead:
    warranty = db.scalars(
        select(Warranty).where(
            Warranty.id == warranty_id, Warranty.broker_id == broker_id
        )
    ).first()
    if warranty is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Warranty not found"
        )
    return WarrantyRead.model_validate(warranty)


__all__ = ["router", "warranties_router", "get_policy_or_404"]
