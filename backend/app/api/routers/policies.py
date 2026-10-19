"""``/policies`` — the main view (v12), the issued contract, warranties, mirror.

v12 made policies THE home of the app (docs/v12/02-backend-api.md §2):

* ``GET /policies`` filters by computed status, cobranza state, "Falta: X",
  ramo/subtipo, origin and owner, and every row carries the cobranza line;
* ``GET /policies/summary`` adds the main-view cards;
* ``POST /policies`` with ``insurance_subtype_id`` is the **vía Manual**: five
  mandatory data, the colocación found or created on demand
  (``services.policy_registry``), default parties, ``origin=manual``;
* ``PATCH /policies/{id}`` is "Completar" (fill empty fields, no reason) or
  "Corregir dato" (change a set field: ``correction_reason`` + before/after);
* ``PUT /policies/{id}/parties`` replaces who is who;
* ``POST /policies/upload`` registers a PDF (``origin=reader``) — also from the
  Pólizas list with no folder yet — and commits its cuotas when they balance.

Kept from earlier passes: ``POST /policies/from-proposal`` builds the draft (the
mirror baseline), ``GET /policies/{id}/mirror-diff`` compares it with the issued
08, ``/policies/{id}/warranties`` tracks R-n / G-n / M-n.

Money reuses ``reconcile_money`` from ``app.schemas.proposal``: one
implementation of the Chilean premium arithmetic, not two.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

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
from app.models.case_file import CaseFile
from app.models.catalog import InsuranceSubtype
from app.models.client import Client, ClientStatus
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.document import Document
from app.models.endorsement import Endorsement
from app.models.enums import (
    CaseEntryOrigin,
    CaseFileStatus,
    EntityType,
    PartyRole,
    PolicyOrigin,
)
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.schemas.wallclock import to_wall_clock
from app.models.insurer import Insurer
from app.models.policy import Claim, Policy, PolicyStatus
from app.models.policy_party import PolicyParty
from app.models.proposal import Proposal, ProposalStatus
from app.models.user import User
from app.models.warranty import Warranty
from app.schemas.catalog import LineRef, SubtypeRef
from app.schemas.policy import (
    CollectionSummaryRead,
    MirrorDiffQueue,
    MirrorDiffQueueResult,
    MirrorDiffRead,
    MirrorDiffRowRead,
    OperabilityRead,
    PartiesReplace,
    PartyRead,
    PolicyCreate,
    PolicyDetail,
    PolicyFromProposal,
    PolicyJourneyRead,
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
from app.services import case_files as machine
from app.services import catalog as catalog_service
from app.services import extraction_commit
from app.services import mirror as mirror_service
from app.services import policy_reading
from app.services import policy_duplicates, policy_reading_commit
from app.services import policy_registry as registry

router = APIRouter(prefix="/policies", tags=["policies"])

#: Warranties are reached by their own id once the policy has been resolved.
warranties_router = APIRouter(prefix="/warranties", tags=["policies"])

#: The Chilean noon convention: a policy period runs 12:00 to 12:00.
NOON = time(12, 0)

#: ``?status=`` values: the COMPUTED status (v12) plus the two stored-only ones.
COMPUTED_STATUSES = ("upcoming", "active", "expired", "cancelled")
STORED_ONLY_STATUSES = ("draft", "renewed")
COLLECTION_FILTERS = ("overdue", "due_soon", "no_plan", "ok")


# --- Helpers -------------------------------------------------------------------

def get_policy_or_404(db: Session, policy_id: int, broker_id: int) -> Policy:
    policy = db.scalars(
        select(Policy).where(Policy.id == policy_id, Policy.broker_id == broker_id)
    ).first()
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Póliza no encontrada")
    return policy


def _resolve_insurer(db: Session, insurer_id: int, broker_id: int) -> Insurer:
    """The insurer, as the broker may see it (the ``insurers._visible_insurers`` rule).

    Native and Radal-seeded insurers are canonical; a non-native insurer a
    broker created is that broker's private row — another broker gets the
    same 404 as an unknown id, never a policy pointing at it.
    """
    insurer = db.scalar(
        select(Insurer).where(
            Insurer.id == insurer_id,
            or_(
                Insurer.is_native.is_(True),
                Insurer.created_by_broker_id.is_(None),
                Insurer.created_by_broker_id == broker_id,
            ),
        )
    )
    if insurer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compañía no encontrada")
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
            detail="Documento no encontrado",
        )


def _resolve_subtype(db: Session, subtype_id: int) -> InsuranceSubtype:
    row = catalog_service.get_subtype(db, subtype_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=catalog_service.SUBTYPE_INVALID_DETAIL,
        )
    return row


def _resolve_own_case(db: Session, case_file_id: int, broker_id: int) -> CaseFile:
    case = db.scalars(
        select(CaseFile).where(CaseFile.id == case_file_id, CaseFile.broker_id == broker_id)
    ).first()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada"
        )
    return case


def _sync_period(policy: Policy) -> None:
    """Keep ``start_date``/``end_date`` and the noon datetimes in step (§2.9).

    The datetimes are Chile wall-clock, offset-less (decision 2026-10-17): an
    aware value is converted to America/Santiago, a derived one is 12:00.
    """
    policy.period_start_at = to_wall_clock(policy.period_start_at)
    policy.period_end_at = to_wall_clock(policy.period_end_at)
    if policy.period_start_at is not None and policy.start_date is None:
        policy.start_date = policy.period_start_at.date()
    if policy.period_end_at is not None and policy.end_date is None:
        policy.end_date = policy.period_end_at.date()
    if policy.start_date is not None and policy.period_start_at is None:
        policy.period_start_at = datetime.combine(policy.start_date, NOON)
    if policy.end_date is not None and policy.period_end_at is None:
        policy.period_end_at = datetime.combine(policy.end_date, NOON)


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


def _with_policy_children():
    """Eager loads the row extras need (cobranza line + parties)."""
    return (
        selectinload(Policy.collection_plans)
        .selectinload(CollectionPlan.installments)
        .selectinload(CollectionInstallment.payments),
        selectinload(Policy.parties),
        selectinload(Policy.coinsurance_shares),
    )


def _read(
    db: Session,
    policy: Policy,
    *,
    today: date | None = None,
    cache: dict | None = None,
) -> PolicyRead:
    """One row of the main view: the policy + names, counts and v12 extras."""
    today = today or registry.today_santiago()
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
    subtype = (
        db.get(InsuranceSubtype, policy.insurance_subtype_id)
        if policy.insurance_subtype_id is not None
        else None
    )
    line = (
        db.get(InsuranceLine, policy.insurance_line_id)
        if policy.insurance_line_id is not None
        else None
    )
    group_id = client.account_group_id if client else None
    if policy.case_file_id is not None:
        case = db.get(CaseFile, policy.case_file_id)
        if case is not None and case.broker_id == policy.broker_id and case.account_group_id:
            group_id = case.account_group_id
    summary = registry.collection_summary(db, policy, today=today, params_cache=cache)
    from app.schemas.collection import PastInstallmentsRead  # noqa: PLC0415

    return PolicyRead.model_validate(policy).model_copy(
        update={
            "insurer_name": insurer.legal_name if insurer else None,
            "client_legal_name": insured.legal_name if insured else None,
            **counts,
            "computed_status": registry.computed_status(policy, today),
            "subtype": (
                SubtypeRef(id=subtype.id, code=subtype.code, name=subtype.name)
                if subtype is not None
                else None
            ),
            "line": LineRef(id=line.id, code=line.code, name=line.name) if line else None,
            "insured_name": insured.legal_name if insured else None,
            "insured_rut": insured.rut if insured else None,
            "group_id": group_id,
            "has_policy_number": not registry.is_placeholder_number(policy.policy_number),
            "collection_summary": CollectionSummaryRead(**summary),
            "past_installments_pending": (
                PastInstallmentsRead(**summary["past_installments_pending"])
                if summary.get("past_installments_pending")
                else None
            ),
            "missing": registry.compute_policy_missing(db, policy),
        }
    )


def _detail(db: Session, policy: Policy, *, warnings: list[str] | None = None) -> PolicyDetail:
    """The ficha: row + parties + operability ("Falta: X") + journey."""
    db.refresh(policy)
    row = _read(db, policy)
    parties = db.scalars(
        select(PolicyParty)
        .where(PolicyParty.broker_id == policy.broker_id, PolicyParty.policy_id == policy.id)
        .order_by(PolicyParty.id.asc())
    ).all()
    journey = registry.policy_journey(db, policy)
    return PolicyDetail(
        **row.model_dump(),
        parties=[PartyRead.model_validate(p) for p in parties],
        operability=[
            OperabilityRead(**item) for item in registry.policy_operability(db, policy, row.missing)
        ],
        journey=PolicyJourneyRead(**journey) if journey else None,
        warnings=list(warnings or []),
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


# --- Main view: summary + list --------------------------------------------------

def _count(db: Session, *where) -> int:
    return int(db.scalar(select(func.count(Policy.id)).where(*where)) or 0)


def _has_installments_clause():
    """``EXISTS`` a cuota on any of the policy's plans."""
    return (
        select(CollectionInstallment.id)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .where(CollectionPlan.policy_id == Policy.id)
        .exists()
    )


def _missing_clause(key: str):
    """SQL twin of one :func:`registry.compute_policy_missing` key (not
    ``payer_contact``, which reads party e-mails and stays in Python)."""
    if key == "source_document":
        return Policy.has_source_document.is_(False)
    if key == "policy_number":
        return Policy.policy_number.like(f"{registry.PLACEHOLDER_NUMBER_PREFIX}%")
    if key == "plan":
        return ~_has_installments_clause()
    if key == "gross_premium":
        return Policy.total_premium_uf.is_(None)
    if key == "premiums":
        return or_(
            Policy.net_premium_uf.is_(None),
            (Policy.taxable_premium_uf.is_(None) & Policy.exempt_premium_uf.is_(None)),
        )
    if key == "commission":
        return Policy.commission_pct.is_(None)
    raise ValueError(f"no SQL clause for missing key {key!r}")


def _late_horizon(cadence: list[int], today: date) -> date:
    """The latest due date a LATE cuota can have today. Late normally needs
    ``today > due``, but ``termination_risk`` fires at ``today > due +
    max(cadence)``: a broker cadence that is all negative makes a cuota late
    before it falls due, so the horizon moves forward by that slack."""
    slack = max(0, -max(cadence)) if cadence else 0
    return today + timedelta(days=slack)


def _collection_narrowing(state: str, today: date, late_before: date | None = None):
    """A NECESSARY condition (SQL) for a cobranza state that needs cuotas — the
    exact state is then computed per row. Every late state needs a cuota past
    due; ``due_soon`` needs one due within the window; ``ok`` needs any."""
    base = (
        select(CollectionInstallment.id)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .where(CollectionPlan.policy_id == Policy.id)
    )
    if state == "overdue":
        return base.where(
            CollectionInstallment.due_date.is_not(None),
            CollectionInstallment.due_date < (late_before or today),
        ).exists()
    if state == "due_soon":
        return base.where(
            CollectionInstallment.due_date.is_not(None),
            CollectionInstallment.due_date <= today + timedelta(days=registry.DUE_SOON_DAYS),
        ).exists()
    return base.exists()


def owner_me_clause(broker_id: int, user_id: int):
    """``owner=me`` on policies: the policy's account is theirs, or its client is
    one they manage. Shared by the list filter and the copilot home."""
    return or_(
        Policy.case_file_id.in_(
            select(CaseFile.id).where(
                CaseFile.broker_id == broker_id,
                CaseFile.owner_user_id == user_id,
            )
        ),
        Policy.client_id.in_(
            select(Client.id).where(
                Client.broker_id == broker_id,
                Client.account_manager_id == user_id,
            )
        ),
    )


def expiring_clauses(today: date, days: int) -> list:
    """Computed-active policies whose ``end_date`` falls within ``days`` (incl.
    today) — ONE definition for the list filter and the cards that count it."""
    return [
        registry.computed_status_clause("active", today),
        Policy.end_date.is_not(None),
        Policy.end_date >= today,
        Policy.end_date <= today + timedelta(days=days),
    ]


def count_expiring(db: Session, scoped: list, today: date, days: int) -> int:
    """Computed-active policies whose ``end_date`` falls within ``days`` (incl. today)."""
    return _count(db, *scoped, *expiring_clauses(today, days))


def _summary_cards(db: Session, broker_id: int, scoped: list, today: date) -> dict[str, Any]:
    """The v12 main-view cards, computed SQL-side.

    Only the arrears need the cuota state machine; they are read over the cuotas
    already past due (every late state needs ``today > due``) of the scoped,
    non-cancelled policies — one query, no per-policy resolver.
    """
    from app.services.collection.status import (
        broker_collection_params,
        compute_installment_state,
    )

    active = registry.computed_status_clause("active", today)
    live = or_(active, registry.computed_status_clause("upcoming", today))
    premium = db.scalar(
        select(func.sum(Policy.total_premium_uf)).where(*scoped, active)
    )
    cards: dict[str, Any] = {
        "active_count": _count(db, *scoped, active),
        "active_premium_uf": Decimal(str(premium or 0)),
        "expiring_90": count_expiring(db, scoped, today, 90),
        "no_plan": _count(db, *scoped, live, ~_has_installments_clause()),
        "without_source_document": _count(
            db, *scoped, live, Policy.has_source_document.is_(False)
        ),
        "provisional": _count(db, *scoped, live, Policy.is_provisional.is_(True)),
        "overdue_installments": 0,
        "overdue_uf": Decimal("0"),
    }

    cadence, tolerance = broker_collection_params(db, broker_id)
    past_due = db.scalars(
        select(CollectionInstallment)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .join(Policy, Policy.id == CollectionPlan.policy_id)
        .where(
            *scoped,
            ~registry.computed_status_clause("cancelled", today),
            CollectionInstallment.broker_id == broker_id,
            CollectionInstallment.due_date.is_not(None),
            CollectionInstallment.due_date < _late_horizon(cadence, today),
        )
        .options(selectinload(CollectionInstallment.payments))
    ).all()
    if past_due:
        from app.services.collection.past_review import review_cutoffs

        cutoffs = review_cutoffs(db, broker_id)
        for inst in past_due:
            state = compute_installment_state(
                inst,
                list(inst.payments or []),
                today=today,
                cadence_days=cadence,
                tolerance_uf=tolerance,
                review_cutoff=cutoffs.get(inst.collection_plan_id),
            )
            if state.state in registry.LATE_STATES:
                cards["overdue_installments"] += 1
                cards["overdue_uf"] += Decimal(str(state.balance_uf))
    for key in ("active_premium_uf", "overdue_uf"):
        cards[key] = Decimal(str(cards[key])).quantize(Decimal("0.0001"))
    return cards


# NOTE: registered before ``/{policy_id}`` so the literal path wins the match.
@router.get("/summary", response_model=PolicySummary)
def get_policies_summary(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
    scope: ScopeFilters = Depends(scope_filters),
    group_by: str | None = group_by_query(),
    owner: Literal["me"] | None = Query(default=None),
    mine: bool = Query(default=False),
) -> PolicySummary:
    """The portfolio board + the v12 main-view cards.

    Legacy fields (``by_status``, ``total_premium_uf``, ``expiring_within_60_days``)
    read the STORED status. v12 cards read the COMPUTED one (dates +
    cancellation): ``active_count`` / ``active_premium_uf`` / ``expiring_90``
    count computed-active policies; ``no_plan``, ``without_source_document`` and
    ``provisional`` count live (upcoming or active) ones; ``overdue_installments``
    / ``overdue_uf`` add up every non-cancelled policy's late cuotas.

    Scope: ``account_group_id`` resolves through ``policy.case_file_id ->
    case_file.account_group_id`` OR ``policy.client_id -> client.account_group_id``.
    The date window sits on ``start_date``. ``group_by`` accepts
    ``status | insurer | insurance_line | account_group | month``.
    """
    scoped = [Policy.broker_id == broker_id, *scope_predicates("policies", scope, broker_id)]
    narrowed = owner == "me" or mine
    if narrowed:
        # Review r2 (B5): the cards follow the list's "Mías" (``owner=me``).
        scoped.append(owner_me_clause(broker_id, _user.id))
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

    today = registry.today_santiago()
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

    cards = _summary_cards(db, broker_id, scoped, today)

    return PolicySummary(
        total=sum(by_status.values()),
        by_status={s.value: by_status.get(s.value, 0) for s in PolicyStatus},
        # SQLite hands aggregates back as floats; round-trip through str() keeps
        # the Decimal exact and quantized to the column's 4 decimals.
        total_premium_uf=(
            Decimal(str(premium_sum)).quantize(Decimal("0.0001"))
            if premium_sum is not None
            else Decimal("0")
        ),
        expiring_within_60_days=expiring,
        owner_scope="mine" if narrowed else "all",
        grouped=grouped_or_none(
            db, "policies", group_by=group_by, broker_id=broker_id, extra_filters=scoped
        ),
        **cards,
    )


def _q_variants(q: str) -> list[str]:
    """The search needle plus its normalised RUT form (``77.111.111-4`` -> ``77111111-4``)."""
    needle = q.strip()
    variants = [needle]
    try:
        from app.services.identifiers import normalize_rut

        normalized = normalize_rut(needle)
        if normalized not in variants:
            variants.append(normalized)
    except Exception:  # noqa: BLE001 - not a RUT, plain text search
        pass
    return variants


@router.get("", response_model=PolicyPage)
def list_policies(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "View")),
    client_id: int | None = Query(default=None, gt=0),
    insurer_id: int | None = Query(default=None, gt=0),
    placement_id: int | None = Query(default=None, gt=0),
    scope: ScopeFilters = Depends(scope_filters),
    status_filter: list[str] | None = Query(
        default=None,
        alias="status",
        description="computed: upcoming | active | expired | cancelled (stored: draft | renewed)",
    ),
    q: str | None = Query(default=None, description="policy number, insured name or RUT"),
    line_code: str | None = Query(default=None, max_length=8),
    subtype_id: int | None = Query(default=None, gt=0),
    collection: list[str] | None = Query(
        default=None, description="overdue | due_soon | no_plan | ok"
    ),
    origin: list[PolicyOrigin] | None = Query(default=None),
    missing: list[str] | None = Query(
        default=None,
        description="source_document | plan | payer_contact | commission | policy_number | …",
    ),
    owner: Literal["me"] | None = Query(default=None),
    mine: bool = Query(default=False, description="alias of owner=me"),
    expiring_within_days: int | None = Query(
        default=None,
        ge=0,
        le=3650,
        description="computed-active policies whose end date falls within N days (today included)",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PolicyPage:
    """The main view: the broker's policies, newest first.

    Scope params: ``account_group_id`` (grupo), ``case_file_id`` (grupo-cuenta),
    ``date_from`` / ``date_to`` on ``start_date``. A foreign id yields an empty
    page. ``status`` / ``collection`` / ``missing`` are COMPUTED per row and
    filtered after the SQL narrowing; repeated values OR together.
    """
    computed: set[str] = set()
    stored: set[str] = set()
    for value in status_filter or []:
        if value in COMPUTED_STATUSES:
            computed.add(value)
        elif value in STORED_ONLY_STATUSES:
            stored.add(value)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Estado desconocido: {value!r}",
            )
    for value in collection or []:
        if value not in COLLECTION_FILTERS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Filtro de cobranza desconocido: {value!r}",
            )
    for value in missing or []:
        if value not in registry.MISSING_KEYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Faltante desconocido: {value!r}",
            )

    stmt = select(Policy).where(
        Policy.broker_id == broker_id, *scope_predicates("policies", scope, broker_id)
    )
    if client_id is not None:
        stmt = stmt.where(Policy.client_id == client_id)
    if insurer_id is not None:
        stmt = stmt.where(Policy.insurer_id == insurer_id)
    if placement_id is not None:
        stmt = stmt.where(Policy.placement_id == placement_id)
    if stored:
        stmt = stmt.where(Policy.status.in_([PolicyStatus(v) for v in stored]))
    if subtype_id is not None:
        stmt = stmt.where(Policy.insurance_subtype_id == subtype_id)
    if line_code:
        code = line_code.strip().upper()
        stmt = stmt.where(
            or_(
                Policy.insurance_line_id.in_(
                    select(InsuranceLine.id).where(
                        InsuranceLine.broker_id.is_(None), InsuranceLine.code == code
                    )
                ),
                Policy.insurance_subtype_id.in_(
                    select(InsuranceSubtype.id)
                    .join(InsuranceLine, InsuranceLine.id == InsuranceSubtype.insurance_line_id)
                    .where(InsuranceLine.code == code)
                ),
            )
        )
    if origin:
        stmt = stmt.where(Policy.origin.in_(origin))
    if owner == "me" or mine:
        stmt = stmt.where(owner_me_clause(broker_id, current_user.id))
    if expiring_within_days is not None:
        # Review r2 (B4): the copilot's "vencen en 30 días" opens exactly this.
        stmt = stmt.where(*expiring_clauses(registry.today_santiago(), expiring_within_days))
    if q and q.strip():
        clauses = []
        for variant in _q_variants(q):
            needle = f"%{variant}%"
            clauses += [
                Policy.policy_number.ilike(needle),
                Insured.legal_name.ilike(needle),
                Insured.rut.ilike(needle),
            ]
        stmt = (
            stmt.join(Client, Client.id == Policy.client_id)
            .join(Insured, Insured.id == Client.insured_id)
            .where(or_(*clauses))
        )

    today = registry.today_santiago()
    cache: dict = {}
    if computed:
        # The computed status is pure date arithmetic: filtered in SQL.
        stmt = stmt.where(
            or_(*(registry.computed_status_clause(value, today) for value in sorted(computed)))
        )
    # Cobranza state and "Falta: X": everything expressible in SQL is filtered
    # there; only the cuota state machine (overdue / due_soon / ok) and the
    # payer contact (party e-mails) are left for Python, over a pre-narrowed set.
    py_collection = sorted(set(collection or []) - {"no_plan"})
    if collection:
        has_cuotas = _has_installments_clause()
        branches = [~has_cuotas] if "no_plan" in collection else []
        late_before = None
        if "overdue" in py_collection:
            from app.services.collection.status import broker_collection_params

            late_before = _late_horizon(broker_collection_params(db, broker_id)[0], today)
        branches += [
            _collection_narrowing(value, today, late_before) for value in py_collection
        ]
        stmt = stmt.where(or_(*branches))
    py_missing = bool(missing) and "payer_contact" in missing
    if missing and not py_missing:
        stmt = stmt.where(or_(*(_missing_clause(key) for key in missing)))
    ordered = stmt.order_by(Policy.id.desc()).options(*_with_policy_children())
    if py_collection or py_missing:
        # Evaluated per policy WITHOUT the level resolver; the full row is
        # built only for the returned page.
        kept: list[Policy] = []
        for policy in db.scalars(ordered).unique().all():
            if py_collection:
                line = registry.collection_summary(
                    db, policy, today=today, params_cache=cache, with_level=False
                )
                if line["state"] not in collection:
                    continue
            if py_missing and not any(
                key in registry.compute_policy_missing(db, policy) for key in missing
            ):
                continue
            kept.append(policy)
        return PolicyPage(
            items=[
                _read(db, policy, today=today, cache=cache)
                for policy in kept[offset: offset + limit]
            ],
            total=len(kept),
            limit=limit,
            offset=offset,
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = db.scalars(ordered.offset(offset).limit(limit)).unique().all()
    return PolicyPage(
        items=[_read(db, row, today=today, cache=cache) for row in page],
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


# --- Create: legacy register + v12 vía Manual -----------------------------------

#: Fields of ``PolicyCreate`` that are NOT policy columns (manual-entry inputs).
_MANUAL_ONLY_FIELDS = ("insured_rut", "insured_name", "policyholder", "payer")


def _client_from_rut(
    db: Session, *, broker_id: int, rut: str, name: str | None, user: User
) -> Client:
    """The broker's client for a RUT, created (with its canonical insured when
    the RUT is new) through the same rules as ``POST /clients`` — a broker is
    never blocked creating a client (rule 7)."""
    insured = db.scalars(select(Insured).where(Insured.rut == rut)).first()
    if insured is None:
        if not (name or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Este RUT no está registrado: indica también el nombre del asegurado (insured_name).",
            )
        insured = Insured(rut=rut, legal_name=name.strip()[:255])
        db.add(insured)
        db.flush()
    client = db.scalars(
        select(Client).where(Client.broker_id == broker_id, Client.insured_id == insured.id)
    ).first()
    if client is not None:
        return client
    client = Client(broker_id=broker_id, insured_id=insured.id, status=ClientStatus.ACTIVE)
    db.add(client)
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=user,
        action="client.created",
        entity_type=EntityType.CLIENT,
        entity_id=client.id,
        description=f"Cliente {insured.legal_name} ({insured.rut}) creado al registrar una póliza",
        meta={"rut": insured.rut, "insured_id": insured.id, "via": "policy"},
    )
    return client


def _number_taken(db: Session, broker_id: int, number: str, exclude_id: int | None = None) -> bool:
    stmt = select(Policy.id).where(Policy.broker_id == broker_id, Policy.policy_number == number)
    if exclude_id is not None:
        stmt = stmt.where(Policy.id != exclude_id)
    return db.scalar(stmt) is not None


def _check_manual_folder(
    db: Session,
    case: CaseFile,
    *,
    client: Client,
    subtype: InsuranceSubtype,
    start_date: date,
) -> None:
    """A manual policy registered INTO a named folder must fit it (422 else):
    an open account, of this insured (its client or a member), of the
    subtipo's ramo, whose vigencia covers the policy's start."""
    unfit = None
    if case.kind not in machine.ACCOUNT_KINDS:
        unfit = "La carpeta indicada no es una cuenta (colocación): elige la cuenta de la póliza."
    elif case.status != CaseFileStatus.OPEN:
        unfit = "La cuenta indicada no está abierta: no se le pueden agregar pólizas."
    elif not _is_member(db, case, client.id):
        unfit = "El asegurado de la póliza no es parte de la cuenta indicada."
    elif case.insurance_line_id is not None and case.insurance_line_id != subtype.insurance_line_id:
        unfit = "El ramo del subtipo elegido no corresponde al ramo de la cuenta indicada."
    elif (
        case.period_start is not None
        and case.period_end is not None
        and not (case.period_start <= start_date < case.period_end)
    ):
        unfit = (
            "La vigencia de la póliza no cae dentro de la vigencia de la cuenta indicada "
            f"({case.period_start.isoformat()} a {case.period_end.isoformat()})."
        )
    if unfit is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=unfit)


def _create_manual(
    db: Session, payload: PolicyCreate, broker_id: int, current_user: User
) -> PolicyDetail:
    """Vía Manual (team 3.2–3.3): RUT asegurado · contratante · compañía ·
    ramo·subtipo · vigencia are mandatory; everything else is "Falta: X"."""
    if payload.insurance_subtype_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elige el ramo y el subtipo de la póliza (insurance_subtype_id).",
        )
    subtype = _resolve_subtype(db, payload.insurance_subtype_id)
    if payload.insurance_line_id is not None and payload.insurance_line_id != subtype.insurance_line_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El ramo indicado no corresponde al subtipo elegido.",
        )
    _resolve_insurer(db, payload.insurer_id, broker_id)
    _resolve_document(db, payload.source_document_id, broker_id)
    if payload.start_date is None or payload.end_date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La vigencia (desde y hasta) es obligatoria para guardar la póliza.",
        )
    if payload.end_date <= payload.start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La vigencia debe terminar después de su inicio.",
        )

    # 2b.5: a policy with no folder hung off an insured already in the cartera.
    linked_existing = False
    if payload.client_id is not None:
        client = db.scalars(
            select(Client).where(Client.id == payload.client_id, Client.broker_id == broker_id)
        ).first()
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asegurado no encontrado")
        linked_existing = True
    elif payload.insured_rut:
        linked_existing = (
            db.scalar(
                select(Client.id)
                .join(Insured, Insured.id == Client.insured_id)
                .where(Client.broker_id == broker_id, Insured.rut == payload.insured_rut)
            )
            is not None
        )
        client = _client_from_rut(
            db, broker_id=broker_id, rut=payload.insured_rut, name=payload.insured_name,
            user=current_user,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Indica el asegurado: client_id, o insured_rut + insured_name.",
        )

    case = None
    if payload.case_file_id is not None:
        case = _resolve_own_case(db, payload.case_file_id, broker_id)
        _check_manual_folder(
            db, case, client=client, subtype=subtype, start_date=payload.start_date
        )

    warnings: list[str] = []
    number = (payload.policy_number or "").strip() or None
    if number is not None and _number_taken(db, broker_id, number):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una póliza con el n° '{number}' en esta corredora.",
        )
    if number is None and registry.possible_duplicates(
        db,
        broker_id=broker_id,
        client_id=client.id,
        insurer_id=payload.insurer_id,
        insurance_subtype_id=subtype.id,
        start_date=payload.start_date,
        end_date=payload.end_date,
    ):
        warnings.append(
            "Posible duplicado: ya hay una póliza del mismo asegurado, compañía, subtipo y vigencia."
        )

    data = payload.model_dump(exclude_unset=False)
    for key in ("client_id", *_MANUAL_ONLY_FIELDS):
        data.pop(key, None)
    data["policy_number"] = number or registry.provisional_number()
    data["insurance_line_id"] = subtype.insurance_line_id
    data["case_file_id"] = case.id if case is not None else None
    if data.get("status") in (None, PolicyStatus.DRAFT):
        data["status"] = PolicyStatus.ACTIVE
    policy = Policy(
        broker_id=broker_id,
        client_id=client.id,
        origin=PolicyOrigin.MANUAL,
        has_source_document=payload.source_document_id is not None,
        **data,
    )
    _apply_money(policy, data)
    _sync_period(policy)
    db.add(policy)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una póliza con el n° '{number}' en esta corredora.",
        ) from exc
    if number is None:
        policy.policy_number = registry.final_placeholder_number(policy)

    try:
        attachment = registry.attach_or_create_account(
            db,
            broker_id=broker_id,
            policy=policy,
            entry_origin=CaseEntryOrigin.MANUAL_ENTRY,
            user=current_user,
        )
    except (ValueError, machine.CaseTransitionError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    warnings += attachment.warnings
    _apply_renewal_link(db, policy, broker_id)
    if payload.payment_method is not None:
        policy.payment_method = payload.payment_method
    registry.ensure_default_parties(
        db,
        policy,
        policyholder=payload.policyholder.model_dump() if payload.policyholder else None,
        payer=payload.payer.model_dump() if payload.payer else None,
    )
    if payload.case_file_id is None and linked_existing:
        registry.notify_policy_linked_insured(
            db,
            broker_id=broker_id,
            policy=policy,
            client=client,
            case=attachment.case,
            case_created=attachment.created,
            uploader=current_user,
        )

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.created",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"Póliza {policy.policy_number} registrada a mano",
        meta={
            "insurer_id": policy.insurer_id,
            "origin": PolicyOrigin.MANUAL.value,
            "case_file_id": attachment.case.id,
            "case_created": attachment.created,
        },
    )
    db.commit()
    return _detail(db, policy, warnings=warnings)


@router.post("", response_model=PolicyDetail, status_code=status.HTTP_201_CREATED)
def create_policy(
    payload: PolicyCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> PolicyDetail:
    """Register a policy.

    With ``insurance_subtype_id`` (or ``insured_rut``) this is the v12 **vía
    Manual**: see :func:`_create_manual` (409 on a duplicate number, colocación
    on demand, parties). Without them it is the legacy register (``client_id``
    or ``placement_id`` + ``policy_number``; 422 on a duplicate number) — kept
    byte-compatible for the earlier flows, now stamped ``origin=manual``.
    """
    if payload.insurance_subtype_id is not None or payload.insured_rut is not None:
        return _create_manual(db, payload, broker_id, current_user)

    if not (payload.policy_number or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="policy_number is required (or use the manual entry with insurance_subtype_id)",
        )
    _resolve_insurer(db, payload.insurer_id, broker_id)
    _resolve_document(db, payload.source_document_id, broker_id)

    data = payload.model_dump(exclude_unset=False)
    for key in _MANUAL_ONLY_FIELDS:
        data.pop(key, None)
    data.pop("insurance_subtype_id", None)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asegurado no encontrado")

    policy = Policy(
        broker_id=broker_id,
        client_id=client.id,
        origin=PolicyOrigin.MANUAL,
        has_source_document=payload.source_document_id is not None,
        **data,
    )
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
    registry.ensure_default_parties(db, policy)

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
    return _detail(db, policy)


# --- Upload (vía lector) ----------------------------------------------------------

def _insured_suggestion(payload: Any) -> dict[str, Any] | None:
    """The insured the reader extracted, as a suggestion ``{rut, legal_name}``.

    Only the INSURED party counts: a contratante distinct from the insured is a
    party of the policy, never an asegurado (R14/R40)."""
    from app.services.identifiers import InvalidRut, validate_rut

    party = getattr(payload, "insured", None)
    if party is None:
        return None
    rut_text = getattr(party, "rut", None)
    name = getattr(party, "legal_name", None) or getattr(party, "trade_name", None)
    rut = None
    if rut_text:
        try:
            rut = validate_rut(str(rut_text))
        except InvalidRut:
            rut = None
    if rut is None and not (name or "").strip():
        return None
    return {"rut": rut, "legal_name": (name or "").strip() or None}


def _client_for_upload(
    db: Session, *, broker_id: int, payload: Any, user: User, create: bool
) -> tuple[Client | None, dict[str, Any] | None, bool]:
    """The insured of a PDF uploaded without a folder (2b.5).

    The RUT the reader extracted for the INSURED is looked up in the broker's
    portfolio: an existing client is ASSOCIATED (never a second one, R11).
    An unknown RUT becomes a client only when the broker confirmed it
    (``create_client``) — suggest → confirm → commit. Returns
    ``(client | None, suggestion, existed)`` — ``existed`` is True only when
    the client was already in the portfolio (the 2b.5 "enlazada" FYI).
    """
    suggestion = _insured_suggestion(payload)
    rut = suggestion.get("rut") if suggestion else None
    if rut is None:
        return None, suggestion, False
    existing = db.scalars(
        select(Client)
        .join(Insured, Insured.id == Client.insured_id)
        .where(Client.broker_id == broker_id, Insured.rut == rut)
    ).first()
    if existing is not None:
        return existing, suggestion, True
    if not create or not suggestion.get("legal_name"):
        return None, suggestion, False
    return (
        _client_from_rut(
            db, broker_id=broker_id, rut=rut, name=suggestion["legal_name"], user=user
        ),
        suggestion,
        False,
    )


def _is_member(db: Session, case: CaseFile, client_id: int) -> bool:
    from app.models.account_client import AccountClient

    if case.client_id == client_id:
        return True
    return (
        db.scalar(
            select(AccountClient.id).where(
                AccountClient.case_file_id == case.id, AccountClient.client_id == client_id
            )
        )
        is not None
    )


def _refuse_duplicate(db: Session, policy: Policy, *, document: Any, user: User) -> None:
    """409 ``policy_duplicate`` (D2). The request's document goes when it is a
    fresh orphan of this user — never when anything links it."""
    detail = policy_duplicates.duplicate_detail(db, policy)
    db.rollback()
    if document.id != policy.source_document_id:
        detail["document_removed"] = policy_duplicates.discard_request_document(
            db, document=document, user=user
        )
    else:
        detail["document_removed"] = False
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post("/upload", response_model=PolicyUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_policy(
    payload: PolicyUploadRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> PolicyUploadResponse:
    """Validate-then-dynamic policy ingestion (v8) — the vía lector (v12).

    Points at an already-filed ``document``, runs the context-aware POLICY
    extraction, checks the fixed minimal core and commits — persisting the FULL
    parse on ``policy.payload``. A file that does not look like a policy is a
    422 ``not_a_policy`` unless the broker sets ``override``. suggest -> confirm
    -> commit: the extraction row is always written first (rule 6).

    v12: the policy is stamped ``origin=reader`` / ``has_source_document``,
    hangs off its colocación (found or created on demand — a document filed
    under no folder needs ``insurance_subtype_id``, and the insured comes from
    ``client_id`` or the RUT the reader extracts), gets its default parties, and
    the extracted payment schedule becomes its collection plan when Σ cuotas
    equals the gross premium (else a warning, as before).
    """

    # The document must be in this workspace (a foreign row is a 404, never 403).
    document = ai_service.get_document(db, payload.document_id, broker_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Documento no encontrado",
        )

    # Review r2 (D2): the exact file of a policy already registered is a 409
    # before any read — no AI call, no side effect.
    same_file = policy_duplicates.duplicate_by_checksum(
        db, broker_id=broker_id, document=document
    )
    if same_file is not None:
        _refuse_duplicate(db, same_file, document=document, user=current_user)

    # Attach the account folder when the caller names one and the document has
    # none — extraction context + the committer both read ``case_file_id``.
    named_client: Client | None = None
    attached_case_here = False
    if payload.case_file_id is not None:
        case = _resolve_own_case(db, payload.case_file_id, broker_id)
        if payload.client_id is not None:
            named_client = db.scalars(
                select(Client).where(
                    Client.id == payload.client_id, Client.broker_id == broker_id
                )
            ).first()
            if named_client is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Asegurado no encontrado"
                )
            if not _is_member(db, case, named_client.id):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="El asegurado indicado no es parte de esta cuenta.",
                )
        if document.case_file_id is None:
            document.case_file_id = case.id
            attached_case_here = True
            db.commit()

    # v12: no folder at all -> the colocación is resolved on demand AFTER the
    # read (it needs the vigencia), so validate the cheap inputs first.
    on_demand = document.case_file_id is None
    subtype: InsuranceSubtype | None = None
    given_client: Client | None = None
    if on_demand:
        if payload.insurance_subtype_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "subtype_required",
                    "reason": (
                        "Elige el ramo y el subtipo de la póliza para ubicar o crear su "
                        "cuenta."
                    ),
                    "field": "insurance_subtype_id",
                },
            )
        subtype = _resolve_subtype(db, payload.insurance_subtype_id)
        if payload.client_id is not None:
            given_client = db.scalars(
                select(Client).where(
                    Client.id == payload.client_id, Client.broker_id == broker_id
                )
            ).first()
            if given_client is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Asegurado no encontrado"
                )

    # SUGGEST: writes exactly one extraction row in every outcome. Fast reading
    # (docs/v12/11): only the CORE groups run here, in parallel; the technical
    # data is a second request (``POST /policies/{id}/technical-read``).
    try:
        result = policy_reading.read_policy_core(
            db,
            document_id=document.id,
            broker_id=broker_id,
            user=current_user,
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

    # Review r2 (D2): a policy the broker already has (same n°, same insurer
    # when resolvable) is a 409 right after the core read — BEFORE any commit
    # work: no client, no plan, no notification, no technical re-read.
    existing = policy_duplicates.duplicate_by_number(
        db,
        broker_id=broker_id,
        payload=result.payload,
        chosen_insurer_id=payload.insurer_id,
    )
    if existing is not None:
        if attached_case_here:
            document.case_file_id = None
            db.commit()
        _refuse_duplicate(db, existing, document=document, user=current_user)

    attach_warnings: list[str] = []
    upload_client: Client | None = named_client
    # 2b.5: the no-folder upload landed on an insured ALREADY in the cartera
    # (picked by the broker, or matched by the RUT the reader extracted).
    linked_existing = False
    case_created = False
    if on_demand:
        client = given_client
        linked_existing = given_client is not None
        suggestion = None
        if client is None:
            client, suggestion, linked_existing = _client_for_upload(
                db,
                broker_id=broker_id,
                payload=result.payload,
                user=current_user,
                create=payload.create_client,
            )
        if client is None:
            known_rut = bool(suggestion and suggestion.get("rut"))
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "insured_unresolved",
                    "reason": (
                        "El RUT del asegurado leído no está en tu cartera: créalo o "
                        "elige el asegurado."
                        if known_rut
                        else "No pudimos identificar al asegurado en el PDF: elige el "
                        "asegurado para registrar la póliza."
                    ),
                    "suggestion": suggestion,
                    "extraction_id": result.extraction.id,
                },
            )
        upload_client = client
        start_at = getattr(result.payload, "period_start_at", None)
        end_at = getattr(result.payload, "period_end_at", None)
        try:
            case, created = registry.resolve_account_for(
                db,
                broker_id=broker_id,
                client=client,
                subtype=subtype,
                insurance_line_id=subtype.insurance_line_id if subtype else None,
                period_start=start_at.date() if start_at else None,
                period_end=end_at.date() if end_at else None,
                entry_origin=CaseEntryOrigin.POLICY_UPLOAD,
                user=current_user,
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        case_created = created
        document.case_file_id = case.id
        result.extraction.case_file_id = case.id
        db.flush()
    elif upload_client is None and document.case_file_id is not None:
        # A folder upload with no client named: the insured RUT the reader
        # extracted picks the member it belongs to (else the folder's client).
        folder = _resolve_own_case(db, document.case_file_id, broker_id)
        read_client, _, _ = _client_for_upload(
            db, broker_id=broker_id, payload=result.payload, user=current_user, create=False
        )
        if read_client is not None and _is_member(db, folder, read_client.id):
            upload_client = read_client

    # COMMIT: the committer re-runs the fixed-core gate (with the same override)
    # and persists the full payload + verdict + provenance (+ the cuotas).
    try:
        commit_result = extraction_commit.commit_extraction(
            db,
            extraction=result.extraction,
            spec=result.spec,
            payload=result.payload,
            broker_id=broker_id,
            user=current_user,
            override=payload.override,
            policy_client_id=upload_client.id if upload_client is not None else None,
            # The broker's explicit insurer choice (the retry after a 422
            # ``insurer_unresolved``) — used only when the PDF states no rut/cmf.
            policy_insurer_id=payload.insurer_id,
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
        policy = get_policy_or_404(db, commit_result.entity_id, broker_id)
        if policy.origin is None:
            policy.origin = PolicyOrigin.READER
        policy.has_source_document = True
        created_now = bool(commit_result.extra.get("created"))
        if subtype is not None and (created_now or policy.insurance_subtype_id is None):
            # The subtipo the broker chose for this upload (2b.6), when it is of
            # the folder's ramo — a folder found by ramo may carry another one.
            if policy.insurance_line_id in (None, subtype.insurance_line_id):
                policy.insurance_subtype_id = subtype.id
                policy.insurance_line_id = subtype.insurance_line_id
        try:
            attachment = registry.attach_or_create_account(
                db,
                broker_id=broker_id,
                policy=policy,
                entry_origin=CaseEntryOrigin.POLICY_UPLOAD,
                user=current_user,
            )
            attach_warnings += attachment.warnings
        except (ValueError, machine.CaseTransitionError) as exc:
            # The policy is registered regardless: the mirror/journey never
            # blocks issuance (R42) — the folder simply keeps its stage.
            attach_warnings.append(
                f"La colocación no pudo pasar a vigente automáticamente: {exc}"
            )
        holder = getattr(result.payload, "policyholder", None)
        holder_data = None
        if holder is not None and (getattr(holder, "legal_name", None) or "").strip():
            from app.services.identifiers import InvalidRut, validate_rut

            rut = None
            if getattr(holder, "rut", None):
                try:
                    rut = validate_rut(str(holder.rut))
                except InvalidRut:
                    rut = None
            client_row = db.get(Client, policy.client_id)
            insured_row = db.get(Insured, client_row.insured_id) if client_row else None
            if rut and insured_row is not None and rut != insured_row.rut:
                holder_data = {
                    "name": holder.legal_name,
                    "rut": rut,
                    "email": getattr(holder, "contact_email", None),
                    "phone": getattr(holder, "contact_phone", None),
                }
        registry.ensure_default_parties(db, policy, policyholder=holder_data)
        # Fast reading: the beneficiaries / acreedores the core read found.
        policy_reading_commit.add_beneficiary_parties(
            db,
            policy,
            getattr(result.payload, "beneficiaries", None),
            additional_insureds=getattr(result.payload, "insured_parties", None),
        )
        if created_now and getattr(result.payload, "is_provisional_certificate", None) is True:
            policy.is_provisional = True

        if on_demand and linked_existing and upload_client is not None:
            registry.notify_policy_linked_insured(
                db,
                broker_id=broker_id,
                policy=policy,
                client=upload_client,
                case=db.get(CaseFile, policy.case_file_id) if policy.case_file_id else None,
                case_created=case_created,
                uploader=current_user,
            )

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
                "case_file_id": policy.case_file_id,
                "plan_committed": commit_result.extra.get("plan_committed"),
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
    policy_row = _read(db, policy)
    return PolicyUploadResponse(
        policy=policy_row,
        past_installments=policy_row.past_installments_pending,
        extraction_id=result.extraction.id,
        is_core_valid=bool(commit_result.extra.get("is_core_valid")),
        core_validation=policy.core_validation,
        overridden=bool(commit_result.extra.get("overridden")),
        warnings=[*result.warnings, *commit_result.warnings, *attach_warnings],
        # The SPA fires the technical read next (a separate request).
        technical_read={"status": "pending"},
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

    _resolve_insurer(db, proposal.insurer_id, broker_id)
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
        # v12: the draft issued at the end of a full journey (the mirror baseline).
        origin=PolicyOrigin.JOURNEY,
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




@router.get("/{policy_id}", response_model=PolicyDetail)
def get_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> PolicyDetail:
    """The ficha: the row + ``parties[]``, ``missing[]``, ``operability[]`` (each
    feature with its Spanish reason), ``collection_summary`` and ``journey``."""
    return _detail(db, get_policy_or_404(db, policy_id, broker_id))


#: Booleans whose default reads as "not filled yet" for Completar.
_EMPTY_WHEN_FALSE = frozenset({"is_provisional"})


def _is_empty(field: str, current: Any) -> bool:
    if current is None or current == "":
        return True
    if field == "policy_number" and registry.is_placeholder_number(current):
        return True
    if field in _EMPTY_WHEN_FALSE and current is False:
        return True
    return False


def _same(current: Any, value: Any) -> bool:
    if current is None or value is None:
        return current is value
    if isinstance(current, Enum) or isinstance(value, Enum):
        return _json_safe(current) == _json_safe(value)
    if isinstance(current, datetime) and isinstance(value, datetime):
        a = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
        b = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return a == b
    return current == value


_MONEY_FIELDS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
)
_PREMIUM_INPUTS = ("taxable_premium_uf", "exempt_premium_uf")
_PREMIUM_DERIVED = ("net_premium_uf", "vat_uf", "total_premium_uf")


def _net_from_stated_total(merged: dict[str, Any]) -> None:
    """A stated gross with an unknown net: ``net = total − vat`` (``vat`` stated
    or ``0.19 × taxable``), so ``reconcile_money`` can then derive the exempt
    part (``net − taxable``) — or 422 when the filled input cannot fit the
    stated total (exempt would go negative). Without this a Completar of
    ``taxable`` on a total-only policy left net/exempt dangling."""
    from app.schemas.proposal import VAT_RATE

    total = merged.get("total_premium_uf")
    if merged.get("net_premium_uf") is not None or total is None:
        return
    vat = merged.get("vat_uf")
    taxable = merged.get("taxable_premium_uf")
    if vat is None and taxable is not None:
        vat = Decimal(str(taxable)) * VAT_RATE
    if vat is None:
        return
    net = (Decimal(str(total)) - Decimal(str(vat))).quantize(Decimal("0.0001"))
    if net < 0:
        from app.schemas.proposal import MONEY_TOLERANCE, _mismatch

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                _mismatch(
                    "total_premium_uf", Decimal(str(vat)), Decimal(str(total)),
                    "total = net + vat (el IVA no puede superar la prima bruta)",
                    MONEY_TOLERANCE,
                )
            ],
        )
    merged["net_premium_uf"] = net


def _post_write_money(policy: Policy, proposed: dict[str, Any]) -> dict[str, Any]:
    """The five premium figures as they will stand after the PATCH — 422 when
    the arithmetic breaks.

    * Completar (an EMPTY input filled): the stored net/VAT/total are kept, so
      a fill that contradicts a stated total is a 422 — never a silent erase.
      Only the empty ones are derived.
    * Corregir (a set input changed): the dependents the caller did not state
      are re-derived when the new inputs allow it, else kept as stored (and
      checked). Either way the router diffs the result field by field.
    """
    merged = {field: proposed.get(field, getattr(policy, field)) for field in _MONEY_FIELDS}
    input_corrected = any(
        field in proposed and not _is_empty(field, getattr(policy, field))
        for field in _PREMIUM_INPUTS
    )
    if input_corrected:
        restated = [field for field in _PREMIUM_DERIVED if field not in proposed]
        trial = {**merged, **{field: None for field in restated}}
        rederived, _ = reconcile_money(trial)
        for field in restated:
            if field in rederived:
                merged[field] = rederived[field]
    _net_from_stated_total(merged)
    derived, errors = reconcile_money(merged)
    if errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=errors)
    return {**merged, **{k: v for k, v in derived.items() if k in _MONEY_FIELDS}}


@router.patch("/{policy_id}", response_model=PolicyDetail)
def update_policy(
    policy_id: int,
    payload: PolicyUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Edit")),
) -> PolicyDetail:
    """"Completar" or "Corregir dato" (team R19).

    Filling an EMPTY field needs no reason. Changing a field that already holds
    a value is a correction: it requires ``correction_reason`` (422
    ``correction_reason_required`` otherwise) and writes an activity with the
    before and the after. Sending the current value is a no-op.
    """
    policy = get_policy_or_404(db, policy_id, broker_id)
    changes = payload.model_dump(exclude_unset=True)
    reason = (changes.pop("correction_reason", None) or "").strip() or None

    if changes.get("insurer_id") is not None:
        _resolve_insurer(db, changes["insurer_id"], broker_id)
    if "source_document_id" in changes:
        _resolve_document(db, changes["source_document_id"], broker_id)
    if changes.get("case_file_id") is not None:
        _resolve_own_case(db, changes["case_file_id"], broker_id)
    if changes.get("insurance_subtype_id") is not None:
        subtype = _resolve_subtype(db, changes["insurance_subtype_id"])
        if "insurance_line_id" not in changes:
            changes["insurance_line_id"] = subtype.insurance_line_id
        elif changes["insurance_line_id"] != subtype.insurance_line_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El ramo indicado no corresponde al subtipo elegido.",
            )
    new_number = changes.get("policy_number")
    if new_number is not None:
        new_number = new_number.strip()
        changes["policy_number"] = new_number
        if _number_taken(db, broker_id, new_number, exclude_id=policy.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ya existe una póliza con el n° '{new_number}' en esta corredora.",
            )

    proposed: dict[str, Any] = {
        field: value
        for field, value in changes.items()
        if not _same(getattr(policy, field), value)
    }
    if not proposed:
        return _detail(db, policy)
    if set(proposed) & set(_MONEY_FIELDS):
        # The POST-WRITE money state, derived fields included, so every figure
        # that would change is diffed below (a changed non-null one is a
        # correction, with its before/after).
        for field, value in _post_write_money(policy, proposed).items():
            if not _same(getattr(policy, field), value):
                proposed[field] = value
            else:
                proposed.pop(field, None)
        if not proposed:
            return _detail(db, policy)

    filled: dict[str, Any] = {}
    corrected: dict[str, tuple[Any, Any]] = {}
    for field, value in proposed.items():
        current = getattr(policy, field)
        if _is_empty(field, current):
            filled[field] = value
        else:
            corrected[field] = (current, value)
    if corrected and reason is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "correction_reason_required",
                "reason": (
                    "Para cambiar un dato ya registrado usa Corregir dato e indica el "
                    "motivo (correction_reason)."
                ),
                "fields": sorted(corrected),
            },
        )

    termination_changed = "terminated_on" in proposed
    if termination_changed:
        from app.services.collection.status import plan_termination_date  # noqa: PLC0415

        plans = list(
            db.scalars(
                select(CollectionPlan).where(
                    CollectionPlan.broker_id == broker_id, CollectionPlan.policy_id == policy.id
                )
            )
        )
        previous_ends = {plan.id: plan_termination_date(plan) for plan in plans}
    for field, value in proposed.items():
        setattr(policy, field, value)
    if termination_changed:
        # A termination voids the cuotas due after it; undoing it (Art. 528
        # rehabilitation, or a corrected date) reopens them (lane B2's ledger).
        from app.services.collection import ledger  # noqa: PLC0415

        db.flush()
        today = registry.today_santiago()
        for plan in plans:
            ledger.reconcile_plan_installments(
                db, plan, previous_end=previous_ends[plan.id], today=today
            )
    # A corrected bare date must re-derive its noon instant (§2.9).
    if "start_date" in proposed and "period_start_at" not in proposed:
        policy.period_start_at = None
    if "end_date" in proposed and "period_end_at" not in proposed:
        policy.period_end_at = None
    _sync_period(policy)

    if filled:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="policy.completed",
            entity_type=EntityType.POLICY,
            entity_id=policy.id,
            description=f"Póliza completada ({', '.join(sorted(filled))})",
            meta={
                "fields": sorted(filled),
                "after": {field: _json_safe(value) for field, value in filled.items()},
            },
        )
    if corrected:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="policy.corrected",
            entity_type=EntityType.POLICY,
            entity_id=policy.id,
            description=f"Dato corregido ({', '.join(sorted(corrected))}): {reason}",
            meta={
                "fields": sorted(corrected),
                "reason": reason,
                "before": {f: _json_safe(before) for f, (before, _) in corrected.items()},
                "after": {f: _json_safe(after) for f, (_, after) in corrected.items()},
            },
        )
    db.commit()
    return _detail(db, policy)


@router.put("/{policy_id}/parties", response_model=PolicyDetail)
def replace_policy_parties(
    policy_id: int,
    payload: PartiesReplace,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Edit")),
) -> PolicyDetail:
    """Replace who is who: exactly one ``policyholder``, ≥1 ``insured``, ≤1
    ``payer`` (optional — the payer defaults to the policyholder). A RUT that is
    one of the broker's clients links ``client_id``; a foreign ``client_id`` is
    a 404."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    roles = [party.role for party in payload.parties]
    if roles.count(PartyRole.POLICYHOLDER) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La póliza debe tener exactamente un contratante.",
        )
    if roles.count(PartyRole.INSURED) < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La póliza debe tener al menos un asegurado.",
        )
    if roles.count(PartyRole.PAYER) > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La póliza admite un solo pagador.",
        )

    resolved: list[tuple[Any, int | None]] = []
    for party in payload.parties:
        client_id = party.client_id
        if client_id is not None:
            owned = db.scalar(
                select(Client.id).where(Client.id == client_id, Client.broker_id == broker_id)
            )
            if owned is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Asegurado no encontrado"
                )
        elif party.rut:
            client_id = db.scalar(
                select(Client.id)
                .join(Insured, Insured.id == Client.insured_id)
                .where(Client.broker_id == broker_id, Insured.rut == party.rut)
            )
        resolved.append((party, client_id))

    before = [
        {"role": p.role.value, "name": p.name, "rut": p.rut, "email": p.email, "phone": p.phone}
        for p in db.scalars(
            select(PolicyParty).where(
                PolicyParty.broker_id == broker_id, PolicyParty.policy_id == policy.id
            )
        ).all()
    ]
    for row in db.scalars(
        select(PolicyParty).where(
            PolicyParty.broker_id == broker_id, PolicyParty.policy_id == policy.id
        )
    ).all():
        db.delete(row)
    db.flush()
    for party, client_id in resolved:
        db.add(
            PolicyParty(
                broker_id=broker_id,
                policy_id=policy.id,
                role=party.role,
                client_id=client_id,
                name=party.name,
                rut=party.rut,
                email=party.email,
                phone=party.phone,
                beneficiary_kind=party.beneficiary_kind,
                share_pct=party.share_pct,
                is_primary=party.is_primary,
                notify_collections=party.notify_collections,
            )
        )
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="policy.parties_replaced",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=f"Partes de la póliza actualizadas ({len(resolved)})",
        meta={
            "before": before,
            "after": [
                {"role": p.role.value, "name": p.name, "rut": p.rut, "email": p.email, "phone": p.phone}
                for p, _ in resolved
            ],
        },
    )
    db.commit()
    return _detail(db, policy)


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
    rows = mirror_service.diff_payloads(
        sources.proposal_payload,
        sources.policy_payload,
        include_collections=sources.policy_collections_read,
    )
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
    rows = mirror_service.diff_payloads(
        sources.proposal_payload,
        sources.policy_payload,
        include_collections=sources.policy_collections_read,
    )
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
