"""Commit dispatcher: a CONFIRMED extraction payload -> its prefill target.

Spec §6.6: ``POST /ai/documents/{extraction_id}/confirm`` "commits the
human-reviewed payload to its target entity", and §3.1's registry names that
target per category. The insurer-quotation path (creating a ``proposal``) lives
in ``app.services.ai.confirm_proposal`` and is dispatched by the router
directly; every OTHER category that has a concrete target commits here.

Rules this module keeps, in every committer:

* **Tenant scope.** Every lookup filters on ``broker_id``; a payload naming
  another tenant's policy number simply does not resolve (422, never a leak).
* **Idempotent-safe.** Confirming the same extraction twice must not duplicate
  child rows: creators UPSERT their parent (matched by provenance or natural
  key) and REPLACE their children wholesale, or reject cleanly with a 422.
* **Transactional per confirm.** Committers ``flush`` but never ``commit`` —
  the router commits once (via ``record_confirmation``) after both the entity
  write and the confirmation stamp are staged, and rolls back on any error.
* **Money.** ``net = taxable + exempt`` · ``vat = 0.19 x taxable`` ·
  ``total = net + vat``. Policies reuse :func:`reconcile_money`; endorsement
  DELTAS use the sign-preserving variant here (an administrative endorsement is
  all-zero/None — valid). A break is a :class:`CommitError` with the expected
  and received figures -> 422, never a silent fix.
* **Identity.** Insurers resolve by normalized rut / cmf_code only —
  :func:`app.services.ai.find_or_create_insurer` — NEVER by name. An addressee
  that only has a name is skipped with a warning, not guessed.

Genuinely informational categories (comparatives, declinations, pronouncements,
recommendation/closing letters, the mirror-baseline 07) deliberately commit
nothing: the reviewed payload is recorded on the extraction and consumed where
it matters (the comparator, the mirror-diff). Those return
``applied=False, informational=True`` with a human-readable ``detail`` so the
UI can say so instead of looking broken.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.activity import Note
from app.models.ai import Extraction
from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.document import Document, DocumentCategory
from app.models.endorsement import Endorsement
from app.models.enums import (
    CollectionPlanStatus,
    EndorsementKind,
    EndorsementStatus,
    EntityType,
    InstallmentStatus,
    PaymentMode,
    WarrantySource,
    WarrantyStatus,
)
from app.models.inspection import Inspection, InspectionStatus
from app.models.policy import (
    Claim,
    ClaimItem,
    ClaimRuling,
    ClaimItemKind,
    ClaimStatus,
    CoverageItem,
    Policy,
    PolicyLocation,
    PolicyStatus,
)
from app.models.quote import QuoteRequest, QuoteRequestStatus
from app.models.user import User
from app.models.warranty import Warranty
from app.schemas.proposal import MONEY_TOLERANCE, reconcile_money
from app.models.enums import CoverageKind

__all__ = ["CommitError", "CommitResult", "commit_extraction", "COMMIT_PERMISSIONS"]

VAT_RATE = Decimal("0.19")

#: The Chilean noon convention for contractual instants derived from bare dates.
_NOON = time(12, 0)


class CommitError(Exception):
    """A rule violation while committing a confirmed payload -> 422.

    ``detail`` may be a string or a structured list (expected/received rows),
    exactly as the router will return it.
    """

    def __init__(self, detail: Any):
        super().__init__(str(detail))
        self.detail = detail


@dataclass
class CommitResult:
    """What one confirm actually did, for the response and the audit trail."""

    applied: bool
    target: str
    informational: bool = False
    entity_type: str | None = None
    entity_id: int | None = None
    detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": self.target,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "detail": self.detail,
        }
        payload.update(self.extra)
        return payload


@dataclass
class _Ctx:
    """Everything a committer needs, resolved once."""

    db: Session
    broker_id: int
    user: User | None
    extraction: Extraction
    payload: Any  # the validated registry schema instance
    document: Document
    case_file: CaseFile | None
    warnings: list[str] = field(default_factory=list)

    @property
    def user_id(self) -> int | None:
        return getattr(self.user, "id", None)


# =============================================================================
# Small helpers
# =============================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _noon(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, _NOON, tzinfo=timezone.utc)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def _already_applied(extraction: Extraction) -> bool:
    """True when THIS extraction was already committed once (idempotency guard
    for non-idempotent scalars such as ``round_no += 1``)."""
    raw = extraction.raw_output if isinstance(extraction.raw_output, dict) else {}
    confirmation = raw.get("confirmation")
    return bool(isinstance(confirmation, dict) and confirmation.get("applied"))


def _stamp_provenance(row: Any, ctx: _Ctx) -> None:
    """Fill the suggest->confirm->commit provenance columns where they exist."""
    if hasattr(row, "extraction_id"):
        row.extraction_id = ctx.extraction.id
    if hasattr(row, "extraction_confidence"):
        row.extraction_confidence = ctx.extraction.confidence
    if hasattr(row, "is_confirmed"):
        row.is_confirmed = True
    if hasattr(row, "confirmed_by_id"):
        row.confirmed_by_id = ctx.user_id
    if hasattr(row, "confirmed_at"):
        row.confirmed_at = _now()


def _resolve_case_file(
    db: Session, extraction: Extraction, document: Document, broker_id: int
) -> CaseFile | None:
    case_id = extraction.case_file_id or document.case_file_id
    if case_id is None and document.entity_type == EntityType.CASE_FILE:
        case_id = document.entity_id
    if case_id is None:
        return None
    return db.scalars(
        select(CaseFile).where(CaseFile.id == case_id, CaseFile.broker_id == broker_id)
    ).first()


def _require_case(ctx: _Ctx, why: str) -> CaseFile:
    if ctx.case_file is None:
        raise CommitError(
            f"The document is not linked to a case file, so there is no {why} to "
            "commit to — attach the document to its expediente and confirm again"
        )
    return ctx.case_file


def _resolve_policy(ctx: _Ctx, policy_number: str | None = None) -> Policy | None:
    """The tenant's policy this document is about: case first, folio second."""
    db, broker_id = ctx.db, ctx.broker_id
    if ctx.case_file is not None:
        if ctx.case_file.policy_id is not None:
            policy = db.scalars(
                select(Policy).where(
                    Policy.id == ctx.case_file.policy_id, Policy.broker_id == broker_id
                )
            ).first()
            if policy is not None:
                return policy
        policy = db.scalars(
            select(Policy)
            .where(Policy.case_file_id == ctx.case_file.id, Policy.broker_id == broker_id)
            .order_by(Policy.id.desc())
        ).first()
        if policy is not None:
            return policy
        # A post-sale case anchors to its account case's policy.
        if ctx.case_file.parent_case_file_id is not None:
            policy = db.scalars(
                select(Policy)
                .where(
                    Policy.case_file_id == ctx.case_file.parent_case_file_id,
                    Policy.broker_id == broker_id,
                )
                .order_by(Policy.id.desc())
            ).first()
            if policy is not None:
                return policy
    number = (policy_number or "").strip()
    if number:
        return db.scalars(
            select(Policy).where(
                Policy.broker_id == broker_id, Policy.policy_number == number
            )
        ).first()
    return None


def _require_policy(ctx: _Ctx, policy_number: str | None, why: str) -> Policy:
    policy = _resolve_policy(ctx, policy_number)
    if policy is None:
        raise CommitError(
            f"No policy of this workspace matches this document, so the {why} "
            "cannot be committed — link the case file to its policy first"
        )
    return policy


def _resolve_quote_request(ctx: _Ctx) -> QuoteRequest:
    case = _require_case(ctx, "quote request")
    db, broker_id = ctx.db, ctx.broker_id
    quote = db.scalars(
        select(QuoteRequest)
        .where(QuoteRequest.broker_id == broker_id, QuoteRequest.case_file_id == case.id)
        .order_by(QuoteRequest.id.desc())
    ).first()
    if quote is None and case.placement_id is not None:
        quote = db.scalars(
            select(QuoteRequest)
            .where(
                QuoteRequest.broker_id == broker_id,
                QuoteRequest.placement_id == case.placement_id,
            )
            .order_by(QuoteRequest.id.desc())
        ).first()
    if quote is None:
        raise CommitError(
            "This case has no quote request yet — create the submission before "
            "confirming market documents against it"
        )
    return quote


def _sync_policy_period(policy: Policy) -> None:
    """Keep ``start_date``/``end_date`` and the noon datetimes in step (§2.9)."""
    if policy.period_start_at is not None and policy.start_date is None:
        policy.start_date = policy.period_start_at.date()
    if policy.period_end_at is not None and policy.end_date is None:
        policy.end_date = policy.period_end_at.date()
    if policy.start_date is not None and policy.period_start_at is None:
        policy.period_start_at = datetime.combine(policy.start_date, _NOON, tzinfo=timezone.utc)
    if policy.end_date is not None and policy.period_end_at is None:
        policy.period_end_at = datetime.combine(policy.end_date, _NOON, tzinfo=timezone.utc)


# --- Sign-preserving delta money (endorsements) -------------------------------


def _mismatch(field_name: str, rule: str, expected: Decimal, received: Decimal) -> dict:
    return {
        "field": field_name,
        "rule": rule,
        "expected": str(expected),
        "received": str(received),
        "tolerance": str(MONEY_TOLERANCE),
    }


def _delta_money(
    taxable: Decimal | None,
    exempt: Decimal | None,
    net: Decimal | None,
    vat: Decimal | None,
    total: Decimal | None,
) -> dict[str, Decimal | None]:
    """The proposal invariants, SIGN-PRESERVING, for endorsement deltas.

    ``reconcile_money`` is deliberately NOT reused here: it forbids a negative
    exempt component, which is correct for a premium but wrong for a delta
    (an exclusion endorsement moves negative money). An all-``None`` set is an
    administrative endorsement and is valid.
    """
    errors: list[dict] = []

    if net is None and taxable is not None:
        net = taxable + (exempt or Decimal("0"))
    elif net is not None and taxable is not None:
        expected_net = taxable + (exempt or Decimal("0"))
        if abs(net - expected_net) > MONEY_TOLERANCE:
            errors.append(
                _mismatch("net_premium_delta_uf", "net = taxable + exempt", expected_net, net)
            )
    elif net is not None and taxable is None and exempt is not None:
        taxable = net - exempt

    if taxable is not None:
        expected_vat = (taxable * VAT_RATE).quantize(Decimal("0.0001"))
        if vat is None:
            vat = expected_vat
        elif abs(vat - expected_vat) > MONEY_TOLERANCE:
            errors.append(
                _mismatch("vat_delta_uf", "vat = 0.19 x taxable (not on net)", expected_vat, vat)
            )

    if total is None and net is not None:
        total = net + (vat or Decimal("0"))
    elif total is not None and net is not None:
        expected_total = net + (vat or Decimal("0"))
        if abs(total - expected_total) > MONEY_TOLERANCE:
            errors.append(
                _mismatch("total_premium_delta_uf", "total = net + vat", expected_total, total)
            )

    if errors:
        raise CommitError(errors)
    return {
        "taxable_premium_delta_uf": taxable,
        "exempt_premium_delta_uf": exempt,
        "net_premium_delta_uf": net,
        "vat_delta_uf": vat,
        "total_premium_delta_uf": total,
    }


# --- Fuzzy Spanish -> enum mappers (document wording varies) ------------------


def _match_keywords(text: Any, table: list[tuple[tuple[str, ...], Any]], default: Any) -> Any:
    needle = _norm(text)
    if not needle:
        return default
    for keywords, value in table:
        if any(keyword in needle for keyword in keywords):
            return value
    return default


def _payment_mode(text: Any) -> PaymentMode:
    try:
        return PaymentMode(_norm(text))
    except ValueError:
        pass
    return _match_keywords(
        text,
        [
            (("cupon", "cupón", "talonario"), PaymentMode.COUPON_BOOK),
            (("pac", "cargo automatico", "cargo automático", "debito", "débito"), PaymentMode.DIRECT_DEBIT),
            (("pat", "tarjeta"), PaymentMode.CARD_DEBIT),
            (("transfer",), PaymentMode.TRANSFER),
            (("contado", "unico", "único", "single"), PaymentMode.SINGLE_CHARGE),
        ],
        PaymentMode.OTHER,
    )


def _installment_status(text: Any) -> InstallmentStatus:
    try:
        return InstallmentStatus(_norm(text))
    except ValueError:
        pass
    return _match_keywords(
        text,
        [
            (("pagada con atraso", "atraso", "tardí", "tardi"), InstallmentStatus.PAID_LATE),
            (("pagad", "paid"), InstallmentStatus.PAID),
            (("moros", "vencid", "impag"), InstallmentStatus.OVERDUE),
            (("abonad", "credit"), InstallmentStatus.CREDITED),
            (("anulad", "cancel"), InstallmentStatus.CANCELLED),
            (("por vencer", "due"), InstallmentStatus.DUE),
        ],
        InstallmentStatus.PENDING,
    )


def _plan_status(text: Any) -> CollectionPlanStatus | None:
    try:
        return CollectionPlanStatus(_norm(text))
    except ValueError:
        pass
    return _match_keywords(
        text,
        [
            (("rehabilit",), CollectionPlanStatus.REHABILITATED),
            (("termin",), CollectionPlanStatus.TERMINATED),
            (("suspend",), CollectionPlanStatus.SUSPENDED),
            (("saldad", "pagad", "settled"), CollectionPlanStatus.SETTLED),
            (("moros", "atras", "overdue"), CollectionPlanStatus.OVERDUE),
            (("al dia", "al día", "vigente", "current"), CollectionPlanStatus.CURRENT),
        ],
        None,
    )


def _claim_ruling(text: Any) -> ClaimRuling | None:
    try:
        return ClaimRuling(_norm(text))
    except ValueError:
        pass
    return _match_keywords(
        text,
        [
            (("parcial",), ClaimRuling.PARTIALLY_COVERED),
            (("rechaz", "sin cobertura", "no cubier", "denegad"), ClaimRuling.REJECTED),
            (("con cobertura", "cubier", "ampar", "acog"), ClaimRuling.COVERED),
            (("pendiente",), ClaimRuling.PENDING),
        ],
        None,
    )


def _claim_item_kind(text: Any) -> ClaimItemKind:
    try:
        return ClaimItemKind(_norm(text))
    except ValueError:
        pass
    return _match_keywords(
        text,
        [
            (("paraliza", "perjuicio", "interrup", "lucro"), ClaimItemKind.BUSINESS_INTERRUPTION),
            (("honorari", "gasto", "escombro", "remocion", "remoción"), ClaimItemKind.EXPENSE),
            (("responsabilidad", "tercero"), ClaimItemKind.LIABILITY),
            (("accidente", "personal"), ClaimItemKind.PERSONAL_ACCIDENT),
            (("recupero", "recovery", "salvamento"), ClaimItemKind.RECOVERY),
        ],
        ClaimItemKind.MATERIAL_DAMAGE,
    )


def _warranty_status(text: Any) -> WarrantyStatus | None:
    try:
        return WarrantyStatus(_norm(text))
    except ValueError:
        pass
    return _match_keywords(
        text,
        [
            (("cumplida con atraso", "atraso"), WarrantyStatus.MET_LATE),
            (("cumplid", "met", "verificad", "complet"), WarrantyStatus.MET_ON_TIME),
            (("incumplid", "breach"), WarrantyStatus.BREACHED),
            (("eximid", "waiv", "liberad"), WarrantyStatus.WAIVED),
            (("en curso", "proceso", "progress"), WarrantyStatus.IN_PROGRESS),
            (("pendiente",), WarrantyStatus.PENDING),
        ],
        None,
    )


def _endorsement_kind(type_text: Any, motive: Any = None) -> EndorsementKind:
    """The capitalised verb + subject of the motive is the signal (§2.5.1)."""
    blob = f"{_norm(type_text)} {_norm(motive)}"
    if not blob.strip():
        return EndorsementKind.OTHER
    excludes = "excluye" in blob or "exclusion" in blob or "exclusión" in blob
    if "vehic" in blob or "vehíc" in blob or "patente" in blob:
        return EndorsementKind.VEHICLE_EXCLUSION if excludes else EndorsementKind.VEHICLE_INCLUSION
    if "ubicacion" in blob or "ubicación" in blob or "local" in blob or "sucursal" in blob:
        return EndorsementKind.LOCATION_EXCLUSION if excludes else EndorsementKind.LOCATION_INCLUSION
    if "prenda" in blob or "prendari" in blob or "acreedor" in blob:
        return EndorsementKind.PLEDGE_UPDATE
    if "contratante" in blob:
        return EndorsementKind.POLICYHOLDER_CHANGE
    if "asegurado adicional" in blob:
        return EndorsementKind.ADDITIONAL_INSURED
    if "deducible" in blob:
        return EndorsementKind.DEDUCTIBLE_REDUCTION
    if "nomina" in blob or "nómina" in blob or "trabajador" in blob or "dotacion" in blob or "dotación" in blob:
        return EndorsementKind.ROSTER_ADJUSTMENT
    if "reinstala" in blob or "rehabilitacion de limite" in blob:
        return EndorsementKind.AGGREGATE_REINSTATEMENT
    if "actividad" in blob or "giro" in blob:
        return EndorsementKind.ACTIVITY_EXTENSION
    if "disminu" in blob or "rebaja" in blob or "reduce" in blob:
        return EndorsementKind.SUM_INSURED_DECREASE
    if "aumenta" in blob or "aumento" in blob or "incrementa" in blob:
        return EndorsementKind.SUM_INSURED_INCREASE
    return EndorsementKind.OTHER


def _next_endorsement_sequence(db: Session, policy: Policy) -> int:
    highest = db.scalar(
        select(func.max(Endorsement.sequence_no)).where(
            Endorsement.broker_id == policy.broker_id,
            Endorsement.policy_id == policy.id,
        )
    )
    return int(highest or 0) + 1


def _upsert_warranty(
    ctx: _Ctx,
    policy: Policy,
    *,
    code: str | None,
    title: str | None = None,
    requirement: str | None = None,
    category: str | None = None,
    deadline_days: int | None = None,
    due_date: date | None = None,
    budget_uf: Decimal | None = None,
    actual_cost_uf: Decimal | None = None,
    source: WarrantySource = WarrantySource.UNDERWRITING_WARRANTY,
    status: WarrantyStatus | None = None,
    completed_on: date | None = None,
    verification: str | None = None,
    sort_order: int = 0,
) -> Warranty:
    """UPSERT by ``(policy, code)`` — the R-n / G-n / M-n code is the thread."""
    db = ctx.db
    row: Warranty | None = None
    if code:
        row = db.scalars(
            select(Warranty).where(
                Warranty.broker_id == ctx.broker_id,
                Warranty.policy_id == policy.id,
                Warranty.code == code,
            )
        ).first()
    if row is None:
        row = Warranty(
            broker_id=ctx.broker_id,
            policy_id=policy.id,
            code=code,
            source=source,
            sort_order=sort_order,
        )
        db.add(row)
    if ctx.case_file is not None:
        row.case_file_id = ctx.case_file.id
    if title is not None:
        row.title = title[:255]
    if requirement is not None:
        row.requirement = requirement
    if category is not None:
        row.category = str(category)[:4]
    if deadline_days is not None:
        row.deadline_days = deadline_days
    if due_date is not None:
        row.due_date = due_date
    if budget_uf is not None:
        row.budget_uf = budget_uf
    if actual_cost_uf is not None:
        row.actual_cost_uf = actual_cost_uf
    if status is not None:
        row.status = status
    if completed_on is not None:
        row.completed_on = completed_on
    if verification is not None:
        row.verification = verification
    return row


# =============================================================================
# Committers — one per category with a concrete prefill target
# =============================================================================


def _commit_prospect_request(ctx: _Ctx) -> CommitResult:
    """00A -> client contact/intake fields + a note on the case (§3.1)."""
    case = _require_case(ctx, "client")
    db, payload = ctx.db, ctx.payload
    client = db.scalars(
        select(Client).where(Client.id == case.client_id, Client.broker_id == ctx.broker_id)
    ).first()
    if client is None:
        raise CommitError("The case file's client could not be resolved in this workspace")

    signer = payload.signer
    if signer.name:
        client.contact_name = signer.name[:255]
    if signer.email:
        client.contact_email = signer.email[:255]
    if signer.phone:
        client.contact_phone = signer.phone[:64]

    # The intake note: what happened, what worries them, what they asked for.
    lines: list[str] = []
    trigger = payload.trigger_event
    if trigger.description:
        piece = f"Hecho gatillante: {trigger.description}"
        if trigger.date:
            piece += f" ({trigger.date.isoformat()})"
        if trigger.loss_uf is not None:
            piece += f" — pérdida UF {trigger.loss_uf}"
        lines.append(piece)
    if payload.concerns:
        lines.append("Preocupaciones: " + "; ".join(str(c) for c in payload.concerns if c))
    if payload.requests:
        lines.append("Solicitudes: " + "; ".join(str(r) for r in payload.requests if r))
    if payload.current_policy.number:
        current = payload.current_policy
        piece = f"Póliza vigente {current.number}"
        if current.insurer:
            piece += f" con {current.insurer}"
        if current.expiry_date:
            piece += f", vence {current.expiry_date.isoformat()}"
        lines.append(piece)

    note_id: int | None = None
    if lines:
        # Idempotency: one note per extraction, keyed through ``phase``.
        phase = f"ai:prospect_request:{ctx.extraction.id}"
        note = db.scalars(
            select(Note).where(
                Note.broker_id == ctx.broker_id,
                Note.entity_type == EntityType.CASE_FILE,
                Note.entity_id == case.id,
                Note.phase == phase,
            )
        ).first()
        if note is None:
            note = Note(
                broker_id=ctx.broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=case.id,
                author_id=ctx.user_id,
                body="",
                is_internal=True,
                phase=phase,
            )
            db.add(note)
        note.body = "\n".join(lines)
        db.flush()
        note_id = note.id

    db.flush()
    return CommitResult(
        applied=True,
        target="client",
        entity_type="client",
        entity_id=client.id,
        detail="Client contacts updated from the prospect request",
        extra={"note_id": note_id, "case_file_id": case.id},
        warnings=ctx.warnings,
    )


def _commit_technical_brief(ctx: _Ctx) -> CommitResult:
    """01 -> quote_request header + requested coverages + placement.brief_document_id."""
    quote = _resolve_quote_request(ctx)
    payload = ctx.payload

    if payload.policy_period_start is not None:
        quote.desired_start = payload.policy_period_start.date()
    if payload.policy_period_end is not None:
        quote.desired_end = payload.policy_period_end.date()

    coverage_lines: list[str] = []
    for line in payload.requested_coverages:
        number = f"{line.number}. " if line.number else ""
        name = line.name or line.text or ""
        limit = line.requested_limit or line.text or ""
        rendered = f"{number}{name}".strip()
        if limit and limit != name:
            rendered = f"{rendered}: {limit}" if rendered else limit
        if rendered:
            coverage_lines.append(rendered)
    for deductible in payload.requested_deductibles:
        text = deductible.requested or deductible.text
        if text:
            peril = f"{deductible.peril}: " if deductible.peril else ""
            coverage_lines.append(f"Deducible {peril}{text}")
    if coverage_lines:
        quote.requested_coverages = "\n".join(coverage_lines)

    if payload.offer_deadline is not None:
        quote.due_at = _noon(payload.offer_deadline)

    placement = quote.placement
    if placement is not None:
        placement.brief_document_id = ctx.document.id

    ctx.db.flush()
    return CommitResult(
        applied=True,
        target="quote_request",
        entity_type="quote_request",
        entity_id=quote.id,
        detail="Quote request header updated from the technical brief",
        extra={"placement_id": placement.id if placement else None},
        warnings=ctx.warnings,
    )


def _resolve_addressees(ctx: _Ctx, addressees: list[Any]) -> list[int]:
    """Addressee insurers by normalized rut/cmf_code — NEVER by name."""
    from app.services.ai import find_or_create_insurer

    ids: list[int] = []
    for entry in addressees:
        rut = getattr(entry, "rut", None)
        cmf_code = getattr(entry, "cmf_code", None)
        name = getattr(entry, "legal_name", None) or getattr(entry, "trade_name", None)
        if not (rut or cmf_code):
            ctx.warnings.append(
                f"Addressee '{name or 'sin nombre'}' has no rut/cmf_code — left "
                "unresolved (insurers are never matched by name)"
            )
            continue
        try:
            insurer = find_or_create_insurer(
                ctx.db,
                cmf_code=cmf_code,
                rut=rut,
                legal_name=name,
                broker_id=ctx.broker_id,
            )
        except ValueError as exc:
            ctx.warnings.append(f"Addressee '{name or rut or cmf_code}': {exc}")
            continue
        if insurer.id not in ids:
            ids.append(insurer.id)
    return ids


def _commit_submission_letter(ctx: _Ctx) -> CommitResult:
    """02 -> quote_request dispatch: recipients snapshot, sent_at, due_at."""
    quote = _resolve_quote_request(ctx)
    payload = ctx.payload

    recipient_ids = _resolve_addressees(ctx, payload.addressee_insurers)
    if recipient_ids:
        quote.recipient_insurer_ids = recipient_ids  # snapshot, replaced wholesale
    quote.sent_at = _noon(payload.letter_date) or quote.sent_at or _now()
    if payload.offer_deadline is not None:
        quote.due_at = _noon(payload.offer_deadline)
    if quote.status == QuoteRequestStatus.DRAFT:
        quote.status = QuoteRequestStatus.SENT

    ctx.db.flush()
    return CommitResult(
        applied=True,
        target="quote_request",
        entity_type="quote_request",
        entity_id=quote.id,
        detail="Submission dispatch recorded from the remission letter",
        extra={"recipient_insurer_ids": recipient_ids},
        warnings=ctx.warnings,
    )


def _commit_resubmission_letter(ctx: _Ctx) -> CommitResult:
    """03F -> quote_request.round_no (+ new deadline). Idempotent on re-confirm."""
    quote = _resolve_quote_request(ctx)
    payload = ctx.payload

    if payload.round_number is not None:
        quote.round_no = max(int(payload.round_number), 1)
    elif not _already_applied(ctx.extraction):
        quote.round_no = int(quote.round_no or 1) + 1
    if payload.quote_deadline is not None:
        quote.due_at = _noon(payload.quote_deadline)

    recipient_ids = _resolve_addressees(ctx, payload.addressee_insurers)
    if recipient_ids:
        quote.recipient_insurer_ids = recipient_ids
    if payload.round_one_responses:
        # Proposal.outcome cannot be set from here: the letter names insurers by
        # NAME only, and identity is never matched by name (§3.2 rule 5).
        ctx.warnings.append(
            "Round-one responses recorded on the extraction only — proposals are "
            "not matched by insurer name"
        )

    ctx.db.flush()
    return CommitResult(
        applied=True,
        target="quote_request",
        entity_type="quote_request",
        entity_id=quote.id,
        detail=f"Submission moved to round {quote.round_no}",
        extra={"round_no": quote.round_no},
        warnings=ctx.warnings,
    )


def _commit_inspection_report(ctx: _Ctx) -> CommitResult:
    """00E -> inspection (scores -> columns, matrices -> checklist JSON) + warranties."""
    case = _require_case(ctx, "inspection")
    db, payload = ctx.db, ctx.payload

    asset_id: int | None = None
    if case.placement_id is not None:
        placement = case.placement
        asset_id = placement.asset_id if placement is not None else None
    if asset_id is None:
        raise CommitError(
            "The case file has no placement/asset, so the inspection cannot be "
            "committed — the inspection is a property of the asset"
        )

    inspection = db.scalars(
        select(Inspection)
        .where(Inspection.broker_id == ctx.broker_id, Inspection.case_file_id == case.id)
        .order_by(Inspection.id.desc())
    ).first()
    if inspection is None and payload.report_folio:
        inspection = db.scalars(
            select(Inspection).where(
                Inspection.broker_id == ctx.broker_id,
                Inspection.asset_id == asset_id,
                Inspection.folio == payload.report_folio,
            )
        ).first()
    if inspection is None:
        inspection = Inspection(broker_id=ctx.broker_id, asset_id=asset_id, version=1)
        db.add(inspection)

    inspection.case_file_id = case.id
    inspection.status = InspectionStatus.ISSUED
    inspection.report_document_id = ctx.document.id
    if payload.report_folio:
        inspection.folio = payload.report_folio[:64]
    if payload.visit_date:
        inspection.visit_date = payload.visit_date
    if payload.report_date:
        inspection.report_date = payload.report_date
    if payload.weighted_technical_score is not None:
        inspection.technical_score = payload.weighted_technical_score
        inspection.overall_score = payload.weighted_technical_score
    if payload.risk_class:
        inspection.risk_classification = payload.risk_class[:255]
    summary = payload.executive_summary or payload.inspector_conclusion
    if summary:
        inspection.findings_summary = summary
    # The matrices are render-only -> the versioned checklist JSON (§3.1).
    inspection.checklist = {
        "module_scores": [row.model_dump(mode="json") for row in payload.module_scores],
        "construction_by_location": [r.model_dump(mode="json") for r in payload.construction_by_location],
        "fire_protection_matrix": [r.model_dump(mode="json") for r in payload.fire_protection_matrix],
        "theft_protection_matrix": [r.model_dump(mode="json") for r in payload.theft_protection_matrix],
        "combustible_load": [r.model_dump(mode="json") for r in payload.combustible_load],
        "exposure_scenarios": [r.model_dump(mode="json") for r in payload.exposure_scenarios],
        "regulatory_compliance": [r.model_dump(mode="json") for r in payload.regulatory_compliance],
        "recommendations": [r.model_dump(mode="json") for r in payload.recommendations],
    }
    db.flush()

    # Warranty rows need their policy (policy_id is NOT NULL by design). At 00E
    # time the policy usually does not exist yet — defer visibly, never guess.
    warranty_ids: list[int] = []
    policy = _resolve_policy(ctx)
    if policy is not None:
        for index, rec in enumerate(payload.recommendations):
            if not rec.code:
                continue
            row = _upsert_warranty(
                ctx,
                policy,
                code=rec.code,
                title=rec.title,
                requirement=rec.requirement,
                category=rec.category,
                deadline_days=rec.deadline_days,
                due_date=rec.due_date,
                budget_uf=rec.budget_uf if rec.budget_uf is not None else rec.cost_uf,
                source=WarrantySource.INSPECTION_RECOMMENDATION,
                sort_order=index,
            )
            db.flush()
            warranty_ids.append(row.id)
    elif payload.recommendations:
        ctx.warnings.append(
            "Recommendations kept on the inspection only — warranty rows are "
            "created once the case has a policy"
        )

    return CommitResult(
        applied=True,
        target="inspection",
        entity_type="inspection",
        entity_id=inspection.id,
        detail="Inspection committed from the report",
        extra={"warranty_ids": warranty_ids},
        warnings=ctx.warnings,
    )


def _commit_endorsement_proposal(ctx: _Ctx) -> CommitResult:
    """07A/07B/09C -> endorsement(status=proposed) with full AI provenance."""
    db, payload = ctx.db, ctx.payload
    policy = _require_policy(ctx, payload.policy_number, "endorsement proposal")

    money = _delta_money(
        payload.additional_net_taxable_uf,
        payload.additional_net_exempt_uf,
        payload.additional_net_premium_uf,
        payload.vat_uf,
        payload.additional_gross_premium_uf,
    )

    row = db.scalars(
        select(Endorsement).where(
            Endorsement.broker_id == ctx.broker_id,
            Endorsement.extraction_id == ctx.extraction.id,
        )
    ).first()
    if row is None:
        row = db.scalars(
            select(Endorsement).where(
                Endorsement.broker_id == ctx.broker_id,
                Endorsement.policy_id == policy.id,
                Endorsement.proposal_document_id == ctx.document.id,
            )
        ).first()
    if row is None:
        row = Endorsement(
            broker_id=ctx.broker_id,
            policy_id=policy.id,
            sequence_no=_next_endorsement_sequence(db, policy),
        )
        db.add(row)
    elif row.status in (EndorsementStatus.ISSUED, EndorsementStatus.APPLIED):
        raise CommitError(
            f"Endorsement {row.id} is already '{row.status.value}' — a proposal "
            "cannot overwrite an issued endorsement"
        )

    row.status = EndorsementStatus.PROPOSED
    row.case_file_id = ctx.case_file.id if ctx.case_file else row.case_file_id
    row.proposal_document_id = ctx.document.id
    if payload.proposed_endorsement_number:
        row.endorsement_number = payload.proposed_endorsement_number[:64]
    row.kind = _endorsement_kind(payload.endorsement_type, payload.motive_text)
    row.effective_at = payload.requested_effective_at or row.effective_at
    row.ends_at = payload.requested_end_at or row.ends_at
    if payload.motive_text:
        row.motive = payload.motive_text
    if payload.contractual_basis:
        row.contractual_basis = payload.contractual_basis[:255]
    if payload.endorsement_days is not None:
        row.prorata_days = payload.endorsement_days
    if payload.unexpired_days is not None:
        row.unexpired_days = payload.unexpired_days
    if payload.total_added_uf is not None or payload.total_removed_uf is not None:
        row.insured_amount_delta_uf = (payload.total_added_uf or Decimal("0")) - (
            payload.total_removed_uf or Decimal("0")
        )
    if payload.broker_commission_delta_uf is not None:
        row.commission_delta_uf = payload.broker_commission_delta_uf
    for field_name, value in money.items():
        if value is not None:
            setattr(row, field_name, value)
    row.effect = {
        "source": "endorsement_proposal",
        "effect_table": [r.model_dump(mode="json") for r in payload.effect_table],
        "state_before": payload.state_before.model_dump(mode="json") if payload.state_before else None,
        "item_amounts": [r.model_dump(mode="json") for r in payload.item_amounts],
    }
    _stamp_provenance(row, ctx)
    db.flush()
    return CommitResult(
        applied=True,
        target="endorsement",
        entity_type="endorsement",
        entity_id=row.id,
        detail="Endorsement proposed from the broker's document",
        extra={"policy_id": policy.id, "sequence_no": row.sequence_no, "status": row.status.value},
        warnings=ctx.warnings,
    )


_PREMIUM_ROW_FIELDS: list[tuple[tuple[str, ...], str]] = [
    (("afecta", "gravad", "taxable"), "taxable"),
    (("exenta", "exento", "exempt"), "exempt"),
    (("iva", "vat", "impuesto"), "vat"),
    (("total", "bruta", "bruto", "gross"), "total"),
    (("neta", "neto", "net"), "net"),
    (("comision", "comisión", "commission"), "commission"),
]


def _premium_movement(rows: list[Any]) -> dict[str, Decimal | None]:
    """Parse the carrier's signed premium-movement rows by their Spanish labels."""
    from app.schemas.extraction.common import parse_uf

    found: dict[str, Decimal] = {}
    for row in rows:
        label = _norm(getattr(row, "label", None) or getattr(row, "detail", None))
        amount = getattr(row, "amount_uf", None)
        if amount is None:
            amount = parse_uf(getattr(row, "value", None))
        if amount is None or not label:
            continue
        for keywords, slot in _PREMIUM_ROW_FIELDS:
            if any(keyword in label for keyword in keywords):
                found.setdefault(slot, amount)
                break
    return {
        "taxable": found.get("taxable"),
        "exempt": found.get("exempt"),
        "net": found.get("net"),
        "vat": found.get("vat"),
        "total": found.get("total"),
        "commission": found.get("commission"),
    }


def _commit_endorsement_issued(ctx: _Ctx) -> CommitResult:
    """08A/08B/09B/09D -> endorsement(status=issued) with the carrier's figures."""
    db, payload = ctx.db, ctx.payload
    policy = _require_policy(ctx, payload.policy_number, "issued endorsement")

    if payload.premium_impact_none:
        money = {
            "taxable_premium_delta_uf": Decimal("0"),
            "exempt_premium_delta_uf": Decimal("0"),
            "net_premium_delta_uf": Decimal("0"),
            "vat_delta_uf": Decimal("0"),
            "total_premium_delta_uf": Decimal("0"),
        }
        commission = None
    else:
        movement = _premium_movement(payload.premium_movement)
        money = _delta_money(
            movement["taxable"], movement["exempt"], movement["net"],
            movement["vat"], movement["total"],
        )
        commission = movement["commission"]

    row = db.scalars(
        select(Endorsement).where(
            Endorsement.broker_id == ctx.broker_id,
            Endorsement.extraction_id == ctx.extraction.id,
        )
    ).first()
    if row is None and payload.endorsement_number:
        row = db.scalars(
            select(Endorsement).where(
                Endorsement.broker_id == ctx.broker_id,
                Endorsement.policy_id == policy.id,
                Endorsement.endorsement_number == payload.endorsement_number,
            )
        ).first()
    if row is None:
        # The carrier's endoso usually answers a proposed one on the same case.
        stmt = select(Endorsement).where(
            Endorsement.broker_id == ctx.broker_id,
            Endorsement.policy_id == policy.id,
            Endorsement.status == EndorsementStatus.PROPOSED,
            Endorsement.issued_document_id.is_(None),
        )
        if ctx.case_file is not None:
            stmt = stmt.where(Endorsement.case_file_id == ctx.case_file.id)
        row = db.scalars(stmt.order_by(Endorsement.id.desc())).first()
    if row is None:
        row = Endorsement(
            broker_id=ctx.broker_id,
            policy_id=policy.id,
            sequence_no=_next_endorsement_sequence(db, policy),
        )
        db.add(row)

    row.status = EndorsementStatus.ISSUED
    row.case_file_id = ctx.case_file.id if ctx.case_file else row.case_file_id
    row.issued_document_id = ctx.document.id
    if payload.endorsement_number:
        row.endorsement_number = payload.endorsement_number[:64]
    if payload.issue_date:
        row.issued_at = payload.issue_date
    row.effective_at = payload.endorsement_effective_at or row.effective_at
    row.ends_at = payload.endorsement_end_at or row.ends_at
    if payload.motive_text:
        row.motive = payload.motive_text
    if row.kind is None or row.kind == EndorsementKind.OTHER:
        row.kind = _endorsement_kind(payload.endorsement_type, payload.motive_text)
    for field_name, value in money.items():
        if value is not None:
            setattr(row, field_name, value)
    if commission is not None:
        row.commission_delta_uf = commission
    if payload.deductible_changes:
        row.deductibles = {
            (change.peril or f"item_{index}"): {
                key: value
                for key, value in change.model_dump(mode="json").items()
                if value is not None
            }
            for index, change in enumerate(payload.deductible_changes)
        }
    row.effect = {
        "source": "endorsement_issued",
        "amounts_moved": [r.model_dump(mode="json") for r in payload.amounts_moved],
        "post_endorsement_schedule": [r.model_dump(mode="json") for r in payload.post_endorsement_schedule],
        "element_change_table": [r.model_dump(mode="json") for r in payload.element_change_table],
        "premium_impact_none": payload.premium_impact_none,
    }
    _stamp_provenance(row, ctx)
    db.flush()
    return CommitResult(
        applied=True,
        target="endorsement",
        entity_type="endorsement",
        entity_id=row.id,
        detail="Endorsement issued — deltas recorded from the carrier's document",
        extra={"policy_id": policy.id, "sequence_no": row.sequence_no, "status": row.status.value},
        warnings=ctx.warnings,
    )


def _installment_sum_check(
    installments: list[Any], expected_total: Decimal | None
) -> None:
    """Σ instalments == the plan's own declared total (§2.6) — 422 on a break.

    The document's OWN total is the reference (§3.2 rule 4: never reconcile
    across documents); the cross-document ledger check lives in the collections
    router.
    """
    if expected_total is None:
        return
    amounts = [row.gross_amount_uf for row in installments if row.gross_amount_uf is not None]
    if not amounts:
        return
    received = sum(amounts, Decimal("0"))
    if abs(received - expected_total) > MONEY_TOLERANCE:
        raise CommitError(
            [
                _mismatch(
                    "installments.gross_amount_uf",
                    "sum(installments) = total_premium_uf",
                    expected_total,
                    received,
                )
            ]
        )


def _commit_payment_plan(ctx: _Ctx) -> CommitResult:
    """09 -> collection_plan + collection_installment (children replaced wholesale)."""
    db, payload = ctx.db, ctx.payload
    policy = _require_policy(ctx, payload.policy_number, "payment plan")

    rows = [row for row in payload.installments if row.gross_amount_uf is not None]
    skipped = len(payload.installments) - len(rows)
    if skipped:
        ctx.warnings.append(
            f"{skipped} installment row(s) had no gross amount and were not committed"
        )
    _installment_sum_check(rows, payload.total_premium_uf)

    plan: CollectionPlan | None = None
    if payload.payment_plan_number:
        plan = db.scalars(
            select(CollectionPlan).where(
                CollectionPlan.broker_id == ctx.broker_id,
                CollectionPlan.policy_id == policy.id,
                CollectionPlan.plan_number == payload.payment_plan_number,
            )
        ).first()
    if plan is None:
        plan = db.scalars(
            select(CollectionPlan)
            .where(
                CollectionPlan.broker_id == ctx.broker_id,
                CollectionPlan.policy_id == policy.id,
            )
            .order_by(CollectionPlan.id.desc())
        ).first()
    if plan is None:
        plan = CollectionPlan(broker_id=ctx.broker_id, policy_id=policy.id)
        db.add(plan)

    plan.case_file_id = ctx.case_file.id if ctx.case_file else plan.case_file_id
    if payload.payment_plan_number:
        plan.plan_number = payload.payment_plan_number[:64]
    plan.payment_mode = _payment_mode(payload.payment_mode)
    if payload.installment_count is not None:
        plan.installment_count = payload.installment_count
    elif rows:
        plan.installment_count = len(rows)
    if payload.total_premium_uf is not None:
        plan.total_premium_uf = payload.total_premium_uf
    if payload.bank:
        plan.bank = payload.bank[:120]
    if payload.account_number:
        plan.account_number = payload.account_number[:64]
    if payload.monthly_interest_rate_pct is not None:
        plan.monthly_interest_rate_pct = payload.monthly_interest_rate_pct
    db.flush()

    # Replace the cuotas wholesale — confirming twice must not duplicate them.
    for existing in list(plan.installments):
        db.delete(existing)
    db.flush()
    for index, row in enumerate(rows):
        db.add(
            CollectionInstallment(
                broker_id=ctx.broker_id,
                collection_plan_id=plan.id,
                number=row.number or index + 1,
                coupon_number=(row.coupon_number or None),
                due_date=row.due_date,
                gross_amount_uf=row.gross_amount_uf,
                net_premium_uf=row.net_premium_uf,
                commission_uf=row.commission_uf,
                paid_on=row.paid_on,
                days_late=row.days_late,
                status=_installment_status(row.status),
            )
        )
    db.flush()
    return CommitResult(
        applied=True,
        target="collection_plan",
        entity_type="collection_plan",
        entity_id=plan.id,
        detail=f"Payment plan committed with {len(rows)} installment(s)",
        extra={"policy_id": policy.id, "installment_count": len(rows)},
        warnings=ctx.warnings,
    )


def _commit_collection_status(ctx: _Ctx) -> CommitResult:
    """09/10 estado de cobranza -> plan status + installment statuses + warranties."""
    db, payload = ctx.db, ctx.payload
    policy = _require_policy(ctx, payload.policy_number, "collection status")
    plan = db.scalars(
        select(CollectionPlan)
        .where(
            CollectionPlan.broker_id == ctx.broker_id,
            CollectionPlan.policy_id == policy.id,
        )
        .order_by(CollectionPlan.id.desc())
    ).first()
    if plan is None:
        raise CommitError(
            "This policy has no collection plan yet — confirm the payment plan "
            "(09) before its collection status"
        )

    if payload.as_of_date is not None:
        plan.as_of_date = payload.as_of_date
    summary = payload.situation_summary
    status = _plan_status(
        (summary.value if summary is not None else None)
        or (summary.detail if summary is not None else None)
        or (summary.label if summary is not None else None)
    )
    termination = payload.coverage_termination
    if termination.terminated_at is not None:
        plan.terminated_at = termination.terminated_at
        status = status or CollectionPlanStatus.TERMINATED
    if termination.rehabilitated_at is not None:
        plan.rehabilitated_at = termination.rehabilitated_at
        status = CollectionPlanStatus.REHABILITATED
    if termination.days_without_cover is not None:
        plan.days_without_cover = termination.days_without_cover
    if termination.rehabilitation_cost_uf is not None:
        plan.rehabilitation_cost_uf = termination.rehabilitation_cost_uf
    if payload.art528_events:
        plan.art528_events = [e.model_dump(mode="json") for e in payload.art528_events]
    if payload.management_note:
        plan.management_note = payload.management_note

    by_number = {row.number: row for row in plan.installments}
    updated = 0
    overdue_seen = False
    for row in payload.installments:
        if row.number is None or row.number not in by_number:
            continue
        cuota = by_number[row.number]
        cuota.status = _installment_status(row.status)
        if row.paid_on is not None:
            cuota.paid_on = row.paid_on
        if row.days_late is not None:
            cuota.days_late = row.days_late
        overdue_seen = overdue_seen or cuota.status == InstallmentStatus.OVERDUE
        updated += 1
    if status is None and overdue_seen:
        status = CollectionPlanStatus.OVERDUE
    if status is not None:
        plan.status = status

    warranty_updates = 0
    for row in payload.warranty_status:
        raw = row.model_dump(mode="json")
        code = raw.get("code") or raw.get("label")
        state = _warranty_status(raw.get("status") or raw.get("value") or raw.get("detail"))
        if not code or state is None:
            continue
        warranty = db.scalars(
            select(Warranty).where(
                Warranty.broker_id == ctx.broker_id,
                Warranty.policy_id == policy.id,
                Warranty.code == str(code).strip(),
            )
        ).first()
        if warranty is not None:
            warranty.status = state
            warranty_updates += 1

    db.flush()
    return CommitResult(
        applied=True,
        target="collection_plan",
        entity_type="collection_plan",
        entity_id=plan.id,
        detail=f"Collection status applied ({updated} installment(s) updated)",
        extra={
            "policy_id": policy.id,
            "plan_status": plan.status.value,
            "installments_updated": updated,
            "warranties_updated": warranty_updates,
        },
        warnings=ctx.warnings,
    )


def _resolve_claim(ctx: _Ctx, claim_number: str | None) -> Claim | None:
    db = ctx.db
    number = (claim_number or "").strip()
    if number:
        claim = db.scalars(
            select(Claim).where(
                Claim.broker_id == ctx.broker_id, Claim.claim_number == number
            )
        ).first()
        if claim is not None:
            return claim
    if ctx.case_file is not None:
        return db.scalars(
            select(Claim)
            .where(Claim.broker_id == ctx.broker_id, Claim.case_file_id == ctx.case_file.id)
            .order_by(Claim.id.desc())
        ).first()
    return None


def _commit_claim_notice(ctx: _Ctx) -> CommitResult:
    """10/11 denuncio -> claim + claim_item rows (children replaced wholesale)."""
    db, payload = ctx.db, ctx.payload
    policy = _resolve_policy(ctx, payload.policy_number)

    claim = _resolve_claim(ctx, payload.claim_number)
    if claim is None:
        client_id: int | None = None
        if ctx.case_file is not None:
            client_id = ctx.case_file.client_id
        elif policy is not None:
            client_id = policy.client_id
        if client_id is None:
            raise CommitError(
                "Neither a case file nor a policy resolves this notice to a "
                "client — link the document before confirming"
            )
        claim = Claim(broker_id=ctx.broker_id, client_id=client_id)
        db.add(claim)

    if policy is not None:
        claim.policy_id = policy.id
    claim.case_file_id = ctx.case_file.id if ctx.case_file else claim.case_file_id
    if payload.claim_number:
        claim.claim_number = payload.claim_number[:64]
    if payload.claim_modality:
        claim.kind = payload.claim_modality[:160]
    if payload.occurrence_at is not None:
        claim.occurred_at = payload.occurrence_at
        claim.event_date = payload.occurrence_at.date()
    if payload.notice_at is not None:
        claim.reported_at = payload.notice_at
        claim.reported_date = payload.notice_at.date()
    if payload.notice_deadline_days is not None:
        claim.notice_deadline_days = payload.notice_deadline_days
    if payload.narrative_text:
        claim.description = payload.narrative_text
    if payload.total_estimated_uf is not None:
        claim.estimated_amount_uf = payload.total_estimated_uf
    db.flush()

    # Items replaced wholesale: the notice IS the declared-damage table.
    for existing in list(claim.items):
        db.delete(existing)
    db.flush()
    order = 0
    for damage in payload.declared_damages:
        db.add(
            ClaimItem(
                broker_id=ctx.broker_id,
                claim_id=claim.id,
                kind=_claim_item_kind(damage.coverage or damage.item),
                item=(damage.item or damage.coverage or "partida")[:255],
                basis=damage.basis,
                notified_uf=damage.amount_uf,
                sort_order=order,
                note=damage.note,
            )
        )
        order += 1
    for label, amount in (
        ("Remoción de escombros", payload.debris_removal_uf),
        ("Honorarios profesionales", payload.professional_fees_uf),
    ):
        if amount is not None:
            db.add(
                ClaimItem(
                    broker_id=ctx.broker_id,
                    claim_id=claim.id,
                    kind=ClaimItemKind.EXPENSE,
                    item=label,
                    notified_uf=amount,
                    sort_order=order,
                )
            )
            order += 1
    db.flush()
    return CommitResult(
        applied=True,
        target="claim",
        entity_type="claim",
        entity_id=claim.id,
        detail=f"Claim notice committed with {order} item(s)",
        extra={"policy_id": claim.policy_id, "items": order},
        warnings=ctx.warnings,
    )


def _apply_quantification(
    ctx: _Ctx, claim: Claim, rows: list[Any]
) -> int:
    """Adjuster figures onto claim items, matched by partida name (idempotent)."""
    db = ctx.db
    existing = {_norm(item.item): item for item in claim.items if item.item}
    touched = 0
    order = max((item.sort_order for item in claim.items), default=-1) + 1
    for row in rows:
        key = _norm(row.item)
        item = existing.get(key)
        if item is None:
            item = ClaimItem(
                broker_id=ctx.broker_id,
                claim_id=claim.id,
                kind=_claim_item_kind(row.kind or row.item),
                item=(row.item or "partida")[:255],
                sort_order=order,
            )
            db.add(item)
            existing[key] = item
            order += 1
        if row.kind:
            item.kind = _claim_item_kind(row.kind)
        if row.basis:
            item.basis = row.basis
        for source_attr, target_attr in (
            ("notified_uf", "notified_uf"),
            ("determined_uf", "determined_uf"),
            ("damage_uf", "damage_uf"),
            ("deductible_uf", "deductible_uf"),
            ("indemnity_uf", "indemnity_uf"),
        ):
            value = getattr(row, source_attr, None)
            if value is not None:
                setattr(item, target_attr, value)
        if row.note:
            item.note = row.note
        touched += 1
    return touched


def _require_claim(ctx: _Ctx, claim_number: str | None, what: str) -> Claim:
    claim = _resolve_claim(ctx, claim_number)
    if claim is None:
        raise CommitError(
            f"No claim of this workspace matches this {what} — confirm the claim "
            "notice (denuncio) first"
        )
    return claim


def _commit_claim_preliminary(ctx: _Ctx) -> CommitResult:
    """11/12 pre-informe -> ruling + adjuster + per-partida determined figures."""
    db, payload = ctx.db, ctx.payload
    claim = _require_claim(ctx, payload.claim_number, "preliminary report")

    adjuster = payload.adjuster
    if adjuster.name:
        name = adjuster.name
        if getattr(adjuster, "role", None):
            name = f"{name} ({adjuster.role})"
        claim.adjuster_name = name[:160]
    if adjuster.registry:
        claim.adjuster_registry = adjuster.registry[:32]
    ruling = _claim_ruling(payload.preliminary_ruling)
    if ruling is not None:
        claim.coverage_ruling = ruling
    if claim.status == ClaimStatus.REPORTED:
        claim.status = ClaimStatus.UNDER_REVIEW

    touched = _apply_quantification(ctx, claim, payload.damage_quantification)

    for line in payload.warranty_analysis:
        state = _warranty_status(line.status)
        if not line.code or state is None or claim.policy_id is None:
            continue
        warranty = db.scalars(
            select(Warranty).where(
                Warranty.broker_id == ctx.broker_id,
                Warranty.policy_id == claim.policy_id,
                Warranty.code == line.code.strip(),
            )
        ).first()
        if warranty is not None:
            warranty.status = state

    db.flush()
    return CommitResult(
        applied=True,
        target="claim",
        entity_type="claim",
        entity_id=claim.id,
        detail=f"Preliminary ruling applied ({touched} item(s) quantified)",
        extra={"coverage_ruling": claim.coverage_ruling.value, "items_updated": touched},
        warnings=ctx.warnings,
    )


def _commit_claim_final(ctx: _Ctx) -> CommitResult:
    """12/13 informe final -> closure columns + final per-partida figures."""
    db, payload = ctx.db, ctx.payload
    claim = _require_claim(ctx, payload.claim_number, "final report")

    ruling = _claim_ruling(payload.coverage_ruling)
    if ruling is not None:
        claim.coverage_ruling = ruling
    if payload.adjuster.name:
        claim.adjuster_name = payload.adjuster.name[:160]
    if payload.adjuster.registry:
        claim.adjuster_registry = payload.adjuster.registry[:32]

    settlement = payload.settlement
    if settlement.deductible_uf is not None:
        claim.deductible_uf = settlement.deductible_uf
    if settlement.indemnity_uf is not None:
        claim.settled_amount_uf = settlement.indemnity_uf
    if settlement.paid_uf is not None:
        claim.paid_amount_uf = settlement.paid_uf
    if payload.loss_ratio_pct is not None:
        claim.loss_ratio_pct = payload.loss_ratio_pct

    touched = _apply_quantification(ctx, claim, payload.final_material_damage)

    if ruling == ClaimRuling.REJECTED:
        claim.status = ClaimStatus.REJECTED
    elif settlement.paid_uf is not None:
        claim.status = ClaimStatus.PAID
    elif claim.status in (ClaimStatus.REPORTED, ClaimStatus.UNDER_REVIEW):
        claim.status = ClaimStatus.SETTLED

    db.flush()
    return CommitResult(
        applied=True,
        target="claim",
        entity_type="claim",
        entity_id=claim.id,
        detail=f"Final ruling applied ({touched} item(s) settled)",
        extra={
            "coverage_ruling": claim.coverage_ruling.value,
            "status": claim.status.value,
            "items_updated": touched,
        },
        warnings=ctx.warnings,
    )


def _commit_compliance_notice(ctx: _Ctx) -> CommitResult:
    """09A -> warranty.status + completed_on for every measure reported done."""
    db, payload = ctx.db, ctx.payload
    policy = _require_policy(ctx, payload.policy_number, "compliance notice")

    updated: list[int] = []
    for line in payload.measures_completed:
        if not line.code:
            continue
        row = _upsert_warranty(
            ctx,
            policy,
            code=line.code.strip(),
            title=line.title,
            requirement=line.requirement,
            status=_warranty_status(line.status) or WarrantyStatus.MET_ON_TIME,
            completed_on=line.due_date or payload.notice_date,
            verification=line.verification,
            actual_cost_uf=line.actual_cost_uf,
        )
        db.flush()
        updated.append(row.id)

    db.flush()
    return CommitResult(
        applied=True,
        target="warranty",
        entity_type="policy",
        entity_id=policy.id,
        detail=f"{len(updated)} warranty(ies) marked complete",
        extra={"warranty_ids": updated},
        warnings=ctx.warnings,
    )


def _commit_policy(ctx: _Ctx) -> CommitResult:
    """08 -> the issued policy: as-issued identity, money, children (§3.1).

    Money goes through the ONE implementation (:func:`reconcile_money`) — the
    same arithmetic the proposal and policy routers enforce. The mirror-diff
    keeps reading the extraction payloads, so nothing is reconciled across
    documents here (§3.2 rule 4).
    """
    db, payload = ctx.db, ctx.payload
    case = _require_case(ctx, "policy")

    policy = _resolve_policy(ctx, payload.policy_number)
    created = policy is None
    if created:
        insurer_ref = payload.insurer
        from app.services.ai import find_or_create_insurer

        try:
            insurer = find_or_create_insurer(
                db,
                cmf_code=insurer_ref.cmf_code,
                rut=insurer_ref.rut,
                legal_name=insurer_ref.legal_name,
                trade_name=insurer_ref.trade_name,
                broker_id=ctx.broker_id,
            )
        except ValueError as exc:
            raise CommitError(
                f"The issuing insurer could not be identified: {exc} — identity "
                "is matched by rut/cmf_code, never by name"
            ) from exc
        number = (payload.policy_number or "").strip()
        if not number:
            raise CommitError("The payload has no policy_number — a policy cannot be created without one")
        policy = Policy(
            broker_id=ctx.broker_id,
            client_id=case.client_id,
            placement_id=case.placement_id,
            insurance_line_id=case.insurance_line_id,
            insurer_id=insurer.id,
            policy_number=number,
            status=PolicyStatus.ACTIVE,
        )
        db.add(policy)
    elif payload.policy_number:
        policy.policy_number = payload.policy_number.strip()

    # --- Money: merged state through the single reconciler -> 422 on a break --
    premium = payload.premium
    merged = {
        field_name: (
            getattr(premium, field_name)
            if getattr(premium, field_name) is not None
            else getattr(policy, field_name)
        )
        for field_name in (
            "taxable_premium_uf",
            "exempt_premium_uf",
            "net_premium_uf",
            "vat_uf",
            "total_premium_uf",
        )
    }
    derived, errors = reconcile_money(merged)
    if errors:
        raise CommitError(errors)
    for field_name, value in merged.items():
        if value is not None:
            setattr(policy, field_name, value)
    for field_name, value in derived.items():
        if hasattr(policy, field_name):
            setattr(policy, field_name, value)

    # --- As-issued identity ---------------------------------------------------
    policy.case_file_id = case.id
    policy.source_document_id = ctx.document.id
    if payload.period_start_at is not None:
        policy.period_start_at = payload.period_start_at
        policy.start_date = payload.period_start_at.date()
    if payload.period_end_at is not None:
        policy.period_end_at = payload.period_end_at
        policy.end_date = payload.period_end_at.date()
    _sync_policy_period(policy)
    if payload.issue_date is not None:
        policy.issued_at = payload.issue_date
    if payload.coverage_modality:
        policy.cover_mode = payload.coverage_modality[:64]
    if payload.cmf_policy_code:
        policy.cmf_policy_code = payload.cmf_policy_code[:32]
    if payload.total_insured_amount_uf is not None:
        policy.insured_amount_uf = payload.total_insured_amount_uf
    if payload.aggregate_limit_uf is not None:
        policy.aggregate_limit_uf = payload.aggregate_limit_uf
    if payload.indemnity_limit:
        policy.indemnity_limit = payload.indemnity_limit
    if payload.valuation_basis:
        policy.insured_amount_semantics = payload.valuation_basis[:255]
    if payload.commission_pct is not None:
        policy.commission_pct = payload.commission_pct
    if payload.average_rate_permille is not None:
        policy.average_rate_permille = payload.average_rate_permille
    if payload.deductibles:
        policy.deductibles = {
            (line.peril or f"item_{index}"): {
                key: value
                for key, value in line.model_dump(mode="json").items()
                if value is not None
            }
            for index, line in enumerate(payload.deductibles)
        }
    if policy.status == PolicyStatus.DRAFT:
        policy.status = PolicyStatus.ACTIVE

    try:
        db.flush()
    except Exception as exc:  # IntegrityError: duplicate policy_number
        from sqlalchemy.exc import IntegrityError

        if isinstance(exc, IntegrityError):
            db.rollback()
            raise CommitError(
                f"Policy number '{payload.policy_number}' already exists for this broker"
            ) from exc
        raise

    # --- Children: replaced wholesale when the payload provides them ---------
    if payload.locations:
        for existing in list(policy.locations):
            db.delete(existing)
        db.flush()
        for row in payload.locations:
            name = row.name or row.address
            if not name:
                continue
            db.add(
                PolicyLocation(
                    policy_id=policy.id,
                    name=name[:255],
                    address=(row.address or None),
                    commune=(row.commune or None),
                    region=(row.region or None),
                    insured_amount_uf=row.insured_amount_uf,
                )
            )
    if payload.coverages or payload.exclusions:
        for existing in list(policy.coverage_items):
            db.delete(existing)
        db.flush()
        order = 0
        for line in payload.coverages:
            text = line.text or line.name or line.offered_condition
            if not text:
                continue
            db.add(
                CoverageItem(
                    policy_id=policy.id, kind=CoverageKind.COVERAGE,
                    description=text, sort_order=order,
                )
            )
            order += 1
        for line in payload.exclusions:
            if not line.text:
                continue
            db.add(
                CoverageItem(
                    policy_id=policy.id, kind=CoverageKind.EXCLUSION,
                    description=line.text, sort_order=order,
                )
            )
            order += 1

    warranty_ids: list[int] = []
    for index, line in enumerate(payload.warranties):
        if not (line.code or line.requirement or line.title):
            continue
        row = _upsert_warranty(
            ctx,
            policy,
            code=(line.code.strip() if line.code else None),
            title=line.title,
            requirement=line.requirement,
            category=line.category,
            deadline_days=line.deadline_days,
            due_date=line.due_date,
            budget_uf=line.budget_uf,
            source=WarrantySource.UNDERWRITING_WARRANTY,
            sort_order=index,
        )
        db.flush()
        warranty_ids.append(row.id)

    if payload.installments:
        ctx.warnings.append(
            "The policy's payment schedule was not committed — confirm the "
            "payment plan document (09) to create the collection ledger"
        )

    if case.policy_id is None:
        case.policy_id = policy.id

    db.flush()
    return CommitResult(
        applied=True,
        target="policy",
        entity_type="policy",
        entity_id=policy.id,
        detail=("Policy created from the issued document" if created else "Policy updated from the issued document"),
        extra={"created": created, "warranty_ids": warranty_ids},
        warnings=ctx.warnings,
    )


# =============================================================================
# The dispatcher
# =============================================================================


_COMMITTERS: dict[DocumentCategory, Callable[[_Ctx], CommitResult]] = {
    DocumentCategory.PROSPECT_REQUEST: _commit_prospect_request,
    DocumentCategory.TECHNICAL_BRIEF: _commit_technical_brief,
    DocumentCategory.SUBMISSION_LETTER: _commit_submission_letter,
    DocumentCategory.RESUBMISSION_LETTER: _commit_resubmission_letter,
    DocumentCategory.INSPECTION_REPORT: _commit_inspection_report,
    DocumentCategory.ENDORSEMENT_PROPOSAL: _commit_endorsement_proposal,
    DocumentCategory.ENDORSEMENT: _commit_endorsement_issued,
    DocumentCategory.PAYMENT_PLAN: _commit_payment_plan,
    DocumentCategory.COLLECTION_STATUS: _commit_collection_status,
    DocumentCategory.CLAIM_NOTICE: _commit_claim_notice,
    DocumentCategory.CLAIM_PRELIMINARY_REPORT: _commit_claim_preliminary,
    DocumentCategory.CLAIM_FINAL_REPORT: _commit_claim_final,
    DocumentCategory.COMPLIANCE_NOTICE: _commit_compliance_notice,
    DocumentCategory.POLICY: _commit_policy,
}

#: Categories that legitimately commit nothing, with the reason the UI shows.
_INFORMATIONAL: dict[DocumentCategory, str] = {
    DocumentCategory.BUSINESS_QUESTIONNAIRE: (
        "Informational: the questionnaire feeds the client/asset review screens; "
        "master-data changes are made there, not auto-committed"
    ),
    DocumentCategory.INSURED_VALUES_SCHEDULE: (
        "Informational: the value schedule feeds the quote's line items, which "
        "are edited on the quote (declared value must equal their sum)"
    ),
    DocumentCategory.LOSS_HISTORY: (
        "Informational: the certificate's own figures are kept verbatim on the "
        "extraction as loss-ratio evidence"
    ),
    DocumentCategory.RISK_ENGINEERING_PLAN: (
        "Informational until the policy exists: engineering measures become "
        "warranty rows on the issued policy"
    ),
    DocumentCategory.DECLINATION: (
        "Informational: a declination is recorded as market evidence; no "
        "proposal row is fabricated for an insurer that did not quote"
    ),
    DocumentCategory.CONDITIONAL_PRONOUNCEMENT: (
        "Informational: the conditions are review items; warranties are created "
        "on the issued policy"
    ),
    DocumentCategory.QUOTE_COMPARISON: (
        "Informational: the comparativo is a generated deliverable — the "
        "comparator reads the confirmed proposals directly"
    ),
    DocumentCategory.ISSUANCE_PROPOSAL: (
        "Informational: the confirmed 07 IS the mirror baseline — the "
        "mirror-diff reads this payload against the issued policy"
    ),
    DocumentCategory.TECHNICAL_RECOMMENDATION: (
        "Informational: the recommendation is the insured-facing letter; the "
        "case advances when the placement order is signed"
    ),
    DocumentCategory.BROKER_CLOSING_NOTE: (
        "Informational: the closing note summarises the settled claim; renewal "
        "cases are opened from the case-file screen"
    ),
}


#: Extra permission the confirm needs, per canonical category (spec §6.6:
#: "per prefill target"). ``Documents.Upload`` is the base gate at the router;
#: the target's own module must also allow the write.
COMMIT_PERMISSIONS: dict[DocumentCategory, tuple[str, str]] = {
    DocumentCategory.PROSPECT_REQUEST: ("Clients", "Edit"),
    DocumentCategory.TECHNICAL_BRIEF: ("Quotes", "Edit"),
    DocumentCategory.SUBMISSION_LETTER: ("Quotes", "Edit"),
    DocumentCategory.RESUBMISSION_LETTER: ("Quotes", "Edit"),
    DocumentCategory.INSPECTION_REPORT: ("Inspections", "Edit"),
    DocumentCategory.ENDORSEMENT_PROPOSAL: ("Endorsements", "Create"),
    DocumentCategory.ENDORSEMENT: ("Endorsements", "Edit"),
    DocumentCategory.PAYMENT_PLAN: ("Collections", "Edit"),
    DocumentCategory.COLLECTION_STATUS: ("Collections", "Edit"),
    DocumentCategory.CLAIM_NOTICE: ("Claims", "Create"),
    DocumentCategory.CLAIM_PRELIMINARY_REPORT: ("Claims", "Edit"),
    DocumentCategory.CLAIM_FINAL_REPORT: ("Claims", "Edit"),
    DocumentCategory.COMPLIANCE_NOTICE: ("Policies", "Edit"),
    DocumentCategory.POLICY: ("Policies", "Create"),
}


def commit_extraction(
    db: Session,
    *,
    extraction: Extraction,
    spec: Any,
    payload: Any,
    broker_id: int,
    user: User | None,
) -> CommitResult:
    """Dispatch a confirmed payload to its category's committer.

    Flushes, never commits: the caller owns the transaction and commits once
    the confirmation stamp is staged too. Raises :class:`CommitError` (-> 422)
    on any rule violation; unknown-but-registered categories degrade to a
    recorded, not-applied confirmation instead of failing the review.
    """
    category: DocumentCategory = spec.category

    reason = _INFORMATIONAL.get(category)
    if reason is not None:
        return CommitResult(
            applied=False,
            informational=True,
            target=spec.prefill_target,
            detail=reason,
        )

    committer = _COMMITTERS.get(category)
    if committer is None:
        # A registered category without a committer yet: record, don't apply.
        return CommitResult(
            applied=False,
            informational=True,
            target=spec.prefill_target,
            detail="This category has no automated commit yet — the reviewed payload was recorded",
        )

    document = db.scalars(
        select(Document).where(
            Document.id == extraction.document_id, Document.broker_id == broker_id
        )
    ).first()
    if document is None:
        raise CommitError("The extraction's source document is not in this workspace")

    ctx = _Ctx(
        db=db,
        broker_id=broker_id,
        user=user,
        extraction=extraction,
        payload=payload,
        document=document,
        case_file=_resolve_case_file(db, extraction, document, broker_id),
    )
    return committer(ctx)
