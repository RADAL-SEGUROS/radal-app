"""AI pipeline — DeepInfra (OpenAI-compatible) proposal processor + chat agents.

Three capabilities, one provider (docs/v2-architecture.md §5):

1. **Proposal processor** — read an uploaded document, emit the standard
   ``ProposalSuggestion`` shape (money, per-peril deductibles, coverages,
   exclusions, and the issuing insurer's ``rut`` / ``cmf_code``).
2. **Proposal chat agent** — scoped to one proposal or one quote request.
3. **General broker agent** — reads the broker's own clients / placements /
   proposals as server-side context.

Two rules govern everything here:

**SUGGEST -> HUMAN CONFIRM -> COMMIT.** ``extract_proposal`` NEVER writes a
``proposal``. It writes exactly one ``extraction`` row (model, prompt version,
raw output, parsed result, confidence, source document) and returns the
suggestion. Only ``confirm_proposal``, driven by a human request carrying the
reviewed payload, creates the proposal.

**Tenant scoping.** Every read and write is filtered by the caller's
``broker_id``. Canonical tables (``insurer``) are reached by normalised
identifier, never by broker.

The API key is read from ``settings.AI_API_KEY`` (env / ``backend/.env``) and is
never hardcoded. When it is absent, or the provider errors or times out, this
module raises a typed :class:`AIError` — the router maps those to 4xx/5xx
*without* ever letting an LLM hiccup surface as a 500.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.models.ai import (
    AgentAction,
    AgentActionStatus,
    AgentMessage,
    AgentRole,
    AgentScope,
    AgentThread,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
)
from app.models.case_file import CaseFile, CasePack
from app.models.client import Client
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    CaseFileKind,
    CaseOrigin,
    CaseSection,
    CoverageKind,
    EntityType,
    RecordExpedienteStatus,
)
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.line_record_schema import LineRecordSchema
from app.models.placement import Placement
from app.models.proposal import Proposal, ProposalCoverage, ProposalOrigin, ProposalStatus
from app.models.quote import QuoteRequest
from app.models.record_expediente import RecordExpediente
from app.models.user import User
from app.schemas.ai import ProposalSuggestion
from app.schemas.proposal import coverage_key, reconcile_money, reconcile_money_verbose
from app.services.ai_models import AITask, model_for
from app.schemas.extraction.common import (
    UF as _CommonUF,
    ExtractionModel as _CommonExtractionModel,
    FlexDate as _CommonFlexDate,
    Int as _CommonInt,
    Pct as _CommonPct,
    RootExtraction as _CommonRootExtraction,
    Row as _CommonRow,
    Str as _CommonStr,
)
from app.services.identifiers import (
    InvalidCmf,
    InvalidRut,
    normalize_codigo_cmf,
    validate_rut,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from app.models.enums import PackKind
    from app.schemas.extraction.registry import CategorySpec

logger = logging.getLogger(__name__)

__all__ = [
    "AIError",
    "AINotConfigured",
    "AIProviderError",
    "AITimeout",
    "AIParseError",
    "DocumentUnavailable",
    "MoneyInconsistent",
    "UnsupportedCategory",
    "AI_ERROR_CODES",
    "ai_error_code",
    "ensure_ai_configured",
    # tool-calling structured-output path (Phase 1)
    "AITask",
    "model_for",
    "ToolCallResult",
    "tool_call",
    "optional_validator_for",
    "PROMPT_VERSION",
    "SUMMARY_PROMPT_VERSION",
    "DocumentExtractionResult",
    "ExtractionResult",
    "SummaryResult",
    "extract_document",
    # v8 dynamic comparison + policy core
    "BUDGET_PROPOSAL_PROMPT_VERSION",
    "BudgetProposalResult",
    "extract_budget_proposal",
    # v9 holistic comparison + outbound propuesta (tool-calling)
    "COMPARE_PROMPT_VERSION",
    "RECOMMEND_PROMPT_VERSION",
    "PROPUESTA_PROMPT_VERSION",
    "ComparisonResult",
    "PropuestaResult",
    "submit_comparison",
    "submit_propuesta",
    # v6 antecedentes expediente (multi-doc consolidation, suggest-only)
    "ANTECEDENTES_PROMPT_VERSION",
    "AntecedentesConsolidation",
    "build_dynamic_schema",
    "resolve_line_record_schema",
    "resolve_account_line_record_schema",
    "consolidate_antecedentes",
    "get_account_case_file",
    "spec_for_category",
    "legacy_proposal_suggestion",
    "validate_category_payload",
    "record_confirmation",
    "summarize_proposal",
    "summarize_case_pack",
    "stream_message",
    "load_document_text",
    "get_document",
    "get_extraction",
    "get_quote_request",
    "get_proposal",
    "get_thread",
    "db_proposal_for_extraction",
    "extract_proposal",
    "find_or_create_insurer",
    "confirm_proposal",
    "create_thread",
    "list_threads",
    "list_messages",
    "send_message",
    "derive_money",
    # v4 agent (tool loop + pending actions)
    "UnsupportedContextRef",
    "ContextRefNotFound",
    "AgentTurnResult",
    "MAX_TOOL_ITERATIONS",
    "MAX_TOOL_CALLS_PER_TURN",
    "MAX_CONTEXT_REFS",
    "resolve_context_refs",
    "run_agent_turn",
    "get_agent_action",
    "list_agent_actions",
    "confirm_agent_action",
    "discard_agent_action",
]

# Bump whenever the extraction prompt or the target schema changes — every
# extraction row records the version that produced it.
PROMPT_VERSION = "proposal-extract-v1"

# The provider context is finite; a 200-page policy wordings PDF is not useful
# past the terms section. Truncate rather than fail.
MAX_DOCUMENT_CHARS = 45_000
MAX_CHAT_HISTORY_MESSAGES = 24

VAT_RATE = Decimal("0.19")
# UF amounts are quoted to 2 decimals in the market; the column keeps 4.
MONEY_QUANT = Decimal("0.0001")
MONEY_TOLERANCE = Decimal("0.05")
RATE_QUANT = Decimal("0.0001")
RATE_TOLERANCE = Decimal("0.005")


# --- Errors ------------------------------------------------------------------


class AIError(Exception):
    """Base for every failure in this module. The router maps these to HTTP."""


class AINotConfigured(AIError):
    """No API key / SDK available — the feature is off, not broken. -> 503."""


class AIProviderError(AIError):
    """The provider refused, errored or returned something unusable. -> 502."""


class AITimeout(AIError):
    """The provider did not answer in time. -> 504."""


class AIParseError(AIError):
    """The model answered, but not with usable JSON. -> 422 (with the raw text kept)."""


class DocumentUnavailable(AIError):
    """The source document's bytes could not be read. -> 422."""


class MoneyInconsistent(AIError):
    """The confirmed payload violates the Chilean premium invariants. -> 422."""


class UnsupportedCategory(AIError):
    """No registry schema exists for that document category. -> 422."""


# Stable machine codes. They ride on the ``X-Radal-AI-Error`` response header and
# inside the SSE ``error`` frame, so a client can branch on the failure without
# parsing prose.
AI_ERROR_CODES: dict[type[AIError], str] = {
    AINotConfigured: "ai_not_configured",
    AIProviderError: "ai_provider",
    AITimeout: "ai_timeout",
    AIParseError: "ai_parse",
    DocumentUnavailable: "document_unavailable",
    MoneyInconsistent: "money_inconsistent",
    UnsupportedCategory: "unsupported_category",
}


def ai_error_code(exc: BaseException) -> str:
    """The stable code for a typed AI failure (``"ai_provider"`` by default)."""
    return AI_ERROR_CODES.get(type(exc), "ai_provider")


# --- Provider client ---------------------------------------------------------


def _ai_client():
    """Build the OpenAI SDK client pointed at ``AI_BASE_URL``.

    The key comes from settings only. Never inline a credential here.
    """
    if not settings.AI_API_KEY:
        raise AINotConfigured(
            "AI_API_KEY is not set — configure it in backend/.env to enable AI features"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AINotConfigured("The `openai` package is not installed") from exc

    return OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=float(settings.AI_TIMEOUT_SECONDS),
        max_retries=1,
    )


@dataclass
class Completion:
    """A provider answer, normalised."""

    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _run_completion(
    messages: Sequence[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> Completion:
    """Call the provider once and normalise the answer.

    Every provider exception is translated into a typed :class:`AIError`, so no
    caller ever has to know what an ``APIStatusError`` is — and no endpoint can
    500 because the model hiccuped.

    ``max_tokens`` defaults to ``AI_EXTRACTION_MAX_TOKENS``: GLM-5.3-Flash is a
    reasoning model that spends the output budget thinking first, so a small
    ceiling yields an empty ``content``. Callers that want a tighter budget (the
    chat agent) pass one explicitly.
    """
    client = _ai_client()
    model = settings.AI_MODEL
    if max_tokens is None:
        max_tokens = settings.AI_EXTRACTION_MAX_TOKENS

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            OpenAIError,
        )
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AINotConfigured("The `openai` package is not installed") from exc

    def _call(payload: dict[str, Any]):
        return client.chat.completions.create(**payload)

    try:
        try:
            response = _call(kwargs)
        except APIStatusError as exc:
            # Not every DeepInfra-hosted model supports response_format. Retry
            # once in plain mode; the prompt already demands raw JSON.
            if json_mode and exc.status_code in (400, 422):
                logger.warning("AI json_mode rejected by provider, retrying plain")
                kwargs.pop("response_format", None)
                response = _call(kwargs)
            else:
                raise
    except APITimeoutError as exc:
        raise AITimeout(f"The AI provider timed out after {settings.AI_TIMEOUT_SECONDS}s") from exc
    except APIConnectionError as exc:
        raise AIProviderError("Could not reach the AI provider") from exc
    except APIStatusError as exc:
        raise AIProviderError(
            f"The AI provider returned {exc.status_code}"
        ) from exc
    except OpenAIError as exc:
        raise AIProviderError(f"AI provider error: {type(exc).__name__}") from exc
    except Exception as exc:  # noqa: BLE001 - last-resort net; must not become a 500
        raise AIProviderError(f"Unexpected AI provider failure: {type(exc).__name__}") from exc

    choices = getattr(response, "choices", None) or []
    if not choices:
        raise AIProviderError("The AI provider returned no choices")
    message = choices[0].message
    content = (getattr(message, "content", None) or "").strip()
    if not content:
        # Reasoning model whose budget was spent thinking: the answer, if any,
        # is in the reasoning trace. Try it before declaring an empty message.
        reasoning = getattr(message, "reasoning_content", None) or getattr(
            message, "reasoning", None
        )
        if isinstance(reasoning, str):
            content = _strip_reasoning(reasoning).strip()
    if not content:
        raise AIProviderError("The AI provider returned an empty message")

    usage = getattr(response, "usage", None)
    try:
        raw = response.model_dump()
    except Exception:  # noqa: BLE001 - raw dump is best-effort audit data
        raw = {"content": content}

    return Completion(
        content=content,
        model=getattr(response, "model", None) or model,
        prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        raw=raw,
    )


# --- Tool-calling structured output (Phase 1) --------------------------------
#
# GLM-5.3-Flash on DeepInfra SUPPORTS tool/function calling (verified live): the
# arguments arrive as a JSON string on ``tool_calls[0].function.arguments`` and
# ``finish_reason == "tool_calls"``; ``reasoning_content`` may be present. This is
# the new, more reliable structured-output path — a slim tool schema forces a
# well-formed object instead of hoping JSON-mode prose parses. DeepInfra is
# flaky, so this helper RETRIES with exponential backoff on transient failures
# and never lets an LLM hiccup surface as anything but a typed :class:`AIError`.

# Retry backoff base (seconds). Only slept between attempts, never on success, so
# the hermetic suite (which fakes ``tool_call`` or the client) never sleeps.
_TOOL_CALL_BACKOFF_BASE = 0.5


@dataclass
class ToolCallResult:
    """A normalised tool/function-call answer.

    ``parsed`` is the raw JSON object from ``function.arguments``; ``payload`` is
    the validated Pydantic model when a ``schema`` was given (``None`` otherwise,
    or when the drop-bad-fields coercion could not build even an empty model);
    ``raw`` is the audit blob for the extraction row; ``usage`` carries the token
    counts; ``reasoning`` keeps the GLM interleaved-thinking trace (never folded
    into the payload).
    """

    parsed: dict[str, Any] | None
    payload: Any | None
    raw: dict[str, Any]
    usage: dict[str, Any] | None = None
    reasoning: str | None = None
    warnings: list[str] = field(default_factory=list)
    model: str | None = None
    # The provider's stop reason. ``"length"`` means the output budget was
    # exhausted and the tool arguments were TRUNCATED — a silent-empty-table trap.
    finish_reason: str | None = None


def _tool_call_client(*, timeout: int):
    """Build a fresh OpenAI client for a tool call.

    Separate from :func:`_ai_client` so it takes an explicit ``timeout`` and sets
    ``max_retries=0`` — this helper owns retrying, and an SDK-level retry would
    hide the failure. A clean monkeypatch seam for hermetic tests.
    """
    if not settings.AI_API_KEY:
        raise AINotConfigured(
            "AI_API_KEY is not set — configure it in backend/.env to enable AI features"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AINotConfigured("The `openai` package is not installed") from exc

    return OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=float(timeout),
        max_retries=0,
    )


def _read_tool_call(
    response: Any,
) -> tuple[str | None, str | None, dict[str, Any] | None, str | None, str | None]:
    """Pull ``(arguments, reasoning, usage, model, finish_reason)`` out of a completion."""
    choices = getattr(response, "choices", None) or []
    arguments: str | None = None
    reasoning: str | None = None
    finish_reason: str | None = None
    if choices:
        finish_reason = getattr(choices[0], "finish_reason", None)
        message = getattr(choices[0], "message", None)
        if message is not None:
            raw_reasoning = getattr(message, "reasoning_content", None) or getattr(
                message, "reasoning", None
            )
            if isinstance(raw_reasoning, str) and raw_reasoning.strip():
                reasoning = raw_reasoning
            for call in getattr(message, "tool_calls", None) or []:
                fn = getattr(call, "function", None)
                candidate = getattr(fn, "arguments", None) if fn is not None else None
                if isinstance(candidate, str) and candidate.strip():
                    arguments = candidate
                    break

    usage_obj = getattr(response, "usage", None)
    usage: dict[str, Any] | None = None
    if usage_obj is not None:
        usage = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
            "completion_tokens": getattr(usage_obj, "completion_tokens", None),
        }
    model_name = getattr(response, "model", None) or None
    return arguments, reasoning, usage, model_name, finish_reason


def tool_call(
    *,
    messages: Sequence[dict[str, str]],
    tool_schema: dict[str, Any],
    model: str,
    timeout: int,
    max_retries: int = 2,
    max_tokens: int | None = None,
    schema: "type | None" = None,
) -> ToolCallResult:
    """Force one function call against ``tool_schema`` and return its parsed args.

    Builds an OpenAI client against ``AI_BASE_URL`` / ``AI_API_KEY``, calls
    ``chat.completions`` with ``tools=[{type:function, function:tool_schema}]``
    and ``tool_choice`` forced to that function, then parses
    ``tool_calls[0].function.arguments`` (a JSON string). When ``schema`` is
    given the parsed object is validated through the existing drop-bad-fields
    :func:`_coerce_payload` leniency, so one unusable field never throws away a
    reviewable read.

    DeepInfra is unreliable: transient failures (timeout, 5xx / 429, connection
    error, empty / malformed / no-``tool_calls`` answer) are RETRIED with
    exponential backoff up to ``max_retries``, after which the last failure is
    raised as a typed :class:`AIError` (``AITimeout`` / ``AIProviderError`` /
    ``AIParseError``) — never a naked exception, never a 500. GLM
    ``reasoning_content`` is captured but never folded into the payload.
    """
    ensure_ai_configured()
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            OpenAIError,
        )
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AINotConfigured("The `openai` package is not installed") from exc

    tool_name = tool_schema.get("name")
    client = _tool_call_client(timeout=timeout)
    request: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "tools": [{"type": "function", "function": tool_schema}],
        "temperature": 0.0,
        "max_tokens": max_tokens if max_tokens is not None else settings.AI_EXTRACTION_MAX_TOKENS,
    }
    if tool_name:
        request["tool_choice"] = {"type": "function", "function": {"name": tool_name}}

    last_error: AIError | None = None
    for attempt in range(max_retries + 1):
        transient: AIError | None = None
        response = None
        try:
            response = client.chat.completions.create(**request)
        except APITimeoutError:
            transient = AITimeout(
                f"The AI provider timed out after {timeout}s"
            )
        except APIConnectionError:
            transient = AIProviderError("Could not reach the AI provider")
        except APIStatusError as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is not None and status_code < 500 and status_code != 429:
                # A 4xx that is not rate-limiting will not fix itself on retry.
                raise AIProviderError(f"The AI provider returned {status_code}") from exc
            transient = AIProviderError(
                f"The AI provider returned {status_code or 'a server error'}"
            )
        except AINotConfigured:
            raise
        except OpenAIError as exc:
            transient = AIProviderError(f"AI provider error: {type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 - last-resort; never a naked raise
            transient = AIProviderError(
                f"Unexpected AI provider failure: {type(exc).__name__}"
            )

        if response is not None:
            arguments, reasoning, usage, model_name, finish_reason = _read_tool_call(response)
            if finish_reason == "length":
                # The output budget was exhausted mid-arguments: whatever JSON we
                # got is TRUNCATED (an empty/near-empty dict at worst). Never let
                # that commit silently — retry, then block as a provider error.
                transient = AIProviderError(
                    "The AI provider truncated the tool call (finish_reason=length); "
                    "the output budget was too small"
                )
            elif not arguments:
                transient = AIProviderError("The AI provider returned no tool_calls")
            else:
                try:
                    parsed = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    transient = AIParseError(
                        "The tool call arguments were not valid JSON"
                    )
                else:
                    if not isinstance(parsed, dict):
                        transient = AIParseError(
                            "The tool call arguments were not a JSON object"
                        )
                    else:
                        warnings: list[str] = []
                        payload = None
                        if schema is not None:
                            payload, warnings = _coerce_payload(schema, parsed)
                        raw: dict[str, Any] = {
                            "content": arguments,
                            "reasoning": reasoning,
                            "parsed": parsed,
                        }
                        return ToolCallResult(
                            parsed=parsed,
                            payload=payload,
                            raw=raw,
                            usage=usage,
                            reasoning=reasoning,
                            warnings=warnings,
                            model=model_name,
                            finish_reason=finish_reason,
                        )

        last_error = transient
        if attempt < max_retries:
            time.sleep(_TOOL_CALL_BACKOFF_BASE * (2 ** attempt))

    raise last_error or AIProviderError("The AI provider tool call failed")


def optional_validator_for(ramo: str | None):
    """Per-ramo minimum-data validator hook — a STUB, OFF by default.

    Returns ``None`` today for every ``ramo``, so no flow validates. This is the
    clean seam a future minimum-data validator plugs into (e.g. "a Todo Riesgo
    offer must state earthquake deductible basis") WITHOUT touching the
    extraction flow: a caller that wants the check does
    ``validator = optional_validator_for(ramo)`` and skips when it is ``None``.
    The signature a real validator will honour is ``Callable[[Any], list[str]]``
    — payload in, list of human-readable gaps out.
    """
    return None


# --- Document text -----------------------------------------------------------

_TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "text/html",
}

_DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}

_XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.macroenabled.12",
}


def _document_bytes(document: Document) -> bytes:
    """Fetch the document's bytes from local storage or S3.

    Local files are tried first so a dev box without AWS credentials still
    works; the S3 object is the canonical location in every deployed
    environment. ``document.s3_key`` is the ONLY place the key lives (rule 8).
    """
    key = document.s3_key
    candidates = [
        Path(key),
        BASE_DIR / key,
        Path(settings.MEDIA_LOCAL_DIR).parent / key,
        Path(settings.MEDIA_LOCAL_DIR) / key,
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_bytes()
        except OSError:  # unreadable path — fall through to the next candidate
            continue

    try:
        import boto3
    except ImportError as exc:
        raise DocumentUnavailable(
            f"Document {document.id} is not available locally and boto3 is not installed"
        ) from exc

    bucket = document.bucket or settings.S3_BUCKET
    try:
        s3 = boto3.client("s3", region_name=settings.S3_REGION)
        obj = s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    except Exception as exc:  # noqa: BLE001 - botocore raises a wide family
        raise DocumentUnavailable(
            f"Could not read document {document.id} from s3://{bucket}/{key}"
        ) from exc


def _pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentUnavailable(
            "pypdf is not installed — cannot read PDF documents"
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
            if sum(len(p) for p in pages) > MAX_DOCUMENT_CHARS:
                break
        return "\n\n".join(pages)
    except Exception as exc:  # noqa: BLE001 - a corrupt PDF is a 422, not a 500
        raise DocumentUnavailable(f"Could not parse the PDF: {type(exc).__name__}") from exc


def _docx_to_text(data: bytes) -> str:
    """Flatten a .docx: headings as ``## ``, tables as ``cell \\t cell`` rows.

    Body children are walked in document order (paragraphs and tables
    interleave), because a bases-técnicas table means nothing without the
    heading that introduces it.
    """
    try:
        from docx import Document as DocxDocument  # python-docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise DocumentUnavailable(
            "python-docx is not installed — cannot read .docx documents"
        ) from exc

    try:
        docx = DocxDocument(io.BytesIO(data))
        parts: list[str] = []
        body = docx.element.body
        for child in body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(child, docx)
                text = (paragraph.text or "").strip()
                if not text:
                    continue
                style = (getattr(paragraph.style, "name", "") or "").lower()
                parts.append(f"## {text}" if style.startswith("heading") else text)
            elif tag == "tbl":
                table = Table(child, docx)
                for row in table.rows:
                    cells = [(cell.text or "").strip().replace("\n", " ") for cell in row.cells]
                    if any(cells):
                        parts.append("\t".join(cells))
            if sum(len(p) for p in parts) > MAX_DOCUMENT_CHARS:
                break
        return "\n".join(parts)
    except DocumentUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - a corrupt .docx is a 422, not a 500
        raise DocumentUnavailable(f"Could not parse the .docx: {type(exc).__name__}") from exc


def _xlsx_to_text(data: bytes) -> str:
    """Flatten a .xlsx: ``### <sheet>`` then TSV rows, blank rows collapsed.

    ``read_only`` + ``data_only`` because the montos / siniestralidad / plan de
    pago workbooks are large and only their COMPUTED values matter.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise DocumentUnavailable(
            "openpyxl is not installed — cannot read .xlsx documents"
        ) from exc

    def _cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value).strip().replace("\t", " ").replace("\n", " ")

    workbook = None
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts: list[str] = []
        total = 0
        for sheet in workbook.worksheets:
            parts.append(f"### {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [_cell(value) for value in row]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells:
                    continue  # collapse the blank spacer rows these sheets are full of
                line = "\t".join(cells)
                parts.append(line)
                total += len(line)
                if total > MAX_DOCUMENT_CHARS:
                    break
            if total > MAX_DOCUMENT_CHARS:
                break
        return "\n".join(parts)
    except DocumentUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - a corrupt workbook is a 422, not a 500
        raise DocumentUnavailable(f"Could not parse the .xlsx: {type(exc).__name__}") from exc
    finally:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:  # noqa: BLE001 - best effort
                pass


def load_document_text(document: Document) -> str:
    """Return the document's plain text, truncated to the provider's budget.

    Handles PDF, .docx, .xlsx and anything text-like. A scanned (image-only) PDF
    yields (almost) nothing — that is surfaced as :class:`DocumentUnavailable`
    rather than sent to the model as empty context. **No OCR in this pass**: a
    scanned file is an explicit, visible failure.
    """
    data = _document_bytes(document)
    mime = (document.mime_type or "").lower()
    name = (document.original_name or "").lower()

    if mime == "application/pdf" or name.endswith(".pdf"):
        text = _pdf_to_text(data)
    elif mime in _DOCX_MIMES or name.endswith(".docx"):
        text = _docx_to_text(data)
    elif mime in _XLSX_MIMES or name.endswith((".xlsx", ".xlsm")):
        text = _xlsx_to_text(data)
    elif mime in _TEXT_MIMES or name.endswith((".txt", ".md", ".json", ".csv", ".xml", ".html")):
        text = data.decode("utf-8", errors="replace")
    else:
        # Unknown type: best-effort decode. Binary noise is rejected below.
        text = data.decode("utf-8", errors="replace")

    # Collapse runs of spaces, but NEVER touch tabs or newlines: the .docx and
    # .xlsx loaders encode table structure as `cell \t cell` rows and it is the
    # only thing that keeps a montos matrix readable to the model.
    text = re.sub(r"[^\S\n\t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < 40:
        raise DocumentUnavailable(
            f"No readable text in document {document.id} "
            f"({document.original_name!r}) — a scanned file needs OCR first"
        )
    if len(text) > MAX_DOCUMENT_CHARS:
        text = text[:MAX_DOCUMENT_CHARS] + "\n\n[...truncated...]"
    return text


# --- Prompting ---------------------------------------------------------------

_EXTRACTION_SCHEMA = """{
  "insurer": {"legal_name": str|null, "trade_name": str|null,
              "rut": str|null, "cmf_code": str|null},
  "modality": str|null,
  "activity_classification": str|null,
  "taxable_premium_uf": number|null,
  "exempt_premium_uf": number|null,
  "net_premium_uf": number|null,
  "vat_uf": number|null,
  "total_premium_uf": number|null,
  "taxable_rate_permille": number|null,
  "exempt_rate_permille": number|null,
  "comprehensive_rate_permille": number|null,
  "commission_pct": number|null,
  "validity_business_days": integer|null,
  "coverage_start": "YYYY-MM-DD"|null,
  "coverage_end": "YYYY-MM-DD"|null,
  "received_at": "YYYY-MM-DD"|null,
  "deductibles": {"<peril>": {"basis": "loss"|"insured_amount"|"fixed"|"days",
                              "pct": number|null, "min_uf": number|null,
                              "days": integer|null, "detail": str|null}},
  "warranties": str|null,
  "notes": str|null,
  "coverages": [{"kind": "coverage"|"exclusion", "text": str, "sort_order": integer}],
  "confidence": number
}"""

_EXTRACTION_SYSTEM = f"""You are a Chilean insurance proposal (propuesta/cotización) parser.
You read one insurer proposal document and return ONE JSON object, nothing else.

OUTPUT RULES
- Return RAW JSON only. No markdown fences, no commentary, no trailing text.
- Use exactly this shape (null for anything the document does not state):
{_EXTRACTION_SCHEMA}
- NEVER invent a value. If the document does not state it, use null.

DOMAIN RULES (Chilean market)
- All amounts are in UF, as plain numbers: no "UF" suffix, no thousands dots,
  decimal point (not comma). "UF 1.234,56" -> 1234.56.
- Premium structure: net = taxable(afecta) + exempt(exenta);
  vat(IVA) = 0.19 * taxable ONLY (earthquake/sismo cover is VAT-exempt);
  total(total a pagar) = net + vat.
  Report each component the document states; do not compute the others.
- Rates ("tasa") are PER MILLE (por mil): report 1.32 for "1,32 por mil".
  comprehensive_rate(tasa total) = taxable_rate + exempt_rate.
- commission_pct is the broker commission ("comisión corredor"), 0-100.
- validity_business_days is the offer validity ("vigencia de la oferta"),
  in BUSINESS days ("días hábiles").
- coverage_start / coverage_end are the cover period ("vigencia del seguro").

DEDUCTIBLES ("deducible") — the most important structured field.
- Key the object by PERIL in English snake_case: fire, earthquake, flood, theft,
  business_interruption, machinery_breakdown, electronic_equipment, water_damage,
  political_risk, other. Use "other" for anything that does not map.
- "basis" says what the percentage applies to:
    "loss"           -> % of the loss ("% de la pérdida", "del siniestro")
    "insured_amount" -> % of the insured amount of the affected item
                        ("% sobre el monto asegurado del ítem afectado")
    "fixed"          -> a flat UF amount
    "days"           -> a waiting period in days (business interruption)
- min_uf is the UF minimum ("con un mínimo de UF X").
- Fire is typically % of the LOSS; earthquake is typically % of the INSURED
  AMOUNT of the affected item, with a UF minimum. Read the document, do not assume.

COVERAGES / EXCLUSIONS
- One entry per line item, "text" copied VERBATIM from the document in Spanish.
- kind="coverage" for covered items ("coberturas", "materia asegurada"),
  kind="exclusion" for exclusions ("exclusiones", "no cubre").

INSURER IDENTITY — critical.
- Report the issuing company's RUT ("R.U.T.", "Rol Único Tributario") exactly as
  printed, and its CMF code ("código CMF", "código de compañía") if present.
- Do NOT guess a RUT or a CMF code from the company name. null is correct.

CONFIDENCE
- "confidence" is 0-100: how completely and unambiguously the document supported
  the fields you filled. Be honest; a partial read should score below 60."""


def _build_extraction_messages(document: Document, text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"File: {document.original_name}\n"
                f"Category: {document.category}\n\n"
                "--- DOCUMENT TEXT ---\n"
                f"{text}\n"
                "--- END DOCUMENT ---\n\n"
                "Return the JSON object now."
            ),
        },
    ]


# --- JSON recovery -----------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
# Reasoning models (GLM-5.3-Flash) may inline their chain-of-thought inside
# ``<think>...</think>`` before the JSON answer. Strip it before parsing so the
# balanced-brace scanner does not trip over a stray ``{`` in the reasoning prose.
_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def _strip_reasoning(content: str) -> str:
    """Remove ``<think>...</think>`` reasoning blocks from a model answer."""
    if not content:
        return content
    return _THINK_RE.sub("", content).strip()


def _extract_json_object(content: str) -> dict[str, Any]:
    """Parse the model's answer into a dict, tolerating fences and stray prose."""
    cleaned = _FENCE_RE.sub("", _strip_reasoning(content).strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None

    if parsed is None:
        # Fall back to the first balanced {...} block in the answer.
        start = cleaned.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(cleaned)):
                char = cleaned[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(cleaned[start : index + 1])
                        except json.JSONDecodeError:
                            parsed = None
                        break

    if not isinstance(parsed, dict):
        raise AIParseError("The model did not return a JSON object")
    return parsed


def _coerce_suggestion(parsed: dict[str, Any]) -> tuple[ProposalSuggestion, list[str]]:
    """Validate the parsed dict, dropping (and reporting) fields that will not coerce.

    A single unreadable date must not throw away an otherwise good extraction —
    the point of SUGGEST is to put something reviewable in front of a human.
    """
    from pydantic import ValidationError

    warnings: list[str] = []
    payload = dict(parsed)
    payload.pop("confidence", None)

    for _attempt in range(4):
        try:
            return ProposalSuggestion.model_validate(payload), warnings
        except ValidationError as exc:
            dropped = False
            for error in exc.errors():
                loc = error.get("loc") or ()
                if not loc:
                    continue
                top = loc[0]
                if isinstance(top, str) and top in payload:
                    warnings.append(
                        f"Discarded field {'.'.join(str(part) for part in loc)}: "
                        f"{error.get('msg')}"
                    )
                    payload.pop(top, None)
                    dropped = True
            if not dropped:
                warnings.append("The extraction could not be coerced into the proposal schema")
                return ProposalSuggestion(), warnings
    return ProposalSuggestion(), warnings


def _confidence_from(parsed: dict[str, Any], suggestion: ProposalSuggestion) -> Decimal:
    """The model's self-reported confidence, or a completeness heuristic."""
    raw = parsed.get("confidence")
    value = _to_decimal(raw)
    if value is not None and Decimal(0) <= value <= Decimal(100):
        return value.quantize(Decimal("0.001"))

    # Heuristic: how many of the fields that matter for comparison were filled.
    signals = [
        suggestion.total_premium_uf is not None,
        suggestion.net_premium_uf is not None or suggestion.taxable_premium_uf is not None,
        bool(suggestion.deductibles),
        bool(suggestion.coverages),
        suggestion.insurer.rut is not None or suggestion.insurer.cmf_code is not None,
        suggestion.coverage_start is not None,
    ]
    score = Decimal(100) * Decimal(sum(1 for s in signals if s)) / Decimal(len(signals))
    return score.quantize(Decimal("0.001"))


# --- Money -------------------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _q(value: Decimal | None, quant: Decimal = MONEY_QUANT) -> Decimal | None:
    return None if value is None else value.quantize(quant)


def derive_money(suggestion: ProposalSuggestion) -> dict[str, Decimal | None]:
    """Back-fill and cross-check the Chilean premium invariants.

    ``net = taxable + exempt`` · ``vat = 0.19 * taxable`` (NOT on net — earthquake
    cover is VAT-exempt) · ``total = net + vat`` ·
    ``comprehensive_rate = taxable_rate + exempt_rate``.

    Missing components are DERIVED where the arithmetic allows. Components that
    are all present but contradict each other raise :class:`MoneyInconsistent`
    — the confirm step is exactly where a bad OCR read must be caught.
    """
    taxable = suggestion.taxable_premium_uf
    exempt = suggestion.exempt_premium_uf
    net = suggestion.net_premium_uf
    vat = suggestion.vat_uf
    total = suggestion.total_premium_uf

    conflicts: list[str] = []

    # net = taxable + exempt
    if net is None and taxable is not None and exempt is not None:
        net = taxable + exempt
    elif taxable is None and net is not None and exempt is not None:
        taxable = net - exempt
    elif exempt is None and net is not None and taxable is not None:
        exempt = net - taxable
    elif net is not None and taxable is not None and exempt is not None:
        if abs(net - (taxable + exempt)) > MONEY_TOLERANCE:
            conflicts.append(
                f"net_premium_uf ({net}) != taxable ({taxable}) + exempt ({exempt})"
            )
    # A single-component proposal: exempt unstated means fully taxable.
    if net is None and taxable is not None and exempt is None:
        net = taxable
    if net is not None and taxable is None and exempt is None:
        taxable, exempt = net, Decimal(0)

    # vat = 0.19 * taxable
    expected_vat = (taxable * VAT_RATE) if taxable is not None else None
    if vat is None:
        vat = expected_vat
    elif expected_vat is not None and abs(vat - expected_vat) > MONEY_TOLERANCE:
        conflicts.append(
            f"vat_uf ({vat}) != 0.19 * taxable_premium_uf ({expected_vat})"
        )

    # total = net + vat
    if total is None and net is not None and vat is not None:
        total = net + vat
    elif net is None and total is not None and vat is not None:
        net = total - vat
    elif total is not None and net is not None and vat is not None:
        if abs(total - (net + vat)) > MONEY_TOLERANCE:
            conflicts.append(f"total_premium_uf ({total}) != net ({net}) + vat ({vat})")

    # comprehensive_rate = taxable_rate + exempt_rate
    taxable_rate = suggestion.taxable_rate_permille
    exempt_rate = suggestion.exempt_rate_permille
    comprehensive_rate = suggestion.comprehensive_rate_permille
    if comprehensive_rate is None and taxable_rate is not None and exempt_rate is not None:
        comprehensive_rate = taxable_rate + exempt_rate
    elif (
        comprehensive_rate is not None
        and taxable_rate is not None
        and exempt_rate is not None
        and abs(comprehensive_rate - (taxable_rate + exempt_rate)) > RATE_TOLERANCE
    ):
        conflicts.append(
            f"comprehensive_rate_permille ({comprehensive_rate}) != "
            f"taxable_rate ({taxable_rate}) + exempt_rate ({exempt_rate})"
        )

    if conflicts:
        raise MoneyInconsistent("; ".join(conflicts))

    return {
        "taxable_premium_uf": _q(taxable),
        "exempt_premium_uf": _q(exempt),
        "net_premium_uf": _q(net),
        "vat_uf": _q(vat),
        "total_premium_uf": _q(total),
        "taxable_rate_permille": _q(taxable_rate, RATE_QUANT),
        "exempt_rate_permille": _q(exempt_rate, RATE_QUANT),
        "comprehensive_rate_permille": _q(comprehensive_rate, RATE_QUANT),
    }


# --- Tenant-scoped lookups ---------------------------------------------------


def get_document(db: Session, document_id: int, broker_id: int) -> Document | None:
    return db.scalars(
        select(Document).where(
            Document.id == document_id, Document.broker_id == broker_id
        )
    ).first()


def get_extraction(db: Session, extraction_id: int, broker_id: int) -> Extraction | None:
    return db.scalars(
        select(Extraction).where(
            Extraction.id == extraction_id, Extraction.broker_id == broker_id
        )
    ).first()


def get_quote_request(db: Session, quote_request_id: int, broker_id: int) -> QuoteRequest | None:
    return db.scalars(
        select(QuoteRequest).where(
            QuoteRequest.id == quote_request_id, QuoteRequest.broker_id == broker_id
        )
    ).first()


def get_proposal(db: Session, proposal_id: int, broker_id: int) -> Proposal | None:
    return db.scalars(
        select(Proposal).where(Proposal.id == proposal_id, Proposal.broker_id == broker_id)
    ).first()


def db_proposal_for_extraction(
    db: Session, extraction_id: int, broker_id: int
) -> Proposal | None:
    """The proposal already committed from this extraction, if any.

    Guards the CONFIRM step against a double submit — one extraction commits at
    most one proposal.
    """
    return db.scalars(
        select(Proposal).where(
            Proposal.extraction_id == extraction_id, Proposal.broker_id == broker_id
        )
    ).first()


def get_thread(db: Session, thread_id: int, broker_id: int) -> AgentThread | None:
    return db.scalars(
        select(AgentThread).where(
            AgentThread.id == thread_id, AgentThread.broker_id == broker_id
        )
    ).first()


# --- 1. Proposal processor (SUGGEST) ----------------------------------------


@dataclass
class ExtractionResult:
    """What SUGGEST returns: the audit row, the typed suggestion, the raw parse."""

    extraction: Extraction
    suggestion: ProposalSuggestion | None
    parsed: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentExtractionResult:
    """The generic, registry-driven SUGGEST result.

    ``payload`` is the category's own Pydantic shape; ``suggestion`` is only
    filled for the insurer-quotation categories, where the legacy proposal
    contract must keep working unchanged.
    """

    extraction: Extraction
    spec: "CategorySpec"
    payload: Any | None
    parsed: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)
    suggestion: ProposalSuggestion | None = None


# --- LangChain provider surface ----------------------------------------------
#
# Minimal LangChain: ``langchain-core`` + ``langchain-openai`` only. No agent
# framework, no vector store. DeepInfra speaks the OpenAI protocol, so one
# ``ChatOpenAI`` pointed at ``AI_BASE_URL`` covers extraction, summaries and
# streaming chat.


def _chat_model(*, temperature: float = 0.0, max_tokens: int | None = None):
    """Build the chat model. Raises :class:`AINotConfigured` when the key is unset.

    ``max_retries=0``: a retry inside the SDK hides the failure from the
    extraction row, and every AI call here is already user-triggered and
    re-runnable.
    """
    ensure_ai_configured()
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AINotConfigured("The `langchain-openai` package is not installed") from exc

    extra: dict[str, Any] = {}
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    return ChatOpenAI(
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL,
        timeout=float(settings.AI_TIMEOUT_SECONDS),
        max_retries=0,
        temperature=temperature,
        **extra,
    )


def ensure_ai_configured() -> None:
    """Raise :class:`AINotConfigured` unless a provider key is configured.

    Called at the very top of every AI entry point so an unconfigured
    deployment answers 503 with a code — never a 500, and never a half-written
    stream.
    """
    if not settings.AI_API_KEY:
        raise AINotConfigured(
            "AI_API_KEY is not set — configure it in backend/.env to enable AI features"
        )


def _wrap_provider_error(exc: BaseException) -> AIError:
    """Map ANY provider / parser exception into the typed :class:`AIError` family.

    This is the single reason an LLM hiccup can never surface as a 500:
    ``langchain_core.exceptions.*``, ``openai.*`` and ``pydantic.ValidationError``
    all come out of here as something the router knows how to answer.
    """
    if isinstance(exc, AIError):
        return exc

    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return AIParseError(f"The model's answer did not match the schema: {exc.error_count()} errors")

    name = type(exc).__name__
    module = type(exc).__module__ or ""

    if name in {"OutputParserException", "OutputParserError"}:
        return AIParseError("The model did not return usable JSON")
    if "Timeout" in name or isinstance(exc, TimeoutError):
        return AITimeout(f"The AI provider timed out after {settings.AI_TIMEOUT_SECONDS}s")
    if "Connection" in name:
        return AIProviderError("Could not reach the AI provider")
    if "Authentication" in name or "PermissionDenied" in name:
        return AIProviderError(f"The AI provider rejected the credentials ({name})")
    if module.startswith(("openai", "langchain", "httpx", "httpcore")):
        return AIProviderError(f"AI provider error: {name}")
    return AIProviderError(f"Unexpected AI provider failure: {name}")


def _reasoning_text(message: Any) -> str:
    """The reasoning trace a reasoning model exposes alongside (or instead of)
    the visible answer.

    GLM-5.3-Flash returns its chain-of-thought in ``reasoning_content`` and, when
    the output budget is exhausted before it starts the final answer, leaves
    ``content`` empty. In that degenerate case the JSON we need is inside the
    reasoning trace, so it is the last thing worth trying before giving up.
    """
    extra = getattr(message, "additional_kwargs", None)
    if isinstance(extra, dict):
        reasoning = extra.get("reasoning_content") or extra.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
    meta = getattr(message, "response_metadata", None)
    if isinstance(meta, dict):
        reasoning = meta.get("reasoning_content") or meta.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
    return ""


def _message_text(message: Any, *, allow_reasoning: bool = False) -> str:
    """The plain text of a LangChain message, whatever content shape it carries.

    ``allow_reasoning`` opts a non-streaming extraction caller into recovering
    the answer from the model's ``reasoning_content`` when ``content`` is empty
    (a reasoning model that spent its whole budget thinking). It is deliberately
    off by default so the SSE streaming loop — which sees one delta at a time —
    never leaks reasoning chunks into the visible answer.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        text = "".join(parts)
    else:
        text = "" if content is None else str(content)

    if allow_reasoning and not text.strip():
        return _reasoning_text(message)
    return text


def _message_usage(message: Any) -> tuple[int | None, int | None]:
    usage = getattr(message, "usage_metadata", None) or {}
    if isinstance(usage, dict) and usage:
        return usage.get("input_tokens"), usage.get("output_tokens")
    meta = getattr(message, "response_metadata", None) or {}
    token_usage = meta.get("token_usage") if isinstance(meta, dict) else None
    if isinstance(token_usage, dict):
        return token_usage.get("prompt_tokens"), token_usage.get("completion_tokens")
    return None, None


def _message_model(message: Any) -> str:
    meta = getattr(message, "response_metadata", None) or {}
    if isinstance(meta, dict):
        model = meta.get("model_name") or meta.get("model")
        if model:
            return str(model)
    return settings.AI_MODEL


def _chat_complete(
    messages: Sequence[tuple[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1200,
) -> Completion:
    """One non-streaming LangChain call, normalised into :class:`Completion`."""
    llm = _chat_model(temperature=temperature, max_tokens=max_tokens)
    try:
        answer = llm.invoke(list(messages))
    except Exception as exc:  # noqa: BLE001 - everything becomes a typed AIError
        raise _wrap_provider_error(exc) from exc

    content = _message_text(answer, allow_reasoning=True).strip()
    if not content:
        raise AIProviderError("The AI provider returned an empty message")
    prompt_tokens, completion_tokens = _message_usage(answer)
    return Completion(
        content=content,
        model=_message_model(answer),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw={"content": content},
    )


# --- Registry-driven extraction ----------------------------------------------

# Every registry schema is described to the model as JSON Schema. Beyond this
# budget the schema itself would crowd out the document, so a flat field list is
# sent instead.
MAX_SCHEMA_CHARS = 14_000

_SCHEMA_NOISE_KEYS = {"title", "default", "additionalProperties"}


def _compact_schema(model: type) -> dict[str, Any]:
    """The model's JSON Schema with the keys that only add tokens removed."""

    def prune(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: prune(v) for k, v in node.items() if k not in _SCHEMA_NOISE_KEYS}
        if isinstance(node, list):
            return [prune(item) for item in node]
        return node

    try:
        return prune(model.model_json_schema())
    except Exception:  # noqa: BLE001 - a schema we cannot render is not fatal
        return {}


def _field_list_hint(model: type) -> str:
    lines = []
    for name, info in getattr(model, "model_fields", {}).items():
        annotation = getattr(info.annotation, "__name__", None) or str(info.annotation)
        description = f" — {info.description}" if info.description else ""
        lines.append(f'  "{name}": {annotation}{description}')
    return "{\n" + ",\n".join(lines) + "\n}"


def _schema_hint(model: type) -> str:
    rendered = json.dumps(_compact_schema(model), ensure_ascii=False, separators=(",", ":"))
    if len(rendered) > MAX_SCHEMA_CHARS:
        return _field_list_hint(model)
    return rendered


# The shared rules. Spanish is correct here: the source documents are Spanish
# and this is an LLM prompt, the one place besides locale values where Spanish
# belongs (CLAUDE.md rule 1).
_GENERIC_EXTRACTION_SYSTEM = """Eres un extractor de documentos de seguros del mercado chileno.
Lees UN documento y devuelves UN objeto JSON, nada más.

REGLAS DE SALIDA
- Devuelve JSON crudo y válido. Sin ```, sin comentarios, sin texto antes o después.
- Usa null para todo lo que el documento no diga. NUNCA inventes un valor.
- Si una lista no aparece en el documento, devuélvela vacía.

MONTOS Y CIFRAS
- Los montos son UF. El punto es separador de miles y la coma es decimal:
  "UF 17.920" -> 17920 ; "UF 12,99" -> 12.99. Puedes devolver el número o el texto
  original: el sistema normaliza ambos.
- Estructura de prima chilena: neta = afecta + exenta ; IVA = 0,19 x AFECTA
  (la cobertura de sismo es exenta, el IVA NUNCA se calcula sobre la neta) ;
  total = neta + IVA. Informa lo que el documento declare, no calcules lo demás.
- Las tasas son por mil. Los porcentajes van de 0 a 100.

FECHAS
- Formato ISO: "AAAA-MM-DD". Si el documento indica hora, usa "AAAA-MM-DDTHH:MM".
- Vigencias, endosos y ocurrencia de siniestros llevan hora cuando el documento la declara:
  la convención de las 12:00 y las franquicias horarias son contractuales.

PROSA
- Los deducibles, los límites de cobertura, las contra-excepciones de exclusiones, las
  estipulaciones prendarias y los relatos del corredor se copian LITERALES, en español,
  además de cualquier número que hayas extraído de ellos.

NO RECONCILIES
- Registra lo que dice ESTE documento. Si contradice a otro, no lo corrijas: la diferencia
  se revisa después. Los RUT, patentes, chasis y folios se copian tal cual.
- Los corredores que aparezcan nombrados son texto del documento, no datos del sistema."""


def _extraction_messages(
    spec: "CategorySpec", document: Document, text: str, *, context_prepend: str = ""
) -> list[tuple[str, str]]:
    system = (
        f"{_GENERIC_EXTRACTION_SYSTEM}\n\n"
        f"GUÍA PARA ESTA CATEGORÍA ({spec.category.value})\n{spec.guidance}\n\n"
        f"ESQUEMA JSON DE SALIDA (respeta exactamente estos nombres de campo)\n"
        f"{_schema_hint(spec.schema)}"
    )
    messages: list[tuple[str, str]] = [("system", system)]
    # An OPTIONAL account-context system block, PREPENDED before the document.
    # It never changes the target schema — it only enriches a policy read with
    # what the account already knows (RecordExpediente, accepted offer, prior
    # policy). Empty for a raw upload.
    if context_prepend:
        messages.append(("system", context_prepend))
    user = (
        f"Archivo: {document.original_name}\n"
        f"Categoría: {spec.category.value}"
        + (f" (código {spec.code})" if spec.code else "")
        + "\n\n--- INICIO DEL DOCUMENTO ---\n"
        f"{text}\n"
        "--- FIN DEL DOCUMENTO ---\n\n"
        "Devuelve ahora el objeto JSON."
    )
    messages.append(("human", user))
    return messages


def _coerce_payload(schema: type, parsed: dict[str, Any]) -> tuple[Any | None, list[str]]:
    """Validate ``parsed`` against ``schema``, dropping the fields that will not coerce.

    One unreadable table must not throw away an otherwise good extraction — the
    whole point of SUGGEST is to put something reviewable in front of a human.
    """
    from pydantic import ValidationError

    warnings: list[str] = []
    payload = dict(parsed)

    for _attempt in range(5):
        try:
            return schema.model_validate(payload), warnings
        except ValidationError as exc:
            dropped = False
            for error in exc.errors():
                loc = error.get("loc") or ()
                if not loc:
                    continue
                top = loc[0]
                if isinstance(top, str) and top in payload:
                    warnings.append(
                        f"Discarded field {'.'.join(str(part) for part in loc)}: "
                        f"{error.get('msg')}"
                    )
                    payload.pop(top, None)
                    dropped = True
            if not dropped:
                warnings.append(
                    f"The extraction could not be coerced into {schema.__name__}"
                )
                try:
                    return schema.model_validate({}), warnings
                except ValidationError:  # pragma: no cover - every schema allows {}
                    return None, warnings
    return None, warnings


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) > 0
    return True


def _payload_confidence(parsed: dict[str, Any], payload: Any) -> Decimal:
    """The model's self-reported confidence, or a completeness heuristic."""
    raw = parsed.get("confidence") if isinstance(parsed, dict) else None
    value = _to_decimal(raw)
    if value is not None and Decimal(0) <= value <= Decimal(100):
        return value.quantize(Decimal("0.001"))

    if payload is None:
        return Decimal("0.000")
    fields = [
        name
        for name in getattr(type(payload), "model_fields", {})
        if name not in {"confidence", "extraction_notes"}
    ]
    if not fields:
        return Decimal("0.000")
    filled = sum(1 for name in fields if _filled(getattr(payload, name, None)))
    score = Decimal(100) * Decimal(filled) / Decimal(len(fields))
    return score.quantize(Decimal("0.001"))


def _invoke_structured_schema(
    schema: type, messages: list[tuple[str, str]]
) -> tuple[Any | None, dict[str, Any], dict[str, Any], str, int | None, int | None, list[str]]:
    """One structured call against ``schema`` with a prepared message list.

    Returns (payload, parsed, raw, model, in, out, warnings). The engine of
    :func:`_invoke_structured`, factored out so the multi-document antecedentes
    consolidation reuses the exact same ``json_mode`` + ``include_raw`` + reasoning
    fallback contract (and the exact same monkeypatch surface in tests).

    ``method="json_mode"`` because DeepInfra's OpenAI-compatible endpoint
    supports JSON mode but NOT the OpenAI tool-calling ``strict`` schema.
    ``include_raw=True`` keeps the audit trail (raw text + token usage) even when
    the structured parse succeeds; when it fails, the fence-tolerant
    :func:`_extract_json_object` is the fallback before we give up.
    """
    llm = _chat_model(temperature=0.0, max_tokens=settings.AI_EXTRACTION_MAX_TOKENS)

    try:
        structured = llm.with_structured_output(
            schema, method="json_mode", include_raw=True
        )
        answer = structured.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - typed AIError or nothing
        raise _wrap_provider_error(exc) from exc

    warnings: list[str] = []
    if isinstance(answer, dict):
        raw_message = answer.get("raw")
        payload = answer.get("parsed")
        parsing_error = answer.get("parsing_error")
    else:  # pragma: no cover - include_raw always yields the dict shape
        raw_message, payload, parsing_error = None, answer, None

    content = _message_text(raw_message, allow_reasoning=True) if raw_message is not None else ""
    prompt_tokens, completion_tokens = _message_usage(raw_message)
    model_name = _message_model(raw_message)
    raw: dict[str, Any] = {"content": content}

    if payload is not None and not isinstance(payload, schema):
        # json_mode can hand back a plain dict when the parser is lenient.
        payload, coerce_warnings = _coerce_payload(schema, dict(payload))
        warnings.extend(coerce_warnings)

    if payload is None:
        if parsing_error is not None:
            warnings.append(
                f"Structured parse failed ({type(parsing_error).__name__}); "
                "recovered the JSON object from the raw answer"
            )
        if not content.strip():
            raise AIProviderError("The AI provider returned an empty message")
        parsed = _extract_json_object(content)  # raises AIParseError
        payload, coerce_warnings = _coerce_payload(schema, parsed)
        warnings.extend(coerce_warnings)
    else:
        parsed = payload.model_dump(mode="json")
        if content.strip():
            try:
                parsed = _extract_json_object(content)
            except AIParseError:
                pass  # the structured parse already succeeded; keep its view

    raw["parsed"] = parsed
    return payload, parsed, raw, model_name, prompt_tokens, completion_tokens, warnings


def _invoke_structured(
    spec: "CategorySpec", document: Document, text: str, *, context_prepend: str = ""
) -> tuple[Any | None, dict[str, Any], dict[str, Any], str, int | None, int | None, list[str]]:
    """One structured call for a single registry document. Thin wrapper over
    :func:`_invoke_structured_schema` that builds the per-category messages."""
    messages = _extraction_messages(spec, document, text, context_prepend=context_prepend)
    return _invoke_structured_schema(spec.schema, messages)


# The two categories that must keep producing the legacy proposal contract.
_QUOTATION_CATEGORIES = {DocumentCategory.PROPOSAL, DocumentCategory.INSURER_QUOTATION}


def _legacy_proposal_suggestion(
    payload: Any, parsed: dict[str, Any]
) -> tuple[ProposalSuggestion, list[str]]:
    """Project an insurer-quotation payload onto the legacy ``ProposalSuggestion``.

    Explicit, not incidental: ``upload.tsx`` and the proposal tests speak this
    shape, so the mapping is written out rather than left to name collisions.
    """
    source: dict[str, Any] = (
        payload.model_dump(mode="json") if payload is not None else dict(parsed)
    )

    coverages: list[dict[str, Any]] = []
    for index, row in enumerate(source.get("coverages") or []):
        if not isinstance(row, dict):
            continue
        text = (row.get("text") or row.get("name") or "").strip()
        if not text:
            continue
        coverages.append(
            {
                "kind": row.get("kind") or "coverage",
                "text": text,
                "normalized_code": row.get("normalized_code"),
                "sort_order": row.get("sort_order") if row.get("sort_order") is not None else index,
            }
        )
    offset = len(coverages)
    for index, row in enumerate(source.get("exclusions") or []):
        text = (row.get("text") if isinstance(row, dict) else row) or ""
        text = str(text).strip()
        if not text:
            continue
        coverages.append(
            {"kind": "exclusion", "text": text, "sort_order": offset + index}
        )

    warranty_texts = [
        str(row.get("requirement") or row.get("title") or "").strip()
        for row in (source.get("warranties") or [])
        if isinstance(row, dict)
    ]
    warranties = " | ".join(text for text in warranty_texts if text) or None

    legacy = {
        "insurer": source.get("insurer") or {},
        "modality": source.get("modality"),
        "activity_classification": source.get("activity_classification"),
        "taxable_premium_uf": source.get("taxable_premium_uf"),
        "exempt_premium_uf": source.get("exempt_premium_uf"),
        "net_premium_uf": source.get("net_premium_uf"),
        "vat_uf": source.get("vat_uf"),
        "total_premium_uf": source.get("total_premium_uf"),
        "taxable_rate_permille": source.get("taxable_rate_permille"),
        "exempt_rate_permille": source.get("exempt_rate_permille"),
        "comprehensive_rate_permille": source.get("comprehensive_rate_permille"),
        "commission_pct": source.get("commission_pct"),
        "validity_business_days": source.get("validity_business_days"),
        "coverage_start": source.get("coverage_start"),
        "coverage_end": source.get("coverage_end"),
        "received_at": source.get("received_at") or source.get("issue_date"),
        "deductibles": source.get("deductibles") or {},
        "warranties": warranties,
        "notes": source.get("notes") or source.get("payment_plan_offered"),
        "coverages": coverages,
    }
    return _coerce_suggestion({k: v for k, v in legacy.items() if v is not None})


def extract_document(
    db: Session,
    *,
    document_id: int,
    broker_id: int,
    user: User | None = None,
    category: "DocumentCategory | str | None" = None,
) -> DocumentExtractionResult:
    """Read one document with its registry schema and SUGGEST a payload.

    Writes exactly ONE ``extraction`` row in EVERY outcome — success, provider
    failure, unparsable answer — carrying the model, the prompt version, the
    category, the case file, the raw output, the parsed result and a confidence.
    It never writes a domain entity: that is the confirm step's job.

    ``category`` overrides ``document.category`` (the upload flow may know
    better than the filing did). Raises :class:`DocumentUnavailable` or
    :class:`UnsupportedCategory` BEFORE any row is written, and other
    :class:`AIError` subclasses after the row exists and has been marked failed.
    """
    from app.schemas.extraction.registry import UnknownCategory, spec_for

    document = get_document(db, document_id, broker_id)
    if document is None:
        raise DocumentUnavailable(f"Document {document_id} not found in this workspace")

    try:
        spec = spec_for(category or document.category)
    except UnknownCategory as exc:
        raise UnsupportedCategory(str(exc)) from exc

    # Fail before creating a row if there is nothing to read.
    text = load_document_text(document)

    extraction = Extraction(
        broker_id=broker_id,
        document_id=document.id,
        case_file_id=document.case_file_id,
        category=spec.category,
        kind=spec.extraction_kind,
        model=settings.AI_MODEL,
        prompt_version=spec.prompt_version,
        status=ExtractionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        created_by_id=getattr(user, "id", None),
    )
    db.add(extraction)
    db.flush()

    # A POLICY read is enriched with what the account already knows (rule 6 is
    # untouched — this only PREPENDS a system block, the target schema is the
    # same). Any other category, or no case file, gets a raw read.
    context_prepend = ""
    if spec.category is DocumentCategory.POLICY and document.case_file_id is not None:
        case_file = db.get(CaseFile, document.case_file_id)
        if case_file is not None and case_file.broker_id == broker_id:
            context_prepend = _account_extraction_context(db, case_file, spec.category)

    def _fail(exc: AIError) -> None:
        extraction.status = ExtractionStatus.FAILED
        extraction.error = f"{type(exc).__name__}: {exc}"
        extraction.finished_at = datetime.now(timezone.utc)
        db.commit()

    try:
        (
            payload,
            parsed,
            raw,
            model_name,
            prompt_tokens,
            completion_tokens,
            warnings,
        ) = _invoke_structured(spec, document, text, context_prepend=context_prepend)
    except AIError as exc:
        _fail(exc)
        raise
    except Exception as exc:  # noqa: BLE001 - last-resort net; never a 500
        wrapped = _wrap_provider_error(exc)
        _fail(wrapped)
        raise wrapped from exc

    extraction.model = model_name
    extraction.prompt_tokens = prompt_tokens
    extraction.completion_tokens = completion_tokens
    extraction.raw_output = raw

    suggestion: ProposalSuggestion | None = None
    if spec.category in _QUOTATION_CATEGORIES:
        suggestion, legacy_warnings = _legacy_proposal_suggestion(payload, parsed)
        warnings = [*warnings, *legacy_warnings]
        # The legacy contract: `parsed` on the row is the proposal shape.
        stored = json.loads(suggestion.model_dump_json())
        if payload is not None:
            raw["payload"] = payload.model_dump(mode="json")
            extraction.raw_output = raw
    else:
        stored = payload.model_dump(mode="json") if payload is not None else parsed

    extraction.parsed = stored
    extraction.confidence = _payload_confidence(parsed, payload)
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(extraction)

    return DocumentExtractionResult(
        extraction=extraction,
        spec=spec,
        payload=payload,
        parsed=parsed,
        warnings=warnings,
        suggestion=suggestion,
    )


def extract_proposal(
    db: Session,
    *,
    document_id: int,
    broker_id: int,
    user: User | None = None,
) -> ExtractionResult:
    """Read a document and SUGGEST a proposal. Writes an ``extraction``, never a proposal.

    A thin wrapper over :func:`extract_document` pinned to the
    ``insurer_quotation`` spec (of which the deprecated ``proposal`` category is
    an alias). Behaviour is unchanged: one extraction row in every outcome, the
    legacy ``ProposalSuggestion`` in the response, and the same
    ``prompt_version`` on the row.
    """
    result = extract_document(
        db,
        document_id=document_id,
        broker_id=broker_id,
        user=user,
        category=DocumentCategory.INSURER_QUOTATION,
    )
    return ExtractionResult(
        extraction=result.extraction,
        suggestion=result.suggestion,
        parsed=result.parsed,
        warnings=result.warnings,
    )


# --- 1a. Context-aware extraction prompts (v8) -------------------------------

# The account-context prepend is a system block, not a document: it is capped far
# below ``MAX_DOCUMENT_CHARS`` so it enriches without crowding out the policy text
# it accompanies.
ACCOUNT_CONTEXT_MAX_CHARS = 8_000


def _account_extraction_context(
    db: Session, case_file: CaseFile, category: "DocumentCategory | str | None"
) -> str:
    """A token-budgeted, tenant-scoped system block for a POLICY extraction.

    Modelled on the agent's ``build_context``: it assembles, ranked by relevance
    and hard-capped like ``ANTECEDENTES_MAX_CHARS``, what the account already
    knows — the consolidated ``RecordExpediente.payload``, the accepted
    proposal / comparison snapshot, and (for a renewal) the prior-period policy.

    Purely additive: the caller PREPENDS the result and the target schema is
    unchanged. Defensive by construction — no prior data yields ``""`` and any
    lookup failure is swallowed, never raised (a hiccup here must not fail the
    extraction it was only meant to help).
    """
    if category not in (DocumentCategory.POLICY, DocumentCategory.POLICY.value):
        return ""

    broker_id = case_file.broker_id
    blocks: list[str] = []

    try:
        # 1) The consolidated antecedentes the account registered (highest signal).
        expediente = db.scalars(
            select(RecordExpediente).where(
                RecordExpediente.broker_id == broker_id,
                RecordExpediente.case_file_id == case_file.id,
            )
        ).first()
        if expediente is not None and expediente.payload:
            rendered = json.dumps(expediente.payload, ensure_ascii=False)[
                :ACCOUNT_CONTEXT_MAX_CHARS
            ]
            blocks.append(
                "ANTECEDENTES CONSOLIDADOS DE LA CUENTA (revisados por el corredor):\n"
                + rendered
            )

        # 2) The accepted inbound offer for this account, if one is marked.
        accepted = db.scalars(
            select(Proposal)
            .where(
                Proposal.broker_id == broker_id,
                Proposal.case_file_id == case_file.id,
                Proposal.status == ProposalStatus.ACCEPTED,
            )
            .order_by(Proposal.id.desc())
        ).first()
        if accepted is not None:
            insurer = db.get(Insurer, accepted.insurer_id)
            blocks.append(
                "OFERTA ACEPTADA (línea base):\n"
                f"- compañía: {insurer.legal_name if insurer else 'n/d'}"
                f" (CMF {insurer.cmf_code if insurer else 'n/d'})"
                f" | prima total UF {_fmt(accepted.total_premium_uf)}"
                f" | vigencia {_fmt(accepted.coverage_start)} -> {_fmt(accepted.coverage_end)}"
            )

        # 3) A renewal reads its prior-period policy — the mirror to diff against.
        if case_file.origin is CaseOrigin.RENEWAL and case_file.origin_case_file_id:
            from app.models.policy import Policy as _Policy

            prior = db.scalars(
                select(_Policy)
                .where(
                    _Policy.broker_id == broker_id,
                    _Policy.case_file_id == case_file.origin_case_file_id,
                )
                .order_by(_Policy.id.desc())
            ).first()
            if prior is not None:
                blocks.append(
                    "PÓLIZA DEL PERÍODO ANTERIOR (para renovación — compara, no copies):\n"
                    f"- N° {prior.policy_number}"
                    f" | vigencia {_fmt(prior.start_date)} -> {_fmt(prior.end_date)}"
                    f" | prima total UF {_fmt(prior.total_premium_uf)}"
                    f" | deducibles {json.dumps(prior.deductibles, ensure_ascii=False) if prior.deductibles else 'n/d'}"
                )
    except Exception:  # noqa: BLE001 - context is best-effort; never fail the extraction
        logger.warning("account extraction context failed for case_file %s", case_file.id)
        return ""

    if not blocks:
        return ""

    header = (
        "CONTEXTO DE LA CUENTA (solo apoyo — extrae SIEMPRE lo que dice el documento; "
        "si la póliza difiere del contexto, transcribe la póliza, no reconcilies):\n\n"
    )
    body = "\n\n".join(blocks)
    combined = header + body
    if len(combined) > ANTECEDENTES_MAX_CHARS:
        combined = combined[:ANTECEDENTES_MAX_CHARS] + "\n\n[...truncado...]"
    return combined


# --- 1b. Antecedentes expediente (v6): multi-document consolidation ----------

# Bump when the consolidation prompt or the dynamic-schema contract changes.
ANTECEDENTES_PROMPT_VERSION = "antecedentes-consolidate-v1"

# The built-in GENERIC antecedentes shape. Used when no ramo template resolves
# (a ramo is advisory and ``insurance_line_id`` is nullable), so a FREE upload
# always yields an extraction instead of a dead end. Deliberately broad: four
# open sections that fit any line.
_GENERIC_ANTECEDENTES_DEFINITION: dict[str, Any] = {
    "sections": [
        {
            "key": "asegurado",
            "label": "Datos del asegurado",
            "fields": [
                {"key": "razon_social", "label": "Razón social", "type": "text"},
                {"key": "rut", "label": "RUT", "type": "text"},
                {"key": "giro", "label": "Giro / actividad", "type": "text"},
                {"key": "direccion", "label": "Dirección", "type": "text"},
                {"key": "contacto", "label": "Contacto", "type": "text"},
            ],
        },
        {
            "key": "materias",
            "label": "Materias y coberturas",
            "fields": [
                {"key": "resumen", "label": "Resumen", "type": "text"},
                {
                    "key": "partidas",
                    "label": "Partidas / coberturas",
                    "type": "list",
                    "fields": [
                        {"key": "item", "label": "Ítem", "type": "text"},
                        {"key": "detalle", "label": "Detalle", "type": "text"},
                        {"key": "monto_uf", "label": "Monto UF", "type": "money_uf"},
                    ],
                },
            ],
        },
        {
            "key": "siniestralidad",
            "label": "Siniestralidad",
            "fields": [
                {"key": "resumen", "label": "Resumen", "type": "text"},
                {
                    "key": "siniestros",
                    "label": "Siniestros",
                    "type": "list",
                    "fields": [
                        {"key": "fecha", "label": "Fecha", "type": "date"},
                        {"key": "descripcion", "label": "Descripción", "type": "text"},
                        {"key": "monto_uf", "label": "Monto UF", "type": "money_uf"},
                    ],
                },
            ],
        },
        {
            "key": "observaciones",
            "label": "Observaciones",
            "fields": [
                {"key": "notas", "label": "Notas", "type": "text"},
            ],
        },
    ]
}

# The aggregate provider budget for a consolidation: several source documents,
# each already capped to ``MAX_DOCUMENT_CHARS`` on its own, are concatenated up
# to this ceiling so a big antecedentes corpus never blows the context window.
ANTECEDENTES_MAX_CHARS = 90_000

# ``line_record_schema.definition`` field ``type`` -> the Annotated helper that
# parses/validates one value. ``list`` and ``group`` are handled structurally.
_DYNAMIC_FIELD_TYPES: dict[str, Any] = {
    "text": _CommonStr,
    "select": _CommonStr,
    "number": _CommonUF,
    "money_uf": _CommonUF,
    "percent": _CommonPct,
    "integer": _CommonInt,
    "boolean": bool | None,
    "date": _CommonFlexDate,
}


def _dynamic_field_annotation(field_def: dict[str, Any], *, path: str):
    """One (annotation, default) pair for :func:`pydantic.create_model`."""
    from pydantic import Field as _PField

    ftype = str(field_def.get("type") or "text").lower()
    key = str(field_def.get("key") or "field")

    if ftype == "group":
        sub = _dynamic_object_model(field_def.get("fields") or [], name=f"{path}_{key}_group")
        return (sub | None, None)
    if ftype == "list":
        nested = field_def.get("fields") or []
        row = (
            _dynamic_object_model(nested, name=f"{path}_{key}_row")
            if nested
            else _CommonRow
        )
        return (list[row], _PField(default_factory=list))
    annotation = _DYNAMIC_FIELD_TYPES.get(ftype, _CommonStr)
    return (annotation, None)


def _dynamic_object_model(fields: list[dict[str, Any]], *, name: str) -> type:
    """A Pydantic submodel with one lenient field per ``fields`` entry."""
    from pydantic import create_model

    definitions: dict[str, Any] = {}
    for field_def in fields or []:
        key = field_def.get("key")
        if not key or not isinstance(key, str):
            continue
        definitions[key] = _dynamic_field_annotation(field_def, path=name)
    return create_model(name, __base__=_CommonExtractionModel, **definitions)


def build_dynamic_schema(definition: dict[str, Any]) -> type:
    """Build a Pydantic model from a ``line_record_schema.definition`` JSON.

    The model mirrors the sectioned shape: one field per ``section.key`` whose
    value is a submodel with one field per ``field.key``. Every leaf uses the
    lenient ``common.py`` Annotated helpers (Chilean number/date parsing, prose
    kept verbatim), and the whole thing inherits :class:`RootExtraction` so the
    model can still self-report ``confidence`` / ``extraction_notes``.

    The result validates both the AI suggestion (SUGGEST) and the human-completed
    payload (register) against the identical contract.
    """
    from pydantic import create_model

    sections = (definition or {}).get("sections") or []
    section_defs: dict[str, Any] = {}
    for section in sections:
        skey = section.get("key")
        if not skey or not isinstance(skey, str):
            continue
        section_model = _dynamic_object_model(
            section.get("fields") or [], name=f"sec_{skey}"
        )
        section_defs[skey] = (section_model | None, None)
    return create_model(
        "AntecedentesRecord", __base__=_CommonRootExtraction, **section_defs
    )


def resolve_line_record_schema(
    db: Session, *, insurance_line_id: int, broker_id: int
) -> LineRecordSchema | None:
    """The active antecedentes schema for a ramo: the broker's own row wins over
    the global Radal seed (``broker_id IS NULL``)."""
    rows = db.scalars(
        select(LineRecordSchema)
        .where(
            LineRecordSchema.insurance_line_id == insurance_line_id,
            LineRecordSchema.is_active.is_(True),
            or_(
                LineRecordSchema.broker_id == broker_id,
                LineRecordSchema.broker_id.is_(None),
            ),
        )
        .order_by(LineRecordSchema.version.desc())
    ).all()
    if not rows:
        return None
    own = [row for row in rows if row.broker_id == broker_id]
    return (own or rows)[0]


def resolve_account_line_record_schema(
    db: Session, case: CaseFile, broker_id: int
) -> LineRecordSchema | None:
    """The LINE that drives an account's antecedentes (v7 resolution order).

    ``case.line_record_schema_id`` (the line explicitly assigned to the account)
    wins; a foreign/other-broker assignment is ignored (broker-filtered → falls
    through). Otherwise fall back to
    :func:`resolve_line_record_schema` (the broker's own line for the ramo, else
    the global template)."""
    assigned_id = getattr(case, "line_record_schema_id", None)
    if assigned_id is not None:
        row = db.get(LineRecordSchema, assigned_id)
        if row is not None and row.broker_id in (broker_id, None):
            return row
    if case.insurance_line_id is None:
        return None
    return resolve_line_record_schema(
        db, insurance_line_id=case.insurance_line_id, broker_id=broker_id
    )


def get_account_case_file(
    db: Session, case_file_id: int, broker_id: int
) -> CaseFile | None:
    """The account case_file (``kind=account``) within the broker's scope.

    A foreign or non-account case_file resolves to ``None`` (the router answers
    404, never 403 — rule 2)."""
    return db.scalars(
        select(CaseFile).where(
            CaseFile.id == case_file_id,
            CaseFile.broker_id == broker_id,
            CaseFile.kind == CaseFileKind.ACCOUNT,
        )
    ).first()


# The COTIZACIÓN / insurer-quotation document types. These belong to the
# COMPARISON, never to the antecedentes: an antecedentes consolidation reads only
# what the INSURED communicated, so every insurer offer (03 Southbridge/HDI/Mapfre
# and the comparison/propuesta artefacts) is excluded from the candidate set.
_COTIZACION_CATEGORIES = frozenset(
    {
        DocumentCategory.INSURER_QUOTATION,
        DocumentCategory.PROPOSAL,  # deprecated alias of INSURER_QUOTATION
        DocumentCategory.DECLINATION,
        DocumentCategory.CONDITIONAL_PRONOUNCEMENT,
        DocumentCategory.QUOTE_COMPARISON,
        DocumentCategory.BUDGET_PROPOSAL,
        DocumentCategory.ISSUANCE_PROPOSAL,
        DocumentCategory.TECHNICAL_RECOMMENDATION,
        DocumentCategory.COMPARISON_PACK,
        DocumentCategory.PROPOSAL_PACK,
    }
)


def _antecedentes_documents(db: Session, case: CaseFile, broker_id: int) -> list[Document]:
    """The candidate antecedentes documents of an account: everything the INSURED
    communicated, filed under ``root_prospect`` or ``submission`` (the ramo
    recommended files + free uploads in the intake sections).

    EXCLUDES the cotización / insurer-quotation documents (03 Southbridge/HDI/
    Mapfre, comparison, propuesta) — those belong to the comparison, never to the
    antecedentes. Both the SECTION filter (no ``insurer_quotes``) and the CATEGORY
    filter (no cotización type) enforce this, so a misfiled insurer offer under a
    submission section still never leaks in."""
    return db.scalars(
        select(Document)
        .where(
            Document.case_file_id == case.id,
            Document.broker_id == broker_id,
            Document.section.in_(
                [CaseSection.ROOT_PROSPECT, CaseSection.SUBMISSION]
            ),
            Document.category.notin_(_COTIZACION_CATEGORIES),
        )
        .order_by(Document.section, Document.id)
    ).all()


def _antecedentes_messages(
    schema: type, sources: list[tuple[Document, str]], *, ramo_name: str
) -> list[tuple[str, str]]:
    """The multi-document consolidation prompt (sibling of
    :func:`_extraction_messages`). Every source is labelled; the schema hint is
    appended so the model returns exactly the sectioned shape."""
    system = (
        f"{_GENERIC_EXTRACTION_SYSTEM}\n\n"
        f"GUÍA: CONSOLIDACIÓN DE ANTECEDENTES ({ramo_name})\n"
        "Estás consolidando TODO lo que el asegurado comunicó para esta cuenta —varios "
        "documentos— en UN solo objeto JSON con la forma del esquema, agrupado por secciones. "
        "Combina la información de todos los documentos. Cuando dos documentos difieran, "
        "prefiere el más específico y anota la discrepancia en extraction_notes. Deja en null "
        "todo lo que ningún documento indique; nunca inventes.\n\n"
        "ESQUEMA JSON DE SALIDA (respeta exactamente estos nombres de campo y su anidación)\n"
        f"{_schema_hint(schema)}"
    )
    blocks: list[str] = []
    for index, (document, text) in enumerate(sources, start=1):
        category = document.category.value if document.category else "documento"
        blocks.append(
            f"--- DOCUMENTO {index}: {document.original_name} ({category}) ---\n"
            f"{text}\n"
            f"--- FIN DOCUMENTO {index} ---"
        )
    user = "\n\n".join(blocks) + "\n\nDevuelve ahora el objeto JSON consolidado."
    return [("system", system), ("human", user)]


@dataclass
class AntecedentesConsolidation:
    """What a consolidation SUGGEST returns: the audit row + the staged payload.

    ``document_extractions`` carries, PER SOURCE DOCUMENT, the pieces of data
    extracted from THAT document (``{document_id, document_name, category,
    section, payload, confidence, needs_vision, warnings}``) — the "Extracción"
    subtab shows extractions per file. ``payload`` is the CONSOLIDATED Bases
    Técnicas, merged from those per-document reads. Nothing auto-commits."""

    extraction: Extraction
    schema: type
    # None when the account fell back to the built-in GENERIC definition.
    line_record_schema: LineRecordSchema | None
    payload: dict[str, Any] | None
    parsed: dict[str, Any] | None
    confidence: Decimal | None
    document_extractions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _merge_antecedentes_payloads(
    definition: dict[str, Any] | None, payloads: list[dict[str, Any]]
) -> dict[str, Any]:
    """Consolidate per-document extractions into ONE Bases Técnicas payload.

    Walks the (fallback GENERIC) sectioned definition and, for each field, picks
    the first non-empty scalar across the documents (document order), the union of
    distinct rows for a ``list`` field, and the first non-empty value per subkey
    for a ``group``. Deterministic and code-only — the model reads each document;
    the CONSOLIDATION is arithmetic, never a second guess."""
    merged: dict[str, Any] = {}
    for section in (definition or {}).get("sections", []) or []:
        skey = section.get("key")
        if not skey:
            continue
        sec_out: dict[str, Any] = {}
        for field_def in section.get("fields", []) or []:
            fkey = field_def.get("key")
            if not fkey:
                continue
            ftype = str(field_def.get("type") or "text").lower()
            values = []
            for payload in payloads:
                secval = payload.get(skey) if isinstance(payload, dict) else None
                if isinstance(secval, dict):
                    values.append(secval.get(fkey))
            if ftype == "list":
                rows: list[Any] = []
                for value in values:
                    if isinstance(value, list):
                        for row in value:
                            if row not in rows:
                                rows.append(row)
                sec_out[fkey] = rows
            elif ftype == "group":
                group: dict[str, Any] = {}
                for value in values:
                    if isinstance(value, dict):
                        for key, sub in value.items():
                            if _filled(sub) and not _filled(group.get(key)):
                                group[key] = sub
                sec_out[fkey] = group or None
            else:
                chosen = None
                for value in values:
                    if _filled(value):
                        chosen = value
                        break
                sec_out[fkey] = chosen
        merged[skey] = sec_out
    return merged


def _antecedentes_needs_vision(document: Document, exc: "DocumentUnavailable") -> bool:
    """True when a document could not be read as text because it is an IMAGE or a
    SCANNED (image-only) PDF — the case a vision-model fallback would handle."""
    mime = (document.mime_type or "").lower()
    name = (document.original_name or "").lower()
    if mime.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        return True
    message = str(exc).lower()
    return "scanned" in message or "no readable text" in message


def consolidate_antecedentes(
    db: Session,
    *,
    case_file_id: int,
    broker_id: int,
    user: User | None = None,
) -> AntecedentesConsolidation:
    """Read every antecedentes document of an account and SUGGEST a consolidated,
    per-ramo record. Writes exactly ONE ``extraction`` row in every outcome and
    NEVER the expediente itself — the review + register steps own that (rule 6).

    Raises :class:`DocumentUnavailable` (no readable antecedentes) BEFORE any row
    is written; other :class:`AIError` subclasses after the row exists and is
    marked failed. A ramo with no configured template does NOT raise — it falls
    back to a built-in GENERIC antecedentes definition so a free upload always
    yields an extraction (ramos are advisory; ``insurance_line_id`` is nullable).
    """
    case = get_account_case_file(db, case_file_id, broker_id)
    if case is None:
        raise DocumentUnavailable(
            f"Account {case_file_id} not found in this workspace"
        )

    # v9: a ramo no longer owns a field schema. The GENERIC definition drives the
    # consolidated Bases-Técnicas shape whenever the ramo has no (deprecated)
    # ``definition``. Resolution order is unchanged; a NULL line falls back too.
    schema_row = resolve_account_line_record_schema(db, case, broker_id)
    if schema_row is not None:
        definition = schema_row.definition or _GENERIC_ANTECEDENTES_DEFINITION
        schema_name = schema_row.name
        schema_id: int | None = schema_row.id
        schema_version = schema_row.version
    else:
        definition = _GENERIC_ANTECEDENTES_DEFINITION
        schema_name = "Antecedentes (genérico)"
        schema_id = None
        schema_version = 0

    documents = _antecedentes_documents(db, case, broker_id)
    if not documents:
        raise DocumentUnavailable(
            "No hay antecedentes que consolidar en esta cuenta"
        )

    sources: list[tuple[Document, str]] = []
    load_warnings: list[str] = []
    # Documents that could not be read as text because they are images / scans —
    # a vision-model fallback would handle them. NOT yet implemented: they are
    # surfaced as ``needs_vision`` per-document entries, never silently dropped.
    vision_pending: list[Document] = []
    budget = ANTECEDENTES_MAX_CHARS
    for document in documents:
        try:
            text = load_document_text(document)
        except DocumentUnavailable as exc:
            if _antecedentes_needs_vision(document, exc):
                vision_pending.append(document)
                load_warnings.append(
                    f"{document.original_name}: requiere extracción por visión "
                    "(imagen/escaneo) — aún no implementado"
                )
            else:
                load_warnings.append(f"{document.original_name}: {exc}")
            continue
        if budget <= 0:
            load_warnings.append(
                f"{document.original_name}: omitido por límite de contexto"
            )
            continue
        if len(text) > budget:
            text = text[:budget] + "\n\n[...truncado...]"
        budget -= len(text)
        sources.append((document, text))

    if not sources and not vision_pending:
        raise DocumentUnavailable(
            "Ningún antecedente de la cuenta pudo leerse (¿archivos escaneados?)"
        )

    schema = build_dynamic_schema(definition)
    ramo_name = getattr(case.insurance_line, "name", None) or schema_name
    primary_document = (sources[0][0] if sources else documents[0])

    extraction = Extraction(
        broker_id=broker_id,
        document_id=primary_document.id,
        case_file_id=case.id,
        category=DocumentCategory.ANTECEDENTES_PACK,
        kind=ExtractionKind.CASE_DOCUMENT,
        model=settings.AI_MODEL,
        prompt_version=ANTECEDENTES_PROMPT_VERSION,
        status=ExtractionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        created_by_id=getattr(user, "id", None),
    )
    db.add(extraction)
    db.flush()

    def _fail(exc: AIError) -> None:
        extraction.status = ExtractionStatus.FAILED
        extraction.error = f"{type(exc).__name__}: {exc}"
        extraction.finished_at = datetime.now(timezone.utc)
        db.commit()

    # PER-DOCUMENT extraction: read each source document on its own so the review
    # UI can show the pieces extracted from THAT file; the consolidated Bases
    # Técnicas is then MERGED from those reads in code (suggest-only, rule 6).
    per_document: list[dict[str, Any]] = []
    doc_payloads: list[dict[str, Any]] = []
    warnings: list[str] = []
    model_name = settings.AI_MODEL
    prompt_tokens = completion_tokens = 0
    last_raw: dict[str, Any] = {}
    for document, text in sources:
        messages = _antecedentes_messages(
            schema, [(document, text)], ramo_name=ramo_name
        )
        try:
            (
                payload,
                parsed,
                raw,
                doc_model,
                in_tokens,
                out_tokens,
                doc_warnings,
            ) = _invoke_structured_schema(schema, messages)
        except AIError as exc:
            _fail(exc)
            raise
        except Exception as exc:  # noqa: BLE001 - last-resort net; never a 500
            wrapped = _wrap_provider_error(exc)
            _fail(wrapped)
            raise wrapped from exc

        doc_stored = payload.model_dump(mode="json") if payload is not None else parsed
        doc_confidence = _payload_confidence(parsed, payload)
        doc_payloads.append(doc_stored if isinstance(doc_stored, dict) else {})
        per_document.append(
            {
                "document_id": document.id,
                "document_name": document.original_name,
                "category": document.category.value if document.category else None,
                "section": document.section.value if document.section else None,
                "payload": doc_stored,
                "confidence": str(doc_confidence) if doc_confidence is not None else None,
                "needs_vision": False,
                "warnings": doc_warnings,
            }
        )
        warnings.extend(doc_warnings)
        model_name = doc_model or model_name
        prompt_tokens += in_tokens or 0
        completion_tokens += out_tokens or 0
        last_raw = raw

    # Image / scanned documents awaiting a vision-model fallback: surfaced as
    # explicit per-document entries so nothing is silently lost.
    for document in vision_pending:
        per_document.append(
            {
                "document_id": document.id,
                "document_name": document.original_name,
                "category": document.category.value if document.category else None,
                "section": document.section.value if document.section else None,
                "payload": None,
                "confidence": None,
                "needs_vision": True,
                "warnings": ["Requiere extracción por visión (aún no implementado)"],
            }
        )

    # CONSOLIDATE the per-document reads into one Bases Técnicas payload, then
    # normalise it through the dynamic schema (Chilean number/date parsing).
    merged = _merge_antecedentes_payloads(definition, doc_payloads)
    merged_payload, merge_warnings = _coerce_payload(schema, merged)
    warnings.extend(merge_warnings)
    if merged_payload is not None:
        stored: dict[str, Any] | None = merged_payload.model_dump(mode="json")
        confidence = _payload_confidence({}, merged_payload)
    else:
        stored = merged
        confidence = None
    warnings = [*load_warnings, *warnings]

    extraction.model = model_name
    extraction.prompt_tokens = prompt_tokens or None
    extraction.completion_tokens = completion_tokens or None
    extraction.raw_output = {**last_raw, "per_document": per_document}
    extraction.parsed = stored
    extraction.confidence = confidence
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = datetime.now(timezone.utc)
    parsed = stored

    # Upsert the expediente to REVIEW holding the suggested payload (suggest-only:
    # the human validates & completes before register commits it).
    expediente = db.scalars(
        select(RecordExpediente).where(
            RecordExpediente.broker_id == broker_id,
            RecordExpediente.case_file_id == case.id,
        )
    ).first()
    if expediente is None:
        expediente = RecordExpediente(broker_id=broker_id, case_file_id=case.id)
        db.add(expediente)
    if expediente.status != RecordExpedienteStatus.REGISTERED:
        expediente.payload = stored
    expediente.status = RecordExpedienteStatus.REVIEW
    expediente.insurance_line_id = case.insurance_line_id
    expediente.line_record_schema_id = schema_id
    expediente.schema_version = schema_version
    expediente.source_extraction_id = extraction.id
    expediente.ai_confidence = confidence

    db.commit()
    db.refresh(extraction)

    return AntecedentesConsolidation(
        extraction=extraction,
        schema=schema,
        line_record_schema=schema_row,
        payload=stored,
        parsed=parsed,
        confidence=confidence,
        document_extractions=per_document,
        warnings=warnings,
    )


# --- 1c. Dynamic budget-proposal extraction (v8) -----------------------------

BUDGET_PROPOSAL_PROMPT_VERSION = "budget-proposal-v1"

# The premium columns the fixed core carries — invariant-checked, never re-derived.
_BUDGET_MONEY_KEYS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
)


def _budget_proposal_messages(
    spec: "CategorySpec", document: Document, text: str
) -> list[dict[str, str]]:
    """OpenAI-style messages for the tool-calling budget-proposal read.

    Unlike ``_extraction_messages`` this does NOT inline the JSON schema hint —
    the SLIM tool schema carries the shape, which is exactly what shed the
    ~5.6 KB of prompt bloat that drove the Stage-5 timeouts. The Spanish guidance
    stays (the source documents are Spanish; CLAUDE.md rule 1).
    """
    system = (
        f"{_GENERIC_EXTRACTION_SYSTEM}\n\n"
        f"GUÍA PARA ESTA CATEGORÍA ({spec.category.value})\n{spec.guidance}"
    )
    user = (
        f"Archivo: {document.original_name}\n"
        f"Categoría: {spec.category.value}\n\n"
        "--- INICIO DEL DOCUMENTO ---\n"
        f"{text}\n"
        "--- FIN DEL DOCUMENTO ---\n\n"
        "Llama a la función con los datos extraídos."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


@dataclass
class BudgetProposalResult:
    """What the dynamic per-offer SUGGEST returns.

    ``document_type`` is ``"not_a_proposal"`` for a wrong file — a SUCCEEDED-but-
    flagged outcome that never raises, so a router can render a rejection card.
    """

    extraction: Extraction
    payload: Any | None
    parsed: dict[str, Any] | None
    document_type: str
    document_type_confidence: Decimal | None
    rejection_reason: str | None
    warnings: list[str] = field(default_factory=list)

    @property
    def extraction_id(self) -> int:
        return self.extraction.id

    @property
    def is_wrong_file(self) -> bool:
        return self.document_type == "not_a_proposal"


def extract_budget_proposal(
    db: Session,
    *,
    document_id: int,
    broker_id: int,
    user: User | None = None,
) -> BudgetProposalResult:
    """Read one insurer offer DYNAMICALLY: wrong-file verdict + fixed core + facets.

    Writes exactly ONE ``extraction`` row in EVERY outcome — success, wrong-file
    (SUCCEEDED-but-flagged), provider failure, unparsable — carrying the model,
    the prompt version, the raw output, the parsed result and a confidence
    (rule 6). It never writes a ``proposal`` or a ``comparison_source``: those are
    the caller/confirm step's job.

    The fixed money/period core is invariant-checked with the SHARED
    :func:`reconcile_money`; a contradiction is recorded as a WARNING (SUGGEST
    never raises on money — 422 belongs to the confirm step).
    """
    from app.schemas.extraction.budget_proposal import budget_proposal_tool_schema
    from app.schemas.extraction.registry import spec_for

    document = get_document(db, document_id, broker_id)
    if document is None:
        raise DocumentUnavailable(f"Document {document_id} not found in this workspace")

    spec = spec_for(DocumentCategory.BUDGET_PROPOSAL)
    # Fail before creating a row if there is nothing to read.
    text = load_document_text(document)

    extraction = Extraction(
        broker_id=broker_id,
        document_id=document.id,
        case_file_id=document.case_file_id,
        category=spec.category,
        kind=spec.extraction_kind,
        model=settings.AI_MODEL,
        prompt_version=spec.prompt_version,
        status=ExtractionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        created_by_id=getattr(user, "id", None),
    )
    db.add(extraction)
    db.flush()

    def _fail(exc: AIError) -> None:
        extraction.status = ExtractionStatus.FAILED
        extraction.error = f"{type(exc).__name__}: {exc}"
        extraction.finished_at = datetime.now(timezone.utc)
        db.commit()

    try:
        result = tool_call(
            messages=_budget_proposal_messages(spec, document, text),
            tool_schema=budget_proposal_tool_schema(),
            model=model_for(AITask.EXTRACT),
            timeout=settings.AI_TIMEOUT_SECONDS,
            schema=spec.schema,
        )
    except AIError as exc:
        _fail(exc)
        raise
    except Exception as exc:  # noqa: BLE001 - last-resort net; never a 500
        wrapped = _wrap_provider_error(exc)
        _fail(wrapped)
        raise wrapped from exc

    payload = result.payload
    parsed = result.parsed
    raw = result.raw
    model_name = result.model or settings.AI_MODEL
    usage = result.usage or {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    warnings = list(result.warnings)

    # Reconcile the fixed money core — best-effort, warnings only (SUGGEST).
    if payload is not None:
        money_values = {
            key: getattr(payload, key, None)
            for key in _BUDGET_MONEY_KEYS
            if getattr(payload, key, None) is not None
        }
        if money_values:
            try:
                _derived, money_errors = reconcile_money(money_values)
                for err in money_errors:
                    warnings.append(
                        f"Money inconsistency in {err.get('field')}: {err.get('rule')}"
                    )
            except Exception:  # noqa: BLE001 - a malformed figure is a warning, not a raise
                warnings.append("Money core could not be reconciled")

    document_type = "budget_proposal"
    document_type_confidence: Decimal | None = None
    rejection_reason: str | None = None
    if payload is not None:
        document_type = getattr(payload, "document_type", None) or "budget_proposal"
        document_type_confidence = getattr(payload, "document_type_confidence", None)
        rejection_reason = getattr(payload, "rejection_reason", None)
    elif isinstance(parsed, dict):
        document_type = parsed.get("document_type") or "budget_proposal"
        rejection_reason = parsed.get("rejection_reason")

    extraction.model = model_name
    extraction.prompt_tokens = prompt_tokens
    extraction.completion_tokens = completion_tokens
    extraction.raw_output = raw
    extraction.parsed = payload.model_dump(mode="json") if payload is not None else parsed
    extraction.confidence = _payload_confidence(parsed, payload)
    # A wrong file is a SUCCEEDED extraction — the read itself worked; the verdict
    # is the payload, not a failure. Never raises.
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(extraction)

    return BudgetProposalResult(
        extraction=extraction,
        payload=payload,
        parsed=parsed,
        document_type=document_type,
        document_type_confidence=document_type_confidence,
        rejection_reason=rejection_reason,
        warnings=warnings,
    )


# --- 1d. Holistic comparison + outbound propuesta (v9, tool-calling) ---------
#
# The comparison is NO LONGER an align-over-compact-facets pass. The model sees
# ALL the cotización READINGS at once (money core + facets) PLUS the prior
# comparison's established dimensions (so standardization is CARRIED FORWARD, not
# re-derived) and produces, through the ``submit_comparison`` tool, a
# standardized table (dimensions aligned across insurers: common + per-insurer
# extras, each cell carrying value + verbatim) AND an AI recommendation.
#
# BLOCK-ON-PROVIDER-DOWN: DeepInfra is flaky and ``tool_call`` already retries
# then raises a typed :class:`AIError`. Here that error PROPAGATES — there is NO
# deterministic slug-match fallback. The step is BLOCKED (the router turns the
# AIError into a clean 502/503/504), never silently degraded to a half-baked
# comparison.

COMPARE_PROMPT_VERSION = "comparison-submit-v1"
RECOMMEND_PROMPT_VERSION = "comparison-recommend-v1"
PROPUESTA_PROMPT_VERSION = "propuesta-submit-v1"

# Cap each facet's label / verbatim in the ASSEMBLED INPUT so a wide programme
# stays inside the window; the batching threshold splits when even that is large.
COMPARE_LABEL_CHARS = 120
COMPARE_VERBATIM_CHARS = 600

# The money/period core each reading contributes (proposal column names).
_COMPARE_CORE_KEYS = (
    "insurer_name",
    "insurer_rut",
    "insurer_cmf_code",
    "quotation_number",
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

# The facet groups a standardized dimension can carry (mirrors the extraction).
_COMPARE_GROUPS = (
    "coverage",
    "exclusion",
    "deductible",
    "sublimit",
    "clause",
    "warranty",
    "other",
)


@dataclass
class ComparisonResult:
    """The holistic comparison: standardized table + AI recommendation + snapshot."""

    extraction: Extraction | None
    table: dict[str, Any]
    recommendation: dict[str, Any] | None
    dictionary: list[dict[str, Any]]
    canonical_version: int
    used_ai: bool
    batched: bool
    batch_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class PropuestaResult:
    """The outbound propuesta: a validated minimum core + a free ``additional`` tail."""

    extraction: Extraction | None
    core: dict[str, Any]
    additional: Any
    is_core_valid: bool
    money_errors: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None


def _source_facets(source: "ComparisonSource") -> list[dict[str, Any]]:
    """The cached facet list of a source, defensively normalised to dict rows."""
    facets = source.facets
    if isinstance(facets, dict):
        facets = facets.get("facets") or list(facets.values())
    if not isinstance(facets, list):
        return []
    return [f for f in facets if isinstance(f, dict)]


def _canonical_key(facet: dict[str, Any]) -> str:
    """A ``coverage_key``-style slug: the alignment key when the AI is unavailable."""
    return coverage_key(None, str(facet.get("key") or facet.get("label") or "unnamed"))


def _comparison_audit_document_id(db: Session, sources: list["ComparisonSource"]) -> int | None:
    """A representative source document for the audit row (extraction needs one)."""
    for source in sources:
        if source.source_extraction_id is not None:
            src_extraction = db.get(Extraction, source.source_extraction_id)
            if src_extraction is not None and src_extraction.document_id is not None:
                return src_extraction.document_id
        proposal = getattr(source, "proposal", None)
        if proposal is not None and getattr(proposal, "source_document_id", None):
            return proposal.source_document_id
    return None


# New submit_comparison / submit_propuesta machinery (v9).


def _short(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _source_money_core(db: Session, source: "ComparisonSource") -> dict[str, Any]:
    """The money/period core a column read, from its persisted extraction parse."""
    if source.source_extraction_id is None:
        return {}
    extraction = db.get(Extraction, source.source_extraction_id)
    if extraction is None or not isinstance(extraction.parsed, dict):
        return {}
    parsed = extraction.parsed
    return {
        key: parsed.get(key) for key in _COMPARE_CORE_KEYS if parsed.get(key) is not None
    }


def _source_reading(db: Session, source: "ComparisonSource") -> dict[str, Any]:
    """One insurer's FULL reading: money core + facets, tagged by column id.

    This is the holistic input — the model sees the whole offer, not a compacted
    facet slug. Labels / verbatim are capped so a wide programme stays bounded.
    """
    reading: dict[str, Any] = {
        "comparison_source_id": source.id,
        "proposal_id": source.proposal_id,
        "is_wrong_file": bool(source.is_wrong_file),
        "money_core": _source_money_core(db, source),
        "facets": [
            {
                "key": f.get("key"),
                "group": f.get("group"),
                "label": _short(f.get("label"), COMPARE_LABEL_CHARS),
                "present": f.get("present"),
                "value": f.get("value"),
                "verbatim": _short(f.get("verbatim"), COMPARE_VERBATIM_CHARS),
            }
            for f in _source_facets(source)
        ],
    }
    if source.is_wrong_file:
        reading["wrong_file_reason"] = source.wrong_file_reason
    return reading


def _compare_input_blob(readings: list[dict[str, Any]], prior_structure: dict[str, Any]) -> str:
    return json.dumps(
        {"prior_structure": prior_structure, "readings": readings},
        ensure_ascii=False,
        default=str,
    )


def _batch_readings(
    readings: list[dict[str, Any]], max_chars: int, prior_structure: dict[str, Any]
) -> list[list[dict[str, Any]]]:
    """Greedily pack readings into batches whose assembled input fits ``max_chars``.

    A single oversized reading still gets its own batch (never dropped).
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for reading in readings:
        trial = current + [reading]
        if current and len(_compare_input_blob(trial, prior_structure)) > max_chars:
            batches.append(current)
            current = [reading]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches or [list(readings)]


def _normalize_dimensions(value: Any) -> list[dict[str, Any]]:
    """Defensively coerce the tool's ``dimensions`` into a list of dict rows."""
    if isinstance(value, dict):
        value = value.get("dimensions") or list(value.values())
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        group = row.get("group")
        if not (isinstance(group, str) and group.strip().lower() in _COMPARE_GROUPS):
            group = "other"
        cells = row.get("cells")
        cells = [c for c in cells if isinstance(c, dict)] if isinstance(cells, list) else []
        out.append(
            {
                "key": row.get("key") or row.get("label") or "unnamed",
                "group": group,
                "label": row.get("label"),
                "scope": row.get("scope") if row.get("scope") in {"common", "extra"} else None,
                "cells": cells,
            }
        )
    return out


def _normalize_recommendation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    caveats = value.get("caveats")
    caveats = [str(c) for c in caveats] if isinstance(caveats, list) else []
    return {
        "recommended_comparison_source_id": value.get("recommended_comparison_source_id"),
        "recommended_proposal_id": value.get("recommended_proposal_id"),
        "rationale": value.get("rationale"),
        "caveats": caveats,
    }


def _merge_dimensions(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union batched dimension lists by key, concatenating their cells.

    Deterministic code merge (the user allows this): the standardized structure
    is preserved and each insurer's cells accumulate under one canonical row.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in existing:
        key = str(row.get("key"))
        by_key[key] = {**row, "cells": list(row.get("cells") or [])}
        order.append(key)
    for row in new:
        key = str(row.get("key"))
        if key in by_key:
            by_key[key]["cells"] = by_key[key]["cells"] + list(row.get("cells") or [])
        else:
            by_key[key] = {**row, "cells": list(row.get("cells") or [])}
            order.append(key)
    return [by_key[key] for key in order]


def _merge_dictionary(
    prior_dictionary: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    current_version: int | None,
    prior_len: int,
) -> tuple[list[dict[str, Any]], int]:
    """Grow the MONOTONIC canonical dictionary from the standardized dimensions.

    Same snapshot semantics as before: the dictionary only appends, and the
    version bumps only when it actually grew on a re-run.
    """
    dictionary = [
        dict(entry) for entry in prior_dictionary if isinstance(entry, dict) and entry.get("key")
    ]
    seen = {str(entry["key"]) for entry in dictionary}
    for dim in dimensions:
        key = dim.get("key")
        if key and str(key) not in seen:
            dictionary.append(
                {"key": key, "group": dim.get("group") or "other", "label": dim.get("label") or key}
            )
            seen.add(str(key))

    grew = len(dictionary) > prior_len
    if prior_len == 0:
        version = max(current_version or 0, 1)
    elif grew:
        version = (current_version or 1) + 1
    else:
        version = current_version or 1
    return dictionary, version


def _submit_comparison_tool_schema() -> dict[str, Any]:
    """The tool that standardizes ALL readings into one table + a recommendation."""
    cell = {
        "type": "object",
        "properties": {
            "comparison_source_id": {"type": "integer", "description": "La columna a la que pertenece la celda."},
            "present": {"type": "boolean", "description": "true si la oferta la ampara, false si la excluye."},
            "value": {"type": ["string", "number"], "description": "Valor: límite UF, %, mínimo UF, días…"},
            "verbatim": {"type": "string", "description": "La redacción EXACTA de esa oferta para esta dimensión."},
        },
        "required": ["comparison_source_id"],
    }
    dimension = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Slug canónico estable, p. ej. 'sismo'."},
            "group": {"type": "string", "enum": list(_COMPARE_GROUPS)},
            "label": {"type": "string", "description": "Etiqueta corta en español."},
            "scope": {"type": "string", "enum": ["common", "extra"], "description": "common si todas las ofertas la tienen; extra si no."},
            "cells": {"type": "array", "items": cell},
        },
        "required": ["key", "group"],
    }
    return {
        "name": "submit_comparison",
        "description": (
            "Estandariza TODAS las lecturas de cotizaciones en UNA tabla comparativa "
            "(dimensiones alineadas entre aseguradoras: comunes + extras por aseguradora, "
            "cada celda con valor y redacción literal) y entrega una recomendación. "
            "Reutiliza las dimensiones del diccionario previo para mantener la estandarización; "
            "no inventes dimensiones ni valores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dimensions": {"type": "array", "items": dimension},
                "recommendation": _recommendation_schema_props(),
            },
            "required": ["dimensions"],
        },
    }


def _recommendation_schema_props(required: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "description": "La oferta recomendada con su fundamento y salvedades.",
        "properties": {
            "recommended_comparison_source_id": {"type": "integer", "description": "La columna recomendada."},
            "recommended_proposal_id": {"type": "integer", "description": "El proposal promovido, si existe."},
            "rationale": {"type": "string", "description": "Fundamento de la recomendación, en español."},
            "caveats": {"type": "array", "items": {"type": "string"}, "description": "Salvedades o riesgos a revisar."},
        },
    }
    if required:
        schema["required"] = ["recommended_comparison_source_id", "rationale", "caveats"]
    return schema


def _recommend_tool_schema() -> dict[str, Any]:
    return {
        "name": "recommend_comparison",
        "description": (
            "Dada la tabla comparativa estandarizada, entrega UNA recomendación: la oferta "
            "recomendada, su fundamento y las salvedades. No inventes datos fuera de la tabla."
        ),
        "parameters": {
            "type": "object",
            "properties": {"recommendation": _recommendation_schema_props(required=True)},
            "required": ["recommendation"],
        },
    }


def _submit_comparison_messages(
    readings: list[dict[str, Any]], prior_structure: dict[str, Any]
) -> list[dict[str, str]]:
    system = (
        "Eres el estandarizador y recomendador de un comparador de ofertas de seguros en Chile. "
        "Recibes las LECTURAS COMPLETAS de cada cotización (núcleo de prima/vigencia + facetas con "
        "su redacción literal) y la ESTRUCTURA PREVIA del comparador (diccionario de dimensiones ya "
        "establecidas). Estandariza todas las ofertas sobre esas dimensiones — AGREGA solo dimensiones "
        "genuinamente nuevas, reutilizando las previas para que la estandarización se mantenga entre "
        "cargas incrementales. Cada dimensión lleva sus celdas por columna (valor + redacción literal) "
        "y su alcance (common/extra). Además entrega una recomendación con fundamento y salvedades. "
        "Llama a la función submit_comparison. No inventes dimensiones ni valores."
    )
    user = _compare_input_blob(readings, prior_structure)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _recommend_messages(
    dimensions: list[dict[str, Any]], readings: list[dict[str, Any]]
) -> list[dict[str, str]]:
    money = [
        {"comparison_source_id": r.get("comparison_source_id"), "money_core": r.get("money_core")}
        for r in readings
    ]
    valid_ids = [m["comparison_source_id"] for m in money if m.get("comparison_source_id") is not None]
    system = (
        "Eres el recomendador de un comparador de ofertas de seguros en Chile. Recibes la tabla "
        "comparativa estandarizada (dimensiones con celdas por columna) y los núcleos de prima de "
        "cada oferta. Entrega UNA recomendación con la columna recomendada, su fundamento y las "
        "salvedades. Llama a la función recommend_comparison. "
        "OBLIGATORIO: DEBES elegir un pick concreto — 'recommended_comparison_source_id' tiene que "
        "ser uno de los comparison_source_id presentes en el arreglo 'money' "
        f"(valores válidos: {valid_ids or 'ver arreglo money'}), nunca null. "
        "El 'rationale' DEBE ser un texto no vacío en español que justifique esa elección. "
        "Aunque las ofertas sean parecidas o falten datos, elige la mejor opción disponible y "
        "explica el porqué; usa 'caveats' para las salvedades, no para evitar elegir."
    )
    user = json.dumps({"dimensions": dimensions, "money": money}, ensure_ascii=False, default=str)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def submit_comparison(
    db: Session,
    *,
    comparison: "Comparison",
    sources: list["ComparisonSource"],
    broker_id: int,
    user: User | None = None,
) -> ComparisonResult:
    """Holistically standardize an account's offers into ONE comparison table.

    The model sees ALL cotización readings at once + the prior comparison
    structure (carried forward). Default is ONE ``submit_comparison`` tool call;
    when the assembled input exceeds ``AI_COMPARE_MAX_INPUT_CHARS`` the readings
    are split into batches, each run, then MERGED deterministically into one
    table, with a final ``recommend_comparison`` call over the merged table.

    BLOCK-ON-PROVIDER-DOWN: a typed :class:`AIError` from ``tool_call`` PROPAGATES
    — there is no deterministic fallback. The router turns it into a clean
    provider error (the step is blocked, never a half-baked comparison).
    """
    from app.models.comparison import ComparisonStatus

    ensure_ai_configured()

    prior_dictionary = comparison.dictionary if isinstance(comparison.dictionary, list) else []
    prior_len = len(prior_dictionary)
    prior_structure = {"dictionary": prior_dictionary}

    readings = [_source_reading(db, source) for source in sources]
    blob = _compare_input_blob(readings, prior_structure)

    warnings: list[str] = []
    batched = False
    batch_count = 1
    compare_model = model_for(AITask.COMPARE)
    prompt_tokens = 0
    completion_tokens = 0
    model_name = compare_model

    if len(blob) <= settings.AI_COMPARE_MAX_INPUT_CHARS:
        result = tool_call(
            messages=_submit_comparison_messages(readings, prior_structure),
            tool_schema=_submit_comparison_tool_schema(),
            model=compare_model,
            timeout=settings.AI_TIMEOUT_SECONDS,
            max_tokens=settings.AI_COMPARE_MAX_TOKENS,
        )
        parsed = result.parsed or {}
        dimensions = _normalize_dimensions(parsed.get("dimensions"))
        recommendation = _normalize_recommendation(parsed.get("recommendation"))
        model_name = result.model or compare_model
        prompt_tokens = (result.usage or {}).get("prompt_tokens") or 0
        completion_tokens = (result.usage or {}).get("completion_tokens") or 0
        # HARD GUARD: an empty table means a truncated / empty model answer.
        # Block cleanly — NEVER commit an empty table with a 200 (the bug this fixes).
        if not dimensions:
            raise AIProviderError(
                "The comparison produced no dimensions (truncated or empty model "
                "answer); the step is blocked rather than committing an empty table"
            )
    else:
        batched = True
        batches = _batch_readings(readings, settings.AI_COMPARE_MAX_INPUT_CHARS, prior_structure)
        batch_count = len(batches)
        merged: list[dict[str, Any]] = []
        for batch in batches:
            r = tool_call(
                messages=_submit_comparison_messages(batch, prior_structure),
                tool_schema=_submit_comparison_tool_schema(),
                model=compare_model,
                timeout=settings.AI_TIMEOUT_SECONDS,
                max_tokens=settings.AI_COMPARE_MAX_TOKENS,
            )
            batch_dimensions = _normalize_dimensions((r.parsed or {}).get("dimensions"))
            # HARD GUARD per batch: a truncated batch would silently drop a whole
            # insurer's dimensions from the merged table.
            if not batch_dimensions:
                raise AIProviderError(
                    "A comparison batch produced no dimensions (truncated or empty "
                    "model answer); the step is blocked"
                )
            merged = _merge_dimensions(merged, batch_dimensions)
            prompt_tokens += (r.usage or {}).get("prompt_tokens") or 0
            completion_tokens += (r.usage or {}).get("completion_tokens") or 0
            model_name = r.model or compare_model
        dimensions = merged
        recommendation = None  # produced by the unified always-recommend step below
        if not dimensions:
            raise AIProviderError(
                "The merged comparison produced no dimensions; the step is blocked"
            )
        warnings.append(
            f"Comparison input exceeded {settings.AI_COMPARE_MAX_INPUT_CHARS} chars; "
            f"ran in {batch_count} batches and merged."
        )

    # ALWAYS produce a recommendation: if the holistic / merged answer named no
    # pick (or gave none at all), run the dedicated recommend call over the table.
    if recommendation is None or recommendation.get("recommended_comparison_source_id") is None:
        rec = tool_call(
            messages=_recommend_messages(dimensions, readings),
            tool_schema=_recommend_tool_schema(),
            model=model_for(AITask.RECOMMEND),
            timeout=settings.AI_TIMEOUT_SECONDS,
            max_tokens=settings.AI_COMPARE_MAX_TOKENS,
        )
        rec_value = _normalize_recommendation((rec.parsed or {}).get("recommendation"))
        if rec_value is not None:
            recommendation = rec_value
        prompt_tokens += (rec.usage or {}).get("prompt_tokens") or 0
        completion_tokens += (rec.usage or {}).get("completion_tokens") or 0
        # SOFT GUARD: the required-pick schema is model-steering, not a hard block.
        # If the model still named no pick, degrade to a warning rather than 500 —
        # the caveats/rationale (if any) are preserved and the table still commits.
        if recommendation is None or recommendation.get("recommended_comparison_source_id") is None:
            warnings.append(
                "The AI recommendation named no pick "
                "(recommended_comparison_source_id is null); review the caveats manually."
            )

    dictionary, canonical_version = _merge_dictionary(
        prior_dictionary, dimensions, comparison.canonical_version, prior_len
    )

    columns = [
        {
            "comparison_source_id": source.id,
            "proposal_id": source.proposal_id,
            "is_wrong_file": bool(source.is_wrong_file),
            "wrong_file_reason": source.wrong_file_reason,
        }
        for source in sources
    ]
    table = {
        "dimensions": dimensions,
        "columns": columns,
        "recommendation": recommendation,
        "used_ai": True,
        "batched": batched,
        "batch_count": batch_count,
    }

    extraction: Extraction | None = None
    document_id = _comparison_audit_document_id(db, sources)
    if document_id is not None:
        extraction = Extraction(
            broker_id=broker_id,
            document_id=document_id,
            case_file_id=comparison.case_file_id,
            kind=ExtractionKind.OTHER,
            model=model_name,
            prompt_version=COMPARE_PROMPT_VERSION,
            status=ExtractionStatus.SUCCEEDED,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            prompt_tokens=prompt_tokens or None,
            completion_tokens=completion_tokens or None,
            raw_output={"batched": batched, "batch_count": batch_count},
            parsed=table,
            created_by_id=getattr(user, "id", None),
        )
        db.add(extraction)
        db.flush()
    else:
        warnings.append("No source document resolved; comparison audit row skipped")

    comparison.dictionary = dictionary
    comparison.canonical_version = canonical_version
    comparison.aligned_matrix = table
    comparison.status = ComparisonStatus.ALIGNED
    if extraction is not None:
        comparison.source_extraction_id = extraction.id
    db.add(comparison)
    db.commit()
    if extraction is not None:
        db.refresh(extraction)

    return ComparisonResult(
        extraction=extraction,
        table=table,
        recommendation=recommendation,
        dictionary=dictionary,
        canonical_version=canonical_version,
        used_ai=True,
        batched=batched,
        batch_count=batch_count,
        warnings=warnings,
    )


# --- 1e. Outbound propuesta (validated minimum core + free tail) -------------

# The premium/period columns the propuesta core validates + carries.
_PROPUESTA_CORE_KEYS = (
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
    "coverage_start",
    "coverage_end",
)
_PROPUESTA_MONEY_KEYS = (
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
)
# The minimum a propuesta must state (missing => a warning, not a hard block —
# the premium arithmetic is the only HARD gate). Insured identity + vigencia are
# populated from the account antecedentes/period when the cotización omits them,
# so these warnings SHRINK for a properly-registered account.
_PROPUESTA_MINIMUM = (
    "insurer_id",
    "net_premium_uf",
    "total_premium_uf",
    "insured_name",
    "coverage_start",
    "coverage_end",
)


def _winner_core(db: Session, winner: "Proposal") -> dict[str, Any]:
    """The authoritative minimum core from the committed winning proposal.

    The premium columns were already reconciled at promote time, so they are the
    trustworthy baseline; the tool's core is merged OVER this, never under it.
    """
    core: dict[str, Any] = {"insurer_id": winner.insurer_id}
    for key in _PROPUESTA_CORE_KEYS:
        value = getattr(winner, key, None)
        if value is not None:
            core[key] = value
    return core


def _winner_facets(db: Session, winner: "Proposal", broker_id: int) -> list[dict[str, Any]]:
    """The winner's cached facets, if the column was sourced from a comparison."""
    from app.models.comparison import ComparisonSource

    source = db.scalars(
        select(ComparisonSource).where(
            ComparisonSource.proposal_id == winner.id,
            ComparisonSource.broker_id == broker_id,
        )
    ).first()
    return _source_facets(source) if source is not None else []


def _submit_propuesta_tool_schema() -> dict[str, Any]:
    return {
        "name": "submit_propuesta",
        "description": (
            "Arma la PROPUESTA de salida (para la aseguradora) desde la cotización seleccionada y "
            "el comparador: un NÚCLEO mínimo estandarizado (identidad de la aseguradora, asegurado, "
            "vigencia y el núcleo de prima en UF: afecta, exenta, neta, IVA, total) y una cola libre "
            "'additional' con todo lo demás que valga la pena llevar (coberturas, deducibles, cláusulas). "
            "Copia las cifras de prima tal como vienen; no inventes montos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "core": {
                    "type": "object",
                    "properties": {
                        "insured_name": {"type": "string"},
                        "insured_rut": {"type": "string"},
                        "coverage_start": {"type": "string"},
                        "coverage_end": {"type": "string"},
                        "validity_business_days": {"type": "integer"},
                        "taxable_premium_uf": {"type": "number"},
                        "exempt_premium_uf": {"type": "number"},
                        "net_premium_uf": {"type": "number"},
                        "vat_uf": {"type": "number"},
                        "total_premium_uf": {"type": "number"},
                        "comprehensive_rate_permille": {"type": "number"},
                        "commission_pct": {"type": "number"},
                    },
                },
                "additional": {
                    "type": "array",
                    "description": "Cola libre: dimensiones heterogéneas que se anexan al final.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "group": {"type": "string"},
                            "label": {"type": "string"},
                            "value": {"type": ["string", "number"]},
                            "verbatim": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["core"],
        },
    }


# The account already KNOWS the insured's identity and the vigencia from its
# registered antecedentes — the winning cotización rarely re-states them. The
# antecedentes payload is a broker-defined, sectioned dict, so we LIFT those
# facts by scanning well-known field keys rather than a fixed path.
_ACCT_NAME_KEYS = (
    "insured_name",
    "razon_social",
    "razonsocial",
    "legal_name",
    "nombre_asegurado",
    "asegurado",
    "contratante",
    "nombre",
)
_ACCT_RUT_KEYS = ("insured_rut", "rut_asegurado", "rut")
_ACCT_START_KEYS = (
    "coverage_start",
    "vigencia_inicio",
    "vigencia_desde",
    "inicio_vigencia",
    "fecha_inicio",
    "desde",
)
_ACCT_END_KEYS = (
    "coverage_end",
    "vigencia_fin",
    "vigencia_hasta",
    "termino_vigencia",
    "fin_vigencia",
    "fecha_termino",
    "hasta",
)


def _scan_payload_for(payload: Any, keys: tuple[str, ...]) -> str | None:
    """First non-empty SCALAR value whose key matches (case-insensitive) ``keys``.

    Walks the sectioned antecedentes payload depth-first. The alias order in
    ``keys`` is precedence: a match on an earlier alias wins over a later one at
    the same depth, so a dedicated ``insured_name`` beats a generic ``nombre``.
    """
    if not isinstance(payload, (dict, list)):
        return None

    def _matches(k: str) -> int | None:
        low = str(k).strip().lower()
        for rank, alias in enumerate(keys):
            if low == alias:
                return rank
        return None

    best_rank: int | None = None
    best_value: str | None = None
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    text = str(v).strip()
                    rank = _matches(k)
                    if text and rank is not None and (best_rank is None or rank < best_rank):
                        best_rank, best_value = rank, text
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    stack.append(item)
    return best_value


def _propuesta_account_context(
    db: Session, comparison: "Comparison", broker_id: int
) -> dict[str, Any]:
    """What the ACCOUNT already knows: insured identity + vigencia defaults.

    Sourced (best-effort, tenant-scoped) from the account's REGISTERED
    ``record_expediente`` (the human-validated antecedentes) and, for the
    vigencia, the ``case_file`` period as a fallback. Returns only the fields it
    could resolve — a miss (no account, no antecedentes) yields ``{}`` and the
    propuesta is unchanged. Never raises: a hiccup here must not fail the mint.
    """
    ctx: dict[str, Any] = {}
    case_file_id = getattr(comparison, "case_file_id", None)
    if case_file_id is None:
        return ctx
    try:
        case_file = db.scalars(
            select(CaseFile).where(
                CaseFile.id == case_file_id,
                CaseFile.broker_id == broker_id,
            )
        ).first()
        if case_file is None:
            return ctx

        expediente = db.scalars(
            select(RecordExpediente).where(
                RecordExpediente.broker_id == broker_id,
                RecordExpediente.case_file_id == case_file.id,
                RecordExpediente.status == RecordExpedienteStatus.REGISTERED,
            )
        ).first()
        if expediente is not None and isinstance(expediente.payload, (dict, list)):
            name = _scan_payload_for(expediente.payload, _ACCT_NAME_KEYS)
            if name:
                ctx["insured_name"] = name
            rut = _scan_payload_for(expediente.payload, _ACCT_RUT_KEYS)
            if rut:
                ctx["insured_rut"] = rut
            start = _scan_payload_for(expediente.payload, _ACCT_START_KEYS)
            if start:
                ctx["coverage_start"] = start
            end = _scan_payload_for(expediente.payload, _ACCT_END_KEYS)
            if end:
                ctx["coverage_end"] = end

        # The registered account PERIOD is the authoritative vigencia fallback.
        if "coverage_start" not in ctx and case_file.period_start is not None:
            ctx["coverage_start"] = case_file.period_start.isoformat()
        if "coverage_end" not in ctx and case_file.period_end is not None:
            ctx["coverage_end"] = case_file.period_end.isoformat()
    except Exception:  # noqa: BLE001 - context is best-effort; never fail the mint
        logger.warning("propuesta account context failed for comparison %s", comparison.id)
        return {}
    return ctx


def _submit_propuesta_messages(
    winner_core: dict[str, Any],
    facets: list[dict[str, Any]],
    dimensions: Any,
    account_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    system = (
        "Eres el estandarizador de la PROPUESTA de salida de un corredor de seguros en Chile. "
        "Recibes la cotización ganadora (núcleo de prima/vigencia) y la tabla comparativa. Devuelve "
        "un núcleo mínimo validado (identidad de la aseguradora, asegurado, vigencia, núcleo de prima "
        "en UF) y una cola libre 'additional' con lo demás. Copia las cifras de prima tal cual; la "
        "estructura chilena es neta = afecta + exenta ; IVA = 0,19 x afecta ; total = neta + IVA. "
        "Llama a la función submit_propuesta."
    )
    if account_context:
        system += (
            "\n\nCONTEXTO DE LA CUENTA (antecedentes ya registrados por el corredor): "
            + json.dumps(account_context, ensure_ascii=False, default=str)
            + ". Usa la identidad del asegurado y la vigencia de este contexto SOLO cuando la "
            "cotización ganadora no las declare; la cotización siempre gana el núcleo de prima y "
            "la identidad de la aseguradora."
        )
    payload = {
        "winner_core": winner_core,
        "winner_facets": facets,
        "comparison_dimensions": dimensions,
        "account_context": account_context or {},
    }
    user = json.dumps(payload, ensure_ascii=False, default=str)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def submit_propuesta(
    db: Session,
    *,
    comparison: "Comparison",
    winner: "Proposal",
    broker_id: int,
    user: User | None = None,
) -> PropuestaResult:
    """Standardize the outbound propuesta: a validated minimum core + a free tail.

    ONE ``submit_propuesta`` tool call (``model_for(PROPUESTA)``) produces the
    core + ``additional`` tail; the core's PREMIUM arithmetic is HARD-validated
    with the SHARED ``reconcile_money_verbose`` (premium hard, rate soft). The
    winner's already-reconciled columns are the authoritative baseline, so the
    tool's core is merged OVER them — a tool that omits (or misreads) the premium
    still yields a valid core from the committed proposal.

    BLOCK-ON-PROVIDER-DOWN: a typed :class:`AIError` PROPAGATES (no fallback).
    """
    ensure_ai_configured()

    winner_core = _winner_core(db, winner)
    facets = _winner_facets(db, winner, broker_id)
    account_context = _propuesta_account_context(db, comparison, broker_id)
    dimensions = (
        comparison.aligned_matrix.get("dimensions")
        if isinstance(comparison.aligned_matrix, dict)
        else None
    )

    result = tool_call(
        messages=_submit_propuesta_messages(
            winner_core, facets, dimensions, account_context=account_context
        ),
        tool_schema=_submit_propuesta_tool_schema(),
        model=model_for(AITask.PROPUESTA),
        timeout=settings.AI_TIMEOUT_SECONDS,
    )
    parsed = result.parsed or {}
    tool_core = parsed.get("core") if isinstance(parsed.get("core"), dict) else {}
    additional = parsed.get("additional")
    if not isinstance(additional, (list, dict)):
        additional = []

    # The validated minimum core, layered by PRECEDENCE (lowest → highest):
    #   account antecedentes/period  <  winning proposal  <  the tool's core.
    # The winning cotización owns the PREMIUM core + the insurer identity; the
    # account only FILLS the insured identity + vigencia the cotización omits.
    core: dict[str, Any] = dict(account_context)
    for key, value in winner_core.items():
        if value is not None:
            core[key] = value
    for key, value in tool_core.items():
        if value is not None:
            core[key] = value

    money = {key: core.get(key) for key in _PROPUESTA_MONEY_KEYS if core.get(key) is not None}
    derived, errors, rate_warnings = reconcile_money_verbose(money)
    for key, value in derived.items():
        core.setdefault(key, value)

    warnings: list[str] = list(result.warnings)
    warnings += [
        f"Rate additivity warning on {w.get('field')}: {w.get('rule')}" for w in rate_warnings
    ]
    warnings += [
        f"Missing minimum propuesta field: {key}"
        for key in _PROPUESTA_MINIMUM
        if core.get(key) in (None, "")
    ]
    is_core_valid = not errors

    extraction: Extraction | None = None
    source_document_id = getattr(winner, "source_document_id", None)
    if source_document_id is not None:
        extraction = Extraction(
            broker_id=broker_id,
            document_id=source_document_id,
            case_file_id=comparison.case_file_id,
            kind=ExtractionKind.OTHER,
            model=result.model or model_for(AITask.PROPUESTA),
            prompt_version=PROPUESTA_PROMPT_VERSION,
            status=ExtractionStatus.SUCCEEDED,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            prompt_tokens=(result.usage or {}).get("prompt_tokens"),
            completion_tokens=(result.usage or {}).get("completion_tokens"),
            raw_output=result.raw,
            parsed={"core": _jsonable(core), "additional": additional},
            created_by_id=getattr(user, "id", None),
        )
        db.add(extraction)
        db.flush()
        db.refresh(extraction)

    return PropuestaResult(
        extraction=extraction,
        core=_jsonable(core),
        additional=additional,
        is_core_valid=is_core_valid,
        money_errors=errors,
        warnings=warnings,
        raw=result.raw,
    )


def _jsonable(core: dict[str, Any]) -> dict[str, Any]:
    """A JSON-safe view of the core (Decimals -> strings)."""
    out: dict[str, Any] = {}
    for key, value in core.items():
        out[key] = str(value) if isinstance(value, Decimal) else value
    return out


# --- 2. Insurer resolution (by normalised code — NEVER by name) --------------


def find_or_create_insurer(
    db: Session,
    *,
    cmf_code: str | None,
    rut: str | None,
    legal_name: str | None = None,
    trade_name: str | None = None,
    broker_id: int | None = None,
) -> Insurer:
    """Resolve the issuing insurer by NORMALISED cmf_code / rut only.

    Names are never used for matching: OCR yields "HDI Seguros S.A.",
    "HDI SEGUROS SA" and "H.D.I." for one company (docs §4.3). A miss creates an
    EXTERNAL insurer (``is_native=False``) attributed to the creating broker —
    which requires BOTH identifiers, since ``insurer`` makes them NOT NULL and
    unique, and a proposal is invalid without them.

    Raises ``ValueError`` when the identifiers are missing or malformed; the
    router turns that into a 422.
    """
    normalized_code: str | None = None
    if cmf_code:
        try:
            normalized_code = normalize_codigo_cmf(cmf_code)
        except InvalidCmf as exc:
            raise ValueError(f"Invalid insurer cmf_code: {exc}") from exc

    normalized_rut: str | None = None
    if rut:
        try:
            normalized_rut = validate_rut(rut)
        except InvalidRut as exc:
            raise ValueError(f"Invalid insurer rut: {exc}") from exc

    if not normalized_code and not normalized_rut:
        raise ValueError(
            "The insurer must be identified by cmf_code or rut — a name is never enough"
        )

    if normalized_code:
        found = db.scalars(select(Insurer).where(Insurer.cmf_code == normalized_code)).first()
        if found is not None:
            return found
    if normalized_rut:
        found = db.scalars(select(Insurer).where(Insurer.rut == normalized_rut)).first()
        if found is not None:
            return found

    if not (normalized_code and normalized_rut):
        raise ValueError(
            "No insurer matches that identifier. Creating one requires BOTH rut and "
            "cmf_code (a proposal is invalid without them)."
        )
    if not legal_name:
        raise ValueError("A new insurer requires legal_name")

    insurer = Insurer(
        rut=normalized_rut,
        cmf_code=normalized_code,
        legal_name=legal_name,
        trade_name=trade_name,
        is_native=False,  # discovered from an uploaded proposal -> external
        created_by_broker_id=broker_id,
    )
    db.add(insurer)
    db.flush()
    return insurer


# --- 3. Confirm -> COMMIT ----------------------------------------------------


def confirm_proposal(
    db: Session,
    *,
    extraction: Extraction,
    quote_request: QuoteRequest,
    suggestion: ProposalSuggestion,
    broker_id: int,
    user: User | None,
    status: str = "submitted",
    is_confirmed: bool = True,
) -> Proposal:
    """COMMIT: turn the reviewed suggestion into a real ``proposal``.

    The payload is whatever the human approved — it may differ from
    ``extraction.parsed``, and that is the point. Provenance is preserved
    (``extraction_id`` + ``extraction_confidence``) and the source document is
    the very file the extraction read, satisfying the NOT NULL rule.
    """
    insurer = find_or_create_insurer(
        db,
        cmf_code=suggestion.insurer.cmf_code,
        rut=suggestion.insurer.rut,
        legal_name=suggestion.insurer.legal_name,
        trade_name=suggestion.insurer.trade_name,
        broker_id=broker_id,
    )

    money = derive_money(suggestion)

    deductibles = {
        peril: {k: v for k, v in entry.model_dump(mode="json").items() if v is not None}
        for peril, entry in suggestion.deductibles.items()
    }

    proposal = Proposal(
        broker_id=broker_id,
        quote_request_id=quote_request.id,
        insurer_id=insurer.id,
        origin=ProposalOrigin.NATIVE if insurer.is_native else ProposalOrigin.EXTERNAL,
        source_document_id=extraction.document_id,
        modality=suggestion.modality,
        activity_classification=suggestion.activity_classification,
        commission_pct=suggestion.commission_pct,
        validity_business_days=suggestion.validity_business_days,
        coverage_start=suggestion.coverage_start,
        coverage_end=suggestion.coverage_end,
        received_at=suggestion.received_at or date.today(),
        deductibles=deductibles or None,
        warranties=suggestion.warranties,
        notes=suggestion.notes,
        status=ProposalStatus(status),
        extraction_id=extraction.id,
        extraction_confidence=extraction.confidence,
        is_confirmed=is_confirmed,
        confirmed_by_id=getattr(user, "id", None) if is_confirmed else None,
        confirmed_at=datetime.now(timezone.utc) if is_confirmed else None,
        **money,
    )
    db.add(proposal)
    db.flush()

    for index, coverage in enumerate(suggestion.coverages):
        text = (coverage.text or "").strip()
        if not text:
            continue
        db.add(
            ProposalCoverage(
                proposal_id=proposal.id,
                kind=CoverageKind(coverage.kind),
                text=text,
                normalized_code=coverage.normalized_code,
                sort_order=coverage.sort_order or index,
            )
        )

    db.commit()
    db.refresh(proposal)
    return proposal


# --- 4. Chat agents ----------------------------------------------------------

_SCOPE_ENTITY = {
    AgentScope.PROPOSAL: EntityType.PROPOSAL,
    AgentScope.QUOTE: EntityType.QUOTE_REQUEST,
}

_GENERAL_SYSTEM = """You are Radal's assistant for an insurance broker in Chile.
You help with clients, placements, quote requests and insurer proposals.

Rules:
- Answer in the language the user writes in (usually Spanish, es-CL).
- Ground every factual claim in the WORKSPACE CONTEXT block. If the context does
  not contain the answer, say so plainly and name what you would need — never
  invent a client, a premium, a policy number or a deductible.
- Amounts are in UF. Chilean premium structure: net = taxable(afecta) + exempt(exenta),
  VAT = 19% of the TAXABLE part only (earthquake cover is exempt), total = net + VAT.
- You cannot modify anything. If asked to create or change a record, explain that
  a human confirms every write in the app, and describe the step to take.
- Be concise and concrete. Prefer a short table or bullet list over prose."""

_PROPOSAL_SYSTEM = """You are Radal's proposal analyst for an insurance broker in Chile.
You compare and explain insurer proposals (propuestas/cotizaciones).

Rules:
- Answer in the language the user writes in (usually Spanish, es-CL).
- Ground every claim in the PROPOSAL CONTEXT block. Never invent a figure, a
  coverage or an exclusion. If a proposal does not state something, say it is not stated.
- Amounts are in UF. net = taxable(afecta) + exempt(exenta); VAT = 19% of the
  TAXABLE part only; total = net + VAT. Rates are per mille.
- Deductibles differ by peril AND by basis: fire is usually a % of the LOSS while
  earthquake is a % of the INSURED AMOUNT of the affected item, with UF minimums.
  When comparing, surface that difference explicitly — a bare "5% vs 1%" is misleading.
- When comparing, lead with total premium, then deductibles, then coverage gaps.
- You cannot modify anything; every write is confirmed by a human in the app."""


def _fmt(value: Any) -> str:
    if value is None:
        return "n/d"
    if isinstance(value, Decimal):
        return f"{value.normalize():f}"
    return str(value)


def _proposal_lines(db: Session, proposals: Iterable[Proposal]) -> list[str]:
    lines: list[str] = []
    for proposal in proposals:
        insurer = db.get(Insurer, proposal.insurer_id)
        lines.append(
            f"- proposal #{proposal.id} | insurer: {insurer.legal_name if insurer else 'n/d'}"
            f" ({insurer.cmf_code if insurer else 'n/d'}) | origin: {proposal.origin}"
            f" | status: {proposal.status}"
            f" | total: UF {_fmt(proposal.total_premium_uf)}"
            f" (net {_fmt(proposal.net_premium_uf)}, taxable {_fmt(proposal.taxable_premium_uf)},"
            f" exempt {_fmt(proposal.exempt_premium_uf)}, vat {_fmt(proposal.vat_uf)})"
            f" | rate: {_fmt(proposal.comprehensive_rate_permille)} per mille"
            f" | commission: {_fmt(proposal.commission_pct)}%"
            f" | cover: {_fmt(proposal.coverage_start)} -> {_fmt(proposal.coverage_end)}"
            f" | confirmed: {proposal.is_confirmed}"
        )
        if proposal.deductibles:
            lines.append(f"    deductibles: {json.dumps(proposal.deductibles, ensure_ascii=False)}")
        covers = [c for c in proposal.coverages if c.kind == CoverageKind.COVERAGE]
        excls = [c for c in proposal.coverages if c.kind == CoverageKind.EXCLUSION]
        if covers:
            lines.append("    coverages: " + " | ".join(c.text for c in covers[:25]))
        if excls:
            lines.append("    exclusions: " + " | ".join(c.text for c in excls[:25]))
        if proposal.warranties:
            lines.append(f"    warranties: {proposal.warranties}")
    return lines


def _proposal_context(db: Session, thread: AgentThread, broker_id: int) -> str:
    """Context for a thread anchored to one proposal or one quote request.

    A proposal-scoped thread also loads its SIBLINGS — "compare these three" is
    the whole point, and it is unanswerable with a single proposal in context.
    """
    quote_request: QuoteRequest | None = None
    if thread.scope == AgentScope.PROPOSAL and thread.entity_id:
        proposal = get_proposal(db, thread.entity_id, broker_id)
        if proposal is None:
            return "PROPOSAL CONTEXT: the anchored proposal no longer exists."
        quote_request = get_quote_request(db, proposal.quote_request_id, broker_id)
    elif thread.entity_id:
        quote_request = get_quote_request(db, thread.entity_id, broker_id)

    if quote_request is None:
        return "PROPOSAL CONTEXT: no quote request is anchored to this thread."

    placement = db.get(Placement, quote_request.placement_id)
    client = db.get(Client, placement.client_id) if placement else None
    insured = db.get(Insured, client.insured_id) if client else None

    lines = [
        "PROPOSAL CONTEXT (broker workspace, read-only)",
        f"quote request #{quote_request.id} | status: {quote_request.status}"
        f" | declared value: UF {_fmt(quote_request.declared_value_uf)}"
        f" | desired cover: {_fmt(quote_request.desired_start)} -> {_fmt(quote_request.desired_end)}",
    ]
    if quote_request.insured_object:
        lines.append(f"insured object: {quote_request.insured_object}")
    if quote_request.requested_coverages:
        lines.append(f"requested coverages: {quote_request.requested_coverages}")
    if insured:
        lines.append(f"insured: {insured.legal_name} ({insured.rut})")
    if quote_request.line_items:
        lines.append("line items:")
        lines += [
            f"- {item.name}: UF {_fmt(item.value_uf)}"
            + (f" — {item.detail}" if item.detail else "")
            for item in quote_request.line_items
        ]

    if thread.scope == AgentScope.PROPOSAL and thread.entity_id:
        lines.append(f"(the user is looking at proposal #{thread.entity_id})")
    lines.append(f"proposals received ({len(quote_request.proposals)}):")
    lines += _proposal_lines(db, quote_request.proposals)
    return "\n".join(lines)


def _general_context(db: Session, broker_id: int) -> str:
    """Server-side, tenant-scoped snapshot of the broker's book of business.

    Read-only and always filtered by ``broker_id`` — the agent can never see
    another broker's workspace, whatever the user types.
    """
    client_rows = db.execute(
        select(Client.status, func.count(Client.id))
        .where(Client.broker_id == broker_id)
        .group_by(Client.status)
    ).all()
    placement_rows = db.execute(
        select(Placement.status, func.count(Placement.id))
        .where(Placement.broker_id == broker_id)
        .group_by(Placement.status)
    ).all()

    lines = [
        "WORKSPACE CONTEXT (this broker only, read-only)",
        "clients by status: "
        + (", ".join(f"{status}={count}" for status, count in client_rows) or "none"),
        "placements by status: "
        + (", ".join(f"{status}={count}" for status, count in placement_rows) or "none"),
    ]

    clients = db.scalars(
        select(Client)
        .where(Client.broker_id == broker_id)
        .order_by(Client.id.desc())
        .limit(25)
    ).all()
    if clients:
        lines.append("recent clients:")
        for client in clients:
            insured = db.get(Insured, client.insured_id)
            lines.append(
                f"- client #{client.id}: {insured.legal_name if insured else 'n/d'}"
                f" ({insured.rut if insured else 'n/d'}) | status: {client.status}"
                f" | sector: {client.sector or 'n/d'}"
            )

    placements = db.scalars(
        select(Placement)
        .where(Placement.broker_id == broker_id)
        .order_by(Placement.id.desc())
        .limit(25)
    ).all()
    if placements:
        lines.append("recent placements:")
        for placement in placements:
            lines.append(
                f"- placement #{placement.id} | client #{placement.client_id}"
                f" | line #{placement.insurance_line_id} | period: {placement.period or 'n/d'}"
                f" | status: {placement.status}"
            )

    quotes = db.scalars(
        select(QuoteRequest)
        .where(QuoteRequest.broker_id == broker_id)
        .order_by(QuoteRequest.id.desc())
        .limit(15)
    ).all()
    if quotes:
        lines.append("recent quote requests:")
        for quote in quotes:
            lines.append(
                f"- quote #{quote.id} | placement #{quote.placement_id}"
                f" | declared: UF {_fmt(quote.declared_value_uf)} | status: {quote.status}"
                f" | proposals: {len(quote.proposals)}"
            )

    proposals = db.scalars(
        select(Proposal)
        .where(Proposal.broker_id == broker_id)
        .order_by(Proposal.id.desc())
        .limit(20)
    ).all()
    if proposals:
        lines.append("recent proposals:")
        lines += _proposal_lines(db, proposals)

    return "\n".join(lines)


def build_context(db: Session, thread: AgentThread, broker_id: int) -> str:
    if thread.scope in (AgentScope.PROPOSAL, AgentScope.QUOTE):
        return _proposal_context(db, thread, broker_id)
    return _general_context(db, broker_id)


def create_thread(
    db: Session,
    *,
    broker_id: int,
    user: User,
    scope: str,
    entity_id: int | None = None,
    title: str | None = None,
) -> AgentThread:
    """Open a conversation. A proposal/quote scope must name an existing entity.

    Raises ``LookupError`` when the anchor does not exist in this workspace, and
    ``ValueError`` when a scoped thread names no anchor at all.
    """
    agent_scope = AgentScope(scope)
    entity_type = _SCOPE_ENTITY.get(agent_scope)

    if entity_type is not None:
        if entity_id is None:
            raise ValueError(f"scope={scope} requires entity_id")
        anchor = (
            get_proposal(db, entity_id, broker_id)
            if agent_scope == AgentScope.PROPOSAL
            else get_quote_request(db, entity_id, broker_id)
        )
        if anchor is None:
            raise LookupError(f"{agent_scope.value} {entity_id} not found in this workspace")
    else:
        entity_id = None

    thread = AgentThread(
        broker_id=broker_id,
        user_id=user.id,
        scope=agent_scope,
        entity_type=entity_type,
        entity_id=entity_id,
        title=(title or "").strip() or None,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def list_threads(db: Session, *, broker_id: int, user_id: int) -> list[AgentThread]:
    """The caller's own threads in this workspace, newest activity first."""
    return list(
        db.scalars(
            select(AgentThread)
            .where(
                AgentThread.broker_id == broker_id,
                AgentThread.user_id == user_id,
                AgentThread.is_archived.is_(False),
            )
            .order_by(AgentThread.id.desc())
        ).all()
    )


def list_messages(db: Session, thread: AgentThread) -> list[AgentMessage]:
    return list(
        db.scalars(
            select(AgentMessage)
            .where(AgentMessage.thread_id == thread.id)
            .order_by(AgentMessage.id)
        ).all()
    )


def _chat_prompt(
    db: Session, thread: AgentThread, broker_id: int, content: str
) -> list[dict[str, str]]:
    """The full prompt for one turn: system + tenant-scoped context + history."""
    history = list_messages(db, thread)[-MAX_CHAT_HISTORY_MESSAGES:]
    system = _PROPOSAL_SYSTEM if thread.scope != AgentScope.GENERAL else _GENERAL_SYSTEM
    context = build_context(db, thread, broker_id)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "system", "content": context},
    ]
    for message in history:
        if message.role in (AgentRole.USER, AgentRole.ASSISTANT) and message.content:
            messages.append({"role": message.role.value, "content": message.content})
    messages.append({"role": "user", "content": content})
    return messages


def _persist_exchange(
    db: Session,
    *,
    thread: AgentThread,
    content: str,
    answer: str,
    model: str | None,
    tokens: int | None,
) -> tuple[AgentMessage, AgentMessage]:
    """Write BOTH messages. Only ever called once the provider actually answered."""
    user_message = AgentMessage(thread_id=thread.id, role=AgentRole.USER, content=content)
    assistant_message = AgentMessage(
        thread_id=thread.id,
        role=AgentRole.ASSISTANT,
        content=answer,
        model=model,
        tokens=tokens,
    )
    db.add(user_message)
    db.add(assistant_message)

    thread.last_message_at = datetime.now(timezone.utc)
    if not thread.title:
        thread.title = content[:120]

    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message


def send_message(
    db: Session,
    *,
    thread: AgentThread,
    broker_id: int,
    content: str,
) -> tuple[AgentMessage, AgentMessage]:
    """One chat turn. Persists BOTH messages only when the provider answered.

    Nothing is written on a provider failure: a stored user message with no
    reply would be replayed as history on the retry and confuse the next turn.
    """
    messages = _chat_prompt(db, thread, broker_id, content)
    completion = _run_completion(messages, temperature=0.3, max_tokens=1600)
    tokens = (completion.prompt_tokens or 0) + (completion.completion_tokens or 0) or None
    return _persist_exchange(
        db,
        thread=thread,
        content=content,
        answer=completion.content,
        model=completion.model,
        tokens=tokens,
    )


# --- 5. Streaming chat (SSE) --------------------------------------------------
#
# ⚠️ DEPLOYMENT CONSTRAINT (docs/deployment.md, OAC constraint 4): behind
# CloudFront both the Lambda Function URL invoke mode AND the Lambda Web Adapter
# env are `buffered`, so in the deployed environment the whole body is delivered
# in ONE chunk when the handler returns. This endpoint is still correct — and it
# truly streams locally — but it does NOT stream in prod. Do NOT "fix" that by
# switching the invoke mode to RESPONSE_STREAM: that regresses the OAC contract
# (signed requests + x-amz-content-sha256) and cost a full debugging cycle once
# already. The frontend falls back to POST /ai/threads/{id}/messages when the
# first token does not arrive in time.


async def stream_message(
    db: Session,
    *,
    thread: AgentThread,
    broker_id: int,
    content: str,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event, data)`` frames for one streamed turn.

    Frames: ``start`` -> ``token``* -> ``done``, or ``error``. BOTH messages are
    persisted only after the stream completes successfully — identical to
    :func:`send_message`. A mid-stream failure emits an ``error`` frame and
    writes nothing; it must never become an HTTP 500, because by then the 200
    status is already on the wire.
    """
    try:
        messages = _chat_prompt(db, thread, broker_id, content)
        llm = _chat_model(temperature=0.3, max_tokens=1600)
    except AIError as exc:
        yield ("error", {"code": ai_error_code(exc), "detail": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001 - context building must not 500 either
        wrapped = _wrap_provider_error(exc)
        yield ("error", {"code": ai_error_code(wrapped), "detail": str(wrapped)})
        return

    yield ("start", {"thread_id": thread.id, "model": settings.AI_MODEL})

    pieces: list[str] = []
    model_name = settings.AI_MODEL
    tokens: int | None = None
    try:
        async for chunk in llm.astream(messages):
            piece = _message_text(chunk)
            if piece:
                pieces.append(piece)
                yield ("token", {"delta": piece})
            prompt_tokens, completion_tokens = _message_usage(chunk)
            if prompt_tokens or completion_tokens:
                tokens = (prompt_tokens or 0) + (completion_tokens or 0) or None
            model_name = _message_model(chunk) or model_name
    except Exception as exc:  # noqa: BLE001 - an SSE error frame, never a 500
        wrapped = _wrap_provider_error(exc)
        yield ("error", {"code": ai_error_code(wrapped), "detail": str(wrapped)})
        return

    answer = "".join(pieces).strip()
    if not answer:
        yield (
            "error",
            {"code": "ai_provider", "detail": "The AI provider returned an empty message"},
        )
        return

    try:
        user_message, assistant_message = _persist_exchange(
            db,
            thread=thread,
            content=content,
            answer=answer,
            model=model_name,
            tokens=tokens,
        )
    except Exception as exc:  # noqa: BLE001 - a write failure is still an SSE error
        db.rollback()
        logger.exception("Could not persist the streamed exchange")
        yield ("error", {"code": "ai_provider", "detail": f"Could not save the answer: {type(exc).__name__}"})
        return

    yield (
        "done",
        {
            "thread_id": thread.id,
            "message_id": assistant_message.id,
            "user_message_id": user_message.id,
            "tokens": tokens,
            "content": answer,
        },
    )


# --- 6. Summaries (suggest -> edit -> confirmar) ------------------------------

SUMMARY_PROMPT_VERSION = "summary-v1"

_SUMMARY_SYSTEM = """Eres el analista de una corredora de seguros chilena y escribes para el
ASEGURADO, no para un suscriptor.

Reglas:
- Responde SIEMPRE en español de Chile, en prosa clara, sin viñetas salvo que ayuden.
- Usa SOLO los datos del bloque DATOS CONFIRMADOS. Si algo no está, dilo o no lo menciones:
  nunca inventes una prima, una cobertura, un deducible ni un número de póliza.
- Los montos son UF. La prima neta = afecta + exenta; el IVA es 19% SOLO sobre la afecta
  (el sismo es exento); el total = neta + IVA.
- Los deducibles cambian de base según el peligro: incendio suele ser % de la pérdida y
  sismo % del monto asegurado del ítem afectado. Explícalo cuando compares.
- Entre 120 y 200 palabras. Termina con la decisión o la acción que corresponde tomar.
- No prometas nada: el corredor revisa y confirma este texto antes de enviarlo."""


@dataclass
class SummaryResult:
    """AI prose + its audit row. The text is written UNCONFIRMED, always."""

    text: str
    extraction: Extraction
    model: str
    prompt_version: str = SUMMARY_PROMPT_VERSION


def _summary_extraction(
    db: Session,
    *,
    broker_id: int,
    document_id: int,
    case_file_id: int | None,
    category: DocumentCategory | None,
    user: User | None,
) -> Extraction:
    extraction = Extraction(
        broker_id=broker_id,
        document_id=document_id,
        case_file_id=case_file_id,
        category=category,
        kind=ExtractionKind.SUMMARY,
        model=settings.AI_MODEL,
        prompt_version=SUMMARY_PROMPT_VERSION,
        status=ExtractionStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        created_by_id=getattr(user, "id", None),
    )
    db.add(extraction)
    db.flush()
    return extraction


def _run_summary(
    db: Session, *, extraction: Extraction, context: str, ask: str
) -> Completion:
    """Call the model for a summary, marking the extraction failed on any error."""
    try:
        return _chat_complete(
            [
                ("system", _SUMMARY_SYSTEM),
                ("human", f"DATOS CONFIRMADOS\n{context}\n\n{ask}"),
            ],
            temperature=0.2,
            max_tokens=900,
        )
    except AIError as exc:
        extraction.status = ExtractionStatus.FAILED
        extraction.error = f"{type(exc).__name__}: {exc}"
        extraction.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - never a 500
        wrapped = _wrap_provider_error(exc)
        extraction.status = ExtractionStatus.FAILED
        extraction.error = f"{type(wrapped).__name__}: {wrapped}"
        extraction.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise wrapped from exc


def summarize_proposal(
    db: Session,
    *,
    proposal: Proposal,
    broker_id: int,
    user: User | None = None,
) -> SummaryResult:
    """Spanish prose about ONE proposal, from the CONFIRMED structured data.

    Never from raw OCR: the model reads the committed columns, coverages and
    deductibles. The text lands on ``proposal.ai_summary`` UNCONFIRMED — the UI
    shows it as a suggestion with an edit box and a "confirmar" action.

    Configuration is deliberately NOT checked up front: the extraction row is
    written first, so even "the key is unset" leaves an audit row behind.
    """
    quote_request = get_quote_request(db, proposal.quote_request_id, broker_id)
    lines = [
        "PROPUESTA (datos ya confirmados en el sistema)",
        *_proposal_lines(db, [proposal]),
    ]
    if quote_request is not None:
        lines.append(
            f"solicitud de cotización #{quote_request.id} | valor declarado: "
            f"UF {_fmt(quote_request.declared_value_uf)} | vigencia deseada: "
            f"{_fmt(quote_request.desired_start)} -> {_fmt(quote_request.desired_end)}"
        )
        siblings = [p for p in quote_request.proposals if p.id != proposal.id]
        if siblings:
            lines.append(f"ofertas competidoras ({len(siblings)}):")
            lines += _proposal_lines(db, siblings)

    extraction = _summary_extraction(
        db,
        broker_id=broker_id,
        document_id=proposal.source_document_id,
        case_file_id=getattr(proposal, "case_file_id", None),
        category=DocumentCategory.INSURER_QUOTATION,
        user=user,
    )
    completion = _run_summary(
        db,
        extraction=extraction,
        context="\n".join(lines),
        ask=(
            "Resume esta propuesta para el asegurado: qué cubre, qué cuesta, qué deducibles "
            "aplican y qué hay que mirar antes de aceptarla."
        ),
    )

    extraction.model = completion.model
    extraction.prompt_tokens = completion.prompt_tokens
    extraction.completion_tokens = completion.completion_tokens
    extraction.raw_output = {"content": completion.content}
    extraction.parsed = {"summary": completion.content}
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = datetime.now(timezone.utc)

    # SUGGEST: the prose is stored unconfirmed, exactly like a parsed payload.
    proposal.ai_summary = completion.content
    proposal.ai_summary_model = completion.model
    proposal.ai_summary_prompt_version = SUMMARY_PROMPT_VERSION
    proposal.is_summary_confirmed = False

    db.commit()
    db.refresh(extraction)
    return SummaryResult(text=completion.content, extraction=extraction, model=completion.model)


def _case_context_lines(db: Session, case_file: CaseFile, broker_id: int) -> list[str]:
    """Everything confirmed about a case that a summary may legitimately use."""
    lines = [
        f"EXPEDIENTE {case_file.reference or case_file.id} | {case_file.title}",
        f"tipo: {case_file.kind} | etapa: {case_file.stage} | estado: {case_file.status}",
    ]
    client = db.get(Client, case_file.client_id) if case_file.client_id else None
    if client is not None:
        insured = db.get(Insured, client.insured_id)
        if insured is not None:
            lines.append(f"asegurado: {insured.legal_name} ({insured.rut})")
    if case_file.placement_id:
        placement = db.get(Placement, case_file.placement_id)
        if placement is not None:
            lines.append(
                f"colocación #{placement.id} | período: {placement.period or 'n/d'}"
                f" | estado: {placement.status}"
            )
            quotes = db.scalars(
                select(QuoteRequest).where(
                    QuoteRequest.placement_id == placement.id,
                    QuoteRequest.broker_id == broker_id,
                )
            ).all()
            for quote in quotes:
                lines.append(
                    f"cotización #{quote.id} | valor declarado: UF "
                    f"{_fmt(quote.declared_value_uf)} | estado: {quote.status}"
                    f" | ofertas: {len(quote.proposals)}"
                )
                confirmed = [p for p in quote.proposals if p.is_confirmed]
                lines += _proposal_lines(db, confirmed or list(quote.proposals))
    if case_file.summary:
        lines.append(f"resumen actual del expediente: {case_file.summary}")
    return lines


def summarize_case_pack(
    db: Session,
    *,
    case_file: CaseFile,
    kind: "PackKind | str | None" = None,
    broker_id: int,
    user: User | None = None,
    pack: CasePack | None = None,
    document_id: int | None = None,
) -> SummaryResult:
    """Spanish prose for a generated pack (or for the expediente itself).

    Called by ``app.services.packs`` with the pack it is building; the text is
    written UNCONFIRMED onto ``case_pack.summary`` (and onto ``case_file.summary``
    when no pack is given), for a human to edit and confirm.
    """
    kind_value = getattr(kind, "value", kind) or "expediente"
    context = "\n".join(_case_context_lines(db, case_file, broker_id))

    # The pack service calls this with a `kind` but without the row it is
    # building; resolve it so the prose lands on the pack rather than on the
    # case, which is what the caller means.
    if pack is None and kind is not None:
        pack = db.scalars(
            select(CasePack)
            .where(
                CasePack.case_file_id == case_file.id,
                CasePack.broker_id == broker_id,
                CasePack.kind == kind,
            )
            .order_by(CasePack.id.desc())
            .limit(1)
        ).first()

    source_document_id = document_id
    if source_document_id is None and pack is not None:
        source_document_id = pack.pdf_document_id
    if source_document_id is None:
        source_document_id = db.scalar(
            select(Document.id)
            .where(Document.broker_id == broker_id, Document.case_file_id == case_file.id)
            .order_by(Document.id)
            .limit(1)
        )
    if source_document_id is None:
        # An extraction row without a source document is not evidence, and the
        # column is NOT NULL by design.
        raise DocumentUnavailable(
            f"Case file {case_file.id} has no document to anchor the summary to"
        )

    extraction = _summary_extraction(
        db,
        broker_id=broker_id,
        document_id=source_document_id,
        case_file_id=case_file.id,
        category=None,
        user=user,
    )
    completion = _run_summary(
        db,
        extraction=extraction,
        context=context,
        ask=(
            f"Redacta el resumen ejecutivo de la carpeta «{kind_value}» de este expediente "
            "para el destinatario que corresponde (compañías si es de remisión, asegurado si "
            "es comparativa o propuesta)."
        ),
    )

    extraction.model = completion.model
    extraction.prompt_tokens = completion.prompt_tokens
    extraction.completion_tokens = completion.completion_tokens
    extraction.raw_output = {"content": completion.content}
    extraction.parsed = {"summary": completion.content, "pack_kind": kind_value}
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = datetime.now(timezone.utc)

    if pack is not None:
        pack.summary = completion.content
        pack.summary_model = completion.model
        pack.summary_prompt_version = SUMMARY_PROMPT_VERSION
        pack.is_summary_confirmed = False
    else:
        case_file.summary = completion.content

    db.commit()
    db.refresh(extraction)
    return SummaryResult(text=completion.content, extraction=extraction, model=completion.model)


# --- 7. Confirm a registry payload -------------------------------------------


def spec_for_category(category: "DocumentCategory | str | None") -> "CategorySpec":
    """Registry lookup that speaks the :class:`AIError` family (-> 422, never 500)."""
    from app.schemas.extraction.registry import UnknownCategory, spec_for

    try:
        return spec_for(category)
    except UnknownCategory as exc:
        raise UnsupportedCategory(str(exc)) from exc


def legacy_proposal_suggestion(
    payload: Any, parsed: dict[str, Any] | None = None
) -> tuple[ProposalSuggestion, list[str]]:
    """Public alias: project an insurer-quotation payload onto ``ProposalSuggestion``."""
    return _legacy_proposal_suggestion(payload, parsed or {})


def validate_category_payload(
    category: "DocumentCategory | str | None", payload: dict[str, Any]
) -> tuple[Any, list[str]]:
    """Validate a human-reviewed payload against its registry schema.

    Returns ``(model, warnings)``. Raises :class:`UnsupportedCategory` when the
    category has no schema — the caller turns that into a 422.
    """
    from app.schemas.extraction.registry import UnknownCategory, spec_for

    try:
        spec = spec_for(category)
    except UnknownCategory as exc:
        raise UnsupportedCategory(str(exc)) from exc
    model, warnings = _coerce_payload(spec.schema, dict(payload or {}))
    if model is None:
        raise AIParseError(f"The payload does not match {spec.schema.__name__}")
    return model, warnings


def record_confirmation(
    db: Session,
    *,
    extraction: Extraction,
    payload: Any,
    user: User | None,
    target: str,
    applied: bool,
) -> Extraction:
    """Store the human-reviewed payload back onto its extraction row.

    The reviewed payload — not what the model first said — becomes
    ``extraction.parsed``, and the confirmation itself is stamped into the audit
    blob. Committing it to the target entity is the owning router's job; this
    only records that a human signed off on these values.
    """
    extraction.parsed = (
        payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload)
    )
    audit = dict(extraction.raw_output or {})
    audit["confirmation"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "by_user_id": getattr(user, "id", None),
        "target": target,
        "applied": applied,
    }
    extraction.raw_output = audit
    db.commit()
    db.refresh(extraction)
    return extraction


# --- 8. The agentic loop (v4 agent spec §5–§7) --------------------------------
#
# READ tools execute freely inside the loop under the CALLER's own grants;
# WRITE tools never execute here — they persist an ``agent_action`` row in
# status ``proposed`` and only :func:`confirm_agent_action` (driven by the
# confirm endpoint, under the confirming user's real RBAC gate) runs the
# executor. CLAUDE.md rule 6, mechanically enforced: the single call site of a
# write spec's ``execute`` is the confirm path.

MAX_TOOL_ITERATIONS = 6       # provider round-trips that returned tool_calls
MAX_TOOL_CALLS_PER_TURN = 12  # absolute cap across iterations
MAX_CONTEXT_REFS = 6
MAX_AGENT_CONTEXT_CHARS = 24_000
# Per-ref context budgets (chars): trees and cases carry more, trimmed first.
_REF_BUDGET_LARGE = 2_000
_REF_BUDGET_SMALL = 1_200
_TRUNCATION_MARKER = "… (contexto truncado)"

_AGENT_SYSTEM = """Eres el asistente operativo de una corredora de seguros chilena dentro de Radal.

HERRAMIENTAS
- Puedes CONSULTAR datos libremente con las herramientas de lectura. Cita lo que leas.
- Las herramientas que escriben NUNCA ejecutan nada: solo PROPONEN una acción que la
  persona debe confirmar o descartar en el chat. Dilo así en tu respuesta cuando propongas algo.
- Si una herramienta responde permission_denied o not_found, explícalo con naturalidad;
  no insistas con la misma llamada.

DISCIPLINA DE DATOS
- Los montos son UF. Prima neta = afecta + exenta; IVA = 0,19 × la parte AFECTA
  (el sismo es exento); total = neta + IVA. NUNCA inventes una cifra, una prima,
  un deducible ni un número de póliza: usa solo lo que las herramientas o el
  contexto entregan.
- Refiérete a las entidades por su etiqueta visible (por ejemplo EXP-2026-0004,
  el nombre del cliente), nunca por ids crudos.

ESTILO
- Responde SIEMPRE en español de Chile, breve y concreto.
- Si el contexto no alcanza para responder, di qué falta y qué consultarías."""

_AGENT_NUDGE = (
    "Responde ahora con lo que tienes; no hay más herramientas en este turno."
)


class UnsupportedContextRef(Exception):
    """A context_ref names an entity type the agent does not resolve. -> 422."""

    def __init__(self, entity_type: str):
        super().__init__(f"Unsupported context ref entity_type '{entity_type}'")
        self.entity_type = entity_type


class ContextRefNotFound(Exception):
    """A context_ref's target is absent or belongs to another tenant. -> 404."""

    def __init__(self, entity_type: str, entity_id: int):
        super().__init__(f"{entity_type} {entity_id} not found")
        self.entity_type = entity_type
        self.entity_id = entity_id


# --- context_refs resolution (spec §6) ----------------------------------------


def _ref_block(label: str, payload: Any, budget: int) -> str:
    body = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, default=str
    )
    text = f"{label}\n{body}"
    if len(text) > budget:
        text = text[: budget - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    return text


def _resolve_one_ref(
    db: Session, *, broker_id: int, user: User, entity_type: str, entity_id: int
) -> str:
    """One ref -> one compact, Spanish, id-bearing context block.

    Every lookup goes through the entity's own broker-scoped helper, so the
    caller's ``partial`` narrowings hold and a foreign row is indistinguishable
    from an absent one.
    """
    from fastapi import HTTPException

    from app.services import agent_tools

    def _not_found() -> ContextRefNotFound:
        return ContextRefNotFound(entity_type, entity_id)

    if entity_type == "account_group":
        try:
            payload = agent_tools._exec_get_group_tree(
                db, broker_id=broker_id, user=user,
                args=agent_tools.GetGroupTreeArgs(group_id=entity_id),
            )
        except HTTPException as exc:
            raise _not_found() from exc
        return _ref_block(
            f"CONTEXTO · grupo #{entity_id}", payload, _REF_BUDGET_LARGE
        )

    if entity_type == "case_file":
        try:
            payload = agent_tools._exec_get_case_file(
                db, broker_id=broker_id, user=user,
                args=agent_tools.GetCaseFileArgs(case_file_id=entity_id),
            )
        except HTTPException as exc:
            raise _not_found() from exc
        # An account/renewal folder automatically appends the immediate prior
        # folder's summary — "compara contra la vigencia anterior" works
        # without the user hunting for ids.
        if payload.get("kind") in ("account", "renewal"):
            try:
                history = agent_tools._exec_get_case_history(
                    db, broker_id=broker_id, user=user,
                    args=agent_tools.GetCaseHistoryArgs(case_file_id=entity_id),
                )
                prior = history.get("prior")
                if prior:
                    payload["vigencia_anterior"] = prior
            except HTTPException:  # pragma: no cover - history is best-effort
                pass
        return _ref_block(
            f"CONTEXTO · expediente #{entity_id}", payload, _REF_BUDGET_LARGE
        )

    if entity_type == "client":
        client = db.scalars(
            select(Client).where(Client.id == entity_id, Client.broker_id == broker_id)
        ).first()
        if client is None:
            raise _not_found()
        insured = db.get(Insured, client.insured_id)
        from app.models.case_file import CaseFile as _CaseFile
        from app.models.policy import Policy as _Policy

        open_cases = db.scalars(
            select(_CaseFile.id).where(
                _CaseFile.broker_id == broker_id,
                _CaseFile.client_id == client.id,
                _CaseFile.status == "open",
            )
        ).all()
        policy_count = db.scalar(
            select(func.count(_Policy.id)).where(
                _Policy.broker_id == broker_id, _Policy.client_id == client.id
            )
        )
        payload = {
            "id": client.id,
            "legal_name": insured.legal_name if insured else None,
            "trade_name": insured.trade_name if insured else None,
            "rut": insured.rut if insured else None,
            "status": str(client.status),
            "open_case_ids": list(open_cases),
            "policy_count": int(policy_count or 0),
        }
        return _ref_block(f"CONTEXTO · cliente #{entity_id}", payload, _REF_BUDGET_SMALL)

    if entity_type == "policy":
        from app.models.case_file import CaseFile as _CaseFile
        from app.models.policy import Policy as _Policy

        policy = db.scalars(
            select(_Policy).where(_Policy.id == entity_id, _Policy.broker_id == broker_id)
        ).first()
        if policy is None:
            raise _not_found()
        insurer = db.get(Insurer, policy.insurer_id)
        children = db.scalars(
            select(_CaseFile).where(
                _CaseFile.broker_id == broker_id, _CaseFile.policy_id == policy.id
            )
        ).all()
        payload = {
            "id": policy.id,
            "policy_number": policy.policy_number,
            "insurer": insurer.legal_name if insurer else None,
            "start_date": policy.start_date,
            "end_date": policy.end_date,
            "status": str(policy.status),
            "insured_amount_uf": _fmt(policy.insured_amount_uf),
            "net_premium_uf": _fmt(policy.net_premium_uf),
            "vat_uf": _fmt(policy.vat_uf),
            "total_premium_uf": _fmt(policy.total_premium_uf),
            "children": [
                {"id": c.id, "reference": c.reference, "kind": str(c.kind),
                 "stage": str(c.stage)}
                for c in children
            ],
        }
        return _ref_block(f"CONTEXTO · póliza #{entity_id}", payload, _REF_BUDGET_SMALL)

    if entity_type == "quote_request":
        quote = get_quote_request(db, entity_id, broker_id)
        if quote is None:
            raise _not_found()
        lines = [
            f"cotización #{quote.id} | ronda {quote.round_no} | estado: {quote.status}"
            f" | valor declarado: UF {_fmt(quote.declared_value_uf)}",
        ]
        for item in quote.line_items:
            lines.append(f"- partida {item.name}: UF {_fmt(item.value_uf)}")
        lines.append(f"ofertas recibidas ({len(quote.proposals)}):")
        lines += _proposal_lines(db, quote.proposals)
        return _ref_block(
            f"CONTEXTO · cotización #{entity_id}", "\n".join(lines), _REF_BUDGET_SMALL
        )

    if entity_type == "proposal":
        proposal = get_proposal(db, entity_id, broker_id)
        if proposal is None:
            raise _not_found()
        return _ref_block(
            f"CONTEXTO · propuesta #{entity_id}",
            "\n".join(_proposal_lines(db, [proposal])),
            _REF_BUDGET_SMALL,
        )

    if entity_type == "document":
        document = get_document(db, entity_id, broker_id)
        if document is None:
            raise _not_found()
        payload: dict[str, Any] = {
            "id": document.id,
            "category": str(document.category) if document.category else None,
            "section": str(document.section) if document.section else None,
            "document_code": document.document_code,
            "original_name": document.original_name,
            "case_file_id": document.case_file_id,
        }
        # The confirmed extraction's parsed payload, when one exists — never
        # raw bytes and never an inline LLM read (that is SuggestionForm's job).
        extraction = db.scalars(
            select(Extraction)
            .where(
                Extraction.document_id == document.id,
                Extraction.broker_id == broker_id,
                Extraction.status == ExtractionStatus.SUCCEEDED,
            )
            .order_by(Extraction.id.desc())
        ).first()
        if extraction is not None and extraction.parsed:
            confirmed = bool((extraction.raw_output or {}).get("confirmation"))
            payload["extraction"] = {
                "confirmed": confirmed,
                "parsed": extraction.parsed,
            }
        return _ref_block(f"CONTEXTO · documento #{entity_id}", payload, _REF_BUDGET_SMALL)

    if entity_type == "sales_lead":
        from app.models.sales_lead import SalesLead

        lead = db.scalars(
            select(SalesLead).where(
                SalesLead.id == entity_id, SalesLead.broker_id == broker_id
            )
        ).first()
        if lead is None:
            raise _not_found()
        payload = {
            "id": lead.id,
            "name": lead.name,
            "status": str(lead.status),
            "insurance_line_id": lead.insurance_line_id,
            "estimated_premium_uf": _fmt(lead.estimated_premium_uf),
            "follow_up_on": lead.follow_up_on,
        }
        return _ref_block(f"CONTEXTO · lead #{entity_id}", payload, _REF_BUDGET_SMALL)

    raise UnsupportedContextRef(entity_type)


def resolve_context_refs(
    db: Session,
    *,
    broker_id: int,
    user: User,
    refs: Sequence[dict[str, Any]],
) -> list[str]:
    """Resolve every ref fresh (stored refs are provenance, never a cache)."""
    if len(refs) > MAX_CONTEXT_REFS:
        raise UnsupportedContextRef(f"more than {MAX_CONTEXT_REFS} refs")
    blocks: list[str] = []
    for ref in refs:
        entity_type = str(ref.get("entity_type") or "")
        entity_id = int(ref.get("entity_id") or 0)
        blocks.append(
            _resolve_one_ref(
                db, broker_id=broker_id, user=user,
                entity_type=entity_type, entity_id=entity_id,
            )
        )
    return blocks


# --- the loop ----------------------------------------------------------------


@dataclass
class AgentTurnResult:
    """Everything one agent turn persisted, in order."""

    thread: AgentThread
    messages: list[AgentMessage]
    actions: list[AgentAction]


def _agent_prompt(
    db: Session,
    thread: AgentThread,
    ref_blocks: list[str],
    content: str,
) -> list[Any]:
    """System + refs + trimmed history + the new user content.

    Tool messages from prior turns are NOT replayed — the prose answers already
    summarise them. Refs are trimmed first, oldest history second.
    """
    history = [
        message
        for message in list_messages(db, thread)
        if message.role in (AgentRole.USER, AgentRole.ASSISTANT) and message.content
    ][-MAX_CHAT_HISTORY_MESSAGES:]

    budget = MAX_AGENT_CONTEXT_CHARS - len(_AGENT_SYSTEM) - len(content)
    blocks = list(ref_blocks)
    while blocks and sum(len(b) for b in blocks) > budget // 2:
        blocks[-1] = _ref_block("CONTEXTO", _TRUNCATION_MARKER, len(_TRUNCATION_MARKER) + 12)
        if sum(len(b) for b in blocks) > budget // 2:
            blocks.pop()
    remaining = budget - sum(len(b) for b in blocks)
    trimmed_history: list[AgentMessage] = []
    used = 0
    for message in reversed(history):
        used += len(message.content or "")
        if used > remaining:
            break
        trimmed_history.append(message)
    trimmed_history.reverse()

    messages: list[Any] = [{"role": "system", "content": _AGENT_SYSTEM}]
    for block in blocks:
        messages.append({"role": "system", "content": block})
    for message in trimmed_history:
        messages.append({"role": message.role.value, "content": message.content})
    messages.append({"role": "user", "content": content})
    return messages


def _serialize_tool_calls(answer: Any) -> list[dict[str, Any]]:
    """The provider's tool_calls, verbatim where available (the audit trail)."""
    extra = getattr(answer, "additional_kwargs", None) or {}
    raw = extra.get("tool_calls") if isinstance(extra, dict) else None
    if raw:
        return [dict(call) for call in raw]
    serialized = []
    for call in getattr(answer, "tool_calls", None) or []:
        serialized.append(
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": call.get("name"),
                    "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False, default=str),
                },
            }
        )
    return serialized


def _invoke_agent(llm: Any, messages: list[Any], tools: list[dict] | None) -> Any:
    """One provider call, with the DeepInfra tools-param fallback.

    If the provider rejects the ``tools`` param (400/422), retry the same
    messages once WITHOUT tools and answer plainly — a degraded answer beats a
    dead endpoint.
    """
    target = llm.bind_tools(tools) if tools else llm
    try:
        return target.invoke(messages)
    except Exception as exc:  # noqa: BLE001 - typed AIError or the tools fallback
        status_code = getattr(exc, "status_code", None)
        if tools and status_code in (400, 422):
            logger.warning("AI provider rejected the tools param; retrying without tools")
            try:
                return llm.invoke(messages)
            except Exception as exc2:  # noqa: BLE001
                raise _wrap_provider_error(exc2) from exc2
        raise _wrap_provider_error(exc) from exc


def _fold_http_exception(exc: Any) -> dict[str, Any]:
    """An HTTPException from a reused router helper, folded for the model."""
    status_code = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if status_code == 404:
        return {"error": "not_found", "detail": detail}
    if status_code == 403:
        return {"error": "permission_denied", "detail": detail}
    return {"error": "invalid_arguments", "detail": detail}


def _dispatch_tool_call(
    db: Session,
    call: dict[str, Any],
    *,
    thread: AgentThread,
    broker_id: int,
    user: User,
    pending: list[AgentAction],
) -> tuple[dict[str, Any], str]:
    """Execute (read) or propose (write) one tool call. Never raises.

    Returns ``(folded_result, status)`` where status is
    ``ok | error | pending_confirmation``. A missing grant, a foreign id, bad
    arguments and even a tool bug all FOLD BACK to the model instead of failing
    the turn (spec §4.1) — a tool bug is not a 500 either.
    """
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.services import agent_tools

    name = str(call.get("name") or "")
    spec = agent_tools.TOOL_REGISTRY.get(name)
    if spec is None:
        return {"error": "unknown_tool", "tool": name}, "error"

    try:
        args = spec.args_schema.model_validate(call.get("args") or {})
    except ValidationError as exc:
        return (
            {
                "error": "invalid_arguments",
                "errors": [
                    {"loc": ".".join(str(p) for p in e.get("loc") or ()), "msg": e.get("msg")}
                    for e in exc.errors()
                ],
            },
            "error",
        )

    try:
        gates = agent_tools.gates_for(spec, args)
    except ValueError as exc:
        return {"error": "invalid_arguments", "detail": str(exc)}, "error"

    for module, action in gates:
        if not user_has_permission_lazy(user, module, action):
            return agent_tools.permission_denied_payload(module, action), "error"

    if spec.kind == "read":
        nested = db.begin_nested()
        try:
            result = spec.execute(db, broker_id=broker_id, user=user, args=args)
            nested.commit()
            return result, "ok"
        except HTTPException as exc:
            nested.rollback()
            return _fold_http_exception(exc), "error"
        except Exception as exc:  # noqa: BLE001 - a tool bug folds, never 500s
            nested.rollback()
            logger.exception("Agent read tool %s failed", name)
            return {"error": "tool_failed", "detail": type(exc).__name__}, "error"

    # WRITE: validate + broker-scoped existence checks, then PROPOSE. The
    # executor runs exclusively inside the confirm endpoint.
    if spec.validate_proposal is not None:
        nested = db.begin_nested()
        try:
            spec.validate_proposal(db, broker_id=broker_id, user=user, args=args)
            nested.commit()
        except HTTPException as exc:
            nested.rollback()
            return _fold_http_exception(exc), "error"
        except Exception as exc:  # noqa: BLE001
            nested.rollback()
            logger.exception("Agent write tool %s proposal validation failed", name)
            return {"error": "tool_failed", "detail": type(exc).__name__}, "error"

    try:
        summary = spec.summarize(db, broker_id=broker_id, args=args) if spec.summarize else None
    except Exception:  # noqa: BLE001 - a summary bug must not block the proposal
        logger.exception("Agent tool %s summarize failed", name)
        summary = None

    module, action = gates[0]
    row = AgentAction(
        broker_id=broker_id,
        thread_id=thread.id,
        tool=name,
        arguments=args.model_dump(mode="json"),
        summary=summary,
        module=module,
        action=action,
        status=AgentActionStatus.PROPOSED,
        proposed_by_id=user.id,
    )
    db.add(row)
    db.flush()  # the id the model refers to; rolled back if the turn fails
    pending.append(row)
    return (
        {"status": "pending_confirmation", "action_id": row.id, "summary": summary},
        "pending_confirmation",
    )


def user_has_permission_lazy(user: User, module: str, action: str) -> bool:
    """Local import so this module keeps its import graph clean."""
    from app.core.permissions import user_has_permission

    return user_has_permission(user, module, action)


def run_agent_turn(
    db: Session,
    *,
    thread: AgentThread,
    broker_id: int,
    user: User,
    content: str,
    context_refs: Sequence[dict[str, Any]] | None = None,
) -> AgentTurnResult:
    """One agentic turn: model loop + tool dispatch + atomic persistence.

    A mid-loop provider failure persists NOTHING (same contract as
    :func:`send_message`); pending actions flushed during the loop are
    discarded by the rollback. The turn always ends in prose.
    """
    from app.services import agent_tools

    refs = list(context_refs or [])
    ref_blocks = resolve_context_refs(db, broker_id=broker_id, user=user, refs=refs)

    llm = _chat_model(temperature=0.2, max_tokens=1600)
    tools = agent_tools.openai_tools()
    messages = _agent_prompt(db, thread, ref_blocks, content)

    pending: list[AgentAction] = []
    # One record per iteration that carried tool calls:
    # (serialized provider tool_calls, [(call, folded_json, status), ...])
    iterations: list[tuple[list[dict[str, Any]], list[tuple[dict, str, str]]]] = []
    calls_used = 0
    answer: Any = None
    prompt_tokens_total = 0
    completion_tokens_total = 0
    model_name = settings.AI_MODEL

    try:
        for iteration in range(MAX_TOOL_ITERATIONS + 1):
            with_tools = iteration < MAX_TOOL_ITERATIONS
            if not with_tools:
                # Budget exhausted and the model still wants tools: one final
                # call, nudged, with NO tools bound (spec §5.3).
                messages.append({"role": "system", "content": _AGENT_NUDGE})
            answer = _invoke_agent(llm, messages, tools if with_tools else None)

            in_tokens, out_tokens = _message_usage(answer)
            prompt_tokens_total += in_tokens or 0
            completion_tokens_total += out_tokens or 0
            model_name = _message_model(answer) or model_name

            calls = list(getattr(answer, "tool_calls", None) or [])
            if not calls or not with_tools:
                break

            serialized = _serialize_tool_calls(answer)
            call_records: list[tuple[dict, str, str]] = []
            messages.append(answer)  # the assistant message, tool_calls and all
            for call in calls:
                if calls_used >= MAX_TOOL_CALLS_PER_TURN:
                    result: dict[str, Any] = {
                        "error": "tool_budget_exhausted",
                        "detail": (
                            f"Se alcanzó el máximo de {MAX_TOOL_CALLS_PER_TURN} "
                            "llamadas por turno"
                        ),
                    }
                    call_status = "error"
                else:
                    calls_used += 1
                    result, call_status = _dispatch_tool_call(
                        db, call, thread=thread, broker_id=broker_id,
                        user=user, pending=pending,
                    )
                spec = agent_tools.TOOL_REGISTRY.get(str(call.get("name") or ""))
                limit = spec.max_result_chars if spec else agent_tools.DEFAULT_MAX_RESULT_CHARS
                folded = json.dumps(result, ensure_ascii=False, default=str)
                if len(folded) > limit:
                    folded = folded[: limit - 3] + '..."'
                call_records.append((call, folded, call_status))
                messages.append(
                    {
                        "role": "tool",
                        "content": folded,
                        "tool_call_id": str(call.get("id") or ""),
                    }
                )
            iterations.append((serialized, call_records))
    except AIError:
        db.rollback()  # discards any flushed pending actions — nothing persists
        raise
    except Exception as exc:  # noqa: BLE001 - never a 500
        db.rollback()
        raise _wrap_provider_error(exc) from exc

    final_text = _message_text(answer).strip()
    if not final_text:
        db.rollback()
        raise AIProviderError("The AI provider returned an empty message")

    tokens_total = (prompt_tokens_total + completion_tokens_total) or None
    persisted = _persist_agent_turn(
        db,
        thread=thread,
        content=content,
        context_refs=refs,
        iterations=iterations,
        final_text=final_text,
        model_name=model_name,
        tokens=tokens_total,
        pending=pending,
    )
    return AgentTurnResult(thread=thread, messages=persisted, actions=pending)


def _persist_agent_turn(
    db: Session,
    *,
    thread: AgentThread,
    content: str,
    context_refs: list[dict[str, Any]],
    iterations: list[tuple[list[dict[str, Any]], list[tuple[dict, str, str]]]],
    final_text: str,
    model_name: str | None,
    tokens: int | None,
    pending: list[AgentAction],
) -> list[AgentMessage]:
    """Write the whole turn in ONE commit, in transcript order."""
    persisted: list[AgentMessage] = []

    def _add(message: AgentMessage) -> AgentMessage:
        db.add(message)
        db.flush()
        persisted.append(message)
        return message

    _add(
        AgentMessage(
            thread_id=thread.id,
            role=AgentRole.USER,
            content=content,
            context_refs=context_refs or None,
        )
    )

    # Map each pending action to the assistant message that proposed it: match
    # by the action_id folded into the tool result of the same iteration.
    action_by_id = {action.id: action for action in pending}
    for serialized_calls, call_records in iterations:
        assistant = _add(
            AgentMessage(
                thread_id=thread.id,
                role=AgentRole.ASSISTANT,
                content=None,
                tool_calls=serialized_calls,
            )
        )
        for call, folded, call_status in call_records:
            _add(
                AgentMessage(
                    thread_id=thread.id,
                    role=AgentRole.TOOL,
                    content=folded,
                    tool_calls={
                        "tool_call_id": call.get("id"),
                        "name": call.get("name"),
                        "status": call_status,
                    },
                )
            )
            if call_status == "pending_confirmation":
                try:
                    action_id = json.loads(folded).get("action_id")
                except Exception:  # noqa: BLE001 - defensive; folded is our JSON
                    action_id = None
                action = action_by_id.get(action_id)
                if action is not None:
                    action.message_id = assistant.id

    _add(
        AgentMessage(
            thread_id=thread.id,
            role=AgentRole.ASSISTANT,
            content=final_text,
            model=model_name,
            tokens=tokens,
        )
    )

    thread.last_message_at = datetime.now(timezone.utc)
    if not thread.title:
        thread.title = content[:120]

    db.commit()
    for message in persisted:
        db.refresh(message)
    for action in pending:
        db.refresh(action)
    return persisted


# --- pending-action lifecycle (spec §7.3–§7.4) --------------------------------


def get_agent_action(db: Session, action_id: int, broker_id: int) -> AgentAction | None:
    return db.scalars(
        select(AgentAction).where(
            AgentAction.id == action_id, AgentAction.broker_id == broker_id
        )
    ).first()


def list_agent_actions(
    db: Session,
    *,
    broker_id: int,
    user_id: int,
    thread_id: int | None = None,
    statuses: Sequence[str] | None = None,
) -> list[AgentAction]:
    """Scoped to the caller's broker AND to threads the caller owns."""
    stmt = (
        select(AgentAction)
        .join(AgentThread, AgentThread.id == AgentAction.thread_id)
        .where(
            AgentAction.broker_id == broker_id,
            AgentThread.user_id == user_id,
        )
    )
    if thread_id is not None:
        stmt = stmt.where(AgentAction.thread_id == thread_id)
    if statuses:
        stmt = stmt.where(AgentAction.status.in_(list(statuses)))
    return list(db.scalars(stmt.order_by(AgentAction.id)).all())


def _append_action_note(
    db: Session, *, thread: AgentThread, action: AgentAction, payload: dict[str, Any]
) -> None:
    """The role=tool transcript note, so the model sees the outcome next turn."""
    db.add(
        AgentMessage(
            thread_id=thread.id,
            role=AgentRole.TOOL,
            content=json.dumps(payload, ensure_ascii=False, default=str),
            tool_calls={
                "action_id": action.id,
                "name": action.tool,
                "status": action.status.value,
            },
        )
    )
    thread.last_message_at = datetime.now(timezone.utc)


def confirm_agent_action(
    db: Session,
    *,
    action: AgentAction,
    thread: AgentThread,
    broker_id: int,
    user: User,
) -> AgentAction:
    """The COMMIT of rule 6. The caller (router) has already loaded the row
    broker-scoped, checked thread ownership and the ``proposed`` status.

    Order (spec §7.3): re-check the REAL gate against the registry -> re-validate
    arguments -> execute the same transaction the normal router runs (its own
    404/422/409 details surface verbatim and leave the row ``proposed``) ->
    stamp the row + provenance activity + transcript note. An executor crash
    after the checks lands the row ``failed`` in a separate small transaction.
    """
    from fastapi import HTTPException, status as http_status
    from pydantic import ValidationError

    from app.api.routers.clients import record_activity
    from app.services import agent_tools

    spec = agent_tools.TOOL_REGISTRY.get(action.tool)
    if spec is None or spec.kind != "write":
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tool '{action.tool}' is not a confirmable write tool",
        )

    try:
        args = spec.args_schema.model_validate(action.arguments or {})
    except ValidationError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The stored arguments no longer validate: {exc.error_count()} errors",
        ) from exc

    # The registry is re-consulted NOW — a later matrix change cannot be
    # bypassed by an old row. Missing grant -> 403 and the row STAYS proposed.
    try:
        gates = agent_tools.gates_for(spec, args)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    for module, act in gates:
        if not user_has_permission_lazy(user, module, act):
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"Not permitted to {act} on {module}",
            )

    try:
        result = spec.execute(db, broker_id=broker_id, user=user, args=args)
    except HTTPException:
        # The executor's own domain refusal (stale target 404, rule 422, state
        # 409) BEFORE anything was written: surface it verbatim; row stays
        # proposed so a fixed world can still confirm it.
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - crash after the checks -> failed
        db.rollback()
        logger.exception("Agent action %s executor crashed", action.id)
        row = db.get(AgentAction, action.id)
        if row is not None:
            row.status = AgentActionStatus.FAILED
            row.error = f"{type(exc).__name__}: {exc}"
            row.confirmed_by_id = user.id
            row.confirmed_at = datetime.now(timezone.utc)
            db.commit()
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=f"The action executor failed: {type(exc).__name__}",
        ) from exc

    action.status = AgentActionStatus.CONFIRMED
    action.result = result
    action.confirmed_by_id = user.id
    action.confirmed_at = datetime.now(timezone.utc)

    try:
        entity_type = EntityType(str(result.get("entity_type")))
        entity_id = result.get("entity_id")
    except ValueError:  # pragma: no cover - executors emit valid EntityType values
        entity_type, entity_id = agent_tools.action_anchor(action.tool, action.arguments)
    record_activity(
        db,
        broker_id=broker_id,
        user=user,
        action="agent.action_confirmed",
        entity_type=entity_type,
        entity_id=entity_id,
        description=action.summary,
        meta={"action_id": action.id, "tool": action.tool, "thread_id": action.thread_id},
    )
    _append_action_note(
        db, thread=thread, action=action,
        payload={"status": "confirmed", "action_id": action.id, **(result or {})},
    )
    db.commit()
    db.refresh(action)
    return action


def discard_agent_action(
    db: Session,
    *,
    action: AgentAction,
    thread: AgentThread,
    broker_id: int,
    user: User,
) -> AgentAction:
    """Declining requires no privilege beyond owning the thread (spec §7.4)."""
    from app.api.routers.clients import record_activity
    from app.services import agent_tools

    action.status = AgentActionStatus.DISCARDED
    action.confirmed_by_id = user.id
    action.confirmed_at = datetime.now(timezone.utc)

    entity_type, entity_id = agent_tools.action_anchor(action.tool, action.arguments)
    record_activity(
        db,
        broker_id=broker_id,
        user=user,
        action="agent.action_discarded",
        entity_type=entity_type,
        entity_id=entity_id,
        description=action.summary,
        meta={"action_id": action.id, "tool": action.tool, "thread_id": action.thread_id},
    )
    _append_action_note(
        db, thread=thread, action=action,
        payload={"status": "discarded", "action_id": action.id},
    )
    db.commit()
    db.refresh(action)
    return action
