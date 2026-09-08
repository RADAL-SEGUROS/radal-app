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

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.core.permissions import user_has_permission
from app.core.roles_config import MODULES
from app.models.ai import (
    AgentAction,
    AgentActionStatus,
    AgentScope,
    AgentThread,
    Extraction,
)
from app.models.case_file import CaseFile
from app.models.enums import EntityType
from app.models.proposal import Proposal
from app.models.user import User
from app.schemas.ai import (
    AgentActionList,
    AgentActionRead,
    AgentTurnRequest,
    AgentTurnResponse,
    CategoryFieldRead,
    CategoryListResponse,
    CategorySpecRead,
    DocumentConfirmRequest,
    DocumentConfirmResponse,
    DocumentExtractionRequest,
    DocumentExtractionResponse,
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
    SummaryRead,
    ThreadCreate,
    ThreadList,
    ThreadRead,
)
from app.schemas.extraction.registry import registry_as_json
from app.services import ai as ai_service
from app.services import extraction_commit
from app.services.ai import (
    AIError,
    AINotConfigured,
    AIParseError,
    AIProviderError,
    AITimeout,
    DocumentUnavailable,
    MoneyInconsistent,
    UnsupportedCategory,
)

router = APIRouter(prefix="/ai", tags=["ai"])


def _permission(module: str, action: str, *, fallback: tuple[str, str]):
    """``require_permission`` that tolerates a module the matrix does not have YET.

    ``CaseFiles`` and friends are added to ``MODULES`` by the permissions owner;
    until then this router must still import. The fallback is always a strictly
    narrower or equivalent gate, never an open door.
    """
    if module in MODULES:
        return require_permission(module, action)
    return require_permission(*fallback)


# --- Error mapping -----------------------------------------------------------

_AI_STATUS = {
    AINotConfigured: status.HTTP_503_SERVICE_UNAVAILABLE,
    AITimeout: status.HTTP_504_GATEWAY_TIMEOUT,
    AIProviderError: status.HTTP_502_BAD_GATEWAY,
    AIParseError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    DocumentUnavailable: status.HTTP_422_UNPROCESSABLE_ENTITY,
    MoneyInconsistent: status.HTTP_422_UNPROCESSABLE_ENTITY,
    UnsupportedCategory: status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _ai_http_error(exc: AIError) -> HTTPException:
    """Translate a typed AI failure into its HTTP status. Never a 500.

    The stable machine code also rides on ``X-Radal-AI-Error`` so a client can
    branch on the failure without parsing the (human) detail string.
    """
    code = _AI_STATUS.get(type(exc), status.HTTP_502_BAD_GATEWAY)
    return HTTPException(
        status_code=code,
        detail=str(exc),
        headers={"X-Radal-AI-Error": ai_service.ai_error_code(exc)},
    )


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


# --- 4. Streaming chat (SSE) -------------------------------------------------


@router.post(
    "/threads/{thread_id}/stream",
    summary="Send one turn and stream the answer as Server-Sent Events",
    response_class=StreamingResponse,
)
async def stream_thread(
    thread_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> StreamingResponse:
    """``event: start | token | done | error`` over ``text/event-stream``.

    Both messages persist only after the stream completes — a mid-stream failure
    emits an ``error`` frame and writes nothing. It is never an HTTP 500: by the
    time the provider fails the 200 is already on the wire.

    Configuration failures ARE answered before the stream opens, so an unset
    ``AI_API_KEY`` still yields a clean 503.

    ⚠️ Behind CloudFront this does not truly stream: the OAC contract requires
    the Function URL invoke mode and the LWA env to both be ``buffered``
    (docs/deployment.md, constraint 4). Do NOT change the invoke mode to fix
    that — ``POST /ai/threads/{id}/messages`` is the documented fallback.
    """
    thread = _load_own_thread(db, thread_id, broker_id, current_user)
    _assert_scope_permission(current_user, thread.scope.value)

    try:
        ai_service.ensure_ai_configured()
    except AIError as exc:
        raise _ai_http_error(exc) from exc

    async def _frames():
        async for event, data in ai_service.stream_message(
            db, thread=thread, broker_id=broker_id, content=payload.content
        ):
            body = json.dumps(data, ensure_ascii=False, default=str)
            yield f"event: {event}\ndata: {body}\n\n"

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell every proxy in the path not to buffer. CloudFront/LWA still
            # will (see the docstring); nginx and the dev server will not.
            "X-Accel-Buffering": "no",
        },
    )


# --- 5. Generic, registry-driven extraction ----------------------------------


@router.get(
    "/categories",
    response_model=CategoryListResponse,
    summary="The document category registry as JSON (drives the generic review form)",
)
def list_categories(
    current_user: User = Depends(require_permission("Documents", "View")),
) -> CategoryListResponse:
    """Every extractable category: code, section, direction, prompt version, fields.

    The UI renders a suggestion form from this, so adding a category needs no
    new React component.
    """
    items = [
        CategorySpecRead(
            category=str(row["category"]),
            canonical_category=str(row["canonical_category"]),
            is_alias=bool(row["is_alias"]),
            code=row["code"],  # type: ignore[arg-type]
            codes=list(row["codes"]),  # type: ignore[arg-type]
            section=str(row["section"]),
            direction=str(row["direction"]),
            prompt_version=str(row["prompt_version"]),
            module=str(row["module"]),
            schema_name=str(row["schema_name"]),
            prefill_target=str(row["prefill_target"]),
            extraction_kind=str(row["extraction_kind"]),
            guidance=str(row["guidance"]),
            fields=[CategoryFieldRead.model_validate(field) for field in row["fields"]],  # type: ignore[union-attr]
        )
        for row in registry_as_json()
    ]
    return CategoryListResponse(items=items, total=len(items))


@router.post(
    "/documents/extract",
    response_model=DocumentExtractionResponse,
    summary="Read any case-file document with its registry schema (writes NO entity)",
)
def extract_document(
    payload: DocumentExtractionRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Documents", "Upload")),
) -> DocumentExtractionResponse:
    """SUGGEST for all 26 categories.

    One ``extraction`` row is written in every outcome — success, provider
    failure, unparsable answer — and nothing else is ever written here.
    """
    document = ai_service.get_document(db, payload.document_id, broker_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {payload.document_id} not found",
        )

    try:
        result = ai_service.extract_document(
            db,
            document_id=document.id,
            broker_id=broker_id,
            user=current_user,
            category=payload.category,
        )
    except AIError as exc:
        raise _ai_http_error(exc) from exc

    spec = result.spec
    return DocumentExtractionResponse(
        extraction=_extraction_read(result.extraction),
        category=(payload.category or document.category.value),
        canonical_category=spec.category.value,
        schema_name=spec.schema.__name__,
        schema_version=spec.prompt_version,
        prefill_target=spec.prefill_target,
        section=spec.section.value,
        payload=(result.payload.model_dump(mode="json") if result.payload is not None else None),
        parsed=result.parsed,
        suggestion=result.suggestion,
        warnings=result.warnings,
    )


@router.post(
    "/documents/{extraction_id}/confirm",
    response_model=DocumentConfirmResponse,
    summary="Commit the human-reviewed payload of any extraction",
)
def confirm_document(
    extraction_id: int,
    payload: DocumentConfirmRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Documents", "Upload")),
) -> DocumentConfirmResponse:
    """COMMIT, generic (spec §6.6): the payload writes its prefill target.

    For the insurer-quotation categories this creates the proposal (with
    ``Proposals.Approve`` additionally required, and a ``quote_request_id`` in
    the body). Every other category dispatches to
    ``app.services.extraction_commit``, which commits the human-reviewed payload
    to the registry's target entity — gated by that target's own module
    permission on top of ``Documents.Upload``, tenant-scoped, idempotent-safe
    and transactional. Genuinely informational categories commit nothing and
    say so (``informational=True`` + ``commit.detail``).
    """
    extraction = ai_service.get_extraction(db, extraction_id, broker_id)
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extraction {extraction_id} not found",
        )

    category = payload.category or (
        extraction.category.value if extraction.category else None
    )
    # Confirming without a body means "the suggestion is correct as it stands".
    # Falling through with the empty default would overwrite ``extraction.parsed``
    # with an all-null payload and destroy what the model read.
    reviewed = (
        payload.payload
        if "payload" in payload.model_fields_set
        else dict(extraction.parsed or {})
    )
    try:
        model, warnings = ai_service.validate_category_payload(category, reviewed)
        spec = ai_service.spec_for_category(category)
    except AIError as exc:
        raise _ai_http_error(exc) from exc

    is_quotation = spec.category.value == "insurer_quotation"

    if is_quotation and payload.quote_request_id is not None:
        if not user_has_permission(current_user, "Proposals", "Approve"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not permitted to Approve on Proposals",
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
                detail=(
                    f"Extraction {extraction_id} was already committed as "
                    f"proposal {existing.id}"
                ),
            )

        suggestion, legacy_warnings = ai_service.legacy_proposal_suggestion(
            model, reviewed
        )
        try:
            proposal = ai_service.confirm_proposal(
                db,
                extraction=extraction,
                quote_request=quote_request,
                suggestion=suggestion,
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
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except AIError as exc:
            db.rollback()
            raise _ai_http_error(exc) from exc

        ai_service.record_confirmation(
            db,
            extraction=extraction,
            payload=model,
            user=current_user,
            target=spec.prefill_target,
            applied=True,
        )
        return DocumentConfirmResponse(
            extraction=_extraction_read(extraction),
            category=spec.category.value,
            prefill_target=spec.prefill_target,
            applied=True,
            commit={
                "target": spec.prefill_target,
                "entity_type": "proposal",
                "entity_id": proposal.id,
                "detail": "Proposal created from the confirmed quotation",
            },
            payload=model.model_dump(mode="json"),
            proposal=_proposal_read(db, proposal),
            warnings=[*warnings, *legacy_warnings],
        )

    if is_quotation:
        # An insurer quotation without a quote_request_id cannot become a
        # proposal; record the review and tell the client what is missing.
        ai_service.record_confirmation(
            db,
            extraction=extraction,
            payload=model,
            user=current_user,
            target=spec.prefill_target,
            applied=False,
        )
        return DocumentConfirmResponse(
            extraction=_extraction_read(extraction),
            category=spec.category.value,
            prefill_target=spec.prefill_target,
            applied=False,
            payload=model.model_dump(mode="json"),
            commit={
                "target": spec.prefill_target,
                "detail": "Provide quote_request_id to commit this quotation as a proposal",
            },
            warnings=warnings,
        )

    # --- Generic commit: the registry's prefill target, per category ---------
    gate = extraction_commit.COMMIT_PERMISSIONS.get(spec.category)
    if gate is not None and not user_has_permission(current_user, *gate):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not permitted to {gate[1]} on {gate[0]}",
        )

    try:
        result = extraction_commit.commit_extraction(
            db,
            extraction=extraction,
            spec=spec,
            payload=model,
            broker_id=broker_id,
            user=current_user,
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

    if result.applied and result.entity_type is not None:
        record_activity(
            db,
            broker_id=broker_id,
            user=current_user,
            action="extraction.committed",
            entity_type=EntityType(result.entity_type),
            entity_id=result.entity_id,
            description=f"Extracción confirmada aplicada a {result.entity_type}",
            meta={"extraction_id": extraction.id, "category": spec.category.value},
        )

    # One transaction per confirm: record_confirmation stages the reviewed
    # payload + audit stamp and commits everything the committer flushed.
    ai_service.record_confirmation(
        db,
        extraction=extraction,
        payload=model,
        user=current_user,
        target=spec.prefill_target,
        applied=result.applied,
    )
    return DocumentConfirmResponse(
        extraction=_extraction_read(extraction),
        category=spec.category.value,
        prefill_target=spec.prefill_target,
        applied=result.applied,
        informational=result.informational,
        commit=result.as_payload(),
        payload=model.model_dump(mode="json"),
        warnings=[*warnings, *result.warnings],
    )


# --- 6. Summaries (suggest -> edit -> confirmar) ------------------------------


@router.post(
    "/proposals/{proposal_id}/summary",
    response_model=SummaryRead,
    summary="Generate the Spanish summary of one proposal (unconfirmed)",
)
def summarize_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Proposals", "Edit")),
) -> SummaryRead:
    """Prose from the CONFIRMED structured data, written unconfirmed.

    The broker edits it and confirms it; nothing is sent anywhere from here.
    """
    proposal = ai_service.get_proposal(db, proposal_id, broker_id)
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Proposal {proposal_id} not found"
        )

    try:
        result = ai_service.summarize_proposal(
            db, proposal=proposal, broker_id=broker_id, user=current_user
        )
    except AIError as exc:
        raise _ai_http_error(exc) from exc

    return SummaryRead(
        extraction_id=result.extraction.id,
        text=result.text,
        model=result.model,
        prompt_version=result.prompt_version,
        is_confirmed=False,
        proposal_id=proposal.id,
    )


@router.post(
    "/case-files/{case_file_id}/summary",
    response_model=SummaryRead,
    summary="Generate the Spanish summary of an expediente (unconfirmed)",
)
def summarize_case_file(
    case_file_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(
        _permission("CaseFiles", "Edit", fallback=("Placements", "Edit"))
    ),
) -> SummaryRead:
    case_file = db.scalars(
        select(CaseFile).where(CaseFile.id == case_file_id, CaseFile.broker_id == broker_id)
    ).first()
    if case_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case file {case_file_id} not found"
        )

    try:
        result = ai_service.summarize_case_pack(
            db, case_file=case_file, broker_id=broker_id, user=current_user
        )
    except AIError as exc:
        raise _ai_http_error(exc) from exc

    return SummaryRead(
        extraction_id=result.extraction.id,
        text=result.text,
        model=result.model,
        prompt_version=result.prompt_version,
        is_confirmed=False,
        case_file_id=case_file.id,
    )


# --- 7. The agentic turn + pending actions (v4 agent spec §7) -----------------
#
# Coarse gate: Dashboard.View, like the existing chat. The REAL authority per
# tool is enforced inside the loop (reads, under the caller's own grants) and
# at confirm time (writes, re-checked against the registry for the CONFIRMING
# user). CLAUDE.md rule 6: a write NEVER executes without a human confirm.


def _agent_action_read(action: AgentAction, user: User) -> AgentActionRead:
    from app.services import agent_tools

    payload = AgentActionRead.model_validate(action)
    payload.allowed = (
        agent_tools.action_allowed(action, user)
        if action.status == AgentActionStatus.PROPOSED
        else False
    )
    return payload


@router.post(
    "/agent/messages",
    response_model=AgentTurnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="One agentic turn: context refs + tool loop + pending write proposals",
)
def post_agent_message(
    payload: AgentTurnRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> AgentTurnResponse:
    """READ tools execute inline; WRITE tools only PROPOSE an ``agent_action``.

    On any provider failure NOTHING is persisted (502/503/504 with
    ``X-Radal-AI-Error``); a retry replays cleanly. Without a ``thread_id`` a
    new general thread is opened and committed with the turn.
    """
    if payload.thread_id is not None:
        thread = _load_own_thread(db, payload.thread_id, broker_id, current_user)
    else:
        thread = AgentThread(
            broker_id=broker_id, user_id=current_user.id, scope=AgentScope.GENERAL
        )
        db.add(thread)
        db.flush()  # committed with the turn; rolled back with a failed one

    refs = [ref.model_dump() for ref in payload.context_refs]
    try:
        result = ai_service.run_agent_turn(
            db,
            thread=thread,
            broker_id=broker_id,
            user=current_user,
            content=payload.content,
            context_refs=refs,
        )
    except ai_service.UnsupportedContextRef as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unsupported_ref", "entity_type": exc.entity_type},
        ) from exc
    except ai_service.ContextRefNotFound as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except AIError as exc:
        db.rollback()
        raise _ai_http_error(exc) from exc

    return AgentTurnResponse(
        thread=ThreadRead.model_validate(result.thread),
        messages=[MessageRead.model_validate(message) for message in result.messages],
        pending_actions=[
            _agent_action_read(action, current_user) for action in result.actions
        ],
    )


@router.get(
    "/agent/actions",
    response_model=AgentActionList,
    summary="Rehydrate the caller's agent actions (own threads only)",
)
def list_agent_actions(
    thread_id: int | None = Query(default=None, gt=0),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> AgentActionList:
    rows = ai_service.list_agent_actions(
        db,
        broker_id=broker_id,
        user_id=current_user.id,
        thread_id=thread_id,
        statuses=status_filter,
    )
    items = [_agent_action_read(action, current_user) for action in rows]
    return AgentActionList(items=items, total=len(items))


def _load_own_action(
    db: Session, action_id: int, broker_id: int, user: User
) -> tuple[AgentAction, AgentThread]:
    """Broker scope + thread ownership; a foreign row is a 404, never a 403.

    Loaded ``FOR UPDATE`` where the engine supports it so two racing confirms
    serialise (the loser sees the 409); SQLite ignores the clause.
    """
    action = db.execute(
        select(AgentAction)
        .where(AgentAction.id == action_id, AgentAction.broker_id == broker_id)
        .with_for_update()
    ).scalars().first()
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Action {action_id} not found"
        )
    thread = ai_service.get_thread(db, action.thread_id, broker_id)
    if thread is None:  # pragma: no cover - FK guarantees the thread exists
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Action {action_id} not found"
        )
    if thread.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action belongs to another user's thread",
        )
    return action, thread


def _require_pending(action: AgentAction) -> None:
    if action.status != AgentActionStatus.PROPOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "action_not_pending", "status": action.status.value},
        )


@router.post(
    "/agent/actions/{action_id}/confirm",
    response_model=AgentActionRead,
    summary="Execute a proposed write under the confirming user's real RBAC gate",
)
def confirm_agent_action(
    action_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> AgentActionRead:
    """The COMMIT of rule 6 — same transaction, invariants and activity rows as
    the normal router, plus an ``agent.action_confirmed`` provenance row.

    403 (missing grant) and the executor's own 404/422/409 leave the row
    ``proposed``; only a crash after the checks lands it ``failed``.
    """
    action, thread = _load_own_action(db, action_id, broker_id, current_user)
    _require_pending(action)
    action = ai_service.confirm_agent_action(
        db, action=action, thread=thread, broker_id=broker_id, user=current_user
    )
    return _agent_action_read(action, current_user)


@router.post(
    "/agent/actions/{action_id}/discard",
    response_model=AgentActionRead,
    summary="Discard a proposed write (declining requires no privilege)",
)
def discard_agent_action(
    action_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Dashboard", "View")),
) -> AgentActionRead:
    action, thread = _load_own_action(db, action_id, broker_id, current_user)
    _require_pending(action)
    action = ai_service.discard_agent_action(
        db, action=action, thread=thread, broker_id=broker_id, user=current_user
    )
    return _agent_action_read(action, current_user)
