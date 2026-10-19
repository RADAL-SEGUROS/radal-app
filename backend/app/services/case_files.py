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
Each returns ``None`` when satisfied or a specific user-facing Spanish message
the router turns into a 422 (the reason is surfaced raw in the UI).
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
from app.services.stage_labels import stage_label, stage_list, stages_omitted

#: Placement statuses in Spanish (prose only — the enum stays the identifier).
PLACEMENT_STATUS_LABELS_ES: dict[PlacementStatus, str] = {
    PlacementStatus.DRAFT: "Borrador",
    PlacementStatus.INSPECTION: "Inspección",
    PlacementStatus.PRE_UNDERWRITING: "Presuscripción",
    PlacementStatus.QUOTING: "Cotizando",
    PlacementStatus.NEGOTIATING: "Negociando",
    PlacementStatus.AWARDED: "Adjudicada",
    PlacementStatus.ACTIVE: "Vigente",
    PlacementStatus.CLOSED: "Cerrada",
}


def placement_status_label(status: PlacementStatus | str | None) -> str:
    if status is None:
        return "—"
    try:
        return PLACEMENT_STATUS_LABELS_ES.get(PlacementStatus(str(getattr(status, "value", status))), "—")
    except ValueError:
        return "—"


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
    # --- Partial journeys (v12) -----------------------------------------
    "SKIP_TO_TARGETS",
    "BACKWARD_LOCKED_STAGES",
    "REASON_BACKWARD_LOCKED",
    "CaseStageConflict",
    "skip_forward",
    "skip_to",
    "activate_stage",
    "stage_states",
    "advance_to_active",
    "case_has_policy",
    "comparison_read_count",
    "advance_for_comparison",
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


#: Stages a folder never walks BACK from (review v12 #9: no moving back once the
#: account is *Vigente*). ``transition_options`` lists the stages behind as
#: ``direction="backward"``, never allowed, with :data:`REASON_BACKWARD_LOCKED`.
BACKWARD_LOCKED_STAGES: frozenset[CaseStage] = frozenset({CaseStage.ACTIVE})

REASON_BACKWARD_LOCKED = (
    "no se puede volver atrás desde «Vigente»: los cambios se hacen por endoso o corrección"
)


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
                # One step back corrects a mis-click — except from ``active``:
                # a Vigente account never walks back (review v12 #9; changes
                # after issuance go through an endorsement or a correction).
                if index > 0 and stage not in BACKWARD_LOCKED_STAGES:
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
    #: ``forward`` | ``backward`` | ``current`` | ``close`` | ``side`` — lets the
    #: UI drop the moves it must not offer (e.g. backward from *Vigente*).
    direction: str = "forward"


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
            "Falta el documento de bases técnicas (01) para poder enviar la "
            "carpeta al mercado."
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
        return (
            "No se resuelve ninguna aseguradora destinataria para este corredor "
            "y ramo."
        )
    return None


def comparison_read_count(db: Session, case: CaseFile) -> int:
    """Cotizaciones READ into the folder's live comparison (review round 2, A6).

    A column whose read succeeded and was not flagged as a wrong file counts as a
    received cotización even before it is promoted to a ``proposal`` — that is the
    v12 comparison flow (upload → read → align → promote only the winner).
    """
    from sqlalchemy import func

    from app.models.ai import Extraction, ExtractionStatus
    from app.models.comparison import (
        Comparison,
        ComparisonEntry,
        ComparisonSource,
        ComparisonStatus,
    )

    return int(
        db.scalar(
            select(func.count(ComparisonEntry.id))
            .join(Comparison, Comparison.id == ComparisonEntry.comparison_id)
            .join(ComparisonSource, ComparisonSource.id == ComparisonEntry.comparison_source_id)
            .join(Extraction, Extraction.id == ComparisonSource.source_extraction_id)
            .where(
                Comparison.broker_id == case.broker_id,
                Comparison.case_file_id == case.id,
                Comparison.status != ComparisonStatus.SUPERSEDED,
                ComparisonSource.is_wrong_file.is_(False),
                Extraction.status == ExtractionStatus.SUCCEEDED,
            )
        )
        or 0
    )


def _guard_comparison(db: Session, case: CaseFile) -> str | None:
    confirmed = [p for p in case_proposals(db, case) if p.is_confirmed]
    if not confirmed and comparison_read_count(db, case) == 0:
        return (
            "Se necesita al menos una cotización confirmada para armar la "
            "comparación."
        )
    return None


#: Why the comparator moved the folder (bitácora + the *omitida* rows).
_COMPARATOR_NOTES: dict[CaseStage, tuple[str, str]] = {
    CaseStage.QUOTES_RECEIVED: (
        "Cotización recibida en el comparador",
        "La cuenta pasó a «Cotizaciones recibidas»: llegó una cotización al comparador",
    ),
    CaseStage.COMPARISON: (
        "Cotizaciones alineadas en el comparador",
        "La cuenta pasó a «Comparación»: las cotizaciones se alinearon",
    ),
}


def advance_for_comparison(
    db: Session, *, case: CaseFile | None, target: CaseStage, user: User | None
) -> str | None:
    """Move an OPEN account folder FORWARD to ``target`` when the comparator gets there
    (A6: a cotización read → *Cotizaciones recibidas*; an alignment → *Comparación*).

    Never moves back, never touches a closed folder, a post-sale kind or one already at
    or past ``target``. Stages it never entered are written *omitida* (``skip_forward``,
    the partial-journey rule); placements walk the placement machine. One bitácora row.
    Returns a Spanish warning when the move is refused (a placement that cannot follow),
    ``None`` otherwise; it never raises — the comparator's own work is already done.
    Flush-only: the caller commits.
    """
    if case is None or case.kind not in ACCOUNT_KINDS or case.status != CaseFileStatus.OPEN:
        return None
    chain = CASE_STAGE_FLOW.get(case.kind, ())
    if target not in chain or case.stage not in chain:
        return None
    if chain.index(case.stage) >= chain.index(target):
        return None
    note, description = _COMPARATOR_NOTES.get(target, (None, None))
    origin = case.stage
    skipped: list[str] = []
    try:
        skip_forward(
            db,
            case=case,
            target=target,
            user=user,
            note=note,
            reason=f"Etapa omitida: la cuenta entró en «{stage_label(target)}» desde el comparador.",
            write_activity=False,
            skipped_out=skipped,
        )
    except CaseTransitionError as exc:
        return str(exc)
    db.add(
        Activity(
            broker_id=case.broker_id,
            user_id=user.id if user else None,
            action="case_file.skipped_to",
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            description=(description or skip_description(origin, target, len(skipped)))
            + (f"; {stages_omitted(len(skipped))}" if skipped else ""),
            meta={
                "from": origin.value,
                "to": target.value,
                "skipped": skipped,
                "via": "comparison",
            },
        )
    )
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
            "Se necesita al menos una cotización aceptada para emitir la propuesta "
            "de emisión (se encontraron 0)."
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
                f"el placement {placement_id}"
                if placement_id is not None
                else "la carpeta (sin placement)"
            )
            return (
                "Se permite como máximo una cotización aceptada por placement para "
                f"emitir la propuesta de emisión ({where} tiene {count})."
            )
    return None


def _guard_policy_issued(db: Session, case: CaseFile) -> str | None:
    for policy in case_policies(db, case):
        if policy.source_document_id is not None:
            return None
    return "No hay ninguna póliza con documento de origen (08) adjunta a esta carpeta."


def _guard_endorsement_issued(db: Session, case: CaseFile) -> str | None:
    rows = db.scalars(
        select(Endorsement).where(
            Endorsement.broker_id == case.broker_id,
            Endorsement.case_file_id == case.id,
        )
    ).all()
    if not rows:
        return "No hay ningún endoso adjunto a esta carpeta."
    if not any(row.issued_document_id is not None for row in rows):
        return "El endoso no tiene un documento emitido (08A/08B/09B/09D) adjunto."
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
        return "No hay ningún plan de cobranza adjunto a esta carpeta."
    pending = db.scalar(
        select(CollectionInstallment.id)
        .where(
            CollectionInstallment.collection_plan_id.in_(plan_ids),
            CollectionInstallment.status.notin_(_SETTLED_INSTALLMENTS),
        )
        .limit(1)
    )
    if pending is not None:
        return (
            "Todas las cuotas deben estar pagadas, abonadas o anuladas antes de "
            "liquidar la cobranza."
        )
    return None


def _guard_claim_final(db: Session, case: CaseFile) -> str | None:
    if not _case_documents(db, case, DocumentCategory.CLAIM_FINAL_REPORT):
        return "El siniestro necesita un documento de informe final."
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
    flow = list(CASE_STAGE_FLOW.get(case.kind, ()))
    current_index = flow.index(case.stage) if case.stage in flow else None

    def _direction(stage: CaseStage) -> str:
        if stage == case.stage:
            return "current"
        if stage == CaseStage.CLOSED:
            return "close"
        if current_index is None or stage not in flow:
            return "side"
        return "backward" if flow.index(stage) < current_index else "forward"

    options: list[TransitionOption] = []
    for stage in chain:
        direction = _direction(stage)
        if stage == case.stage:
            options.append(
                TransitionOption(
                    to_stage=stage, allowed=False, reason="etapa actual", direction=direction
                )
            )
            continue
        if direction == "backward" and case.stage in BACKWARD_LOCKED_STAGES:
            options.append(
                TransitionOption(
                    to_stage=stage,
                    allowed=False,
                    reason=REASON_BACKWARD_LOCKED,
                    direction=direction,
                )
            )
            continue
        if stage not in structurally:
            options.append(
                TransitionOption(
                    to_stage=stage,
                    allowed=False,
                    # Spanish label, never the enum code (QA v12 leaked 'active').
                    reason=f"no se puede pasar desde «{_stage_label_es(case.stage)}»",
                    direction=direction,
                )
            )
            continue
        reason = guard_reason(db, case, stage)
        options.append(
            TransitionOption(
                to_stage=stage, allowed=reason is None, reason=reason, direction=direction
            )
        )
    return options


def _stage_label_es(stage: CaseStage | None) -> str:
    return stage_label(stage)


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
                f"Etapa {stage_label(from_stage)} → {stage_label(to_stage)}"
                if from_stage is not None
                else f"Etapa inicial: {stage_label(to_stage)}"
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
                f"La colocación no puede pasar de «{placement_status_label(placement.status)}» "
                f"a «{placement_status_label(target)}», que exige la etapa "
                f"«{stage_label(to_stage)}»."
            )
        previous_status = placement.status
        placement.status = target
        db.add(
            Activity(
                broker_id=case.broker_id,
                user_id=None,
                action="placement.transitioned",
                entity_type=EntityType.PLACEMENT,
                entity_id=placement.id,
                description=(
                    f"Colocación: {placement_status_label(previous_status)} → "
                    f"{placement_status_label(target)}"
                ),
                meta={
                    "from": previous_status.value,
                    "to": target.value,
                    "case_file_id": case.id,
                },
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
        raise CaseTransitionError(
            f"La carpeta ya se encuentra en la etapa «{stage_label(to_stage)}»."
        )

    flow = CASE_STAGE_FLOW.get(case.kind, ())
    if (
        previous in BACKWARD_LOCKED_STAGES
        and previous in flow
        and to_stage in flow
        and flow.index(to_stage) < flow.index(previous)
    ):
        raise CaseStageConflict(
            "La cuenta está Vigente: no se puede volver a etapas anteriores. "
            "Los cambios se hacen por endoso o corrección."
        )

    allowed = allowed_stages(case)
    if to_stage not in allowed:
        raise CaseTransitionError(
            f"No se puede mover la carpeta de «{stage_label(previous)}» a "
            f"«{stage_label(to_stage)}». Permitidas: "
            f"{stage_list(allowed) or 'ninguna (etapa terminal)'}."
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


# =============================================================================
# Partial journeys (v12, docs/v12/02-backend-api.md §3, team R6/R34)
# =============================================================================
#
# A colocación may ENTER the account chain at a later stage (portal quotes, an
# uploaded policy, a typed-in one). Every stage it jumps over is written as a
# ``case_file_stage_event`` with ``is_skipped=True`` ("omitida", user = system),
# so the rail draws entered stages solid and skipped ones as thin ticks — the
# timeline stays rows, never JSON, and nothing is ever deleted. A stage that was
# really entered before (a walk back and forth) is kept as completed and gets
# no skipped row (R6: "conserva las completadas").

#: Where ``POST /case-files/{id}/skip-to`` may land an account folder.
SKIP_TO_TARGETS: tuple[CaseStage, ...] = (
    CaseStage.QUOTES_RECEIVED,
    CaseStage.INSURED_DECISION,
    CaseStage.POLICY_ISSUED,
    CaseStage.ACTIVE,
)

#: Targets that only make sense once the folder holds a policy.
_POLICY_TARGETS: frozenset[CaseStage] = frozenset({CaseStage.POLICY_ISSUED, CaseStage.ACTIVE})


class CaseStageConflict(CaseTransitionError):
    """A partial-journey move that conflicts with the folder's state -> 409."""


def case_has_policy(db: Session, case: CaseFile) -> bool:
    """True when any policy hangs off the folder (its own, its placement's)."""
    return case.policy_id is not None or bool(case_policies(db, case))


def _entered_stages(db: Session, case: CaseFile) -> set[CaseStage]:
    """Stages the folder REALLY entered at some point (non-skipped events)."""
    return set(
        db.scalars(
            select(CaseFileStageEvent.to_stage).where(
                CaseFileStageEvent.case_file_id == case.id,
                CaseFileStageEvent.is_skipped.is_(False),
            )
        ).all()
    )


def _placement_path(
    current: PlacementStatus, target: PlacementStatus
) -> list[PlacementStatus] | None:
    """Shortest legal walk through the placement machine (BFS), excluding the
    start. ``[]`` when already there, ``None`` when unreachable. ``closed`` is
    never used as a stepping stone (it is terminal)."""
    if current == target:
        return []
    from collections import deque

    from app.api.routers.placements import ALLOWED_TRANSITIONS

    queue: deque[tuple[PlacementStatus, list[PlacementStatus]]] = deque([(current, [])])
    seen = {current}
    while queue:
        status, path = queue.popleft()
        for nxt in ALLOWED_TRANSITIONS.get(status, ()):
            if nxt in seen:
                continue
            if nxt == target:
                return [*path, nxt]
            if nxt == PlacementStatus.CLOSED:
                continue
            seen.add(nxt)
            queue.append((nxt, [*path, nxt]))
    return None


def _walk_placements(db: Session, case: CaseFile, to_stage: CaseStage) -> str | None:
    """Move every placement of the folder to the status ``to_stage`` demands,
    stepping through the placement machine (a jump never bypasses it). Returns
    a Spanish reason when ANY placement cannot get there; the caller rolls back
    so the fan-out stays all-or-nothing."""
    if case.kind not in ACCOUNT_KINDS:
        return None
    target = STAGE_TO_PLACEMENT_STATUS.get(to_stage)
    if target is None:
        return None
    placements = case_placements(db, case)
    plans: list[tuple[Placement, list[PlacementStatus]]] = []
    for placement in placements:
        path = _placement_path(placement.status, target)
        if path is None:
            return (
                f"La colocación no puede pasar de «{placement_status_label(placement.status)}» "
                f"a «{placement_status_label(target)}», que exige la etapa "
                f"«{stage_label(to_stage)}»."
            )
        plans.append((placement, path))
    for placement, path in plans:
        if not path:
            continue
        previous = placement.status
        placement.status = path[-1]
        db.add(
            Activity(
                broker_id=case.broker_id,
                user_id=None,
                action="placement.transitioned",
                entity_type=EntityType.PLACEMENT,
                entity_id=placement.id,
                description=(
                    f"Colocación: {placement_status_label(previous)} → "
                    f"{placement_status_label(placement.status)}"
                ),
                meta={
                    "from": previous.value,
                    "to": placement.status.value,
                    "path": [step.value for step in path],
                    "case_file_id": case.id,
                },
            )
        )
    return None


def _event_clock(now):
    """A strictly increasing clock for one burst of events, so the timeline's
    ``occurred_at`` order equals the write order even inside one request."""
    from datetime import datetime, timedelta, timezone

    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    step = 0

    def tick():
        nonlocal step
        value = base + timedelta(microseconds=step)
        step += 1
        return value

    return tick


def skip_forward(
    db: Session,
    *,
    case: CaseFile,
    target: CaseStage,
    user: User | None,
    note: str | None = None,
    include_current: bool = False,
    now=None,
    reason: str | None = None,
    write_activity: bool = True,
    skipped_out: list[str] | None = None,
) -> list[CaseFileStageEvent]:
    """Jump ``case`` forward to ``target``, writing one ``is_skipped`` event for
    every intermediate stage it never entered, then the (non-skipped) arrival.

    ``include_current`` treats the current stage as never entered too (a folder
    created on demand for a policy: it was born at the entry point, so even its
    first stage is *omitida*). Placements walk the placement machine step by
    step to the target's status. No §5.2 guard runs on skipped stages — they did
    not happen. Raises :class:`CaseStageConflict` when ``target`` is not ahead
    of the current stage and :class:`CaseTransitionError` when a placement
    refuses. Flushes nothing itself; the caller commits.

    The per-stage ``case_file_stage_event`` rows are always written (the rail
    reads them); the bitácora gets ONE ``activity`` row — or none with
    ``write_activity=False`` (the caller writes a collapsed one; the skipped
    stage values are appended to ``skipped_out``).
    """
    chain = CASE_STAGE_FLOW.get(case.kind, ())
    if target not in chain:
        raise CaseTransitionError(
            f"La etapa «{stage_label(target)}» no pertenece a este tipo de carpeta."
        )
    target_index = chain.index(target)
    if case.stage in chain:
        current_index = chain.index(case.stage)
    else:
        raise CaseStageConflict(
            f"La carpeta está en «{stage_label(case.stage)}»: no se puede saltar a "
            f"«{stage_label(target)}»."
        )
    start = current_index if include_current else current_index + 1
    if target_index < start or (not include_current and target_index <= current_index):
        raise CaseStageConflict(
            f"La etapa «{stage_label(target)}» no está más adelante que la etapa actual "
            f"«{stage_label(case.stage)}»: para volver atrás usa la barra del viaje."
        )

    placement_error = _walk_placements(db, case, target)
    if placement_error:
        raise CaseTransitionError(placement_error)

    entered = _entered_stages(db, case)
    tick = _event_clock(now)
    why = reason or f"Etapa omitida: la colocación entró en «{stage_label(target)}»."
    events: list[CaseFileStageEvent] = []
    previous: CaseStage | None = None if include_current else case.stage
    skipped_values: list[str] = []
    for stage in chain[start:target_index]:
        if stage in entered and not (include_current and stage == case.stage):
            previous = stage
            continue
        event = CaseFileStageEvent(
            broker_id=case.broker_id,
            case_file_id=case.id,
            from_stage=previous,
            to_stage=stage,
            occurred_at=tick(),
            user_id=None,  # quién = sistema (R6)
            note=why,
            meta={"skipped": True, "target": target.value},
            is_skipped=True,
        )
        db.add(event)
        events.append(event)
        skipped_values.append(stage.value)
        previous = stage

    origin_stage = case.stage
    case.stage = target
    arrival = CaseFileStageEvent(
        broker_id=case.broker_id,
        case_file_id=case.id,
        from_stage=previous,
        to_stage=target,
        occurred_at=tick(),
        user_id=user.id if user else None,
        note=note,
        meta={"skipped_stages": skipped_values} if skipped_values else None,
    )
    db.add(arrival)
    events.append(arrival)
    if skipped_out is not None:
        skipped_out.extend(skipped_values)
    if write_activity:
        db.add(
            Activity(
                broker_id=case.broker_id,
                user_id=user.id if user else None,
                action="case_file.skipped_to",
                entity_type=EntityType.CASE_FILE,
                entity_id=case.id,
                description=skip_description(origin_stage, target, len(skipped_values)),
                meta={
                    "from": origin_stage.value,
                    "to": target.value,
                    "skipped": skipped_values,
                    "note": note,
                },
            )
        )
    return events


def skip_description(origin: CaseStage, target: CaseStage, skipped: int) -> str:
    """``"Saltó de Interés a Cotizaciones recibidas; 4 etapas omitidas"``."""
    text = f"Saltó de {stage_label(origin)} a {stage_label(target)}"
    if skipped:
        text += f"; {stages_omitted(skipped)}"
    return text


def skip_to(
    db: Session,
    *,
    case: CaseFile,
    target: CaseStage,
    user: User | None,
    note: str | None = None,
    now=None,
) -> list[CaseFileStageEvent]:
    """``POST /case-files/{id}/skip-to`` — the broker's "Saltar a…" menu.

    Accounts only, open folders only, targets in :data:`SKIP_TO_TARGETS`;
    ``policy_issued`` / ``active`` need a policy on the folder (422 otherwise).
    """
    if case.kind not in ACCOUNT_KINDS:
        raise CaseTransitionError("Solo una colocación (cuenta) puede saltar etapas.")
    if case.status != CaseFileStatus.OPEN:
        raise CaseTransitionError("La carpeta no está abierta: no se pueden saltar etapas.")
    if target not in SKIP_TO_TARGETS:
        raise CaseTransitionError(
            "Solo se puede saltar a: " + stage_list(SKIP_TO_TARGETS) + "."
        )
    if target in _POLICY_TARGETS and not case_has_policy(db, case):
        raise CaseTransitionError(
            "Para saltar a la emisión o a vigente la colocación necesita una póliza: "
            "súbela o regístrala a mano."
        )
    return skip_forward(db, case=case, target=target, user=user, note=note, now=now)


def activate_stage(
    db: Session,
    *,
    case: CaseFile,
    stage: CaseStage,
    user: User | None,
    note: str | None = None,
    now=None,
) -> CaseFileStageEvent:
    """``POST /case-files/{id}/activate-stage`` — re-enter a SKIPPED stage.

    Only while the folder has NO policy (409 — after issuance the changes go
    through an endorsement or a correction). The folder moves to ``stage`` with
    a normal (non-skipped) event; placements walk back with it.

    Accounts only, open folders only, and only a skipped stage BEHIND the
    current one: re-entering never jumps forward (that is ``skip-to``, with
    its own targets and guards).
    """
    if case.kind not in ACCOUNT_KINDS:
        raise CaseTransitionError("Solo una colocación (cuenta) puede activar etapas omitidas.")
    if case.status != CaseFileStatus.OPEN:
        raise CaseTransitionError(
            "La carpeta no está abierta: no se pueden activar etapas omitidas."
        )
    chain = list(CASE_STAGE_FLOW.get(case.kind, ()))
    if (
        stage not in chain
        or case.stage not in chain
        or chain.index(stage) >= chain.index(case.stage)
    ):
        raise CaseTransitionError(
            f"La etapa «{stage_label(stage)}» no está antes de la etapa actual: solo se "
            "reactiva una etapa omitida anterior."
        )
    if case_has_policy(db, case):
        raise CaseStageConflict(
            "Ya tiene póliza: los cambios se hacen por endoso o corrección"
        )
    states = {row["stage"]: row for row in stage_states(db, case)}
    row = states.get(stage)
    if row is None or row["state"] != "skipped":
        raise CaseTransitionError(
            f"La etapa «{stage_label(stage)}» no está omitida: solo se activa una etapa omitida."
        )
    placement_error = _walk_placements(db, case, stage)
    if placement_error:
        raise CaseTransitionError(placement_error)
    previous = case.stage
    case.stage = stage
    tick = _event_clock(now)
    event = CaseFileStageEvent(
        broker_id=case.broker_id,
        case_file_id=case.id,
        from_stage=previous,
        to_stage=stage,
        occurred_at=tick(),
        user_id=user.id if user else None,
        note=note or "Etapa omitida activada",
        meta={"activated": True},
    )
    db.add(event)
    db.add(
        Activity(
            broker_id=case.broker_id,
            user_id=user.id if user else None,
            action="case_file.stage_activated",
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            description=(
                f"Etapa omitida {stage_label(stage)} activada (desde {stage_label(previous)})"
            ),
            meta={"from": previous.value, "to": stage.value, "note": note},
        )
    )
    return event


def stage_states(db: Session, case: CaseFile) -> list[dict]:
    """The rail: every stage of the folder's chain with its state.

    ``state`` is ``current`` (the folder's stage), ``done`` (entered before),
    ``skipped`` (*omitida* — an ``is_skipped`` event is the latest for it, or a
    stage behind the current one that was never entered at all, e.g. a legacy
    documented skip) or ``pending`` (ahead, never entered). ``entered_at`` is
    the latest event's instant for the stage; ``skipped`` is True when the
    stage counts as omitted.
    """
    chain = list(CASE_STAGE_FLOW.get(case.kind, ()))
    events = db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case.id)
        .order_by(CaseFileStageEvent.occurred_at.asc(), CaseFileStageEvent.id.asc())
    ).all()
    latest: dict[CaseStage, CaseFileStageEvent] = {}
    for event in events:
        latest[event.to_stage] = event
    current_index = chain.index(case.stage) if case.stage in chain else len(chain)

    rows: list[dict] = []
    for index, stage in enumerate(chain):
        event = latest.get(stage)
        if stage == case.stage:
            state = "current"
        elif event is not None and event.is_skipped:
            state = "skipped"
        elif index < current_index:
            state = "done" if event is not None else "skipped"
        else:
            state = "pending"
        rows.append(
            {
                "stage": stage,
                "state": state,
                "entered_at": event.occurred_at if event is not None else None,
                "skipped": state == "skipped",
            }
        )
    if case.stage not in chain:
        event = latest.get(case.stage)
        rows.append(
            {
                "stage": case.stage,
                "state": "current",
                "entered_at": event.occurred_at if event is not None else None,
                "skipped": False,
            }
        )
    return rows


def advance_to_active(
    db: Session,
    *,
    case: CaseFile,
    user: User | None,
    fresh: bool = False,
    now=None,
    note: str | None = None,
) -> list[CaseFileStageEvent]:
    """Registering a policy closes the colocación: jump to ``policy_issued``
    (skipping what never happened) and on to ``active`` (R34, R42 — the mirror
    never blocks; an un-run mirror validation is written as *omitida*).

    ``fresh`` = the folder was just created for this policy (no events yet).
    A folder already at/after ``active``, closed or not an account is left as is.
    Raises :class:`CaseTransitionError` when a placement refuses the walk.
    """
    if case.kind not in ACCOUNT_KINDS:
        return []
    chain = CASE_STAGE_FLOW.get(case.kind, ())
    if case.stage not in chain or case.stage == CaseStage.ACTIVE:
        return []
    events: list[CaseFileStageEvent] = []
    origin_stage = case.stage
    skipped: list[str] = []
    issued_index = chain.index(CaseStage.POLICY_ISSUED)
    reason = "Etapa omitida: la colocación entró con la póliza."
    if fresh or chain.index(case.stage) < issued_index:
        events += skip_forward(
            db,
            case=case,
            target=CaseStage.POLICY_ISSUED,
            user=user,
            note=note or "Póliza registrada",
            include_current=fresh,
            now=now,
            reason=reason,
            write_activity=False,
            skipped_out=skipped,
        )
    # policy_issued / mirror_validation -> active.
    from datetime import datetime, timedelta, timezone

    later = (now or datetime.now(timezone.utc)) + timedelta(microseconds=len(events) + 1)
    events += skip_forward(
        db,
        case=case,
        target=CaseStage.ACTIVE,
        user=user,
        note="Colocación vigente",
        now=later,
        # The mirror never blocks; an un-run validation is simply omitted.
        reason="Etapa omitida: la validación espejo no se realizó (no bloquea la vigencia).",
        write_activity=False,
        skipped_out=skipped,
    )
    # ONE bitácora line for the whole entry (review v12 #3) — the per-stage
    # events above stay in ``case_file_stage_event`` for the rail.
    db.add(
        Activity(
            broker_id=case.broker_id,
            user_id=user.id if user else None,
            action="case_file.skipped_to",
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            description=(
                "La cuenta entró con la póliza"
                + (f"; {stages_omitted(len(skipped))}" if skipped else "")
            ),
            meta={
                "from": origin_stage.value,
                "to": CaseStage.ACTIVE.value,
                "skipped": skipped,
                "via": "policy",
            },
        )
    )
    return events
