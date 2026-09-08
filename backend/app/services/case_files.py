"""The journey stage machine of a ``case_file`` (docs/v2-case-files-spec.md §5).

It deliberately mirrors ``app.api.routers.placements.ALLOWED_TRANSITIONS`` so the
two feel identical to a reader and to the UI:

* forward one step along :data:`CASE_STAGE_FLOW` for the case's ``kind``;
* plus the documented SKIPS the corpus actually shows (``intake ->
  technical_basis`` when the line needs no inspection, the collection happy path
  that never goes overdue, …);
* one step BACK is always allowed — an operator must be able to correct a
  mis-click;
* ``closed`` is reachable from anywhere and is terminal.

Every accepted transition writes a ``case_file_stage_event`` **and** an
``activity`` row, and an ``account`` case also moves its ``placement.status``
through :data:`STAGE_TO_PLACEMENT_STATUS` inside the same transaction — refusing
with 422 when the placement machine forbids that move.

The seven guards of §5.2 are data, not scattered ``if``s: see :data:`STAGE_GUARDS`.
Each returns ``None`` when satisfied or a specific Spanish-free English message
the router turns into a 422.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.document import Document, DocumentCategory
from app.models.endorsement import Endorsement
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    CaseStage,
    EntityType,
    InstallmentStatus,
)
from app.models.insurance_line import InsuranceLine
from app.models.placement import Placement, PlacementStatus
from app.models.policy import Policy
from app.models.proposal import Proposal, ProposalStatus
from app.models.quote import QuoteRequest
from app.models.user import User

__all__ = [
    "CASE_STAGE_FLOW",
    "CASE_STAGE_SKIPS",
    "ALLOWED_CASE_TRANSITIONS",
    "STAGE_TO_PLACEMENT_STATUS",
    "STAGE_GUARDS",
    "CaseTransitionError",
    "TransitionOption",
    "allowed_stages",
    "transition_options",
    "guard_reason",
    "apply_transition",
    "record_stage_event",
    "initial_stage_for",
    "next_sequence_no",
    "build_reference",
    "build_post_sale_reference",
    # --- Groups & accounts (spec v3) ------------------------------------
    "ACCOUNT_KINDS",
    "PERIOD_EDITABLE_STAGES",
    "period_label_for",
    "case_placements",
    "is_period_locked",
    "account_member_client_ids",
    "find_open_folder",
]


# =============================================================================
# The canonical forward path, per kind
# =============================================================================

_ACCOUNT_CHAIN: tuple[CaseStage, ...] = (
    CaseStage.LEAD,
    CaseStage.INTAKE,
    CaseStage.PRE_UNDERWRITING,
    CaseStage.TECHNICAL_BASIS,
    CaseStage.MARKET_SUBMISSION,
    CaseStage.QUOTES_RECEIVED,
    CaseStage.COMPARISON,
    CaseStage.INSURED_DECISION,
    CaseStage.PROPOSAL_ISSUED,
    CaseStage.RATIFIED,
    CaseStage.POLICY_ISSUED,
    CaseStage.MIRROR_VALIDATION,
    CaseStage.ACTIVE,
)

#: A renewal replays the account chain, prefixed by the review of the prior period.
_RENEWAL_CHAIN: tuple[CaseStage, ...] = (CaseStage.RENEWAL_REVIEW, *_ACCOUNT_CHAIN[1:])

CASE_STAGE_FLOW: dict[CaseFileKind, tuple[CaseStage, ...]] = {
    CaseFileKind.ACCOUNT: _ACCOUNT_CHAIN,
    CaseFileKind.RENEWAL: _RENEWAL_CHAIN,
    CaseFileKind.ENDORSEMENT: (
        CaseStage.ENDORSEMENT_REQUESTED,
        CaseStage.ENDORSEMENT_PROPOSED,
        CaseStage.ENDORSEMENT_ISSUED,
        CaseStage.ENDORSEMENT_APPLIED,
    ),
    CaseFileKind.COLLECTION: (
        CaseStage.COLLECTION_SCHEDULED,
        CaseStage.COLLECTION_IN_PROGRESS,
        CaseStage.COLLECTION_OVERDUE,
        CaseStage.COLLECTION_SETTLED,
    ),
    CaseFileKind.CLAIM: (
        CaseStage.CLAIM_REPORTED,
        CaseStage.CLAIM_ADJUSTING,
        CaseStage.CLAIM_PRELIMINARY,
        CaseStage.CLAIM_FINAL,
        CaseStage.CLAIM_SETTLED,
    ),
}

#: Documented forward skips — each one is a path the demo corpus really takes.
CASE_STAGE_SKIPS: dict[CaseFileKind, dict[CaseStage, tuple[CaseStage, ...]]] = {
    # No inspection required (``insurance_line.requires_inspection is False``):
    # the antecedentes go straight into the bases técnicas. And a carrier that
    # issues the policy without a separate ratification note is routine.
    CaseFileKind.ACCOUNT: {
        CaseStage.INTAKE: (CaseStage.TECHNICAL_BASIS,),
        CaseStage.PROPOSAL_ISSUED: (CaseStage.POLICY_ISSUED,),
    },
    CaseFileKind.RENEWAL: {
        CaseStage.INTAKE: (CaseStage.TECHNICAL_BASIS,),
        CaseStage.PROPOSAL_ISSUED: (CaseStage.POLICY_ISSUED,),
    },
    # The happy path never goes overdue; suspension (Art. 528) is a side stage
    # reachable from either live stage and rehabilitated back into progress.
    CaseFileKind.COLLECTION: {
        CaseStage.COLLECTION_IN_PROGRESS: (
            CaseStage.COLLECTION_SETTLED,
            CaseStage.COLLECTION_SUSPENDED,
        ),
        CaseStage.COLLECTION_OVERDUE: (CaseStage.COLLECTION_SUSPENDED,),
        CaseStage.COLLECTION_SUSPENDED: (
            CaseStage.COLLECTION_IN_PROGRESS,
            CaseStage.COLLECTION_OVERDUE,
            CaseStage.COLLECTION_SETTLED,
        ),
    },
    # A small loss can be settled off a single adjuster visit, with no
    # pre-informe in between.
    CaseFileKind.CLAIM: {CaseStage.CLAIM_ADJUSTING: (CaseStage.CLAIM_FINAL,)},
}


def _build_transitions() -> dict[CaseFileKind, dict[CaseStage, tuple[CaseStage, ...]]]:
    """Forward one step + documented skips + one step back + ``closed``."""
    table: dict[CaseFileKind, dict[CaseStage, tuple[CaseStage, ...]]] = {}
    for kind, chain in CASE_STAGE_FLOW.items():
        skips = CASE_STAGE_SKIPS.get(kind, {})
        per_kind: dict[CaseStage, tuple[CaseStage, ...]] = {}
        # Stages that belong to this kind: the chain plus any side stage only
        # reachable through a documented skip (e.g. collection_suspended).
        stages: list[CaseStage] = list(chain)
        for source, targets in skips.items():
            for stage in (source, *targets):
                if stage not in stages:
                    stages.append(stage)

        for stage in stages:
            allowed: list[CaseStage] = []
            if stage in chain:
                index = chain.index(stage)
                if index + 1 < len(chain):
                    allowed.append(chain[index + 1])
                if index > 0:
                    allowed.append(chain[index - 1])
            for target in skips.get(stage, ()):  # documented skips / side moves
                if target not in allowed:
                    allowed.append(target)
            allowed.append(CaseStage.CLOSED)
            per_kind[stage] = tuple(dict.fromkeys(allowed))
        per_kind[CaseStage.CLOSED] = ()
        table[kind] = per_kind
    return table


#: ``{kind: {from_stage: (to_stage, ...)}}`` — the whole machine, precomputed.
ALLOWED_CASE_TRANSITIONS: dict[
    CaseFileKind, dict[CaseStage, tuple[CaseStage, ...]]
] = _build_transitions()


#: Account cases keep their placement in step. Anything absent leaves the
#: placement untouched (post-sale kinds have no placement at all).
STAGE_TO_PLACEMENT_STATUS: dict[CaseStage, PlacementStatus] = {
    CaseStage.LEAD: PlacementStatus.DRAFT,
    CaseStage.INTAKE: PlacementStatus.DRAFT,
    CaseStage.PRE_UNDERWRITING: PlacementStatus.PRE_UNDERWRITING,
    CaseStage.TECHNICAL_BASIS: PlacementStatus.PRE_UNDERWRITING,
    CaseStage.MARKET_SUBMISSION: PlacementStatus.QUOTING,
    CaseStage.QUOTES_RECEIVED: PlacementStatus.QUOTING,
    CaseStage.COMPARISON: PlacementStatus.NEGOTIATING,
    CaseStage.INSURED_DECISION: PlacementStatus.NEGOTIATING,
    CaseStage.PROPOSAL_ISSUED: PlacementStatus.AWARDED,
    CaseStage.RATIFIED: PlacementStatus.AWARDED,
    CaseStage.POLICY_ISSUED: PlacementStatus.AWARDED,
    CaseStage.MIRROR_VALIDATION: PlacementStatus.AWARDED,
    CaseStage.ACTIVE: PlacementStatus.ACTIVE,
    CaseStage.CLOSED: PlacementStatus.CLOSED,
}


class CaseTransitionError(ValueError):
    """A stage move the machine or a guard refuses. The router maps it to 422."""


@dataclass(frozen=True)
class TransitionOption:
    """One row of ``GET /case-files/{id}/transitions``."""

    to_stage: CaseStage
    allowed: bool
    reason: str | None = None


# =============================================================================
# Guards (§5.2) — each one answers "why not", or None when satisfied
# =============================================================================

def _case_documents(db: Session, case: CaseFile, category: DocumentCategory) -> int:
    return int(
        db.scalar(
            select(Document.id)
            .where(
                Document.broker_id == case.broker_id,
                Document.case_file_id == case.id,
                Document.category == category,
            )
            .limit(1)
        )
        or 0
    )


def _case_quote_requests(db: Session, case: CaseFile) -> list[QuoteRequest]:
    """Quote requests of the case: linked directly or through its placement."""
    clauses = [QuoteRequest.case_file_id == case.id]
    if case.placement_id is not None:
        clauses.append(QuoteRequest.placement_id == case.placement_id)
    from sqlalchemy import or_

    return list(
        db.scalars(
            select(QuoteRequest).where(
                QuoteRequest.broker_id == case.broker_id, or_(*clauses)
            )
        ).all()
    )


def case_proposals(db: Session, case: CaseFile) -> list[Proposal]:
    """Every proposal that belongs to this case, however it was attached."""
    from sqlalchemy import or_

    quote_ids = [q.id for q in _case_quote_requests(db, case)]
    clauses = [Proposal.case_file_id == case.id]
    if quote_ids:
        clauses.append(Proposal.quote_request_id.in_(quote_ids))
    return list(
        db.scalars(
            select(Proposal).where(
                Proposal.broker_id == case.broker_id, or_(*clauses)
            )
        ).all()
    )


def case_policies(db: Session, case: CaseFile) -> list[Policy]:
    """Policies reachable from the case (its own, or its placement's)."""
    from sqlalchemy import or_

    clauses = [Policy.case_file_id == case.id]
    if case.policy_id is not None:
        clauses.append(Policy.id == case.policy_id)
    if case.placement_id is not None:
        clauses.append(Policy.placement_id == case.placement_id)
    return list(
        db.scalars(
            select(Policy).where(Policy.broker_id == case.broker_id, or_(*clauses))
        ).all()
    )


def _guard_market_submission(db: Session, case: CaseFile) -> str | None:
    if not _case_documents(db, case, DocumentCategory.TECHNICAL_BRIEF):
        return (
            "The case needs a technical_brief document (01 bases técnicas) "
            "before it can be sent to the market"
        )
    recipients = 0
    for quote in _case_quote_requests(db, case):
        recipients += len(quote.recipient_insurer_ids or [])
    if recipients == 0:
        from app.services.packs import resolve_recipients

        recipients = len(
            resolve_recipients(
                db,
                broker_id=case.broker_id,
                insurance_line_id=case.insurance_line_id,
                with_contact_only=True,
            )
        )
    if recipients == 0:
        return "No recipient insurer resolves for this broker and insurance line"
    return None


def _guard_comparison(db: Session, case: CaseFile) -> str | None:
    confirmed = [p for p in case_proposals(db, case) if p.is_confirmed]
    if not confirmed:
        return "At least one confirmed proposal is required to build the comparison"
    return None


def _guard_proposal_issued(db: Session, case: CaseFile) -> str | None:
    """At least one accepted proposal, and at most one PER PLACEMENT.

    A folder with two ramos-worth of placements awards one carrier each, so
    "exactly one" was wrong once an account could wrap N placements (spec v3
    §2.5). Two winners on the SAME placement is still a contradiction.
    """
    accepted = [
        p for p in case_proposals(db, case) if p.status == ProposalStatus.ACCEPTED
    ]
    if not accepted:
        return (
            "At least one accepted proposal is required to issue the propuesta de "
            "emisión (found 0)"
        )
    quotes = {q.id: q for q in _case_quote_requests(db, case)}
    per_placement: dict[int | None, int] = {}
    for proposal in accepted:
        quote = quotes.get(proposal.quote_request_id) or db.get(
            QuoteRequest, proposal.quote_request_id
        )
        key = quote.placement_id if quote is not None else None
        per_placement[key] = per_placement.get(key, 0) + 1
    for placement_id, count in sorted(
        per_placement.items(), key=lambda item: (item[0] is None, item[0])
    ):
        if count > 1:
            where = (
                f"placement {placement_id}"
                if placement_id is not None
                else "the case (no placement)"
            )
            return (
                "At most one accepted proposal per placement is allowed to issue "
                f"the propuesta de emisión ({where} has {count})"
            )
    return None


def _guard_policy_issued(db: Session, case: CaseFile) -> str | None:
    for policy in case_policies(db, case):
        if policy.source_document_id is not None:
            return None
    return "No policy with a source document (08) is attached to this case"


def _guard_endorsement_issued(db: Session, case: CaseFile) -> str | None:
    rows = db.scalars(
        select(Endorsement).where(
            Endorsement.broker_id == case.broker_id,
            Endorsement.case_file_id == case.id,
        )
    ).all()
    if not rows:
        return "No endorsement is attached to this case"
    if not any(row.issued_document_id is not None for row in rows):
        return "The endorsement has no issued document (08A/08B/09B/09D) attached"
    return None


_SETTLED_INSTALLMENTS = (
    InstallmentStatus.PAID,
    InstallmentStatus.PAID_LATE,
    InstallmentStatus.CREDITED,
    InstallmentStatus.CANCELLED,
)


def _guard_collection_settled(db: Session, case: CaseFile) -> str | None:
    from sqlalchemy import or_

    clauses = [CollectionPlan.case_file_id == case.id]
    if case.policy_id is not None:
        clauses.append(CollectionPlan.policy_id == case.policy_id)
    plan_ids = list(
        db.scalars(
            select(CollectionPlan.id).where(
                CollectionPlan.broker_id == case.broker_id, or_(*clauses)
            )
        ).all()
    )
    if not plan_ids:
        return "No collection plan is attached to this case"
    pending = db.scalar(
        select(CollectionInstallment.id)
        .where(
            CollectionInstallment.collection_plan_id.in_(plan_ids),
            CollectionInstallment.status.notin_(_SETTLED_INSTALLMENTS),
        )
        .limit(1)
    )
    if pending is not None:
        return "Every instalment must be paid, credited or cancelled before settling"
    return None


def _guard_claim_final(db: Session, case: CaseFile) -> str | None:
    if not _case_documents(db, case, DocumentCategory.CLAIM_FINAL_REPORT):
        return "The claim needs a claim_final_report document (informe final)"
    return None


#: The seven guards of §5.2, keyed by the stage they protect.
STAGE_GUARDS: dict[CaseStage, Callable[[Session, CaseFile], str | None]] = {
    CaseStage.MARKET_SUBMISSION: _guard_market_submission,
    CaseStage.COMPARISON: _guard_comparison,
    CaseStage.PROPOSAL_ISSUED: _guard_proposal_issued,
    CaseStage.POLICY_ISSUED: _guard_policy_issued,
    CaseStage.ENDORSEMENT_ISSUED: _guard_endorsement_issued,
    CaseStage.COLLECTION_SETTLED: _guard_collection_settled,
    CaseStage.CLAIM_FINAL: _guard_claim_final,
}


def guard_reason(db: Session, case: CaseFile, to_stage: CaseStage) -> str | None:
    """The reason ``to_stage`` is blocked right now, or ``None`` when it is free."""
    guard = STAGE_GUARDS.get(to_stage)
    if guard is None:
        return None
    return guard(db, case)


# =============================================================================
# Public helpers
# =============================================================================

def allowed_stages(case: CaseFile) -> tuple[CaseStage, ...]:
    """The structurally legal next stages, before the guards have their say."""
    return ALLOWED_CASE_TRANSITIONS.get(case.kind, {}).get(case.stage, ())


def transition_options(db: Session, case: CaseFile) -> list[TransitionOption]:
    """Every stage of the case's chain with ``allowed`` + ``reason``.

    The UI enables its journey buttons straight off this list, so a control is
    never rendered live for a move the server would refuse.
    """
    chain = list(CASE_STAGE_FLOW.get(case.kind, ()))
    for stage in ALLOWED_CASE_TRANSITIONS.get(case.kind, {}):
        if stage not in chain:
            chain.append(stage)
    if CaseStage.CLOSED not in chain:
        chain.append(CaseStage.CLOSED)

    structurally = allowed_stages(case)
    options: list[TransitionOption] = []
    for stage in chain:
        if stage == case.stage:
            options.append(
                TransitionOption(to_stage=stage, allowed=False, reason="current stage")
            )
            continue
        if stage not in structurally:
            options.append(
                TransitionOption(
                    to_stage=stage,
                    allowed=False,
                    reason=f"not reachable from '{case.stage.value}'",
                )
            )
            continue
        reason = guard_reason(db, case, stage)
        options.append(
            TransitionOption(to_stage=stage, allowed=reason is None, reason=reason)
        )
    return options


def record_stage_event(
    db: Session,
    *,
    case: CaseFile,
    from_stage: CaseStage | None,
    to_stage: CaseStage,
    user: User | None,
    note: str | None = None,
    meta: dict | None = None,
) -> CaseFileStageEvent:
    """Append one timeline row + one audit row. The caller commits."""
    event = CaseFileStageEvent(
        broker_id=case.broker_id,
        case_file_id=case.id,
        from_stage=from_stage,
        to_stage=to_stage,
        user_id=user.id if user else None,
        note=note,
        meta=meta,
    )
    db.add(event)
    db.add(
        Activity(
            broker_id=case.broker_id,
            user_id=user.id if user else None,
            action="case_file.transitioned",
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            description=(
                f"Etapa {from_stage.value if from_stage else '—'} → {to_stage.value}"
            ),
            meta={
                "from": from_stage.value if from_stage else None,
                "to": to_stage.value,
                "note": note,
            },
        )
    )
    return event


def _sync_placement(db: Session, case: CaseFile, to_stage: CaseStage) -> str | None:
    """Move EVERY placement of the account case in the same transaction.

    An account folder is one insurance line x one vigencia x N RUTs (spec v3
    §2.5), so it can wrap more than one placement: ``case_file.placement_id``
    is the primary one and ``placement.case_file_id`` is the authority for the
    rest. The single-placement path is byte-identical to what it was.

    Returns an error message when the placement machine forbids the move for
    ANY of them; the caller turns that into a 422 and rolls back, so the fan-out
    is all-or-nothing.
    """
    if case.kind not in ACCOUNT_KINDS:
        return None
    target = STAGE_TO_PLACEMENT_STATUS.get(to_stage)
    if target is None:
        return None

    # Imported lazily: placements.py imports clients.py, which imports schemas.
    from app.api.routers.placements import ALLOWED_TRANSITIONS

    for placement in case_placements(db, case):
        if placement.status == target:
            continue
        if target not in ALLOWED_TRANSITIONS.get(placement.status, ()):
            return (
                f"The placement cannot move from '{placement.status.value}' to "
                f"'{target.value}', which stage '{to_stage.value}' requires"
            )
        placement.status = target
        db.add(
            Activity(
                broker_id=case.broker_id,
                user_id=None,
                action="placement.transitioned",
                entity_type=EntityType.PLACEMENT,
                entity_id=placement.id,
                description=(
                    f"Estado {placement.status.value} (por expediente {case.id})"
                ),
                meta={"to": target.value, "case_file_id": case.id},
            )
        )
    return None


def apply_transition(
    db: Session,
    *,
    case: CaseFile,
    to_stage: CaseStage,
    user: User | None,
    note: str | None = None,
    force: bool = False,
) -> CaseFileStageEvent:
    """Validate and apply a stage move. Raises :class:`CaseTransitionError`.

    ``force`` skips the §5.2 guards but never the structural machine — it exists
    for the importer, which back-dates a real history that already happened.
    The caller commits.
    """
    previous = case.stage
    if to_stage == previous:
        raise CaseTransitionError(f"The case is already in stage '{to_stage.value}'")

    allowed = allowed_stages(case)
    if to_stage not in allowed:
        raise CaseTransitionError(
            f"Cannot move a '{case.kind.value}' case from '{previous.value}' to "
            f"'{to_stage.value}'. Allowed: "
            f"{', '.join(s.value for s in allowed) or 'none (terminal)'}"
        )

    if not force:
        reason = guard_reason(db, case, to_stage)
        if reason:
            raise CaseTransitionError(reason)

    placement_error = _sync_placement(db, case, to_stage)
    if placement_error:
        raise CaseTransitionError(placement_error)

    case.stage = to_stage
    if to_stage is CaseStage.CLOSED and case.closed_at is None:
        from app.models.base_class import utcnow

        case.closed_at = utcnow()
    return record_stage_event(
        db, case=case, from_stage=previous, to_stage=to_stage, user=user, note=note
    )


def initial_stage_for(kind: CaseFileKind) -> CaseStage:
    """The first stage of a kind's chain — what a freshly created case sits in."""
    chain = CASE_STAGE_FLOW.get(kind)
    return chain[0] if chain else CaseStage.INTAKE


def next_sequence_no(db: Session, *, broker_id: int, policy_id: int | None,
                     kind: CaseFileKind) -> int:
    """The next ordinal within ``(policy_id, kind)`` — E1, E2, ...

    Enforced in the service, not by a unique constraint: a NULL ``policy_id``
    behaves differently per engine (§2.2).
    """
    if policy_id is None:
        return 1
    from sqlalchemy import func

    highest = db.scalar(
        select(func.max(CaseFile.sequence_no)).where(
            CaseFile.broker_id == broker_id,
            CaseFile.policy_id == policy_id,
            CaseFile.kind == kind,
        )
    )
    return int(highest or 0) + 1


def build_reference(
    db: Session, *, broker_id: int, prefix: str = "EXP", year: int | None = None
) -> str:
    """Allocate the next human code for the broker, e.g. ``EXP-2026-0007``.

    ``year`` is explicit so a renewal / re-period folder is stamped with the
    year of the vigencia it opens (``period_start.year``), not with the year the
    button happened to be pressed (spec v3 §3.3).
    """
    from datetime import datetime, timezone

    from sqlalchemy import func

    if year is None:
        year = datetime.now(timezone.utc).year
    stem = f"{prefix}-{year}-"
    used = db.scalar(
        select(func.count(CaseFile.id)).where(
            CaseFile.broker_id == broker_id, CaseFile.reference.like(f"{stem}%")
        )
    )
    n = int(used or 0) + 1
    while True:
        candidate = f"{stem}{n:04d}"
        taken = db.scalar(
            select(CaseFile.id).where(
                CaseFile.broker_id == broker_id, CaseFile.reference == candidate
            )
        )
        if taken is None:
            return candidate
        n += 1


#: Reference suffix per post-sale kind — the corpus convention the importer
#: writes (``EXP-2025-0001-E1``, ``-CB1``, ``-SN1``, ``-RN1``), so a case opened
#: through the API is indistinguishable from an imported one.
POST_SALE_REFERENCE_SUFFIX: dict[CaseFileKind, str] = {
    CaseFileKind.ENDORSEMENT: "E",
    CaseFileKind.COLLECTION: "CB",
    CaseFileKind.CLAIM: "SN",
    CaseFileKind.RENEWAL: "RN",
}


def build_post_sale_reference(
    db: Session,
    *,
    broker_id: int,
    kind: CaseFileKind,
    sequence_no: int,
    parent: CaseFile | None,
) -> str:
    """``{account reference}-{suffix}{n}`` for a post-sale case.

    Falls back to the plain broker sequence when there is no account case to
    hang off, and walks the ordinal forward if that exact code is somehow taken.
    """
    suffix = POST_SALE_REFERENCE_SUFFIX.get(kind)
    if parent is None or suffix is None:
        return build_reference(db, broker_id=broker_id)
    n = max(int(sequence_no or 1), 1)
    while True:
        candidate = f"{parent.reference}-{suffix}{n}"
        taken = db.scalar(
            select(CaseFile.id).where(
                CaseFile.broker_id == broker_id, CaseFile.reference == candidate
            )
        )
        if taken is None:
            return candidate
        n += 1


def requires_inspection(db: Session, case: CaseFile) -> bool:
    """Whether the case's line demands an inspection before the bases técnicas.

    Used by the UI to explain the ``intake -> technical_basis`` skip; the machine
    itself allows the skip either way, because the operator, not the config, owns
    the decision on a real file.
    """
    if case.insurance_line_id is None:
        return False
    line = db.get(InsuranceLine, case.insurance_line_id)
    return bool(line and line.requires_inspection)


# =============================================================================
# Groups & accounts (spec v3 §2.5, §4.3)
# =============================================================================

#: The kinds that ARE an account: one insurance line x one vigencia x N RUTs.
#: A renewal folder is a sibling of the prior vigencia, so it wraps placements
#: exactly like an ``account`` folder does.
ACCOUNT_KINDS: tuple[CaseFileKind, ...] = (CaseFileKind.ACCOUNT, CaseFileKind.RENEWAL)

#: While the folder has never moved past these stages the vigencia is still a
#: draft and may be edited in place (rule 1). One stage event to anything else
#: freezes it: the only door afterwards is ``POST /case-files/{id}/reperiod``.
PERIOD_EDITABLE_STAGES: frozenset[CaseStage] = frozenset(
    {CaseStage.LEAD, CaseStage.INTAKE, CaseStage.RENEWAL_REVIEW}
)


def period_label_for(start, end) -> str | None:
    """``2026-2027`` — the GROUPING label over per-account full dates.

    Vigencia is a label, not a shared range (spec §1): Coccolino runs Vehículos
    ago-ago and Incendio abr-abr, both under ``2026-2027``.
    """
    if start is None or end is None:
        return None
    return f"{start.year}-{end.year}"


def case_placements(db: Session, case: CaseFile) -> list[Placement]:
    """Every placement of the folder: the primary one plus the attached ones.

    ``placement.case_file_id`` is the authority (spec §2.5); ``case.placement_id``
    is kept as the primary back-pointer and is included even when the placement
    row's own FK was never written.
    """
    from sqlalchemy import or_

    clauses = [Placement.case_file_id == case.id]
    if case.placement_id is not None:
        clauses.append(Placement.id == case.placement_id)
    rows = list(
        db.scalars(
            select(Placement)
            .where(Placement.broker_id == case.broker_id, or_(*clauses))
            .order_by(Placement.id.asc())
        ).all()
    )
    # Primary first, so a single-placement folder behaves exactly as before.
    rows.sort(key=lambda p: (p.id != case.placement_id, p.id))
    return rows


def is_period_locked(db: Session, case: CaseFile) -> bool:
    """True once the folder has moved beyond the draft stages (rule 1).

    Read off the stage EVENTS, not off ``case.stage``: a folder walked forward
    and then corrected back to ``intake`` has still been out in the world, and
    its vigencia must not silently change under the documents that quote it.
    """
    if case.kind not in ACCOUNT_KINDS:
        return True
    moved = db.scalar(
        select(CaseFileStageEvent.id)
        .where(
            CaseFileStageEvent.case_file_id == case.id,
            CaseFileStageEvent.to_stage.notin_(tuple(PERIOD_EDITABLE_STAGES)),
        )
        .limit(1)
    )
    return moved is not None


def account_member_client_ids(db: Session, case: CaseFile) -> list[int]:
    """The RUTs of the account: ``account_client`` rows UNION its placements'.

    The contratante (``case_file.client_id``) always comes first (rule 6).
    """
    from app.models.account_client import AccountClient

    ordered: list[int] = []
    if case.client_id is not None:
        ordered.append(case.client_id)
    for client_id in db.scalars(
        select(AccountClient.client_id)
        .where(AccountClient.case_file_id == case.id)
        .order_by(AccountClient.is_primary.desc(), AccountClient.id.asc())
    ).all():
        if client_id not in ordered:
            ordered.append(int(client_id))
    for placement in case_placements(db, case):
        if placement.client_id not in ordered:
            ordered.append(placement.client_id)
    return ordered


def find_open_folder(
    db: Session,
    *,
    broker_id: int,
    account_group_id: int | None,
    insurance_line_id: int | None,
    period_start,
    period_end,
    exclude_id: int | None = None,
) -> CaseFile | None:
    """The open folder that already covers this (group, line, vigencia).

    Rule 5 — no hybrid states: one open folder per
    ``(broker, group, line, period_start, period_end)``. Returns it so the
    caller can answer 422 ``{"code": "folder_exists", "case_file_id": N}``
    instead of quietly opening a twin.

    Enforced in the service rather than as a UNIQUE index: three of the five
    columns are nullable and NULL comparison differs per engine.
    """
    if account_group_id is None or period_start is None or period_end is None:
        return None
    stmt = select(CaseFile).where(
        CaseFile.broker_id == broker_id,
        CaseFile.account_group_id == account_group_id,
        CaseFile.kind.in_(ACCOUNT_KINDS),
        CaseFile.status == CaseFileStatus.OPEN,
        CaseFile.period_start == period_start,
        CaseFile.period_end == period_end,
    )
    if insurance_line_id is None:
        stmt = stmt.where(CaseFile.insurance_line_id.is_(None))
    else:
        stmt = stmt.where(CaseFile.insurance_line_id == insurance_line_id)
    if exclude_id is not None:
        stmt = stmt.where(CaseFile.id != exclude_id)
    return db.scalars(stmt.order_by(CaseFile.id.asc()).limit(1)).first()
