"""AI endpoints — proposal extraction (SUGGEST -> CONFIRM -> COMMIT) + agent chat.

Mounted at ``{API_V1_PREFIX}/ai``.

    POST   /ai/proposals/extract              read a document, return a SUGGESTION
    GET    /ai/extractions/{id}               re-read a persisted suggestion
    POST   /ai/proposals/{extraction_id}/confirm   COMMIT the reviewed payload
    POST   /ai/threads                        open a chat thread
    GET    /ai/threads                        list the caller's threads
    GET    /ai/threads/{id}/messages          full transcript
    POST   /ai/threads/{id}/messages          one turn (persists user + assistant)

Two invariants this module enforces on every path:

**Nothing auto-commits.** ``/proposals/extract`` writes exactly one
``extraction`` row and returns a suggestion. Only ``/proposals/{id}/confirm``,
carrying a payload a human reviewed (and may have edited), creates a proposal.

**No 500 from a provider hiccup.** Every :class:`AIError` is mapped to a precise
status: 503 not configured, 504 timeout, 502 provider error, 422 unusable answer
or unreadable document. The LLM failing is a normal, reportable outcome.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.core.permissions import user_has_permission
from app.models.ai import AgentScope, Extraction
from app.models.proposal import Proposal
from app.models.user import User
from app.schemas.ai import (
    ExtractionRead,
    ExtractionRequest,
    ExtractionSuggestionResponse,
    MessageCreate,
    MessageExchange,
    MessageList,
    MessageRead,
    ProposalConfirmRequest,
    ProposalCoverageRead,
    ProposalRead,
    ProposalSuggestion,
    ThreadCreate,
    ThreadList,
    ThreadRead,
)
from app.services import ai as ai_service
from app.services.ai import (
    AIError,
    AINotConfigured,
    AIParseError,
    AIProviderError,
    AITimeout,
    DocumentUnavailable,
    MoneyInconsistent,
)

router = APIRouter(prefix="/ai", tags=["ai"])


# --- Error mapping -----------------------------------------------------------

_AI_STATUS = {
    AINotConfigured: status.HTTP_503_SERVICE_UNAVAILABLE,
    AITimeout: status.HTTP_504_GATEWAY_TIMEOUT,
    AIProviderError: status.HTTP_502_BAD_GATEWAY,
    AIParseError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    DocumentUnavailable: status.HTTP_422_UNPROCESSABLE_ENTITY,
    MoneyInconsistent: status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _ai_http_error(exc: AIError) -> HTTPException:
    """Translate a typed AI failure into its HTTP status. Never a 500."""
    code = _AI_STATUS.get(type(exc), status.HTTP_502_BAD_GATEWAY)
    return HTTPException(status_code=code, detail=str(exc))


# --- Serialisation helpers ---------------------------------------------------


def _extraction_read(extraction: Extraction) -> ExtractionRead:
    return ExtractionRead.model_validate(extraction)


def _proposal_read(db: Session, proposal: Proposal) -> ProposalRead:
    payload = ProposalRead.model_validate(proposal)
    insurer = proposal.insurer
    if insurer is not None:
        payload.insurer_name = insurer.legal_name
        payload.insurer_cmf_code = insurer.cmf_code
    payload.coverages = [
        ProposalCoverageRead.model_validate(coverage) for coverage in proposal.coverages
    ]
    return payload


# --- 1. SUGGEST --------------------------------------------------------------


@router.post(
    "/proposals/extract",
    response_model=ExtractionSuggestionResponse,
    summary="Read a proposal document and return a suggestion (writes NO proposal)",
)
def extract_proposal(
    payload: ExtractionRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Create")),
) -> ExtractionSuggestionResponse:
    """Parse an already-uploaded document into the standard proposal shape.

    Suggest-only: the response is a proposal-shaped *draft* plus the persisted
    ``extraction`` that produced it. Committing it is a separate, human-driven
    call to ``/ai/proposals/{extraction_id}/confirm``.
    """
    document = ai_service.get_document(db, payload.document_id, broker_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {payload.document_id} not found",
        )

    try:
        result = ai_service.extract_proposal(
            db, document_id=document.id, broker_id=broker_id, user=current_user
        )
    except AIError as exc:
        raise _ai_http_error(exc) from exc

    return ExtractionSuggestionResponse(
        extraction=_extraction_read(result.extraction),
        suggestion=result.suggestion,
        parsed=result.parsed,
        warnings=result.warnings,
    )


@router.get(
    "/extractions/{extraction_id}",
    response_model=ExtractionSuggestionResponse,
    summary="Re-read a persisted extraction and its suggestion",
)
def read_extraction(
    extraction_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "View")),
) -> ExtractionSuggestionResponse:
    extraction = ai_service.get_extraction(db, extraction_id, broker_id)
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extraction {extraction_id} not found",
        )

    suggestion = None
    warnings: list[str] = []
    if extraction.parsed:
        try:
            suggestion = ProposalSuggestion.model_validate(extraction.parsed)
        except Exception:  # noqa: BLE001 - a stale parse must not break the read
            warnings.append("The stored suggestion no longer matches the proposal schema")

    return ExtractionSuggestionResponse(
        extraction=_extraction_read(extraction),
        suggestion=suggestion,
        parsed=extraction.parsed,
        warnings=warnings,
    )


# --- 2. CONFIRM -> COMMIT ----------------------------------------------------


@router.post(
    "/proposals/{extraction_id}/confirm",
    response_model=ProposalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Commit a reviewed suggestion as a real proposal",
)
def confirm_proposal(
    extraction_id: int,
    payload: ProposalConfirmRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Approve")),
) -> ProposalRead:
    """Create the proposal from the payload the human approved.

    The payload — not ``extraction.parsed`` — is what gets committed: the broker
    may have corrected any field. The extraction stays linked as provenance, and
    the source document is the exact file the extraction read
    (``proposal.source_document_id`` is NOT NULL by rule).
    """
    extraction = ai_service.get_extraction(db, extraction_id, broker_id)
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extraction {extraction_id} not found",
        )

    quote_request = ai_service.get_quote_request(db, payload.quote_request_id, broker_id)
    if quote_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Quote request {payload.quote_request_id} not found",
        )

    existing = ai_service.db_proposal_for_extraction(db, extraction.id, broker_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Extraction {extraction_id} was already committed as proposal {existing.id}",
        )

    try:
        proposal = ai_service.confirm_proposal(
            db,
            extraction=extraction,
            quote_request=quote_request,
            suggestion=payload.proposal,
            broker_id=broker_id,
            user=current_user,
            status=payload.status,
            is_confirmed=payload.is_confirmed,
        )
    except MoneyInconsistent as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Premium figures are inconsistent: {exc}",
        ) from exc
    except ValueError as exc:
        # Insurer identification failures (missing / malformed rut or cmf_code).
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AIError as exc:
        db.rollback()
        raise _ai_http_error(exc) from exc

    return _proposal_read(db, proposal)


# --- 3. Chat -----------------------------------------------------------------


def _assert_scope_permission(user: User, scope: str) -> None:
    """A proposal/quote-scoped thread additionally needs Proposals:View."""
    if scope in (AgentScope.PROPOSAL.value, AgentScope.QUOTE.value):
        if not user_has_permission(user, "Proposals", "View"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not permitted to View on Proposals",
            )


@router.post(
    "/threads",
    response_model=ThreadRead,
    status_code=status.HTTP_201_CREATED,
    summary="Open an agent thread (general, or anchored to a proposal / quote)",
)
def create_thread(
    payload: ThreadCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> ThreadRead:
    _assert_scope_permission(current_user, payload.scope)
    try:
        thread = ai_service.create_thread(
            db,
            broker_id=broker_id,
            user=current_user,
            scope=payload.scope,
            entity_id=payload.entity_id,
            title=payload.title,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return ThreadRead.model_validate(thread)


@router.get(
    "/threads",
    response_model=ThreadList,
    summary="List the caller's own threads in this workspace",
)
def list_threads(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> ThreadList:
    threads = ai_service.list_threads(db, broker_id=broker_id, user_id=current_user.id)
    items = [ThreadRead.model_validate(thread) for thread in threads]
    return ThreadList(items=items, total=len(items))


def _load_own_thread(db: Session, thread_id: int, broker_id: int, user: User):
    """Tenant scope + ownership: a thread is private to the user who opened it."""
    thread = ai_service.get_thread(db, thread_id, broker_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Thread {thread_id} not found"
        )
    if thread.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This thread belongs to another user"
        )
    return thread


@router.get(
    "/threads/{thread_id}/messages",
    response_model=MessageList,
    summary="Full transcript of a thread",
)
def get_messages(
    thread_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> MessageList:
    thread = _load_own_thread(db, thread_id, broker_id, current_user)
    messages = ai_service.list_messages(db, thread)
    items = [MessageRead.model_validate(message) for message in messages]
    return MessageList(items=items, total=len(items))


@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageExchange,
    status_code=status.HTTP_201_CREATED,
    summary="Send one turn; the agent answers with tenant-scoped context",
)
def post_message(
    thread_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> MessageExchange:
    """One chat turn.

    Context is assembled SERVER-SIDE and always scoped to the caller's broker —
    the client cannot widen it. On a provider failure nothing is persisted, so a
    retry replays cleanly.
    """
    thread = _load_own_thread(db, thread_id, broker_id, current_user)
    _assert_scope_permission(current_user, thread.scope.value)

    try:
        user_message, assistant_message = ai_service.send_message(
            db, thread=thread, broker_id=broker_id, content=payload.content
        )
    except AIError as exc:
        db.rollback()
        raise _ai_http_error(exc) from exc

    return MessageExchange(
        thread_id=thread.id,
        user_message=MessageRead.model_validate(user_message),
        assistant_message=MessageRead.model_validate(assistant_message),
    )
