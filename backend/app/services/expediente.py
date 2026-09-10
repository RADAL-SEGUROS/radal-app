"""The **expediente completo** aggregator — one read of the whole grupo-cuenta.

This is the read model behind ``GET /case-files/{id}/expediente`` (and its PDF):
identity, the empresas on the folder, the five journey milestones with their
REAL completion dates, the antecedentes index, the comparación, the propuesta,
the pólizas, every document, the money core and the last bitácora entries.

Three rules shape it:

* **Nothing is invented.** ``completed_at`` is read off ``case_file_stage_event``
  and is ``null`` when the machine never recorded the move; a milestone's
  ``pending_reason`` is the SAME Spanish guard reason ``STAGE_GUARDS`` gives the
  transitions endpoint (translated at the source in v9 — never re-translated
  here); the money core goes through :func:`reconcile_money_verbose`, never
  through hand arithmetic.
* **Nothing is silently blank.** A section that does not exist yet is ``None``
  with a Spanish reason ("Pronto, al cerrar Comparación") so the overview can
  render the reason where the section would be.
* **Nothing leaks.** Every query filters ``broker_id`` and reaches only rows of
  the folder the caller already resolved; no document ever carries its S3 key
  (rule 8).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account_client import AccountClient
from app.models.account_group import AccountGroup
from app.models.activity import Activity, Note
from app.models.ai import Extraction
from app.models.broker_proposal import BrokerProposal
from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.client import Client
from app.models.comparison import Comparison, ComparisonSource, ComparisonStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseStage, EntityType, RecordExpedienteStatus
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.policy import Policy
from app.models.proposal import ProposalStatus
from app.models.record_expediente import RecordExpediente
from app.models.user import User
from app.schemas.expediente import (
    AccountExpediente,
    ExpedienteActivity,
    ExpedienteAntecedentes,
    ExpedienteAntecedentesSlot,
    ExpedienteCaseFile,
    ExpedienteClient,
    ExpedienteComparison,
    ExpedienteComparisonColumn,
    ExpedienteDocument,
    ExpedienteGroup,
    ExpedienteJourneyStep,
    ExpedienteMoney,
    ExpedientePolicy,
    ExpedienteProposal,
    ExpedienteRecommendation,
)
from app.schemas.extraction.common import parse_uf
from app.schemas.proposal import reconcile_money_verbose
from app.services import case_files as machine

__all__ = [
    "CASE_STATUS_ES",
    "MILESTONES",
    "missing_artifact_reason",
    "STAGE_LABELS_ES",
    "Milestone",
    "build_expediente",
    "expediente_pdf_data",
    "soon_reason",
]


# =============================================================================
# Copy (Spanish) — labels are OUTPUT, never identifiers (rule 1)
# =============================================================================

#: The account chain in Spanish, for the journey lines and the PDF.
STAGE_LABELS_ES: dict[CaseStage, str] = {
    CaseStage.LEAD: "Prospecto",
    CaseStage.INTAKE: "Antecedentes",
    CaseStage.PRE_UNDERWRITING: "Pre-suscripción",
    CaseStage.TECHNICAL_BASIS: "Bases técnicas",
    CaseStage.MARKET_SUBMISSION: "Envío al mercado",
    CaseStage.QUOTES_RECEIVED: "Cotizaciones recibidas",
    CaseStage.COMPARISON: "Comparación",
    CaseStage.INSURED_DECISION: "Decisión del asegurado",
    CaseStage.PROPOSAL_ISSUED: "Propuesta emitida",
    CaseStage.RATIFIED: "Propuesta ratificada",
    CaseStage.POLICY_ISSUED: "Póliza emitida",
    CaseStage.MIRROR_VALIDATION: "Validación espejo",
    CaseStage.ACTIVE: "Vigente",
    CaseStage.RENEWAL_REVIEW: "Revisión de renovación",
    CaseStage.CLOSED: "Cerrado",
}

#: The folder's own lifecycle, in Spanish — the PDF is copy, not identifiers.
CASE_STATUS_ES: dict[str, str] = {
    "open": "Abierto",
    "on_hold": "En espera",
    "won": "Ganado",
    "lost": "Perdido",
    "cancelled": "Anulado",
    "closed": "Cerrado",
}

_RECORD_STATUS_ES: dict[str, str] = {
    RecordExpedienteStatus.DRAFT.value: "Sin registrar",
    RecordExpedienteStatus.PROCESSING.value: "Consolidación en curso",
    RecordExpedienteStatus.REVIEW.value: "Borrador sugerido, falta validarlo",
    RecordExpedienteStatus.REGISTERED.value: "Registradas",
}


def _fmt_uf(value: Any) -> Any:
    """Chilean money formatting (``1120.1300`` → ``1.120,13``).

    Borrowed from the shared PDF helpers on purpose: a UF amount must read the
    same in a journey summary, in the propuesta card and in the PDF — one
    formatter, one look. Copy, not an identifier, so Spanish grouping applies.
    """
    from app.services.pdf_templates import _fmt_money

    parsed = parse_uf(value)
    return _fmt_money(parsed) if parsed is not None else value


def soon_reason(label: str) -> str:
    """The one "not yet, and here is why" line: ``Pronto, al cerrar X``."""
    return f"Pronto, al cerrar {label}"


def missing_artifact_reason(artifact: str) -> str:
    """The OTHER reason a block is empty: the milestone closed but the artifact
    was never created in Radal (a pre-v9 or imported folder — legitimate, not an
    error). Stated as fact, never as a failure.

    Never confuse the two: printing a prerequisite ("Pronto, al cerrar Bases
    Técnicas") next to a journey that already shows Comparación completed makes
    the page contradict itself, and the broker cannot tell which half to believe.
    """
    return f"La cuenta avanzó sin registrar {artifact} en Radal."


def _block_reason(
    *,
    present: bool,
    milestone_key: str,
    statuses: dict[str, str],
    artifact: str,
    prerequisite_label: str | None,
    not_started: str | None = None,
) -> str | None:
    """Why a block of the aggregate is empty, in Spanish — or ``None``.

    Three cases, in order: the block exists (no reason); the milestone that owns
    it is already COMPLETE, so the artifact simply was never registered; the
    milestone is still ahead, so the block is gated by the previous one.
    """
    if present:
        return None
    if statuses.get(milestone_key) == "complete":
        return missing_artifact_reason(artifact)
    if not_started is not None:
        return not_started
    return soon_reason(prerequisite_label) if prerequisite_label else None


# =============================================================================
# The five milestones of the v9 journey
# =============================================================================

@dataclass(frozen=True)
class Milestone:
    """One tab of the account: its key, its copy and the stages it covers."""

    key: str
    label: str
    #: The representative stage (what the milestone "is at" while in progress).
    stage: CaseStage
    #: Every stage of the account chain the milestone owns, in flow order.
    stages: tuple[CaseStage, ...]


#: Antecedentes → Bases Técnicas → Comparación → Propuesta → Pólizas.
#: Every account-chain stage belongs to exactly one milestone except ``active``
#: and ``closed``, which sit BEYOND the last one (so Pólizas reads complete once
#: the account is vigente).
MILESTONES: tuple[Milestone, ...] = (
    Milestone(
        key="antecedentes",
        label="Antecedentes",
        stage=CaseStage.INTAKE,
        stages=(
            CaseStage.RENEWAL_REVIEW,
            CaseStage.LEAD,
            CaseStage.INTAKE,
            CaseStage.PRE_UNDERWRITING,
        ),
    ),
    Milestone(
        key="technical_basis",
        label="Bases Técnicas",
        stage=CaseStage.TECHNICAL_BASIS,
        stages=(CaseStage.TECHNICAL_BASIS,),
    ),
    Milestone(
        key="comparison",
        label="Comparación",
        stage=CaseStage.COMPARISON,
        stages=(
            CaseStage.MARKET_SUBMISSION,
            CaseStage.QUOTES_RECEIVED,
            CaseStage.COMPARISON,
            CaseStage.INSURED_DECISION,
        ),
    ),
    Milestone(
        key="proposal",
        label="Propuesta",
        stage=CaseStage.PROPOSAL_ISSUED,
        stages=(CaseStage.PROPOSAL_ISSUED, CaseStage.RATIFIED),
    ),
    Milestone(
        key="policies",
        label="Pólizas",
        stage=CaseStage.POLICY_ISSUED,
        stages=(CaseStage.POLICY_ISSUED, CaseStage.MIRROR_VALIDATION),
    ),
)

#: Milestone key → the Spanish label, for the "Pronto, al cerrar X" lines.
MILESTONE_LABELS: dict[str, str] = {m.key: m.label for m in MILESTONES}


def _chain(case: CaseFile) -> tuple[CaseStage, ...]:
    return machine.CASE_STAGE_FLOW.get(case.kind, ())


def _index(chain: tuple[CaseStage, ...], stage: CaseStage | None) -> int:
    """Position on the chain. ``closed`` sits beyond the end; unknown is -1."""
    if stage is None:
        return -1
    if stage is CaseStage.CLOSED:
        return len(chain)
    try:
        return chain.index(stage)
    except ValueError:
        return -1


# =============================================================================
# Aggregation
# =============================================================================

def _insured_of(db: Session, client: Client | None) -> Insured | None:
    if client is None:
        return None
    return db.get(Insured, client.insured_id)


def _clients(db: Session, case: CaseFile) -> list[ExpedienteClient]:
    """Account membership: ``account_client`` ∪ placements, contratante first."""
    roles: dict[int, str] = {}
    for row in db.scalars(
        select(AccountClient).where(AccountClient.case_file_id == case.id)
    ).all():
        roles[int(row.client_id)] = row.role.value

    out: list[ExpedienteClient] = []
    for client_id in machine.account_member_client_ids(db, case):
        client = db.scalars(
            select(Client).where(
                Client.id == client_id, Client.broker_id == case.broker_id
            )
        ).first()
        if client is None:  # a foreign row never leaks into the membership list
            continue
        insured = _insured_of(db, client)
        is_contratante = client.id == case.client_id
        out.append(
            ExpedienteClient(
                id=client.id,
                rut=getattr(insured, "rut", None),
                legal_name=getattr(insured, "legal_name", None),
                trade_name=getattr(insured, "trade_name", None),
                role=roles.get(client.id, "policyholder" if is_contratante else "insured"),
                is_contratante=is_contratante,
            )
        )
    return out


def _group(db: Session, case: CaseFile) -> ExpedienteGroup | None:
    if case.account_group_id is None:
        return None
    group = db.scalars(
        select(AccountGroup).where(
            AccountGroup.id == case.account_group_id,
            AccountGroup.broker_id == case.broker_id,
        )
    ).first()
    if group is None:
        return None
    # The one place the group avatar is resolved (image icons resolve to a
    # scoped document URL) — reused, never re-implemented.
    from app.api.routers.account_groups import _icon_read

    return ExpedienteGroup(
        id=group.id,
        name=group.name,
        slug=group.slug,
        status=group.status,
        icon=_icon_read(db, group),
    )


def _antecedentes(
    db: Session, case: CaseFile, broker_id: int
) -> tuple[ExpedienteAntecedentes, list[Document]]:
    """The intake index. Documents are scoped by ``ai._antecedentes_documents``,
    so cotización / comparison categories never leak in (v9 error #4)."""
    from app.services import ai as ai_service

    documents = list(ai_service._antecedentes_documents(db, case, broker_id))
    by_category: dict[str, list[Document]] = {}
    for doc in documents:
        key = doc.category.value if doc.category else "other"
        by_category.setdefault(key, []).append(doc)

    schema_row = ai_service.resolve_account_line_record_schema(db, case, broker_id)
    recommended = list(getattr(schema_row, "recommended_files", None) or [])

    slots: list[ExpedienteAntecedentesSlot] = []
    claimed: set[int] = set()
    missing_required: list[str] = []
    for position, raw in enumerate(recommended, start=1):
        if not isinstance(raw, dict):
            continue
        category = raw.get("category")
        matched = by_category.get(str(category), []) if category else []
        for doc in matched:
            claimed.add(doc.id)
        label = (
            raw.get("label")
            or _category_label(category)
            or raw.get("doc_type")
            or f"Documento {position}"
        )
        required = bool(raw.get("required"))
        slot = ExpedienteAntecedentesSlot(
            key=str(raw.get("key") or category or f"slot_{position}"),
            label=str(label),
            category=str(category) if category else None,
            doc_type=raw.get("doc_type"),
            format=raw.get("format"),
            required=required,
            filled=bool(matched),
            documents_count=len(matched),
            document_ids=[doc.id for doc in matched],
            description=raw.get("description") or raw.get("explanation"),
        )
        slots.append(slot)
        if required and not matched:
            missing_required.append(slot.label)

    record = db.scalars(
        select(RecordExpediente).where(
            RecordExpediente.broker_id == broker_id,
            RecordExpediente.case_file_id == case.id,
        )
    ).first()

    document_ids = [doc.id for doc in documents]
    extractions = 0
    if document_ids:
        extractions = int(
            db.scalar(
                select(func.count(Extraction.id)).where(
                    Extraction.broker_id == broker_id,
                    Extraction.document_id.in_(document_ids),
                )
            )
            or 0
        )

    filled = sum(1 for slot in slots if slot.filled)
    payload = ExpedienteAntecedentes(
        ramo_name=getattr(schema_row, "name", None),
        record_status=(record.status.value if record else RecordExpedienteStatus.DRAFT.value),
        registered_at=(record.registered_at if record else None),
        slots=slots,
        recommended_total=len(slots),
        recommended_filled=filled,
        free_uploads_count=sum(1 for doc in documents if doc.id not in claimed),
        documents_count=len(documents),
        extractions_count=extractions,
        complete=bool(documents) and not missing_required,
        missing_required=missing_required,
    )
    return payload, documents


def _category_label(category: Any) -> str | None:
    """The Spanish label of a ``DocumentCategory`` value, or ``None``.

    Reuses the pack builder's ``CATEGORY_LABELS_ES`` — the one Spanish document
    vocabulary the server owns.
    """
    if not category:
        return None
    from app.models.document import DocumentCategory
    from app.services.packs import category_label

    try:
        return category_label(DocumentCategory(str(category)))
    except ValueError:
        return None


def _comparison(db: Session, case: CaseFile, broker_id: int) -> Comparison | None:
    """The account's live comparison (the newest that is not superseded)."""
    return db.scalars(
        select(Comparison)
        .where(
            Comparison.broker_id == broker_id,
            Comparison.case_file_id == case.id,
            Comparison.status != ComparisonStatus.SUPERSEDED,
        )
        .order_by(Comparison.id.desc())
    ).first()


def _comparison_read(
    db: Session, broker_id: int, comparison: Comparison
) -> ExpedienteComparison:
    matrix = comparison.aligned_matrix if isinstance(comparison.aligned_matrix, dict) else {}
    raw_columns = matrix.get("columns") or []
    recommendation = matrix.get("recommendation") or {}
    rec_sid = recommendation.get("recommended_comparison_source_id")

    # The insurer label + premium resolution the comparison PDF already owns.
    from app.api.routers.comparisons import _insurer_label, _premium_core

    columns: list[ExpedienteComparisonColumn] = []
    rec_label: str | None = None
    for position, column in enumerate(raw_columns, start=1):
        source_id = column.get("comparison_source_id")
        source = db.get(ComparisonSource, source_id) if source_id is not None else None
        label = _insurer_label(db, broker_id, column, source, position)
        premium = _premium_core(db, source)
        is_rec = source_id is not None and source_id == rec_sid
        if is_rec:
            rec_label = label
        columns.append(
            ExpedienteComparisonColumn(
                label=label,
                recommended=is_rec,
                wrong_file=bool(column.get("is_wrong_file")),
                total_premium_uf=parse_uf(getattr(premium, "total_premium_uf", None)),
            )
        )

    caveats = [str(c) for c in (recommendation.get("caveats") or []) if c]
    rec = None
    if rec_label or recommendation.get("rationale") or caveats:
        rec = ExpedienteRecommendation(
            pick_label=rec_label,
            rationale=recommendation.get("rationale"),
            caveats=caveats,
        )

    return ExpedienteComparison(
        id=comparison.id,
        status=comparison.status.value,
        canonical_version=comparison.canonical_version,
        entry_count=len(comparison.entries or []),
        columns=columns,
        recommendation=rec,
        pdf_document_id=comparison.pdf_document_id,
        updated_at=comparison.updated_at,
    )


def _broker_proposal(db: Session, case: CaseFile, broker_id: int) -> BrokerProposal | None:
    return db.scalars(
        select(BrokerProposal)
        .where(
            BrokerProposal.broker_id == broker_id,
            BrokerProposal.case_file_id == case.id,
        )
        .order_by(BrokerProposal.id.desc())
    ).first()


def _proposal_read(db: Session, bp: BrokerProposal) -> ExpedienteProposal:
    payload = bp.payload if isinstance(bp.payload, dict) else {}
    core = payload.get("core") if isinstance(payload.get("core"), dict) else {}

    insurer_name = None
    insurer_id = core.get("insurer_id")
    if insurer_id is not None:
        insurer = db.get(Insurer, insurer_id)
        if insurer is not None:
            insurer_name = insurer.trade_name or insurer.legal_name

    return ExpedienteProposal(
        id=bp.id,
        status=bp.status.value,
        is_ratified=bool(bp.is_ratified),
        ratified_at=bp.ratified_at,
        content_hash=bp.content_hash,
        insurer_id=insurer_id if isinstance(insurer_id, int) else None,
        insurer_name=insurer_name,
        insured_name=core.get("insured_name"),
        insured_rut=core.get("insured_rut"),
        coverage_start=_as_text(core.get("coverage_start")),
        coverage_end=_as_text(core.get("coverage_end")),
        net_premium_uf=_as_decimal(core.get("net_premium_uf")),
        vat_uf=_as_decimal(core.get("vat_uf")),
        total_premium_uf=_as_decimal(core.get("total_premium_uf")),
        commission_pct=_as_decimal(core.get("commission_pct")),
        pdf_document_id=bp.pdf_document_id,
    )


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a free payload may hold anything
        return None


def _policies(db: Session, case: CaseFile) -> list[ExpedientePolicy]:
    rows = machine.case_policies(db, case)
    rows.sort(key=lambda policy: policy.id)
    out: list[ExpedientePolicy] = []
    for policy in rows:
        insurer = db.get(Insurer, policy.insurer_id) if policy.insurer_id else None
        out.append(
            ExpedientePolicy(
                id=policy.id,
                policy_number=policy.policy_number,
                status=policy.status.value,
                insurer_id=policy.insurer_id,
                insurer_name=(
                    (insurer.trade_name or insurer.legal_name) if insurer else None
                ),
                start_date=policy.start_date,
                end_date=policy.end_date,
                insured_amount_uf=policy.insured_amount_uf,
                net_premium_uf=policy.net_premium_uf,
                vat_uf=policy.vat_uf,
                total_premium_uf=policy.total_premium_uf,
                issued_at=policy.issued_at,
            )
        )
    return out


def _documents(db: Session, case: CaseFile, broker_id: int) -> list[ExpedienteDocument]:
    """Every document of the folder — id, name, category, section, size. No key."""
    from app.api.routers.case_files import SECTION_LABELS_ES
    from app.services.packs import category_label

    rows = db.scalars(
        select(Document)
        .where(Document.case_file_id == case.id, Document.broker_id == broker_id)
        .order_by(Document.section, Document.id)
    ).all()
    out: list[ExpedienteDocument] = []
    for doc in rows:
        section = doc.section.value if doc.section else None
        out.append(
            ExpedienteDocument(
                id=doc.id,
                filename=doc.original_name,
                category=doc.category.value if doc.category else "other",
                category_label=category_label(doc.category),
                section=section,
                section_label=(SECTION_LABELS_ES.get(section) if section else None),
                document_code=doc.document_code,
                mime_type=doc.mime_type,
                size_bytes=doc.size_bytes,
                created_at=doc.created_at,
            )
        )
    return out


_MONEY_KEYS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
)


def _money_from_policies(policies: list[Policy]) -> dict[str, Any] | None:
    """Σ over the account's policies — the invariants are linear, so the sum of
    conforming policies conforms too."""
    values: dict[str, Any] = {}
    seen = False
    for key in _MONEY_KEYS:
        total: Decimal | None = None
        for policy in policies:
            raw = getattr(policy, key, None)
            if raw is None:
                continue
            total = Decimal(raw) if total is None else total + Decimal(raw)
        if total is not None:
            seen = True
            values[key] = total
    if not seen:
        return None
    # A commission percentage only means something for a single policy.
    if len(policies) == 1 and policies[0].commission_pct is not None:
        values["commission_pct"] = policies[0].commission_pct
    return values


def _money(
    db: Session,
    case: CaseFile,
    bp: BrokerProposal | None,
    accepted: list[Any] | None = None,
) -> ExpedienteMoney | None:
    """The account's money core, reconciled — never hand-computed.

    Preference order: the issued pólizas (the truth) → the propuesta core → the
    accepted insurer offer. ``None`` when no source carries a premium; WHY it is
    absent is the caller's call (it depends on the journey), not this function's.
    """
    source: str | None = None
    values: dict[str, Any] | None = None

    policies = machine.case_policies(db, case)
    if policies:
        values = _money_from_policies(policies)
        if values is not None:
            source = "policy"

    if values is None and bp is not None:
        payload = bp.payload if isinstance(bp.payload, dict) else {}
        core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
        candidate = {
            key: _as_decimal(core.get(key))
            for key in (*_MONEY_KEYS, "commission_pct")
            if core.get(key) is not None
        }
        if any(candidate.get(key) is not None for key in _MONEY_KEYS):
            values, source = candidate, "broker_proposal"

    if values is None:
        accepted = accepted or []
        if accepted:
            proposal = accepted[0]
            candidate = {
                key: getattr(proposal, key, None)
                for key in (*_MONEY_KEYS, "commission_pct")
                if getattr(proposal, key, None) is not None
            }
            if any(candidate.get(key) is not None for key in _MONEY_KEYS):
                values, source = candidate, "proposal"

    if values is None or source is None:
        return None

    derived, errors, warnings = reconcile_money_verbose(dict(values))
    merged = dict(values)
    for key, value in derived.items():
        merged.setdefault(key, value)

    messages = [
        f"{item['field']}: se esperaba {item['expected']}, se registró {item['received']}"
        for item in (*errors, *warnings)
    ]
    return ExpedienteMoney(
        source=source,  # type: ignore[arg-type]
        currency="UF",
        taxable_premium_uf=merged.get("taxable_premium_uf"),
        exempt_premium_uf=merged.get("exempt_premium_uf"),
        net_premium_uf=merged.get("net_premium_uf"),
        vat_uf=merged.get("vat_uf"),
        total_premium_uf=merged.get("total_premium_uf"),
        commission_pct=merged.get("commission_pct"),
        derived_fields=[key for key in derived if key not in values],
        warnings=messages,
    )


def _activity(
    db: Session, case: CaseFile, broker_id: int, limit: int
) -> list[ExpedienteActivity]:
    """The bitácora, newest first: stage events + activity rows + notes."""
    entries: list[ExpedienteActivity] = []
    names: dict[int, str] = {}

    def _name(user_id: int | None) -> str | None:
        if user_id is None:
            return None
        if user_id not in names:
            user = db.get(User, user_id)
            names[user_id] = getattr(user, "full_name", None) or ""
        return names[user_id] or None

    for event in db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case.id)
        .order_by(CaseFileStageEvent.occurred_at.desc(), CaseFileStageEvent.id.desc())
        .limit(limit)
    ).all():
        entries.append(
            ExpedienteActivity(
                kind="stage",
                occurred_at=event.occurred_at,
                title=STAGE_LABELS_ES.get(event.to_stage, event.to_stage.value),
                detail=event.note,
                user_id=event.user_id,
                user_name=_name(event.user_id),
                from_stage=event.from_stage.value if event.from_stage else None,
                to_stage=event.to_stage.value,
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
        .order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .limit(limit)
    ).all():
        entries.append(
            ExpedienteActivity(
                kind="activity",
                occurred_at=activity.occurred_at,
                title=activity.action,
                detail=activity.description,
                user_id=activity.user_id,
                user_name=_name(activity.user_id),
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
        .order_by(Note.id.desc())
        .limit(limit)
    ).all():
        entries.append(
            ExpedienteActivity(
                kind="note",
                occurred_at=note.created_at or case.opened_at,
                title="Nota interna" if note.is_internal else "Nota compartida",
                detail=note.body,
                user_id=note.author_id,
                user_name=_name(note.author_id),
            )
        )

    entries.sort(
        key=lambda item: (item.occurred_at is not None, item.occurred_at),
        reverse=True,
    )
    return entries[:limit]


# =============================================================================
# The journey
# =============================================================================

def _completion_events(db: Session, case: CaseFile) -> list[CaseFileStageEvent]:
    return list(
        db.scalars(
            select(CaseFileStageEvent)
            .where(CaseFileStageEvent.case_file_id == case.id)
            .order_by(CaseFileStageEvent.occurred_at.asc(), CaseFileStageEvent.id.asc())
        ).all()
    )


def _artifact_phrases(
    *,
    antecedentes: ExpedienteAntecedentes,
    comparison: ExpedienteComparison | None,
    proposal: ExpedienteProposal | None,
    policies: list[ExpedientePolicy],
    quotes_count: int,
    awarded_insurer: str | None,
    has_technical_brief: bool,
) -> dict[str, str | None]:
    """Per milestone: the Spanish clause for "this work already exists", or None.

    A folder can legitimately hold the artifact of a milestone its STAGE has not
    reached yet (the broker uploaded the cotizaciones before walking the stage
    machine forward). When that happens the step must not print a bare
    prerequisite under a summary that just listed the work — the two lines would
    contradict each other. This map is what lets the reason acknowledge it.
    """
    registered = antecedentes.record_status == RecordExpedienteStatus.REGISTERED.value
    return {
        "antecedentes": (
            "Los antecedentes ya están cargados"
            if antecedentes.documents_count
            else None
        ),
        "technical_basis": (
            "Las bases técnicas ya están cargadas"
            if (registered or has_technical_brief)
            else None
        ),
        "comparison": (
            "La comparación ya está cargada"
            if comparison is not None
            else ("Las cotizaciones ya están cargadas" if quotes_count else None)
        ),
        "proposal": (
            "La propuesta ya está registrada"
            if proposal is not None
            else (
                "La cotización adjudicada ya está registrada"
                if awarded_insurer
                else None
            )
        ),
        "policies": (
            (
                "Las pólizas ya están emitidas"
                if len(policies) > 1
                else "La póliza ya está emitida"
            )
            if policies
            else None
        ),
    }


def _journey(
    db: Session,
    case: CaseFile,
    *,
    antecedentes: ExpedienteAntecedentes,
    comparison: ExpedienteComparison | None,
    proposal: ExpedienteProposal | None,
    policies: list[ExpedientePolicy],
    quotes_count: int = 0,
    awarded_insurer: str | None = None,
    has_technical_brief: bool = False,
) -> list[ExpedienteJourneyStep]:
    chain = _chain(case)
    position = _index(chain, case.stage)
    events = _completion_events(db, case)
    artifacts = _artifact_phrases(
        antecedentes=antecedentes,
        comparison=comparison,
        proposal=proposal,
        policies=policies,
        quotes_count=quotes_count,
        awarded_insurer=awarded_insurer,
        has_technical_brief=has_technical_brief,
    )

    steps: list[ExpedienteJourneyStep] = []
    previous_label: str | None = None
    for milestone in MILESTONES:
        stages = [stage for stage in milestone.stages if stage in chain]
        if not stages:
            stages = [milestone.stage]
        indexes = [_index(chain, stage) for stage in stages]
        first, last = min(indexes), max(indexes)

        if position > last:
            status = "complete"
        elif first <= position <= last:
            status = "in_progress"
        else:
            status = "pending"

        # The completion date is a FACT off the stage events: the first move that
        # crossed beyond this milestone. Never inferred — a force-moved or
        # imported folder legitimately reads complete with a null date.
        completed_at = None
        if status == "complete":
            for event in events:
                if _index(chain, event.to_stage) > last:
                    completed_at = event.occurred_at
                    break

        steps.append(
            ExpedienteJourneyStep(
                key=milestone.key,  # type: ignore[arg-type]
                label=milestone.label,
                stage=milestone.stage,
                stages=stages,
                status=status,  # type: ignore[arg-type]
                completed_at=completed_at,
                summary=_milestone_summary(
                    milestone,
                    antecedentes=antecedentes,
                    comparison=comparison,
                    proposal=proposal,
                    policies=policies,
                    quotes_count=quotes_count,
                    awarded_insurer=awarded_insurer,
                    has_technical_brief=has_technical_brief,
                ),
                pending_reason=(
                    None
                    if status == "complete"
                    else _milestone_reason(
                        db,
                        case,
                        milestone,
                        chain=chain,
                        position=position,
                        status=status,
                        previous_label=previous_label,
                        artifact_phrase=artifacts.get(milestone.key),
                    )
                ),
            )
        )
        previous_label = milestone.label
    return steps


def _milestone_reason(
    db: Session,
    case: CaseFile,
    milestone: Milestone,
    *,
    chain: tuple[CaseStage, ...],
    position: int,
    status: str,
    previous_label: str | None,
    artifact_phrase: str | None = None,
) -> str | None:
    """Why the milestone is not closed yet, in Spanish.

    Three cases, and the FIRST one exists only so the step cannot contradict its
    own summary:

    * NOT reached but the artifact is ALREADY there (the broker loaded the
      cotizaciones before walking the stage machine forward) — say that, and
      name what actually closes the step: the folder leaving its current stage.
      A bare "Pronto, al cerrar X" under a summary that just counted four
      ofertas reads as a bug.
    * NOT reached and empty — "Pronto, al cerrar {milestone anterior}".
    * BEING WORKED ON — the machine's own guard reason (``STAGE_GUARDS``,
      Spanish at the source since v9, never re-translated here) for the first
      stage still to clear, falling back to the next step when nothing blocks.
    """
    if status == "pending":
        if artifact_phrase:
            here = STAGE_LABELS_ES.get(case.stage, case.stage.value)
            return (
                f"{artifact_phrase}; la etapa se cierra cuando el expediente "
                f"avance desde {here}."
            )
        return soon_reason(previous_label) if previous_label else None

    for stage in milestone.stages:
        index = _index(chain, stage)
        if index < 0 or index <= position:
            continue
        reason = machine.guard_reason(db, case, stage)
        if reason:
            return reason

    next_index = position + 1
    if 0 <= next_index < len(chain):
        label = STAGE_LABELS_ES.get(chain[next_index], chain[next_index].value)
        return f"En curso · el siguiente paso es {label}"
    return "En curso"


def _milestone_summary(
    milestone: Milestone,
    *,
    antecedentes: ExpedienteAntecedentes,
    comparison: ExpedienteComparison | None,
    proposal: ExpedienteProposal | None,
    policies: list[ExpedientePolicy],
    quotes_count: int = 0,
    awarded_insurer: str | None = None,
    has_technical_brief: bool = False,
) -> str | None:
    """A short Spanish factual line — counts, never adjectives."""
    if milestone.key == "antecedentes":
        if antecedentes.recommended_total:
            return (
                f"{antecedentes.recommended_filled} de "
                f"{antecedentes.recommended_total} archivos recomendados"
            )
        count = antecedentes.documents_count
        return f"{count} archivo{'s' if count != 1 else ''} cargado{'s' if count != 1 else ''}"

    if milestone.key == "technical_basis":
        label = _RECORD_STATUS_ES.get(antecedentes.record_status, antecedentes.record_status)
        if antecedentes.record_status != RecordExpedienteStatus.REGISTERED.value and has_technical_brief:
            # A folder imported from the corpus carries the 01 document without a
            # v9 ``record_expediente`` — say what is actually there.
            label = "Documento de bases técnicas adjunto"
        if antecedentes.extractions_count:
            return f"{label} · {antecedentes.extractions_count} extracción(es)"
        return label

    if milestone.key == "comparison":
        if comparison is None:
            if quotes_count:
                return f"{quotes_count} cotización(es) recibida(s), sin comparación armada"
            return "Sin comparación"
        parts = [f"{len(comparison.columns) or comparison.entry_count} oferta(s) comparada(s)"]
        pick = comparison.recommendation.pick_label if comparison.recommendation else None
        if pick:
            parts.append(f"recomendación: {pick}")
        return " · ".join(parts)

    if milestone.key == "proposal":
        if proposal is None:
            if awarded_insurer:
                return f"Cotización adjudicada · {awarded_insurer}, sin propuesta registrada"
            return "Sin propuesta"
        state = "ratificada" if proposal.is_ratified else "en borrador"
        parts = [f"Propuesta {state}"]
        if proposal.insurer_name:
            parts.append(proposal.insurer_name)
        if proposal.total_premium_uf is not None:
            # Chilean grouping, like every other money string on the page.
            parts.append(f"UF {_fmt_uf(proposal.total_premium_uf)}")
        return " · ".join(parts)

    if milestone.key == "policies":
        if not policies:
            return "Sin pólizas"
        return f"{len(policies)} póliza(s) emitida(s)"
    return None


# =============================================================================
# Public entry point
# =============================================================================

def build_expediente(
    db: Session, *, case: CaseFile, broker_id: int, activity_limit: int = 20
) -> AccountExpediente:
    """The whole aggregate for one already-resolved, tenant-scoped account.

    The caller (the router) owns the 404: it resolves ``case`` through
    ``get_case_or_404`` so ``CASE_VIEW_SCOPE`` narrowing applies here too.
    """
    line = (
        db.get(InsuranceLine, case.insurance_line_id)
        if case.insurance_line_id is not None
        else None
    )
    antecedentes, _intake_documents = _antecedentes(db, case, broker_id)

    comparison_row = _comparison(db, case, broker_id)
    comparison = (
        _comparison_read(db, broker_id, comparison_row) if comparison_row else None
    )
    bp = _broker_proposal(db, case, broker_id)
    proposal = _proposal_read(db, bp) if bp else None
    policies = _policies(db, case)
    documents = _documents(db, case, broker_id)

    # The inbound insurer offers of the folder — the money fallback AND the
    # honest journey summary for a folder imported before the v9 comparison.
    quotes = machine.case_proposals(db, case)
    accepted = [p for p in quotes if p.status == ProposalStatus.ACCEPTED]
    awarded_insurer = None
    if accepted and accepted[0].insurer_id is not None:
        insurer = db.get(Insurer, accepted[0].insurer_id)
        awarded_insurer = (
            (insurer.trade_name or insurer.legal_name) if insurer else None
        )
    money = _money(db, case, bp, accepted)

    header = ExpedienteCaseFile(
        id=case.id,
        reference=case.reference,
        title=case.title,
        kind=case.kind,
        stage=case.stage,
        status=case.status,
        origin=case.origin,
        period_start=case.period_start,
        period_end=case.period_end,
        period_label=case.period_label
        or machine.period_label_for(case.period_start, case.period_end),
        period_locked=machine.is_period_locked(db, case),
        opened_at=case.opened_at,
        closed_at=case.closed_at,
        due_at=case.due_at,
        insurance_line_id=case.insurance_line_id,
        insurance_line_name=getattr(line, "name", None),
        ramo_name=antecedentes.ramo_name,
        summary=case.summary,
    )
    group = _group(db, case)

    journey = _journey(
        db,
        case,
        antecedentes=antecedentes,
        comparison=comparison,
        proposal=proposal,
        policies=policies,
        quotes_count=len(quotes),
        awarded_insurer=awarded_insurer,
        has_technical_brief=any(
            document.category == DocumentCategory.TECHNICAL_BRIEF.value
            for document in documents
        ),
    )
    # The block reasons are derived FROM the journey, never in parallel with it:
    # a milestone the folder already closed can never be explained by a
    # prerequisite that is itself complete (that contradiction is what the page
    # showed on legacy folders). Once a milestone is complete, an empty block
    # means the artifact was never registered in Radal — say exactly that.
    statuses = {step.key: step.status for step in journey}

    return AccountExpediente(
        case_file=header,
        group=group,
        group_pending_reason=(
            None if group else "La cuenta aún no está asociada a un grupo"
        ),
        clients=_clients(db, case),
        journey=journey,
        antecedentes=antecedentes,
        antecedentes_pending_reason=_block_reason(
            present=bool(antecedentes.documents_count),
            milestone_key="antecedentes",
            statuses=statuses,
            artifact="antecedentes",
            prerequisite_label=None,
            not_started="Aún no se ha cargado ningún antecedente",
        ),
        comparison=comparison,
        comparison_pending_reason=_block_reason(
            present=comparison is not None,
            milestone_key="comparison",
            statuses=statuses,
            artifact="una comparación",
            prerequisite_label=MILESTONE_LABELS["technical_basis"],
        ),
        proposal=proposal,
        proposal_pending_reason=_block_reason(
            present=proposal is not None,
            milestone_key="proposal",
            statuses=statuses,
            artifact="una propuesta",
            prerequisite_label=MILESTONE_LABELS["comparison"],
        ),
        policies=policies,
        policies_pending_reason=_block_reason(
            present=bool(policies),
            milestone_key="policies",
            statuses=statuses,
            artifact="pólizas",
            prerequisite_label=MILESTONE_LABELS["proposal"],
        ),
        documents=documents,
        documents_count=len(documents),
        money=money,
        # The premium is known from the propuesta onwards, so the propuesta
        # milestone is the one that decides between the two explanations.
        money_pending_reason=_block_reason(
            present=money is not None,
            milestone_key="proposal",
            statuses=statuses,
            artifact="el desglose de prima",
            prerequisite_label=MILESTONE_LABELS["comparison"],
        ),
        activity=_activity(db, case, broker_id, activity_limit),
        generated_at=datetime.now(timezone.utc),
    )


# =============================================================================
# PDF data (pure shaping — the renderer never touches the DB)
# =============================================================================

_MILESTONE_STATUS_ES = {
    "complete": "Completado",
    "in_progress": "En curso",
    "pending": "Pendiente",
}


def _date_text(value: datetime | None) -> str | None:
    return value.date().isoformat() if value is not None else None


def _vigencia_text(header: ExpedienteCaseFile) -> str | None:
    if header.period_start and header.period_end:
        return f"{header.period_start.isoformat()} — {header.period_end.isoformat()}"
    return header.period_label


def expediente_pdf_data(
    db: Session, broker_id: int, case: CaseFile, payload: AccountExpediente
) -> dict[str, Any]:
    """Shape the aggregate into the plain dict the renderer takes (no DB inside
    the template). The comparison matrix is reused verbatim from the comparison
    PDF builder, so both artifacts always print the same table."""
    header = payload.case_file

    identity_rows = [
        {"label": "Grupo", "value": payload.group.name if payload.group else None, "required": True},
        {"label": "Expediente", "value": header.reference, "required": True},
        {"label": "Ramo", "value": header.ramo_name or header.insurance_line_name, "required": True},
        {"label": "Vigencia", "value": _vigencia_text(header), "required": True},
        {"label": "Etapa actual", "value": STAGE_LABELS_ES.get(header.stage, header.stage.value)},
        {"label": "Estado", "value": CASE_STATUS_ES.get(header.status.value, header.status.value)},
        {"label": "Apertura", "value": _date_text(header.opened_at)},
        {"label": "Cierre", "value": _date_text(header.closed_at)},
        {"label": "Vigencia bloqueada", "value": "Sí" if header.period_locked else "No"},
    ]

    clients_rows = [
        {
            "legal_name": member.legal_name,
            "rut": member.rut,
            "role": "Contratante" if member.is_contratante else "Asegurado",
        }
        for member in payload.clients
    ]

    journey_rows = [
        {
            "milestone": step.label,
            "state": _MILESTONE_STATUS_ES.get(step.status, step.status),
            "completed_at": _date_text(step.completed_at),
            "summary": step.summary,
            "reason": step.pending_reason,
        }
        for step in payload.journey
    ]

    antecedentes_rows: list[dict[str, Any]] = []
    if payload.antecedentes:
        for slot in payload.antecedentes.slots:
            antecedentes_rows.append(
                {
                    "file": slot.label,
                    "required": "Sí" if slot.required else "No",
                    "state": (
                        f"{slot.documents_count} archivo(s)" if slot.filled else "Falta"
                    ),
                }
            )

    comparison_data = None
    comparison_row = _comparison(db, case, broker_id)
    if comparison_row is not None:
        from app.api.routers.comparisons import _comparison_pdf_data

        comparison_data = _comparison_pdf_data(db, broker_id, comparison_row)

    propuesta_rows: list[dict[str, Any]] = []
    if payload.proposal is not None:
        proposal = payload.proposal
        propuesta_rows = [
            {"label": "Aseguradora", "value": proposal.insurer_name},
            {"label": "Asegurado", "value": proposal.insured_name},
            {"label": "RUT asegurado", "value": proposal.insured_rut},
            {
                "label": "Vigencia",
                "value": (
                    f"{proposal.coverage_start or '—'} — {proposal.coverage_end or '—'}"
                ),
            },
            {"label": "Prima total (UF)", "value": _fmt_uf(proposal.total_premium_uf)},
            {"label": "Estado", "value": "Ratificada" if proposal.is_ratified else "Borrador"},
            {"label": "Hash de contenido", "value": proposal.content_hash},
        ]

    policies_rows = [
        {
            "number": policy.policy_number,
            "insurer": policy.insurer_name,
            "period": (
                f"{policy.start_date.isoformat() if policy.start_date else '—'} — "
                f"{policy.end_date.isoformat() if policy.end_date else '—'}"
            ),
            "premium": _fmt_uf(policy.total_premium_uf),
        }
        for policy in payload.policies
    ]

    documents_rows = [
        {
            "name": document.filename,
            "category": document.category_label,
            "section": document.section_label or "—",
            "date": _date_text(document.created_at),
        }
        for document in payload.documents
    ]

    money_rows: list[dict[str, Any]] = []
    if payload.money is not None:
        money = payload.money
        money_rows = [
            {"label": "Prima afecta", "value": _fmt_uf(money.taxable_premium_uf)},
            {"label": "Prima exenta", "value": _fmt_uf(money.exempt_premium_uf)},
            {"label": "Prima neta", "value": _fmt_uf(money.net_premium_uf)},
            {"label": "IVA", "value": _fmt_uf(money.vat_uf)},
            {"label": "Prima total", "value": _fmt_uf(money.total_premium_uf)},
        ]

    return {
        "identity_rows": identity_rows,
        "clients_rows": clients_rows,
        "journey_rows": journey_rows,
        "antecedentes_rows": antecedentes_rows,
        "antecedentes_note": (
            f"{payload.antecedentes.recommended_filled} de "
            f"{payload.antecedentes.recommended_total} archivos recomendados · "
            f"{payload.antecedentes.free_uploads_count} archivo(s) libre(s)"
            if payload.antecedentes and payload.antecedentes.recommended_total
            else payload.antecedentes_pending_reason
        ),
        "comparison": comparison_data,
        "comparison_pending": payload.comparison_pending_reason,
        "propuesta_rows": propuesta_rows,
        "propuesta_pending": payload.proposal_pending_reason,
        "policies_rows": policies_rows,
        "policies_pending": payload.policies_pending_reason,
        "documents_rows": documents_rows,
        "money_rows": money_rows,
        "money_pending": payload.money_pending_reason,
        "money_source": (payload.money.source if payload.money else None),
    }
