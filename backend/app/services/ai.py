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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.models.ai import (
    AgentMessage,
    AgentRole,
    AgentScope,
    AgentThread,
    Extraction,
    ExtractionKind,
    ExtractionStatus,
)
from app.models.client import Client
from app.models.document import Document
from app.models.enums import CoverageKind, EntityType
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.placement import Placement
from app.models.proposal import Proposal, ProposalCoverage, ProposalOrigin, ProposalStatus
from app.models.quote import QuoteRequest
from app.models.user import User
from app.schemas.ai import ProposalSuggestion
from app.services.identifiers import (
    InvalidCmf,
    InvalidRut,
    normalize_codigo_cmf,
    validate_rut,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AIError",
    "AINotConfigured",
    "AIProviderError",
    "AITimeout",
    "AIParseError",
    "DocumentUnavailable",
    "MoneyInconsistent",
    "PROMPT_VERSION",
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
    max_tokens: int = 4000,
) -> Completion:
    """Call the provider once and normalise the answer.

    Every provider exception is translated into a typed :class:`AIError`, so no
    caller ever has to know what an ``APIStatusError`` is — and no endpoint can
    500 because the model hiccuped.
    """
    client = _ai_client()
    model = settings.AI_MODEL

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
    content = (choices[0].message.content or "").strip()
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


def load_document_text(document: Document) -> str:
    """Return the document's plain text, truncated to the provider's budget.

    A scanned (image-only) PDF yields (almost) nothing — that is surfaced as
    :class:`DocumentUnavailable` rather than sent to the model as empty context.
    """
    data = _document_bytes(document)
    mime = (document.mime_type or "").lower()
    name = (document.original_name or "").lower()

    if mime == "application/pdf" or name.endswith(".pdf"):
        text = _pdf_to_text(data)
    elif mime in _TEXT_MIMES or name.endswith((".txt", ".md", ".json", ".csv", ".xml", ".html")):
        text = data.decode("utf-8", errors="replace")
    else:
        # Unknown type: best-effort decode. Binary noise is rejected below.
        text = data.decode("utf-8", errors="replace")

    text = re.sub(r"[ \t]+", " ", text)
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


def _extract_json_object(content: str) -> dict[str, Any]:
    """Parse the model's answer into a dict, tolerating fences and stray prose."""
    cleaned = _FENCE_RE.sub("", content.strip())
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


def extract_proposal(
    db: Session,
    *,
    document_id: int,
    broker_id: int,
    user: User | None = None,
) -> ExtractionResult:
    """Read a document and SUGGEST a proposal. Writes an ``extraction``, never a proposal.

    The extraction row is persisted in every outcome — success, provider failure
    and unparsable answer alike — because the audit trail of "we asked and this
    is what came back" is exactly what makes the confirm step trustworthy.

    Raises :class:`DocumentUnavailable` (404/422 at the router) before any row is
    written, and :class:`AIError` subclasses after the row exists.
    """
    document = get_document(db, document_id, broker_id)
    if document is None:
        raise DocumentUnavailable(f"Document {document_id} not found in this workspace")

    # Fail before creating a row if there is nothing to read.
    text = load_document_text(document)

    extraction = Extraction(
        broker_id=broker_id,
        document_id=document.id,
        kind=ExtractionKind.PROPOSAL,
        model=settings.AI_MODEL,
        prompt_version=PROMPT_VERSION,
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
        completion = _run_completion(
            _build_extraction_messages(document, text), json_mode=True
        )
    except AIError as exc:
        _fail(exc)
        raise

    extraction.model = completion.model
    extraction.prompt_tokens = completion.prompt_tokens
    extraction.completion_tokens = completion.completion_tokens
    extraction.raw_output = completion.raw

    try:
        parsed = _extract_json_object(completion.content)
    except AIParseError as exc:
        extraction.raw_output = {"content": completion.content, "response": completion.raw}
        _fail(exc)
        raise

    suggestion, warnings = _coerce_suggestion(parsed)
    confidence = _confidence_from(parsed, suggestion)

    extraction.parsed = json.loads(suggestion.model_dump_json())
    extraction.confidence = confidence
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(extraction)

    return ExtractionResult(
        extraction=extraction, suggestion=suggestion, parsed=parsed, warnings=warnings
    )


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

    completion = _run_completion(messages, temperature=0.3, max_tokens=1600)

    now = datetime.now(timezone.utc)
    user_message = AgentMessage(thread_id=thread.id, role=AgentRole.USER, content=content)
    assistant_message = AgentMessage(
        thread_id=thread.id,
        role=AgentRole.ASSISTANT,
        content=completion.content,
        model=completion.model,
        tokens=(completion.prompt_tokens or 0) + (completion.completion_tokens or 0) or None,
    )
    db.add(user_message)
    db.add(assistant_message)

    thread.last_message_at = now
    if not thread.title:
        thread.title = content[:120]

    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message
