"""Pydantic v2 schemas for the AI pipeline (extraction + agent chat).

Two shapes matter here:

``ProposalSuggestion``
    The STANDARD proposal shape the model must emit, and the same shape the
    broker edits before confirming. It is a *suggestion* — never a proposal.
    Nothing in this module writes anything; the commit step takes this payload
    back in (possibly edited) via ``ProposalConfirmRequest``.

``AgentMessage*`` / ``AgentThread*``
    The chat surface over ``agent_thread`` / ``agent_message``.

Money follows the Chilean invariants (docs/v2-architecture.md §4.2):
``net = taxable + exempt``, ``vat = 0.19 * taxable``, ``total = net + vat``,
``comprehensive_rate = taxable_rate + exempt_rate``. The schemas carry every
component as an OPTIONAL field: the model rarely emits all of them and the
service back-fills what it can derive.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def _enum_value(value: Any) -> Any:
    """Unwrap a SQLAlchemy enum member to its lowercase English string value.

    Needed because ``Enum.__hash__`` is the MEMBER NAME, so a ``Literal[...]``
    lookup would miss ``CoverageKind.COVERAGE`` even though it compares equal to
    ``"coverage"``. Applied to every literal alias below so the read schemas
    accept ORM objects directly.
    """
    return value.value if isinstance(value, Enum) else value


EnumStr = Annotated[str, BeforeValidator(_enum_value)]

# --- Shared vocabularies (mirror the model enums, kept as literals so the API
# contract does not leak SQLAlchemy types) ------------------------------------

CoverageKindLiteral = Annotated[
    Literal["coverage", "exclusion"], BeforeValidator(_enum_value)
]
AgentScopeLiteral = Annotated[
    Literal["proposal", "quote", "general"], BeforeValidator(_enum_value)
]
AgentRoleLiteral = Annotated[
    Literal["system", "user", "assistant", "tool"], BeforeValidator(_enum_value)
]
ExtractionStatusLiteral = Annotated[
    Literal["pending", "running", "succeeded", "failed"], BeforeValidator(_enum_value)
]
ProposalOriginLiteral = Annotated[
    Literal["native", "external"], BeforeValidator(_enum_value)
]
ProposalStatusLiteral = Annotated[
    Literal["draft", "submitted", "accepted", "rejected", "withdrawn", "expired"],
    BeforeValidator(_enum_value),
]


class ApiModel(BaseModel):
    """Base: ORM-friendly, trims incidental whitespace on strings."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


# --- Proposal suggestion (the AI output contract) ----------------------------


class DeductibleEntry(ApiModel):
    """One peril's deductible.

    Deliberately permissive (``extra="allow"``): perils differ per insurance
    line and the basis differs per peril — fire is a % of the LOSS, earthquake a
    % of the INSURED AMOUNT of the affected item, with UF minimums. Anything the
    document states beyond these fields is kept verbatim rather than dropped.
    """

    model_config = ConfigDict(from_attributes=True, extra="allow")

    basis: str | None = Field(
        default=None,
        description="loss | insured_amount | fixed | days — what the % applies to",
    )
    pct: Decimal | None = Field(default=None, description="Percentage, 0-100")
    min_uf: Decimal | None = Field(default=None, description="Minimum deductible in UF")
    max_uf: Decimal | None = None
    days: int | None = Field(default=None, description="Waiting period, when time-based")
    detail: str | None = None


class CoverageSuggestion(ApiModel):
    """One covered item or one exclusion, as read off the document."""

    kind: CoverageKindLiteral = "coverage"
    text: str
    normalized_code: str | None = None
    sort_order: int = 0


class InsurerRef(ApiModel):
    """How the extraction names the issuing insurer.

    Matching is by NORMALISED ``cmf_code`` / ``rut`` ONLY — never by name
    (docs §4.3). ``legal_name`` is carried for display and for creating the row
    when no match exists, not for lookup.
    """

    cmf_code: str | None = None
    rut: str | None = None
    legal_name: str | None = None
    trade_name: str | None = None


class ProposalSuggestion(ApiModel):
    """The standard proposal shape — AI-filled, human-editable, not yet committed."""

    insurer: InsurerRef = Field(default_factory=InsurerRef)

    modality: str | None = None
    activity_classification: str | None = None

    # --- Money (UF) ---
    taxable_premium_uf: Decimal | None = None
    exempt_premium_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    total_premium_uf: Decimal | None = None

    # --- Rates (per mille) ---
    taxable_rate_permille: Decimal | None = None
    exempt_rate_permille: Decimal | None = None
    comprehensive_rate_permille: Decimal | None = None

    commission_pct: Decimal | None = None

    validity_business_days: int | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    received_at: date | None = None

    deductibles: dict[str, DeductibleEntry] = Field(
        default_factory=dict,
        description='Keyed by peril, e.g. {"fire": {...}, "earthquake": {...}}',
    )
    warranties: str | None = None
    notes: str | None = None

    coverages: list[CoverageSuggestion] = Field(default_factory=list)

    @field_validator("deductibles", mode="before")
    @classmethod
    def _coerce_deductibles(cls, value: Any) -> Any:
        """Accept ``null``, a list of ``{peril: ...}`` rows, or the canonical dict."""
        if value in (None, "", []):
            return {}
        if isinstance(value, list):
            coerced: dict[str, Any] = {}
            for row in value:
                if not isinstance(row, dict):
                    continue
                peril = row.get("peril") or row.get("name") or row.get("kind")
                if peril:
                    coerced[str(peril)] = {
                        k: v for k, v in row.items() if k not in {"peril", "name", "kind"}
                    }
            return coerced
        if isinstance(value, dict):
            return {
                str(k): (v if isinstance(v, dict) else {"detail": str(v)})
                for k, v in value.items()
                if v is not None
            }
        return {}

    @field_validator("coverages", mode="before")
    @classmethod
    def _coerce_coverages(cls, value: Any) -> Any:
        """Accept a list of plain strings as coverages."""
        if value is None:
            return []
        if isinstance(value, list):
            return [{"text": v} if isinstance(v, str) else v for v in value]
        return value


# --- Extraction --------------------------------------------------------------


class ExtractionRequest(ApiModel):
    """Ask the model to read one already-uploaded document."""

    document_id: int = Field(..., gt=0)


class ExtractionRead(ApiModel):
    """The persisted AI job — the audit trail, always returned with the suggestion."""

    id: int
    document_id: int
    kind: EnumStr
    model: str
    prompt_version: str
    status: ExtractionStatusLiteral
    confidence: Decimal | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class ExtractionSuggestionResponse(ApiModel):
    """SUGGEST step: what the model read, plus everything needed to review it."""

    extraction: ExtractionRead
    suggestion: ProposalSuggestion | None = None
    # Raw parsed JSON, kept alongside the typed suggestion so a field the schema
    # could not coerce is still visible to the reviewer instead of vanishing.
    parsed: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


# --- Confirm / commit --------------------------------------------------------


class ProposalConfirmRequest(ApiModel):
    """COMMIT step: the reviewed (possibly edited) suggestion becomes a proposal."""

    quote_request_id: int = Field(..., gt=0)
    proposal: ProposalSuggestion
    status: ProposalStatusLiteral = "submitted"
    # The reviewer is asserting the numbers. False only for a save-as-draft flow.
    is_confirmed: bool = True


class ProposalCoverageRead(ApiModel):
    id: int
    kind: CoverageKindLiteral
    text: str
    normalized_code: str | None = None
    sort_order: int


class ProposalRead(ApiModel):
    """The committed proposal."""

    id: int
    broker_id: int
    quote_request_id: int
    insurer_id: int
    insurer_name: str | None = None
    insurer_cmf_code: str | None = None
    origin: ProposalOriginLiteral
    source_document_id: int
    status: ProposalStatusLiteral

    modality: str | None = None
    activity_classification: str | None = None

    taxable_premium_uf: Decimal | None = None
    exempt_premium_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    total_premium_uf: Decimal | None = None
    taxable_rate_permille: Decimal | None = None
    exempt_rate_permille: Decimal | None = None
    comprehensive_rate_permille: Decimal | None = None
    commission_pct: Decimal | None = None

    validity_business_days: int | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    received_at: date | None = None

    deductibles: dict[str, Any] | None = None
    warranties: str | None = None
    notes: str | None = None

    extraction_id: int | None = None
    extraction_confidence: Decimal | None = None
    is_confirmed: bool
    confirmed_by_id: int | None = None
    confirmed_at: datetime | None = None

    coverages: list[ProposalCoverageRead] = Field(default_factory=list)


# --- Agent chat --------------------------------------------------------------


class ThreadCreate(ApiModel):
    """Open a conversation. ``proposal``/``quote`` scope anchors it to an entity."""

    scope: AgentScopeLiteral = "general"
    entity_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=255)


class ThreadRead(ApiModel):
    id: int
    broker_id: int
    user_id: int
    scope: AgentScopeLiteral
    entity_type: EnumStr | None = None
    entity_id: int | None = None
    title: str | None = None
    is_archived: bool
    last_message_at: datetime | None = None
    created_at: datetime | None = None


class ThreadList(ApiModel):
    items: list[ThreadRead]
    total: int


class MessageCreate(ApiModel):
    content: str = Field(..., min_length=1, max_length=8000)


class MessageRead(ApiModel):
    id: int
    thread_id: int
    role: AgentRoleLiteral
    content: str | None = None
    # Additive (v4 agent): the provider's tool_calls on an assistant message /
    # the linkage stub on a tool message, and the refs a user message carried.
    tool_calls: dict[str, Any] | list[Any] | None = None
    context_refs: list[dict[str, Any]] | None = None
    model: str | None = None
    tokens: int | None = None
    created_at: datetime | None = None


class MessageList(ApiModel):
    items: list[MessageRead]
    total: int


class MessageExchange(ApiModel):
    """One turn: what the user said and what the assistant answered."""

    thread_id: int
    user_message: MessageRead
    assistant_message: MessageRead


# --- Agentic turn (v4 agent spec §7) ------------------------------------------

AgentActionStatusLiteral = Annotated[
    Literal["proposed", "confirmed", "discarded", "failed"],
    BeforeValidator(_enum_value),
]

#: Ref entity types the agent endpoint resolves (spec §6.1). The service is the
#: authority; this mirrors it so the request 422s early with a clear message.
CONTEXT_REF_TYPES = (
    "account_group",
    "client",
    "case_file",
    "policy",
    "quote_request",
    "proposal",
    "document",
    "sales_lead",
)

MAX_CONTEXT_REFS = 6


class ContextRef(ApiModel):
    """One @-mention attached to a user message."""

    entity_type: str = Field(..., min_length=1, max_length=30)
    entity_id: int = Field(..., gt=0)


class AgentTurnRequest(ApiModel):
    """``POST /ai/agent/messages`` — one agentic turn."""

    thread_id: int | None = Field(default=None, gt=0)
    content: str = Field(..., min_length=1, max_length=8000)
    context_refs: list[ContextRef] = Field(default_factory=list, max_length=MAX_CONTEXT_REFS)


class AgentActionRead(ApiModel):
    """One proposed/resolved write. ``allowed`` is computed per response for
    the CALLER, so the card can render Confirmar disabled-with-reason without a
    second request (the server still re-checks on confirm)."""

    id: int
    thread_id: int
    message_id: int | None = None
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    module: str
    action: str
    status: AgentActionStatusLiteral
    result: dict[str, Any] | None = None
    error: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    allowed: bool = False


class AgentActionList(ApiModel):
    items: list[AgentActionRead]
    total: int


class AgentTurnResponse(ApiModel):
    """Everything one agent turn persisted, in order."""

    thread: ThreadRead
    messages: list[MessageRead]
    pending_actions: list[AgentActionRead] = Field(default_factory=list)


# --- Registry-driven extraction (v2) -----------------------------------------
#
# The generic path: any of the 26 registry categories, one response shape, so the
# UI can render a review form for a category it has never seen.


class DocumentExtractionRequest(ApiModel):
    """Ask the model to read one uploaded document with its registry schema."""

    document_id: int = Field(..., gt=0)
    category: str | None = Field(
        default=None,
        description="Override the document's filed category (registry slug)",
    )


# The spec's own name for the same shape.
ExtractionRequestV2 = DocumentExtractionRequest


class CategoryFieldRead(ApiModel):
    """One top-level field of a category schema, for the generic review form."""

    name: str
    type: str | None = None
    required: bool = False
    description: str | None = None


class CategorySpecRead(ApiModel):
    """One registry row as JSON — everything the UI needs to render it."""

    category: str
    canonical_category: str
    is_alias: bool = False
    code: str | None = None
    codes: list[str] = Field(default_factory=list)
    section: str
    direction: str
    prompt_version: str
    module: str
    schema_name: str
    prefill_target: str
    extraction_kind: str
    guidance: str
    fields: list[CategoryFieldRead] = Field(default_factory=list)


class CategoryListResponse(ApiModel):
    items: list[CategorySpecRead]
    total: int


class DocumentExtractionResponse(ApiModel):
    """SUGGEST, generic: the audit row plus the category's own payload."""

    extraction: ExtractionRead
    category: str
    canonical_category: str
    schema_name: str
    schema_version: str
    prefill_target: str
    section: str
    payload: dict[str, Any] | None = None
    # The raw parse, kept alongside the typed payload so a field the schema could
    # not coerce is still visible to the reviewer instead of vanishing.
    parsed: dict[str, Any] | None = None
    # Only filled for the insurer-quotation categories: the legacy contract.
    suggestion: ProposalSuggestion | None = None
    warnings: list[str] = Field(default_factory=list)


class DocumentConfirmRequest(ApiModel):
    """COMMIT the human-reviewed payload for any registry category."""

    payload: dict[str, Any] = Field(default_factory=dict)
    category: str | None = Field(
        default=None, description="Override the extraction's recorded category"
    )
    # Only for the insurer-quotation categories, where confirming really does
    # create a proposal.
    quote_request_id: int | None = Field(default=None, gt=0)
    status: ProposalStatusLiteral = "submitted"
    is_confirmed: bool = True


class DocumentConfirmResponse(ApiModel):
    extraction: ExtractionRead
    category: str
    prefill_target: str
    # True when the confirm wrote the target entity (spec §6.6). False either
    # because the category is genuinely informational (``informational=True``)
    # or because something required is missing (see ``commit.detail``).
    applied: bool = False
    # True for categories that legitimately commit nothing (comparatives,
    # declinations, cover letters, closure notes) — the UI says so instead of
    # looking broken.
    informational: bool = False
    # What the commit did: {target, entity_type, entity_id, detail, ...extras}.
    commit: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    proposal: ProposalRead | None = None
    warnings: list[str] = Field(default_factory=list)


class SummaryRead(ApiModel):
    """AI prose, always UNCONFIRMED: suggest -> edit -> confirmar."""

    extraction_id: int
    text: str
    model: str
    prompt_version: str
    is_confirmed: bool = False
    proposal_id: int | None = None
    case_file_id: int | None = None
    pack_id: int | None = None


class StreamFrame(ApiModel):
    """One SSE frame. Documentation-only: the endpoint writes text/event-stream.

    ``start`` -> ``token``* -> ``done``, or a single ``error`` frame.
    """

    event: Literal["start", "token", "done", "error"]
    data: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ApiModel",
    "EnumStr",
    "DocumentExtractionRequest",
    "ExtractionRequestV2",
    "CategoryFieldRead",
    "CategorySpecRead",
    "CategoryListResponse",
    "DocumentExtractionResponse",
    "DocumentConfirmRequest",
    "DocumentConfirmResponse",
    "SummaryRead",
    "StreamFrame",
    "DeductibleEntry",
    "CoverageSuggestion",
    "InsurerRef",
    "ProposalSuggestion",
    "ExtractionRequest",
    "ExtractionRead",
    "ExtractionSuggestionResponse",
    "ProposalConfirmRequest",
    "ProposalCoverageRead",
    "ProposalRead",
    "ThreadCreate",
    "ThreadRead",
    "ThreadList",
    "MessageCreate",
    "MessageRead",
    "MessageList",
    "MessageExchange",
    "CONTEXT_REF_TYPES",
    "MAX_CONTEXT_REFS",
    "ContextRef",
    "AgentTurnRequest",
    "AgentActionRead",
    "AgentActionList",
    "AgentTurnResponse",
]
