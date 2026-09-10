"""``/comparisons`` — the dynamic, incremental proposal comparison milestone.

Offers arrive one at a time. Each uploaded cotización is read DYNAMICALLY by
``extract_budget_proposal`` (one extraction row per attempt, rule 6), cached
per-column on ``comparison_source``, and shown in the grid — a wrong file is a
VISIBLE rejected column, never a 422. ``align`` re-aligns the cached compact
facets over a monotonic canonical dictionary. ``promote`` is the explicit human
step that turns a column's fixed money core into a real inbound ``proposal`` so
accept / offering keep working (suggest -> confirm -> commit).

RBAC reuses the ``Proposals`` module (View / Create / Edit; promote =
``Proposals.Approve``); every query is scoped to the caller's ``broker_id`` and a
foreign-tenant row is a 404, never a 403.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.ai import _ai_http_error
from app.api.routers.antecedentes import _cover_meta, _logo_datauri
from app.api.routers.documents import build_download_payload, store_generated_document
from app.models.broker import Broker
from app.models.case_file import CaseFile
from app.models.comparison import (
    Comparison,
    ComparisonEntry,
    ComparisonSource,
    ComparisonStatus,
)
from app.models.document import Document, DocumentCategory
from app.models.enums import EntityType
from app.models.insurer import Insurer
from app.models.proposal import Proposal, ProposalOrigin, ProposalStatus
from app.models.quote import QuoteRequest, QuoteRequestStatus
from app.models.user import User
from app.schemas.document import DocumentDownload
from app.services import pdf_templates
from app.services.pdf_templates import _fmt_money
from app.schemas.extraction.common import parse_uf
from app.schemas.comparison import (
    ComparisonAlignmentResult,
    ComparisonCreate,
    ComparisonEntryCreate,
    ComparisonEntryRead,
    ComparisonEntryResult,
    ComparisonEntryUpdate,
    ComparisonPremiumCore,
    ComparisonPromote,
    ComparisonPromoteResult,
    ComparisonRead,
)
from app.schemas.proposal import reconcile_money_verbose
from app.services import ai as ai_service

router = APIRouter(prefix="/comparisons", tags=["comparisons"])

# The fixed money core columns the promote step reconciles (proposal names).
_MONEY_KEYS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
)

# The fixed money/period core surfaced per column PRE-promotion (display only —
# read straight from the persisted ``BudgetProposalExtraction`` parse, never
# re-extracted and never re-validated here).
_PREMIUM_KEYS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
    "commission_pct",
    "validity_business_days",
    "period_start_at",
    "period_end_at",
    "coverage_start",
    "coverage_end",
)


# --- Internal helpers --------------------------------------------------------

def _first_present(*values: Any) -> str | None:
    """The first non-empty (trimmed) string among ``values``, else ``None``.

    Encodes the promote precedence for insurer identity: a reviewer-supplied
    value wins over the extraction-parsed one, and a blank string counts as
    absent.
    """
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _get_comparison(db: Session, broker_id: int, comparison_id: int) -> Comparison:
    comparison = db.scalars(
        select(Comparison).where(
            Comparison.id == comparison_id, Comparison.broker_id == broker_id
        )
    ).first()
    if comparison is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comparison {comparison_id} not found",
        )
    return comparison


def _get_case(db: Session, broker_id: int, case_file_id: int) -> CaseFile:
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
    return case


def _get_document(db: Session, broker_id: int, document_id: int) -> Document:
    document = db.scalars(
        select(Document).where(
            Document.id == document_id, Document.broker_id == broker_id
        )
    ).first()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found in this workspace",
        )
    return document


def _get_entry(db: Session, comparison: Comparison, entry_id: int) -> ComparisonEntry:
    """A comparison entry is a PURE CHILD — its tenant scope is its parent's."""
    entry = db.scalars(
        select(ComparisonEntry).where(
            ComparisonEntry.id == entry_id,
            ComparisonEntry.comparison_id == comparison.id,
        )
    ).first()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comparison entry {entry_id} not found on comparison {comparison.id}",
        )
    return entry


def _source_of(db: Session, entry: ComparisonEntry) -> ComparisonSource | None:
    if entry.comparison_source_id is None:
        return None
    return db.get(ComparisonSource, entry.comparison_source_id)


def _document_of_source(db: Session, source: ComparisonSource | None) -> int | None:
    """The uploaded doc behind a column, reached through its cached extraction."""
    if source is None or source.source_extraction_id is None:
        return None
    from app.models.ai import Extraction

    extraction = db.get(Extraction, source.source_extraction_id)
    return extraction.document_id if extraction is not None else None


def _premium_core(
    db: Session, source: ComparisonSource | None
) -> ComparisonPremiumCore | None:
    """The fixed money/period core, read from the column's persisted parse.

    Display only: it surfaces what the entry already extracted BEFORE the column
    is promoted to a real ``proposal``. Null for a wrong-file column or one with
    no readable parse — never re-extracts, never re-validates money invariants.
    """
    if source is None or source.is_wrong_file or source.source_extraction_id is None:
        return None
    from app.models.ai import Extraction

    extraction = db.get(Extraction, source.source_extraction_id)
    if extraction is None or not isinstance(extraction.parsed, dict):
        return None
    parsed = extraction.parsed
    return ComparisonPremiumCore(**{key: parsed.get(key) for key in _PREMIUM_KEYS})


def _entry_read(db: Session, entry: ComparisonEntry) -> ComparisonEntryRead:
    """Flatten a column + its cached source verdict for the grid."""
    source = _source_of(db, entry)
    return ComparisonEntryRead(
        id=entry.id,
        comparison_id=entry.comparison_id,
        proposal_id=entry.proposal_id,
        comparison_source_id=entry.comparison_source_id,
        is_recommended=entry.is_recommended,
        sort_order=entry.sort_order,
        is_wrong_file=bool(source.is_wrong_file) if source else False,
        wrong_file_reason=source.wrong_file_reason if source else None,
        source_extraction_id=source.source_extraction_id if source else None,
        document_id=_document_of_source(db, source),
        facets=source.facets if source else None,
        premium=_premium_core(db, source),
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _comparison_read(db: Session, comparison: Comparison) -> ComparisonRead:
    entries = db.scalars(
        select(ComparisonEntry)
        .where(ComparisonEntry.comparison_id == comparison.id)
        .order_by(ComparisonEntry.sort_order, ComparisonEntry.id)
    ).all()
    read = ComparisonRead.model_validate(comparison)
    read.entries = [_entry_read(db, entry) for entry in entries]
    # The AI recommendation is snapshotted inside aligned_matrix (no DB column):
    # surface it as a first-class field so the frontend need not dig for it.
    if isinstance(comparison.aligned_matrix, dict):
        read.recommendation = comparison.aligned_matrix.get("recommendation")
    return read


def _compact_facets(payload: Any | None) -> list[dict[str, Any]]:
    """The compact per-facet cache: label + description + group re-aligned later."""
    if payload is None:
        return []
    facets = getattr(payload, "facets", None) or []
    compact: list[dict[str, Any]] = []
    for facet in facets:
        if hasattr(facet, "model_dump"):
            compact.append(facet.model_dump(mode="json"))
        elif isinstance(facet, dict):
            compact.append(facet)
    return compact


def _live_sources(db: Session, comparison: Comparison) -> list[ComparisonSource]:
    """Every cached source column of the comparison, in column order."""
    entries = db.scalars(
        select(ComparisonEntry)
        .where(ComparisonEntry.comparison_id == comparison.id)
        .order_by(ComparisonEntry.sort_order, ComparisonEntry.id)
    ).all()
    sources: list[ComparisonSource] = []
    for entry in entries:
        source = _source_of(db, entry)
        if source is not None:
            sources.append(source)
    return sources


# --- Comparison lifecycle ----------------------------------------------------

@router.post("", response_model=ComparisonRead, status_code=status.HTTP_201_CREATED)
def create_comparison(
    payload: ComparisonCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Create")),
) -> ComparisonRead:
    """Create (or return the live) comparison for an account's COMPARISON stage.

    One DRAFT/ALIGNED comparison is kept live per case file; a repeat call
    returns it rather than spawning a duplicate (re-runs are a history the align
    step versions, not a fresh header per click).
    """
    case = _get_case(db, broker_id, payload.case_file_id)
    placement_id = payload.placement_id if payload.placement_id is not None else case.placement_id

    existing = db.scalars(
        select(Comparison)
        .where(
            Comparison.broker_id == broker_id,
            Comparison.case_file_id == case.id,
            Comparison.status != ComparisonStatus.SUPERSEDED,
        )
        .order_by(Comparison.id.desc())
    ).first()
    if existing is not None:
        return _comparison_read(db, existing)

    comparison = Comparison(
        broker_id=broker_id,
        case_file_id=case.id,
        placement_id=placement_id,
        status=ComparisonStatus.DRAFT,
        canonical_version=1,
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return _comparison_read(db, comparison)


@router.get("/{comparison_id}", response_model=ComparisonRead)
def get_comparison(
    comparison_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "View")),
) -> ComparisonRead:
    """Header + columns + aligned matrix + dictionary + per-column verdicts."""
    comparison = _get_comparison(db, broker_id, comparison_id)
    return _comparison_read(db, comparison)


# --- Entries -----------------------------------------------------------------

@router.post(
    "/{comparison_id}/entries",
    response_model=ComparisonEntryResult,
    status_code=status.HTTP_201_CREATED,
)
def add_entry(
    comparison_id: int,
    payload: ComparisonEntryCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Create")),
) -> ComparisonEntryResult:
    """Read one uploaded cotización dynamically and add it as a grid column.

    The document is created through the documents flow first; here it is read
    (``extract_budget_proposal``), cached on a fresh ``comparison_source`` (no
    ``proposal_id`` yet — that arrives only on promote), and shown as a column.
    A ``not_a_proposal`` verdict is returned as a VISIBLE rejection, not a 422.
    """
    comparison = _get_comparison(db, broker_id, comparison_id)
    document = _get_document(db, broker_id, payload.document_id)

    try:
        result = ai_service.extract_budget_proposal(
            db, document_id=document.id, broker_id=broker_id, user=current_user
        )
    except ai_service.AIError as exc:
        raise _ai_http_error(exc) from exc

    source = ComparisonSource(
        broker_id=broker_id,
        proposal_id=None,
        source_extraction_id=result.extraction_id,
        facets=_compact_facets(result.payload),
        is_wrong_file=result.is_wrong_file,
        wrong_file_reason=result.rejection_reason if result.is_wrong_file else None,
        extracted_at=datetime.now(timezone.utc),
    )
    db.add(source)
    db.flush()

    next_order = int(
        db.scalar(
            select(func.coalesce(func.max(ComparisonEntry.sort_order), -1)).where(
                ComparisonEntry.comparison_id == comparison.id
            )
        )
        or -1
    ) + 1
    entry = ComparisonEntry(
        comparison_id=comparison.id,
        proposal_id=None,
        comparison_source_id=source.id,
        sort_order=next_order,
    )
    db.add(entry)
    # A new column supersedes the last alignment — it must be re-run.
    if comparison.status == ComparisonStatus.ALIGNED:
        comparison.status = ComparisonStatus.DRAFT
    db.commit()
    db.refresh(entry)

    return ComparisonEntryResult(
        entry=_entry_read(db, entry),
        extraction_id=result.extraction_id,
        document_type=result.document_type,
        is_wrong_file=result.is_wrong_file,
        rejection_reason=result.rejection_reason,
        warnings=result.warnings,
    )


@router.patch(
    "/{comparison_id}/entries/{entry_id}", response_model=ComparisonEntryRead
)
def update_entry(
    comparison_id: int,
    entry_id: int,
    payload: ComparisonEntryUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> ComparisonEntryRead:
    """Recommend / reorder a column, or override its cached facets by hand."""
    comparison = _get_comparison(db, broker_id, comparison_id)
    entry = _get_entry(db, comparison, entry_id)
    data = payload.model_dump(exclude_unset=True)

    if "is_recommended" in data and data["is_recommended"] is not None:
        entry.is_recommended = data["is_recommended"]
    if "sort_order" in data and data["sort_order"] is not None:
        entry.sort_order = data["sort_order"]
    if "facets" in data and data["facets"] is not None:
        source = _source_of(db, entry)
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "no_source",
                    "message": "This column has no cached source to override.",
                },
            )
        source.facets = data["facets"]
        # A human override supersedes the last alignment.
        if comparison.status == ComparisonStatus.ALIGNED:
            comparison.status = ComparisonStatus.DRAFT
    db.commit()
    db.refresh(entry)
    return _entry_read(db, entry)


@router.delete(
    "/{comparison_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_entry(
    comparison_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Edit")),
) -> Response:
    """Remove a column (e.g. a rejected wrong-file upload) from the grid."""
    comparison = _get_comparison(db, broker_id, comparison_id)
    entry = _get_entry(db, comparison, entry_id)
    source = _source_of(db, entry)
    db.delete(entry)
    # The per-column cache is only useful through its entry; drop it too, but
    # keep it if it was already promoted to a proposal (provenance for accept).
    if source is not None and source.proposal_id is None:
        db.delete(source)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Align -------------------------------------------------------------------

@router.post("/{comparison_id}/align", response_model=ComparisonAlignmentResult)
def align(
    comparison_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Edit")),
) -> ComparisonAlignmentResult:
    """Holistically standardize the cached columns into one table + recommendation.

    Delegates to ``submit_comparison`` — the model sees ALL cotización readings at
    once plus the prior comparison structure (carried forward), and returns a
    standardized table + an AI recommendation, snapshotted onto the comparison
    (dictionary / canonical version / aligned matrix / status=ALIGNED).

    BLOCK-ON-PROVIDER-DOWN: when the provider is unavailable ``submit_comparison``
    raises a typed ``AIError`` and this endpoint returns a clean provider error
    (502/503/504) — the step is BLOCKED, never degraded to a half-baked view.
    """
    comparison = _get_comparison(db, broker_id, comparison_id)
    sources = _live_sources(db, comparison)
    if not sources:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "no_columns",
                "message": "Add at least one cotización column before aligning.",
            },
        )
    try:
        result = ai_service.submit_comparison(
            db, comparison=comparison, sources=sources, broker_id=broker_id, user=current_user
        )
    except ai_service.AIError as exc:
        raise _ai_http_error(exc) from exc
    db.refresh(comparison)
    return ComparisonAlignmentResult(
        comparison=_comparison_read(db, comparison),
        used_ai=result.used_ai,
        canonical_version=result.canonical_version,
        batched=result.batched,
        recommendation=result.recommendation,
        warnings=result.warnings,
    )


# --- Promote a column into a real inbound proposal ---------------------------

def _resolve_quote_request(
    db: Session, broker_id: int, comparison: Comparison, override_id: int | None
) -> QuoteRequest:
    """The quote request the promoted proposal hangs off — reused or created.

    A proposal needs a ``quote_request`` (which needs a placement). The account's
    placement is authoritative; without one the column cannot be promoted.
    """
    if override_id is not None:
        quote = db.scalars(
            select(QuoteRequest).where(
                QuoteRequest.id == override_id, QuoteRequest.broker_id == broker_id
            )
        ).first()
        if quote is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Quote request {override_id} not found",
            )
        return quote

    case = (
        db.get(CaseFile, comparison.case_file_id)
        if comparison.case_file_id is not None
        else None
    )
    placement_id = comparison.placement_id or (case.placement_id if case else None)
    if placement_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "no_placement",
                "message": (
                    "The comparison's account has no placement, so a proposal "
                    "cannot be created from a column. Attach a placement first."
                ),
            },
        )

    clauses = [QuoteRequest.placement_id == placement_id]
    if comparison.case_file_id is not None:
        from sqlalchemy import or_

        clauses = [or_(QuoteRequest.placement_id == placement_id,
                       QuoteRequest.case_file_id == comparison.case_file_id)]
    quote = db.scalars(
        select(QuoteRequest)
        .where(QuoteRequest.broker_id == broker_id, *clauses)
        .order_by(QuoteRequest.id.desc())
    ).first()
    if quote is not None:
        return quote

    quote = QuoteRequest(
        broker_id=broker_id,
        placement_id=placement_id,
        case_file_id=comparison.case_file_id,
        status=QuoteRequestStatus.RECEIVING,
    )
    db.add(quote)
    db.flush()
    return quote


@router.post(
    "/{comparison_id}/entries/{entry_id}/promote",
    response_model=ComparisonPromoteResult,
    status_code=status.HTTP_201_CREATED,
)
def promote_entry(
    comparison_id: int,
    entry_id: int,
    payload: ComparisonPromote | None = Body(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Proposals", "Approve")),
) -> ComparisonPromoteResult:
    """Promote a column's fixed money core into a real inbound ``proposal``.

    Reuses the shared ``reconcile_money`` path so accept / offering keep working;
    a broken money invariant is a 422 ``money_inconsistent``. The insurer is
    resolved by normalised rut / cmf_code only (never name), and the resulting
    ``proposal_id`` is linked back onto the entry and its cached source.
    """
    payload = payload or ComparisonPromote()
    comparison = _get_comparison(db, broker_id, comparison_id)
    entry = _get_entry(db, comparison, entry_id)
    source = _source_of(db, entry)

    if source is not None and source.is_wrong_file:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "wrong_file",
                "message": (
                    "This column was flagged as not a proposal; it cannot be "
                    "promoted. Delete it or upload the correct file."
                ),
            },
        )
    if entry.proposal_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_promoted",
                "message": f"Column {entry.id} is already promoted to proposal {entry.proposal_id}.",
                "proposal_id": entry.proposal_id,
            },
        )

    from app.models.ai import Extraction

    extraction = (
        db.get(Extraction, source.source_extraction_id)
        if source is not None and source.source_extraction_id is not None
        else None
    )
    if extraction is None or not isinstance(extraction.parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "no_extraction",
                "message": "This column has no readable extraction to promote from.",
            },
        )
    parsed = extraction.parsed
    source_document_id = extraction.document_id
    if source_document_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "no_source_document",
                "message": "The extraction has no source document; a proposal requires one.",
            },
        )

    # Resolve the insurer by normalised rut / cmf_code only (never by name).
    # Real cotización PDFs carry only the INSURED's RUT, so the extraction can
    # legitimately lack the insurer's identity. Precedence: a reviewer-supplied
    # value WINS per field; otherwise fall back to whatever the extraction
    # parsed. find_or_create_insurer normalises/validates (mod-11 RUT, CMF
    # normalisation) and refuses name-only resolution.
    cmf_code = _first_present(payload.insurer_cmf_code, parsed.get("insurer_cmf_code"))
    rut = _first_present(payload.insurer_rut, parsed.get("insurer_rut"))
    if not cmf_code and not rut:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "insurer_identity_incomplete",
                "message": (
                    "This cotización does not state the insurer's identity, and none "
                    "was supplied. Supply the insurer's RUT or CMF code to promote it "
                    "(an insurer is never matched by name)."
                ),
                "field": "insurer",
            },
        )
    try:
        insurer = ai_service.find_or_create_insurer(
            db,
            cmf_code=cmf_code,
            rut=rut,
            legal_name=parsed.get("insurer_name"),
            broker_id=broker_id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "insurer_unresolved",
                "message": (
                    f"{exc} Supply the insurer's RUT or CMF code to promote this column."
                ),
                "field": "insurer",
            },
        ) from exc

    quote = _resolve_quote_request(db, broker_id, comparison, payload.quote_request_id)

    # Reconcile the fixed money core with the SHARED rule. The PREMIUM arithmetic
    # is HARD (422 on a contradiction); rate additivity is a SOFT warning now —
    # a non-additive comprehensive "tasa media" (HDI) must not block a promote.
    money: dict[str, Any] = {
        key: parsed.get(key) for key in _MONEY_KEYS if parsed.get(key) is not None
    }
    derived, errors, rate_warnings = reconcile_money_verbose(money)
    if errors:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "money_inconsistent",
                "message": (
                    "The offer's premium figures are inconsistent. Chilean rules: "
                    "net = taxable + exempt; vat = 0.19 * taxable; total = net + vat."
                ),
                "errors": errors,
            },
        )

    proposal = Proposal(
        broker_id=broker_id,
        quote_request_id=quote.id,
        insurer_id=insurer.id,
        source_document_id=source_document_id,
        origin=ProposalOrigin.NATIVE if insurer.is_native else ProposalOrigin.EXTERNAL,
        status=ProposalStatus.DRAFT,
        validity_business_days=parsed.get("validity_business_days"),
        extraction_id=extraction.id,
        extraction_confidence=extraction.confidence,
    )
    for key in _MONEY_KEYS:
        if parsed.get(key) is not None:
            setattr(proposal, key, parsed.get(key))
    for key, value in derived.items():
        setattr(proposal, key, value)

    db.add(proposal)
    db.flush()

    entry.proposal_id = proposal.id
    if source is not None:
        source.proposal_id = proposal.id
    db.commit()
    db.refresh(entry)

    return ComparisonPromoteResult(
        entry=_entry_read(db, entry),
        proposal_id=proposal.id,
        insurer_id=insurer.id,
        quote_request_id=quote.id,
        warnings=[
            f"{w.get('field')}: {w.get('rule')} (expected {w.get('expected')}, "
            f"read {w.get('received')})"
            for w in rate_warnings
        ],
    )


# --- Branded PDF -------------------------------------------------------------

# The premium/rate highlight rows, in display order.
_PREMIUM_HIGHLIGHT = (
    ("total_premium_uf", "Prima total (UF)"),
    ("net_premium_uf", "Prima neta (UF)"),
    ("taxable_premium_uf", "Prima afecta (UF)"),
    ("exempt_premium_uf", "Prima exenta (UF)"),
    ("vat_uf", "IVA (UF)"),
    ("comprehensive_rate_permille", "Tasa integral (‰)"),
)


def _fmt_uf(value: Any) -> Any:
    """Format a money/rate value with Chilean grouping when numeric, else pass it
    through (a bare string). Used only for display in the PDF."""
    if value is None:
        return None
    parsed = parse_uf(value)
    return _fmt_money(parsed) if parsed is not None else value


def _insurer_label(
    db: Session, broker_id: int, col: dict, source: ComparisonSource | None, ordinal: int
) -> str:
    """A human column header: the promoted insurer's name, else the parsed insurer
    name, else a positional 'Oferta N'."""
    proposal_id = col.get("proposal_id")
    if proposal_id is not None:
        proposal = db.get(Proposal, proposal_id)
        if proposal is not None and proposal.insurer_id is not None:
            insurer = db.get(Insurer, proposal.insurer_id)
            name = getattr(insurer, "trade_name", None) or getattr(insurer, "legal_name", None)
            if name:
                return str(name)
    if source is not None and source.source_extraction_id is not None:
        from app.models.ai import Extraction

        extraction = db.get(Extraction, source.source_extraction_id)
        if extraction is not None and isinstance(extraction.parsed, dict):
            parsed_name = extraction.parsed.get("insurer_name")
            if parsed_name:
                return str(parsed_name)
    return f"Oferta {ordinal}"


def _comparison_pdf_data(db: Session, broker_id: int, comparison: Comparison) -> dict:
    """Shape the aligned matrix into the plain dict ``render_comparison_html`` takes."""
    matrix = comparison.aligned_matrix if isinstance(comparison.aligned_matrix, dict) else {}
    raw_columns = matrix.get("columns") or []
    dimensions = matrix.get("dimensions") or []
    recommendation = matrix.get("recommendation") or {}
    rec_sid = recommendation.get("recommended_comparison_source_id")

    col_specs: list[dict] = []
    for idx, col in enumerate(raw_columns, start=1):
        sid = col.get("comparison_source_id")
        source = db.get(ComparisonSource, sid) if sid is not None else None
        col_specs.append(
            {
                "source_id": sid,
                "label": _insurer_label(db, broker_id, col, source, idx),
                "wrong_file": bool(col.get("is_wrong_file")),
                "recommended": sid is not None and sid == rec_sid,
                "premium": _premium_core(db, source),
            }
        )

    columns = [
        {"label": c["label"], "recommended": c["recommended"], "wrong_file": c["wrong_file"]}
        for c in col_specs
    ]

    premium_rows: list[dict] = []
    for key, label in _PREMIUM_HIGHLIGHT:
        values: list[Any] = []
        has_value = False
        for c in col_specs:
            raw = getattr(c["premium"], key, None) if c["premium"] is not None else None
            if raw is not None:
                has_value = True
            values.append(_fmt_uf(raw))
        if has_value:
            premium_rows.append({"label": label, "values": values})

    order = [c["source_id"] for c in col_specs]
    common: list[dict] = []
    extra: list[dict] = []
    for dim in dimensions:
        by_sid: dict[Any, dict] = {}
        for cell in dim.get("cells") or []:
            if isinstance(cell, dict):
                by_sid[cell.get("comparison_source_id")] = cell
        row = {
            "label": dim.get("label"),
            "key": dim.get("key"),
            "group": dim.get("group"),
            "cells": [by_sid.get(sid) for sid in order],
        }
        (extra if dim.get("scope") == "extra" else common).append(row)

    rec_label = None
    if rec_sid is not None:
        match = next((c for c in col_specs if c["source_id"] == rec_sid), None)
        if match is not None:
            rec_label = match["label"]

    return {
        "columns": columns,
        "premium_rows": premium_rows,
        "common_dimensions": common,
        "extra_dimensions": extra,
        "recommendation": {
            "pick_label": rec_label,
            "rationale": recommendation.get("rationale"),
            "caveats": recommendation.get("caveats") or [],
        },
    }


def _pdf_branding(db: Session, broker_id: int, case: CaseFile | None, *, running_title: str, eyebrow: str) -> dict:
    broker = db.get(Broker, broker_id)
    return {
        "radal_wordmark": "Radal.",
        "broker_logo_datauri": _logo_datauri(broker) if broker else None,
        "broker_name": (broker.trade_name or broker.legal_name) if broker else None,
        "broker_rut": broker.rut if broker else None,
        "cmf_code": broker.cmf_code if broker else None,
        "cover_meta": _cover_meta(case) if case is not None else [],
        "eyebrow": eyebrow,
        "running_title": running_title,
    }


@router.get("/{comparison_id}/pdf", response_model=DocumentDownload)
async def comparison_pdf(
    comparison_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "View")),
) -> DocumentDownload:
    """Generate (or regenerate) the branded comparison PDF as a download link.

    The comparison must be ALIGNED — an empty/unaligned comparison has no matrix
    to render (422 ``comparison_not_aligned``). The PDF is stored as a
    ``comparison_pack`` document (rule 8: its S3 key lives only on that row) and
    linked back onto ``comparison.pdf_document_id``.
    """
    comparison = _get_comparison(db, broker_id, comparison_id)
    if comparison.status != ComparisonStatus.ALIGNED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "comparison_not_aligned",
                "message": (
                    "Align the comparison before rendering its PDF "
                    "(POST /comparisons/{id}/align)."
                ),
            },
        )

    case = None
    if comparison.case_file_id is not None:
        case = db.scalars(
            select(CaseFile).where(
                CaseFile.id == comparison.case_file_id, CaseFile.broker_id == broker_id
            )
        ).first()

    branding = _pdf_branding(
        db, broker_id, case, running_title="Comparación", eyebrow="Comparación de ofertas"
    )
    insured = getattr(getattr(case, "client", None), "insured", None)
    title = getattr(insured, "legal_name", None) or (case.title if case else None) or "Comparación de ofertas"

    html = pdf_templates.render_comparison_html(
        title=title,
        branding=branding,
        data=_comparison_pdf_data(db, broker_id, comparison),
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
        entity_id=comparison.case_file_id or comparison.id,
        category=DocumentCategory.COMPARISON_PACK,
        data=data,
        original_name=f"comparacion-{comparison.id}.pdf",
        mime_type="application/pdf",
        key=f"comparisons/{comparison.id}/comparison.pdf",
        uploaded_by_id=current_user.id,
    )
    comparison.pdf_document_id = document.id
    db.commit()
    db.refresh(document)
    return build_download_payload(document)


__all__ = ["router"]
