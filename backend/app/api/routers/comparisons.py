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
from app.models.enums import CaseStage, EntityType
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
from app.schemas.comparison import ComparisonInsurerSuggestion
from app.schemas.proposal import effective_rate_permille, reconcile_money_verbose
from app.services import ai as ai_service
from app.services import case_files as machine
from app.services.ai_error_copy import (
    AI_PARSE_ES,
    AI_PROVIDER_DOWN_ES,
    AI_TIMEOUT_ES,
    DOCUMENT_UNAVAILABLE_ES,
    ai_user_error,
)
from app.services.comparison_standardize import insurer_short_name

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

def _comparison_ai_error(exc: ai_service.AIError) -> HTTPException:
    """A typed AI failure of a comparison read/standardization, as a structured error.

    Same status and ``X-Radal-AI-Error`` header as :func:`_ai_http_error`; the body is
    the structured ``{"code", "message"}`` the other comparison errors use, so the SPA
    can tell a TIMEOUT (``ai_timeout``) from an outage (``ai_unavailable``) and show
    the Spanish copy as-is.
    """
    base = _ai_http_error(exc)
    return HTTPException(
        status_code=base.status_code, detail=ai_user_error(exc), headers=base.headers
    )


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


def _source_extraction(db: Session, source: ComparisonSource | None):
    if source is None or source.source_extraction_id is None:
        return None
    from app.models.ai import Extraction

    return db.get(Extraction, source.source_extraction_id)


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
    extraction = _source_extraction(db, source)
    if extraction is None or not isinstance(extraction.parsed, dict):
        return None
    parsed = extraction.parsed
    core = ComparisonPremiumCore(**{key: parsed.get(key) for key in _PREMIUM_KEYS})
    core.insured_amount_uf = parsed.get("insured_amount_uf")
    if core.comprehensive_rate_permille is None:
        # R4: never a sum of rates — the effective rate over the whole insured amount.
        effective = effective_rate_permille(parsed.get("net_premium_uf"), parsed.get("insured_amount_uf"))
        if effective is not None:
            core.comprehensive_rate_permille = effective
            core.comprehensive_rate_derived = True
    return core


# --- Read state of a column (review round 2, A2) ------------------------------

#: A read that "succeeded" with nothing in it is not a column to compare.
READ_EMPTY_ES = (
    "La lectura no encontró prima ni condiciones en este archivo. Reintenta la lectura "
    "o revisa que sea la cotización de la compañía."
)

_FAILURE_COPY = {
    "ai_timeout": AI_TIMEOUT_ES,
    "ai_unavailable": AI_PROVIDER_DOWN_ES,
    "ai_parse": AI_PARSE_ES,
    "document_unavailable": DOCUMENT_UNAVAILABLE_ES,
    "read_empty": READ_EMPTY_ES,
}

_ERROR_CLASS_CODES = {
    "AITimeout": "ai_timeout",
    "AIParseError": "ai_parse",
    "DocumentUnavailable": "document_unavailable",
    "AINotConfigured": "ai_unavailable",
    "AIProviderError": "ai_unavailable",
}

#: The core figures a column should carry for the comparison to mean anything.
_CORE_HINT_KEYS = ("insurer_name", "total_premium_uf", "net_premium_uf")


def _failure_code(error: str | None) -> str:
    """The stable code of a FAILED extraction row, from its ``"<Class>: msg"`` error."""
    text = error or ""
    code = _ERROR_CLASS_CODES.get(text.split(":", 1)[0].strip(), "ai_unavailable")
    if code == "ai_unavailable" and ("returned 504" in text or "returned 408" in text):
        return "ai_timeout"
    return code


def _read_state(source: ComparisonSource | None, extraction: Any) -> tuple[str, str | None]:
    """``(status, error_code)`` of a column — see ``ComparisonEntryRead.status``."""
    from app.models.ai import ExtractionStatus

    if source is None:
        return "ok", None  # a legacy column built from a proposal
    if source.is_wrong_file:
        return "wrong_file", None
    if extraction is None:
        return ("ok", None) if source.facets else ("empty", "read_empty")
    if extraction.status != ExtractionStatus.SUCCEEDED:
        return "failed", _failure_code(extraction.error)
    parsed = extraction.parsed if isinstance(extraction.parsed, dict) else {}
    has_money = any(parsed.get(key) is not None for key in _PREMIUM_KEYS[:5])
    if not has_money and not source.facets and not parsed.get("insurer_name"):
        return "empty", "read_empty"
    return "ok", None


def _is_usable(db: Session, source: ComparisonSource) -> bool:
    """A column the standardizer may read: ``ok`` or ``wrong_file`` (shown rejected)."""
    status_, _code = _read_state(source, _source_extraction(db, source))
    return status_ in {"ok", "wrong_file"}


def _lookup_insurer(db: Session, broker_id: int, rut: Any, cmf_code: Any) -> Insurer | None:
    """An EXISTING insurer the broker can see, by normalised rut / cmf (never by name,
    never created here)."""
    from app.services.extraction_commit import _insurer_visibility
    from app.services.identifiers import (
        InvalidCmf,
        InvalidRut,
        normalize_codigo_cmf,
        validate_rut,
    )

    clauses = []
    if cmf_code:
        try:
            clauses.append(Insurer.cmf_code == normalize_codigo_cmf(str(cmf_code)))
        except (InvalidCmf, ValueError):
            pass  # an unreadable code resolves nothing
    if rut:
        try:
            clauses.append(Insurer.rut == validate_rut(str(rut)))
        except (InvalidRut, ValueError):
            pass
    for clause in clauses:
        found = db.scalars(select(Insurer).where(clause, _insurer_visibility(broker_id))).first()
        if found is not None:
            return found
    return None


def _insurer_display(insurer: Insurer | None, read_name: Any) -> str | None:
    if insurer is not None:
        return insurer.trade_name or insurer_short_name(insurer.legal_name)
    return insurer_short_name(read_name)


def _suggestion_of(insurer: Insurer) -> ComparisonInsurerSuggestion:
    return ComparisonInsurerSuggestion(
        id=insurer.id,
        name=insurer.legal_name or insurer.trade_name,
        rut=insurer.rut,
        cmf_code=insurer.cmf_code,
    )


def _entry_insurer(
    db: Session, broker_id: int, entry: ComparisonEntry, parsed: dict[str, Any]
) -> tuple[Insurer | None, list[ComparisonInsurerSuggestion]]:
    """The insurer of a column: the promoted proposal's, else an existing catalogue
    insurer matching the read rut / cmf; plus catalogue suggestions on the read NAME
    (suggestions only — rule 7, the broker confirms one at promote)."""
    from app.services.extraction_commit import insurer_name_suggestions

    insurer: Insurer | None = None
    if entry.proposal_id is not None:
        proposal = db.get(Proposal, entry.proposal_id)
        if proposal is not None and proposal.insurer_id is not None:
            insurer = db.get(Insurer, proposal.insurer_id)
    if insurer is None:
        insurer = _lookup_insurer(
            db, broker_id, parsed.get("insurer_rut"), parsed.get("insurer_cmf_code")
        )
    if insurer is not None:
        return insurer, [_suggestion_of(insurer)]
    suggestions = [
        ComparisonInsurerSuggestion(**row)
        for row in insurer_name_suggestions(db, broker_id, parsed.get("insurer_name"))
    ]
    return None, suggestions


def _aligned_ids(comparison: Comparison | None) -> set[int]:
    matrix = comparison.aligned_matrix if comparison is not None else None
    if not isinstance(matrix, dict):
        return set()
    return {
        col.get("comparison_source_id")
        for col in matrix.get("columns") or []
        if isinstance(col, dict) and col.get("comparison_source_id") is not None
    }


def _entry_read(
    db: Session,
    entry: ComparisonEntry,
    comparison: Comparison | None = None,
) -> ComparisonEntryRead:
    """Flatten a column + its cached source verdict for the grid."""
    if comparison is None:
        comparison = db.get(Comparison, entry.comparison_id)
    source = _source_of(db, entry)
    extraction = _source_extraction(db, source)
    status_, error_code = _read_state(source, extraction)
    parsed = (
        extraction.parsed
        if extraction is not None and isinstance(extraction.parsed, dict)
        else {}
    )
    insurer, suggestions = (None, [])
    if comparison is not None and status_ not in {"failed", "wrong_file"}:
        insurer, suggestions = _entry_insurer(db, comparison.broker_id, entry, parsed)
    missing = (
        [key for key in _CORE_HINT_KEYS if parsed.get(key) in (None, "")]
        if status_ == "ok" and source is not None
        else []
    )
    document_id = extraction.document_id if extraction is not None else None
    # Review r3 (R5): *Reintentar* is offered for a failed / empty read AND for a read
    # that succeeded without its core (old reads with no insurer or prima total) —
    # never for a promoted column or one without its file (``retry_entry``'s 409/422).
    can_retry = (
        entry.proposal_id is None
        and document_id is not None
        and (status_ in {"failed", "empty"} or (status_ == "ok" and bool(missing)))
    )
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
        document_id=document_id,
        facets=source.facets if source else None,
        premium=_premium_core(db, source) if status_ == "ok" else None,
        status=status_,
        error_code=error_code,
        error_message=_FAILURE_COPY.get(error_code) if error_code else None,
        aligned=(
            entry.comparison_source_id is not None
            and entry.comparison_source_id in _aligned_ids(comparison)
        ),
        insurer_name=_insurer_display(insurer, parsed.get("insurer_name")),
        insurer_read_name=parsed.get("insurer_name"),
        insurer_id=insurer.id if insurer is not None else None,
        insurer_suggestions=suggestions,
        missing_core=missing,
        can_retry=can_retry,
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
    read.entries = [_entry_read(db, entry, comparison) for entry in entries]
    # The AI recommendation is snapshotted inside aligned_matrix (no DB column):
    # surface it as a first-class field so the frontend need not dig for it.
    if isinstance(comparison.aligned_matrix, dict):
        read.recommendation = comparison.aligned_matrix.get("recommendation")
    return read


def _error(code: str, reason: str, status_code: int = status.HTTP_422_UNPROCESSABLE_ENTITY, **extra: Any) -> HTTPException:
    """The structured error of review round 2: ``{"code", "reason", "message", …}`` —
    ``reason`` per contract, ``message`` the same Spanish text the SPA already shows."""
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "reason": reason, "message": reason, **extra},
    )


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


def _columns_signature(db: Session, comparison: Comparison) -> set[tuple[int, int | None]]:
    """``{(source_id, extraction_id)}`` of every column, read with a plain SELECT (fresh
    column values, not the session's identity map): it changes when a column is added,
    deleted or re-read — by another request, while this one waits on the provider."""
    rows = db.execute(
        select(ComparisonSource.id, ComparisonSource.source_extraction_id)
        .join(ComparisonEntry, ComparisonEntry.comparison_source_id == ComparisonSource.id)
        .where(ComparisonEntry.comparison_id == comparison.id)
    ).all()
    return {(row[0], row[1]) for row in rows}


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

def _comparison_case(db: Session, comparison: Comparison) -> CaseFile | None:
    if comparison.case_file_id is None:
        return None
    return db.scalars(
        select(CaseFile).where(
            CaseFile.id == comparison.case_file_id,
            CaseFile.broker_id == comparison.broker_id,
        )
    ).first()


def _next_sort_order(db: Session, comparison: Comparison) -> int:
    current = db.scalar(
        select(func.max(ComparisonEntry.sort_order)).where(
            ComparisonEntry.comparison_id == comparison.id
        )
    )
    # ``max`` of a first column is 0: ``0 or -1`` gave every column sort_order 0.
    return (int(current) if current is not None else -1) + 1


def _store_read(
    db: Session,
    *,
    comparison: Comparison,
    entry: ComparisonEntry | None,
    broker_id: int,
    extraction_id: int,
    payload: Any | None,
    is_wrong_file: bool,
    rejection_reason: str | None,
) -> ComparisonEntry:
    """Point a column at one read attempt — a NEW column, or the same column again
    (retry). A failed attempt is stored too (A2): the column shows it failed."""
    source = _source_of(db, entry) if entry is not None else None
    if source is None:
        source = ComparisonSource(broker_id=broker_id, proposal_id=None)
        db.add(source)
    source.source_extraction_id = extraction_id
    source.facets = _compact_facets(payload)
    source.is_wrong_file = bool(is_wrong_file)
    source.wrong_file_reason = rejection_reason if is_wrong_file else None
    source.extracted_at = datetime.now(timezone.utc)
    db.flush()
    if entry is None:
        entry = ComparisonEntry(
            comparison_id=comparison.id,
            proposal_id=None,
            comparison_source_id=source.id,
            sort_order=_next_sort_order(db, comparison),
        )
        db.add(entry)
    else:
        entry.comparison_source_id = source.id
    # A new (or re-read) column supersedes the last alignment — it must be re-run.
    if comparison.status == ComparisonStatus.ALIGNED:
        comparison.status = ComparisonStatus.DRAFT
    db.flush()
    return entry


def _read_into(
    db: Session,
    *,
    comparison: Comparison,
    entry: ComparisonEntry | None,
    document: Document,
    broker_id: int,
    user: User,
) -> ComparisonEntryResult:
    """Read ``document`` into ``entry`` (or a new column); commit; advance the journey.

    A typed AI failure keeps the attempt as a VISIBLE failed column (status
    ``failed`` + ``error_code``, *Reintentar*), commits it, and re-raises the same
    structured error the endpoint always answered — so a browser that lost the
    answer at the edge finds the column by polling instead of nothing.
    """
    try:
        result = ai_service.extract_budget_proposal(
            db, document_id=document.id, broker_id=broker_id, user=user
        )
    except ai_service.AIError as exc:
        extraction_id = getattr(exc, "extraction_id", None)
        db.rollback()
        if entry is not None and _read_state(
            _source_of(db, entry), _source_extraction(db, _source_of(db, entry))
        )[0] in {"ok", "wrong_file"}:
            # A failed RE-read of a good column keeps the good read (the attempt
            # still has its own FAILED extraction row, rule 6).
            extraction_id = None
        if extraction_id is not None:
            _store_read(
                db,
                comparison=comparison,
                entry=entry,
                broker_id=broker_id,
                extraction_id=extraction_id,
                payload=None,
                is_wrong_file=False,
                rejection_reason=None,
            )
            db.commit()
        raise _comparison_ai_error(exc) from exc

    entry = _store_read(
        db,
        comparison=comparison,
        entry=entry,
        broker_id=broker_id,
        extraction_id=result.extraction_id,
        payload=result.payload,
        is_wrong_file=result.is_wrong_file,
        rejection_reason=result.rejection_reason,
    )
    warnings = list(result.warnings)
    read = _entry_read(db, entry, comparison)
    if read.status == "ok":
        # A6: a cotización in the comparator = the account received quotes.
        warning = machine.advance_for_comparison(
            db,
            case=_comparison_case(db, comparison),
            target=CaseStage.QUOTES_RECEIVED,
            user=user,
        )
        if warning:
            warnings.append(warning)
    db.commit()
    db.refresh(entry)

    return ComparisonEntryResult(
        entry=_entry_read(db, entry, comparison),
        extraction_id=result.extraction_id,
        document_type=result.document_type,
        is_wrong_file=result.is_wrong_file,
        rejection_reason=result.rejection_reason,
        warnings=warnings,
    )


def _failed_entry_for(db: Session, comparison: Comparison, document_id: int) -> ComparisonEntry | None:
    """The FAILED column of this document, if any — a re-upload retries it in place."""
    for entry in db.scalars(
        select(ComparisonEntry).where(ComparisonEntry.comparison_id == comparison.id)
    ).all():
        if entry.proposal_id is not None:
            continue
        source = _source_of(db, entry)
        extraction = _source_extraction(db, source)
        if extraction is None or extraction.document_id != document_id:
            continue
        if _read_state(source, extraction)[0] in {"failed", "empty"}:
            return entry
    return None


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
    A failed read is a VISIBLE failed column (``status="failed"``) and the same
    structured AI error; posting the same document again retries that column.
    A read cotización moves the account to *Cotizaciones recibidas* (A6).
    """
    comparison = _get_comparison(db, broker_id, comparison_id)
    document = _get_document(db, broker_id, payload.document_id)
    return _read_into(
        db,
        comparison=comparison,
        entry=_failed_entry_for(db, comparison, document.id),
        document=document,
        broker_id=broker_id,
        user=current_user,
    )


@router.post(
    "/{comparison_id}/entries/{entry_id}/retry",
    response_model=ComparisonEntryResult,
)
def retry_entry(
    comparison_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Create")),
) -> ComparisonEntryResult:
    """*Reintentar*: read the column's document again INTO THE SAME column (A2).

    Same answers as ``POST …/entries`` (a failure keeps the column failed and returns
    the structured AI error). A promoted column is 409; a column with no document 422.
    """
    comparison = _get_comparison(db, broker_id, comparison_id)
    entry = _get_entry(db, comparison, entry_id)
    if entry.proposal_id is not None:
        raise _error(
            "already_promoted",
            "Esta cotización ya se promovió a propuesta: no se vuelve a leer.",
            status.HTTP_409_CONFLICT,
            proposal_id=entry.proposal_id,
        )
    extraction = _source_extraction(db, _source_of(db, entry))
    if extraction is None or extraction.document_id is None:
        raise _error(
            "no_document",
            "Esta columna no tiene archivo que volver a leer: súbelo de nuevo.",
        )
    document = _get_document(db, broker_id, extraction.document_id)
    return _read_into(
        db,
        comparison=comparison,
        entry=entry,
        document=document,
        broker_id=broker_id,
        user=current_user,
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
            raise _error(
                "no_source",
                "Esta columna no tiene una lectura que corregir.",
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
    # A2: failed / empty reads are never standardized (they would read as an offer
    # with every condition "No indicada"); wrong files stay visible rejected columns.
    sources = [source for source in _live_sources(db, comparison) if _is_usable(db, source)]
    if not any(not source.is_wrong_file for source in sources):
        raise _error(
            "no_columns",
            "Agrega al menos una cotización leída antes de alinear (las lecturas fallidas "
            "se reintentan desde su columna).",
        )
    used = _columns_signature(db, comparison)

    def _still_current() -> bool:
        # A1: a column that arrived (or was re-read) while the provider worked makes
        # this table stale — keep the comparison ``draft`` instead of "aligned".
        return _columns_signature(db, comparison) == used

    try:
        result = ai_service.submit_comparison(
            db,
            comparison=comparison,
            sources=sources,
            broker_id=broker_id,
            user=current_user,
            still_current=_still_current,
        )
    except ai_service.AIError as exc:
        raise _comparison_ai_error(exc) from exc
    db.refresh(comparison)
    warnings = list(result.warnings)
    if comparison.status == ComparisonStatus.ALIGNED:
        # A6: aligned cotizaciones = the account is in *Comparación*.
        warning = machine.advance_for_comparison(
            db, case=_comparison_case(db, comparison), target=CaseStage.COMPARISON, user=current_user
        )
        if warning:
            warnings.append(warning)
        db.commit()
        db.refresh(comparison)
    return ComparisonAlignmentResult(
        comparison=_comparison_read(db, comparison),
        used_ai=result.used_ai,
        canonical_version=result.canonical_version,
        batched=result.batched,
        recommendation=result.recommendation,
        warnings=warnings,
    )


# --- Promote a column into a real inbound proposal ---------------------------

def _ensure_placement(
    db: Session, broker_id: int, comparison: Comparison, user: User
) -> tuple[int, bool]:
    """The placement a promoted proposal hangs off — the account's, or one created ON
    DEMAND (review round 2, A5 [D3]); ``(placement_id, created)``.

    A v12 account opened from the UI (or a partial journey) has no placement until a
    cotización is promoted. Creating it here mirrors the colocación-on-demand of the
    policy upload (``policy_registry``): the account's client and ramo, its vigencia,
    the client's asset (one is opened when it has none), the status the account's
    stage implies (``STAGE_TO_PLACEMENT_STATUS``), linked both ways and on the
    comparison, one ``placement.created`` bitácora row. Flush-only.
    """
    from app.models.activity import Activity
    from app.models.asset import Asset
    from app.models.placement import Placement, PlacementStatus

    case = _comparison_case(db, comparison)
    placement_id = comparison.placement_id or (case.placement_id if case is not None else None)
    if placement_id is None and case is not None:
        placement_id = db.scalar(
            select(Placement.id)
            .where(Placement.broker_id == broker_id, Placement.case_file_id == case.id)
            .order_by(Placement.id)
            .limit(1)
        )
    if placement_id is not None:
        if comparison.placement_id is None:
            comparison.placement_id = placement_id
        return placement_id, False
    if case is None:
        raise _error(
            "no_account",
            "Esta comparación no pertenece a una cuenta: ábrela desde la cuenta del asegurado "
            "para promover una cotización.",
        )
    if case.insurance_line_id is None:
        raise _error(
            "account_without_line",
            "La cuenta no tiene ramo: elige el ramo de la cuenta antes de promover la cotización.",
        )

    from app.models.client import Client

    client = db.scalars(
        select(Client).where(Client.id == case.client_id, Client.broker_id == broker_id)
    ).first()
    if client is None:
        raise _error(
            "account_without_client",
            "La cuenta no tiene asegurado: agrégalo antes de promover la cotización.",
        )
    asset = db.scalars(
        select(Asset)
        .where(Asset.broker_id == broker_id, Asset.client_id == client.id)
        .order_by(Asset.id.desc())
    ).first()
    if asset is None:
        insured = getattr(client, "insured", None)
        asset = Asset(
            broker_id=broker_id,
            client_id=client.id,
            asset_type="general",
            name=(getattr(insured, "legal_name", None) or case.title or f"Cliente {client.id}")[:255],
        )
        db.add(asset)
        db.flush()
    placement = Placement(
        broker_id=broker_id,
        client_id=client.id,
        asset_id=asset.id,
        insurance_line_id=case.insurance_line_id,
        period=case.period_label,
        period_start=case.period_start,
        period_end=case.period_end,
        status=machine.STAGE_TO_PLACEMENT_STATUS.get(case.stage, PlacementStatus.DRAFT),
        case_file_id=case.id,
    )
    db.add(placement)
    db.flush()
    case.placement_id = placement.id
    comparison.placement_id = placement.id
    db.add(
        Activity(
            broker_id=broker_id,
            user_id=user.id,
            action="placement.created",
            entity_type=EntityType.PLACEMENT,
            entity_id=placement.id,
            description=(
                f"Colocación creada al promover una cotización ({case.reference or case.title})"
            ),
            meta={
                "case_file_id": case.id,
                "comparison_id": comparison.id,
                "status": placement.status.value,
                "on_demand": True,
                "via": "comparison_promote",
            },
        )
    )
    return placement.id, True


def _resolve_quote_request(
    db: Session, broker_id: int, comparison: Comparison, override_id: int | None, placement_id: int | None
) -> QuoteRequest:
    """The quote request the promoted proposal hangs off — reused or created on the
    account's placement (created on demand by :func:`_ensure_placement`)."""
    if override_id is not None:
        quote = db.scalars(
            select(QuoteRequest).where(
                QuoteRequest.id == override_id, QuoteRequest.broker_id == broker_id
            )
        ).first()
        if quote is None:
            raise _error(
                "quote_request_not_found",
                "La solicitud de cotización indicada no existe.",
                status.HTTP_404_NOT_FOUND,
            )
        return quote

    from sqlalchemy import or_

    clauses = [QuoteRequest.placement_id == placement_id]
    if comparison.case_file_id is not None:
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
    current_user: User = Depends(require_permission("Proposals", "Approve")),
) -> ComparisonPromoteResult:
    """Promote a column's fixed money core into a real inbound ``proposal``.

    Reuses the shared ``reconcile_money`` path so accept / offering keep working;
    a broken money invariant is a 422 ``money_inconsistent``. The insurer is the
    catalogue choice (``insurer_id``, as in Subir póliza), else resolved by
    normalised rut / cmf_code (typed, then read) — never by name. Without a
    placement on the account one is created on demand (A5). Every error is a
    structured Spanish ``{"code", "reason", "message"}``.
    """
    from app.services.extraction_commit import _visible_insurer

    payload = payload or ComparisonPromote()
    comparison = _get_comparison(db, broker_id, comparison_id)
    entry = _get_entry(db, comparison, entry_id)
    source = _source_of(db, entry)

    if source is not None and source.is_wrong_file:
        raise _error(
            "wrong_file",
            "Este archivo no es una cotización: no se puede promover. Elimínalo o sube la "
            "cotización correcta.",
        )
    if entry.proposal_id is not None:
        raise _error(
            "already_promoted",
            "Esta cotización ya se promovió a propuesta.",
            status.HTTP_409_CONFLICT,
            proposal_id=entry.proposal_id,
        )

    extraction = _source_extraction(db, source)
    state, error_code = _read_state(source, extraction)
    if state == "failed":
        raise _error(
            "read_failed",
            "La lectura de esta cotización falló: reintenta la lectura antes de promoverla.",
            error_code=error_code,
        )
    if extraction is None or not isinstance(extraction.parsed, dict):
        raise _error(
            "no_extraction",
            "Esta columna no tiene una lectura de la cotización: reintenta la lectura.",
        )
    parsed = extraction.parsed
    source_document_id = extraction.document_id
    if source_document_id is None:
        raise _error(
            "no_source_document",
            "La lectura no tiene archivo de origen; una propuesta lo requiere.",
        )

    # The insurer: the catalogue choice wins; else normalised rut / cmf (typed wins
    # over read per field). find_or_create_insurer validates (mod-11 RUT, CMF) and
    # never resolves by name (rule 7).
    if payload.insurer_id is not None:
        insurer = _visible_insurer(db, broker_id, payload.insurer_id)
        if insurer is None:
            raise _error(
                "insurer_not_found",
                "La compañía elegida no está en tu catálogo.",
                status.HTTP_404_NOT_FOUND,
                field="insurer_id",
            )
    else:
        cmf_code = _first_present(payload.insurer_cmf_code, parsed.get("insurer_cmf_code"))
        rut = _first_present(payload.insurer_rut, parsed.get("insurer_rut"))
        if not cmf_code and not rut:
            from app.services.extraction_commit import insurer_name_suggestions

            raise _error(
                "insurer_identity_incomplete",
                "Elige la compañía de la cotización en el catálogo. Si no está en la lista, "
                "escribe su RUT o código CMF (una compañía nunca se identifica solo por el "
                "nombre).",
                field="insurer",
                insurer_name=parsed.get("insurer_name"),
                insurer_suggestions=insurer_name_suggestions(
                    db, broker_id, parsed.get("insurer_name")
                ),
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
            raise _error(
                "insurer_unresolved",
                "No encontramos esa compañía con el RUT o código CMF indicado. Revisa el dato "
                "o elige la compañía en el catálogo (para crear una nueva se necesitan RUT y "
                "código CMF).",
                field="insurer",
            ) from exc

    placement_id, placement_created = _ensure_placement(db, broker_id, comparison, current_user)
    quote = _resolve_quote_request(db, broker_id, comparison, payload.quote_request_id, placement_id)

    # Reconcile the fixed money core with the SHARED rule. The PREMIUM arithmetic
    # is HARD (422 on a contradiction); rate additivity is a SOFT warning now —
    # a non-additive comprehensive "tasa media" (HDI) must not block a promote.
    money: dict[str, Any] = {
        key: parsed.get(key) for key in _MONEY_KEYS if parsed.get(key) is not None
    }
    # R4: the base of a derived tasa integral (net / insured × 1000) — never stored as
    # a proposal column, only used for the derivation.
    if parsed.get("insured_amount_uf") is not None:
        money["insured_amount_uf"] = parsed.get("insured_amount_uf")
    derived, errors, rate_warnings = reconcile_money_verbose(money)
    if errors:
        db.rollback()
        raise _error(
            "money_inconsistent",
            "Las primas de la cotización no cuadran. Reglas: neta = afecta + exenta; "
            "IVA = 0,19 × afecta; total = neta + IVA.",
            errors=errors,
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
        entry=_entry_read(db, entry, comparison),
        proposal_id=proposal.id,
        insurer_id=insurer.id,
        quote_request_id=quote.id,
        placement_id=placement_id,
        placement_created=placement_created,
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
            name = _insurer_display(insurer, None) if insurer is not None else None
            if name:
                return str(name)
    if source is not None and source.source_extraction_id is not None:
        from app.models.ai import Extraction

        extraction = db.get(Extraction, source.source_extraction_id)
        if extraction is not None and isinstance(extraction.parsed, dict):
            parsed_name = insurer_short_name(extraction.parsed.get("insurer_name"))
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
        raise _error(
            "comparison_not_aligned",
            "Alinea la comparación antes de generar su PDF.",
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
