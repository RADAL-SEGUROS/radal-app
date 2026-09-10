"""``/broker-proposals`` — the outbound broker propuesta (la propuesta).

TERMINOLOGY TRAP: this is NOT a ``Proposal`` (the insurer's inbound offer). It is
the artifact the broker assembles SOLELY from a comparison snapshot, ratifies,
and sends. Minting it snapshots the aligned terms into ``payload`` with a stable
``content_hash`` and best-effort advances the account toward PROPOSAL_ISSUED via
the existing transition machine (respecting the server-side guards; never forced).

RBAC reuses ``Proposals.Approve`` for both mint and ratify — issuing the propuesta
is an approval act, consistent with accepting an inbound proposal. Every query is
scoped to the caller's ``broker_id``; a foreign-tenant row is a 404, never a 403.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.ai import _ai_http_error
from app.api.routers.antecedentes import _cover_meta, _logo_datauri
from app.api.routers.documents import build_download_payload, store_generated_document
from app.models.activity import Note
from app.models.broker import Broker
from app.models.broker_proposal import BrokerProposal, BrokerProposalStatus
from app.models.case_file import CaseFile
from app.models.comparison import Comparison, ComparisonEntry, ComparisonStatus
from app.models.document import DocumentCategory
from app.models.enums import CaseStage, EntityType
from app.models.insurer import Insurer
from app.models.proposal import Proposal
from app.models.user import User
from app.schemas.broker_proposal import (
    BrokerProposalCreate,
    BrokerProposalRatify,
    BrokerProposalRead,
)
from app.schemas.document import DocumentDownload
from app.schemas.extraction.common import parse_uf
from app.services import ai as ai_service
from app.services import pdf_templates
from app.services.pdf_templates import _fmt_money

router = APIRouter(prefix="/broker-proposals", tags=["broker_proposals"])


# --- Internal helpers --------------------------------------------------------

def _get_broker_proposal(db: Session, broker_id: int, bp_id: int) -> BrokerProposal:
    bp = db.scalars(
        select(BrokerProposal).where(
            BrokerProposal.id == bp_id, BrokerProposal.broker_id == broker_id
        )
    ).first()
    if bp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Broker proposal {bp_id} not found",
        )
    return bp


def _content_hash(snapshot: dict[str, Any]) -> str:
    """A stable SHA-256 over the snapshot — the tamper / version fingerprint."""
    blob = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _advance_toward(
    db: Session, case: CaseFile, target: CaseStage, user: User
) -> None:
    """Best-effort walk the account forward to ``target`` via the real machine.

    Each step runs the server-side guards; the FIRST blocked step stops the walk
    with the stage left where the machine allows — the propuesta is minted either
    way (issuing it is never blocked by a downstream guard). Never forced.
    """
    from app.services import case_files as machine

    chain = machine.CASE_STAGE_FLOW.get(case.kind, ())
    if target not in chain or case.stage not in chain:
        return
    target_idx = chain.index(target)
    for _ in range(len(chain)):
        if case.stage not in chain:
            break
        cur_idx = chain.index(case.stage)
        if cur_idx >= target_idx:
            break
        next_stage = chain[cur_idx + 1]
        try:
            machine.apply_transition(
                db, case=case, to_stage=next_stage, user=user, note="Propuesta emitida"
            )
            db.commit()
            db.refresh(case)
        except machine.CaseTransitionError:
            db.rollback()
            db.refresh(case)
            break


# --- Mint --------------------------------------------------------------------

@router.post("", response_model=BrokerProposalRead, status_code=status.HTTP_201_CREATED)
def create_broker_proposal(
    payload: BrokerProposalCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Approve")),
) -> BrokerProposalRead:
    """Mint a propuesta from an ALIGNED comparison and its chosen winning offer.

    The propuesta is built SOLELY from the comparison: its aligned terms and the
    winning column are snapshotted into ``payload`` with a stable ``content_hash``
    (status=draft). The PDF is rendered in a later phase — ``pdf_document_id``
    stays null here so no Chromium is needed. The account is then best-effort
    advanced toward PROPOSAL_ISSUED.
    """
    comparison = db.scalars(
        select(Comparison).where(
            Comparison.id == payload.comparison_id,
            Comparison.broker_id == broker_id,
        )
    ).first()
    if comparison is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comparison {payload.comparison_id} not found",
        )
    if comparison.status != ComparisonStatus.ALIGNED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "comparison_not_aligned",
                "message": (
                    "Align the comparison before minting a propuesta from it "
                    "(POST /comparisons/{id}/align)."
                ),
            },
        )

    winner = db.scalars(
        select(Proposal).where(
            Proposal.id == payload.winning_proposal_id,
            Proposal.broker_id == broker_id,
        )
    ).first()
    if winner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal {payload.winning_proposal_id} not found",
        )

    # The propuesta is built SOLELY from the comparison: the winner must be one
    # of its promoted columns.
    in_comparison = db.scalars(
        select(ComparisonEntry.id).where(
            ComparisonEntry.comparison_id == comparison.id,
            ComparisonEntry.proposal_id == winner.id,
        )
    ).first()
    if in_comparison is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "winner_not_in_comparison",
                "message": (
                    "The winning proposal is not a promoted column of this "
                    "comparison. Promote it first (POST "
                    "/comparisons/{id}/entries/{entry_id}/promote)."
                ),
                "winning_proposal_id": winner.id,
                "comparison_id": comparison.id,
            },
        )

    comparison_snapshot: dict[str, Any] = {
        "comparison_id": comparison.id,
        "case_file_id": comparison.case_file_id,
        "canonical_version": comparison.canonical_version,
        "dictionary": comparison.dictionary,
        "aligned_matrix": comparison.aligned_matrix,
        "winning_proposal": {
            "id": winner.id,
            "insurer_id": winner.insurer_id,
            "taxable_premium_uf": str(winner.taxable_premium_uf)
            if winner.taxable_premium_uf is not None
            else None,
            "exempt_premium_uf": str(winner.exempt_premium_uf)
            if winner.exempt_premium_uf is not None
            else None,
            "net_premium_uf": str(winner.net_premium_uf)
            if winner.net_premium_uf is not None
            else None,
            "vat_uf": str(winner.vat_uf) if winner.vat_uf is not None else None,
            "total_premium_uf": str(winner.total_premium_uf)
            if winner.total_premium_uf is not None
            else None,
            "comprehensive_rate_permille": str(winner.comprehensive_rate_permille)
            if winner.comprehensive_rate_permille is not None
            else None,
        },
    }

    # Standardize the outbound propuesta: a validated minimum core + a free tail.
    # BLOCK-ON-PROVIDER-DOWN: an AIError is a clean provider error, never a 500.
    try:
        propuesta = ai_service.submit_propuesta(
            db, comparison=comparison, winner=winner, broker_id=broker_id, user=current_user
        )
    except ai_service.AIError as exc:
        raise _ai_http_error(exc) from exc
    # The premium arithmetic on the core is HARD (rate additivity is soft).
    if not propuesta.is_core_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "money_inconsistent",
                "message": (
                    "The propuesta's premium core is inconsistent. Chilean rules: "
                    "net = taxable + exempt; vat = 0.19 * taxable; total = net + vat."
                ),
                "errors": propuesta.money_errors,
            },
        )

    snapshot: dict[str, Any] = {
        "core": propuesta.core,
        "additional": propuesta.additional,
        "comparison_snapshot": comparison_snapshot,
    }

    bp = BrokerProposal(
        broker_id=broker_id,
        case_file_id=comparison.case_file_id,
        comparison_id=comparison.id,
        winning_proposal_id=winner.id,
        content_hash=_content_hash(snapshot),
        payload=snapshot,
        status=BrokerProposalStatus.DRAFT,
    )
    db.add(bp)

    # Winner rationale reuses the note system (no dedicated column). Attach it to
    # the account case file — there is no broker_proposal EntityType.
    if payload.winner_note and comparison.case_file_id is not None:
        db.add(
            Note(
                broker_id=broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=comparison.case_file_id,
                author_id=current_user.id,
                body=payload.winner_note,
                is_internal=True,
            )
        )
    db.commit()
    db.refresh(bp)

    # Best-effort stage advance AFTER the propuesta is persisted, so a blocked
    # guard never loses the artifact.
    if comparison.case_file_id is not None:
        case = db.scalars(
            select(CaseFile).where(
                CaseFile.id == comparison.case_file_id,
                CaseFile.broker_id == broker_id,
            )
        ).first()
        if case is not None:
            _advance_toward(db, case, CaseStage.PROPOSAL_ISSUED, current_user)

    db.refresh(bp)
    return BrokerProposalRead.model_validate(bp)


@router.get("", response_model=list[BrokerProposalRead])
def list_broker_proposals(
    case_file_id: int = Query(..., description="The account case file to list propuestas for"),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
) -> list[BrokerProposalRead]:
    """List an account's propuesta(s), newest-first.

    Scoped to the caller's ``broker_id``; a foreign-tenant (or missing) case file
    is a 404, never a 403 — the same pattern every path uses. This is the
    authoritative source of a case's current propuesta(s) (it retires a fragile
    frontend ``localStorage`` workaround), so it must reliably return them.
    """
    case = db.scalars(
        select(CaseFile).where(
            CaseFile.id == case_file_id, CaseFile.broker_id == broker_id
        )
    ).first()
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case file {case_file_id} not found",
        )
    rows = db.scalars(
        select(BrokerProposal)
        .where(
            BrokerProposal.broker_id == broker_id,
            BrokerProposal.case_file_id == case.id,
        )
        .order_by(BrokerProposal.id.desc())
    ).all()
    return [BrokerProposalRead.model_validate(bp) for bp in rows]


@router.get("/{bp_id}", response_model=BrokerProposalRead)
def get_broker_proposal(
    bp_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
) -> BrokerProposalRead:
    return BrokerProposalRead.model_validate(_get_broker_proposal(db, broker_id, bp_id))


@router.post("/{bp_id}/ratify", response_model=BrokerProposalRead)
def ratify_broker_proposal(
    bp_id: int,
    payload: BrokerProposalRatify | None = Body(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Approve")),
) -> BrokerProposalRead:
    """Manual ratification: freeze the artifact and stamp who ratified it.

    The ratified ``content_hash`` is frozen as-is — a later payload edit would
    change the recomputed hash and reveal the tamper.
    """
    payload = payload or BrokerProposalRatify()
    bp = _get_broker_proposal(db, broker_id, bp_id)
    if bp.is_ratified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_ratified",
                "message": f"Broker proposal {bp.id} is already ratified.",
            },
        )
    bp.is_ratified = True
    bp.ratified_at = datetime.now(timezone.utc)
    bp.ratified_by_id = current_user.id
    bp.status = BrokerProposalStatus.RATIFIED
    # Freeze the ratified hash = the current content hash.
    if bp.payload is not None:
        bp.content_hash = _content_hash(bp.payload)

    if payload.note and bp.case_file_id is not None:
        db.add(
            Note(
                broker_id=broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=bp.case_file_id,
                author_id=current_user.id,
                body=payload.note,
                is_internal=True,
            )
        )
    db.commit()
    db.refresh(bp)
    return BrokerProposalRead.model_validate(bp)


# --- Branded PDF -------------------------------------------------------------

# The premium core rows of the propuesta money table, in display order.
_PROPUESTA_MONEY = (
    ("taxable_premium_uf", "Prima afecta"),
    ("exempt_premium_uf", "Prima exenta"),
    ("net_premium_uf", "Prima neta"),
    ("vat_uf", "IVA"),
    ("total_premium_uf", "Prima total"),
    ("comprehensive_rate_permille", "Tasa integral (‰)"),
)


def _fmt_uf(value: Any) -> Any:
    if value is None:
        return None
    parsed = parse_uf(value)
    return _fmt_money(parsed) if parsed is not None else value


def _vigencia_sentence(core: dict) -> str | None:
    start = core.get("coverage_start")
    end = core.get("coverage_end")
    if not start and not end:
        return None
    return f"{start or '—'} — {end or '—'}"


def _propuesta_pdf_data(db: Session, broker_id: int, bp: BrokerProposal) -> dict:
    """Shape the propuesta payload into the plain dict ``render_propuesta_html`` takes."""
    payload = bp.payload if isinstance(bp.payload, dict) else {}
    core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
    additional = payload.get("additional")
    snapshot = payload.get("comparison_snapshot") if isinstance(payload.get("comparison_snapshot"), dict) else {}

    insurer_name = None
    insurer_id = core.get("insurer_id")
    if insurer_id is not None:
        insurer = db.get(Insurer, insurer_id)
        if insurer is not None:
            insurer_name = insurer.trade_name or insurer.legal_name

    identity_rows = [
        {"label": "Aseguradora", "value": insurer_name, "required": True},
        {"label": "Asegurado", "value": core.get("insured_name"), "required": True},
        {"label": "RUT asegurado", "value": core.get("insured_rut")},
        {"label": "Vigencia", "value": _vigencia_sentence(core), "required": True},
        {"label": "Validez (días hábiles)", "value": core.get("validity_business_days")},
        {"label": "Comisión (%)", "value": core.get("commission_pct")},
    ]

    money_rows = [
        {"label": label, "value": _fmt_uf(core.get(key))} for key, label in _PROPUESTA_MONEY
    ]

    winning_rows: list[dict] = []
    winner = snapshot.get("winning_proposal") if isinstance(snapshot.get("winning_proposal"), dict) else {}
    if winner:
        winning_rows = [
            {"label": "Prima total (UF)", "value": _fmt_uf(winner.get("total_premium_uf"))},
            {"label": "Prima neta (UF)", "value": _fmt_uf(winner.get("net_premium_uf"))},
            {"label": "IVA (UF)", "value": _fmt_uf(winner.get("vat_uf"))},
            {"label": "Tasa integral (‰)", "value": _fmt_uf(winner.get("comprehensive_rate_permille"))},
        ]

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    items = additional if isinstance(additional, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        group = str(item.get("group") or "Otros")
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append(
            {
                "label": item.get("label"),
                "value": item.get("value"),
                "verbatim": item.get("verbatim"),
            }
        )
    additional_groups = [{"group": group, "items": groups[group]} for group in order]

    return {
        "identity_rows": identity_rows,
        "money_rows": money_rows,
        "winning_rows": winning_rows,
        "additional_groups": additional_groups,
        "content_hash": bp.content_hash,
        "ratified": bool(bp.is_ratified),
    }


@router.get("/{bp_id}/pdf", response_model=DocumentDownload)
async def broker_proposal_pdf(
    bp_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "View")),
) -> DocumentDownload:
    """Generate (or regenerate) the branded propuesta PDF as a download link.

    The propuesta's validated core, winning-quote summary and free ``additional``
    tail are rendered onto the shared branded shell; the ``content_hash`` and the
    ratified state ride in the header/state callout. Stored as a ``proposal_pack``
    document (rule 8: its S3 key lives only on that row) and linked back onto
    ``broker_proposal.pdf_document_id``.
    """
    bp = _get_broker_proposal(db, broker_id, bp_id)

    case = None
    if bp.case_file_id is not None:
        case = db.scalars(
            select(CaseFile).where(
                CaseFile.id == bp.case_file_id, CaseFile.broker_id == broker_id
            )
        ).first()

    broker = db.get(Broker, broker_id)
    state = "Ratificada" if bp.is_ratified else "Borrador"
    hash_tag = (bp.content_hash or "")[:8]
    branding = {
        "radal_wordmark": "Radal.",
        "broker_logo_datauri": _logo_datauri(broker) if broker else None,
        "broker_name": (broker.trade_name or broker.legal_name) if broker else None,
        "broker_rut": broker.rut if broker else None,
        "cmf_code": broker.cmf_code if broker else None,
        "cover_meta": _cover_meta(case) if case is not None else [],
        "eyebrow": f"Propuesta · {state}",
        "running_title": f"Propuesta · {hash_tag}" if hash_tag else "Propuesta",
    }
    insured = getattr(getattr(case, "client", None), "insured", None)
    title = getattr(insured, "legal_name", None) or (case.title if case else None) or "Propuesta"

    html = pdf_templates.render_propuesta_html(
        title=title,
        branding=branding,
        data=_propuesta_pdf_data(db, broker_id, bp),
    )

    # Function-scoped import so pytest collection never needs Chromium.
    from app.services.pdf import PDFGenerationError, render_html_to_pdf

    try:
        data = await render_html_to_pdf(html)
    except PDFGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo generar el PDF: {exc}",
        ) from exc

    document = store_generated_document(
        db,
        broker_id=broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=bp.case_file_id or bp.id,
        category=DocumentCategory.PROPOSAL_PACK,
        data=data,
        original_name=f"propuesta-{bp.id}.pdf",
        mime_type="application/pdf",
        key=f"broker-proposals/{bp.id}/propuesta.pdf",
        uploaded_by_id=current_user.id,
    )
    bp.pdf_document_id = document.id
    db.commit()
    db.refresh(document)
    return build_download_payload(document)


__all__ = ["router"]
