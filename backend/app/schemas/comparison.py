"""Schemas for the incremental proposal comparison milestone (v8).

The comparison is the account's COMPARISON-stage worktable: offers arrive one at a
time (each extracted dynamically, cached per-column in ``comparison_source``),
then re-aligned over a monotonic canonical dictionary. These are the wire shapes
the ``/comparisons`` router speaks; the persisted layers live in
``app.models.comparison`` and the alignment logic in ``app.services.ai``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.comparison import ComparisonStatus


# --- Create ------------------------------------------------------------------

class ComparisonCreate(BaseModel):
    """Open (or fetch) the comparison for an account's COMPARISON stage."""

    case_file_id: int
    placement_id: int | None = None


class ComparisonEntryCreate(BaseModel):
    """Add one uploaded cotización to the grid.

    The document is created through the normal documents flow first; here we only
    receive its id and read it dynamically.
    """

    document_id: int


class ComparisonEntryUpdate(BaseModel):
    """Human curation of one column: recommend, reorder, or override the facets."""

    is_recommended: bool | None = None
    sort_order: int | None = None
    # A human-corrected compact facet list; replaces the cached extraction facets
    # on the column's ``comparison_source`` when supplied.
    facets: list[dict[str, Any]] | None = None


class ComparisonPromote(BaseModel):
    """Promote a column's fixed money core into a real inbound ``proposal``."""

    # An override of the resolved quote request; normally left unset so the
    # router resolves/creates one from the account's placement.
    quote_request_id: int | None = None

    # Reviewer-supplied insurer identity. Real cotización PDFs state only the
    # INSURED's RUT, so ``extract_budget_proposal`` legitimately can't fill the
    # insurer's identity and ``find_or_create_insurer`` refuses name-only
    # resolution ("dedup by RUT + CMF, never by name"). These let the human
    # confirming the promotion supply it; a valid supplied value WINS over the
    # parsed one, and the router normalises/validates them exactly as the
    # insurers flow does (mod-11 RUT, CMF normalisation).
    insurer_cmf_code: str | None = None
    insurer_rut: str | None = None


# --- Read --------------------------------------------------------------------

class ComparisonPremiumCore(BaseModel):
    """The fixed money/period core an entry extracted, surfaced PRE-promotion.

    Read DIRECTLY from the column's already-persisted ``extraction.parsed``
    (the ``BudgetProposalExtraction`` payload) — never re-extracted, never
    re-validated (money invariants stay on promote/proposal). This lets the grid
    show a premium/rate highlight row-group across columns before any column is
    promoted to a real ``proposal``. Null when the column is a wrong-file or has
    no parse. Values are surfaced verbatim as stored (display only), so every
    leaf is lenient.
    """

    model_config = ConfigDict(extra="ignore")

    taxable_premium_uf: Any = None
    exempt_premium_uf: Any = None
    net_premium_uf: Any = None
    vat_uf: Any = None
    total_premium_uf: Any = None
    taxable_rate_permille: Any = None
    exempt_rate_permille: Any = None
    comprehensive_rate_permille: Any = None
    commission_pct: Any = None
    validity_business_days: Any = None
    period_start_at: Any = None
    period_end_at: Any = None
    coverage_start: Any = None
    coverage_end: Any = None


class ComparisonEntryRead(BaseModel):
    """One column of a comparison, with its cached source verdict."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    comparison_id: int
    proposal_id: int | None = None
    comparison_source_id: int | None = None
    is_recommended: bool = False
    sort_order: int = 0

    # Flattened from the linked ``comparison_source`` for the grid.
    is_wrong_file: bool = False
    wrong_file_reason: str | None = None
    source_extraction_id: int | None = None
    document_id: int | None = None
    facets: list[Any] | dict[str, Any] | None = None
    # The fixed money/period core read from the column's persisted extraction,
    # so the premium/rate highlight is visible before promotion. Null for a
    # wrong-file column or one with no parse. Display only.
    premium: ComparisonPremiumCore | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class ComparisonRead(BaseModel):
    """The comparison header + columns + aligned grid + snapshotted dictionary."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    case_file_id: int | None = None
    placement_id: int | None = None
    status: ComparisonStatus
    canonical_version: int
    dictionary: list[Any] | dict[str, Any] | None = None
    aligned_matrix: list[Any] | dict[str, Any] | None = None
    # The AI recommendation from the holistic ``submit_comparison`` pass. Stored
    # inside ``aligned_matrix`` (no DB column) and surfaced here as a first-class
    # field: {recommended_comparison_source_id, recommended_proposal_id,
    # rationale, caveats}. Null until the comparison is aligned.
    recommendation: dict[str, Any] | None = None
    pdf_document_id: int | None = None
    source_extraction_id: int | None = None

    entries: list[ComparisonEntryRead] = Field(default_factory=list)

    created_at: datetime | None = None
    updated_at: datetime | None = None


class ComparisonEntryResult(BaseModel):
    """The response to adding a column: the entry plus the read verdict.

    A ``not_a_proposal`` upload is a visible rejection here (``is_wrong_file`` +
    ``rejection_reason``), never a 422 — the broker keeps a rejected column in
    the grid and can delete it.
    """

    entry: ComparisonEntryRead
    extraction_id: int
    document_type: str
    is_wrong_file: bool
    rejection_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ComparisonAlignmentResult(BaseModel):
    """The response to POST /comparisons/{id}/align — the standardized table.

    ``used_ai`` is always True now (the holistic comparison is AI-only; when the
    provider is down the endpoint returns a clean provider error instead of this).
    ``batched`` flags that the readings exceeded the input threshold and were run
    in batches then merged. ``recommendation`` is the AI's recommended offer.
    """

    comparison: ComparisonRead
    used_ai: bool
    canonical_version: int
    batched: bool = False
    recommendation: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class ComparisonPromoteResult(BaseModel):
    """The response to promoting a column into a real ``proposal``.

    ``warnings`` carries SOFT money notes (currently rate additivity) that did not
    block the promote — e.g. a comprehensive "tasa media" that is not the sum of
    the per-peril rates.
    """

    entry: ComparisonEntryRead
    proposal_id: int
    insurer_id: int
    quote_request_id: int
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "ComparisonCreate",
    "ComparisonEntryCreate",
    "ComparisonEntryUpdate",
    "ComparisonPromote",
    "ComparisonPremiumCore",
    "ComparisonEntryRead",
    "ComparisonRead",
    "ComparisonEntryResult",
    "ComparisonAlignmentResult",
    "ComparisonPromoteResult",
]
