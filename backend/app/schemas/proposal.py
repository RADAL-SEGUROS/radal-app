"""Pydantic v2 schemas for ``proposal``, its coverage children, and the comparator.

Two things live here besides plain I/O models:

1. :func:`reconcile_money` — the Chilean premium arithmetic
   (docs/v2-architecture.md §4.2). It DERIVES what it can and VALIDATES what the
   caller supplied, so the router can answer with a precise ``422``::

       net    = taxable + exempt          # earthquake cover is VAT-exempt
       vat    = 0.19 * taxable            # on the TAXABLE part, NOT on net
       total  = net + vat
       comprehensive_rate = taxable_rate + exempt_rate

2. The comparison payload — competing proposals aligned side by side: premiums,
   rates, deductibles per peril, and a coverage matrix keyed by
   ``normalized_code`` (falling back to normalised text) so the UI can render
   "who covers what" as a grid.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CoverageKind
from app.models.proposal import ProposalOrigin, ProposalStatus
from app.models.quote import QuoteRequestStatus

# Chilean VAT. Applies to the taxable premium only.
VAT_RATE = Decimal("0.19")

# UF amounts are Numeric(14,4); insurer documents round to 2 decimals.
MONEY_TOLERANCE = Decimal("0.02")
# Per-mille rates are Numeric(9,4) and quoted to 2-4 decimals.
RATE_TOLERANCE = Decimal("0.005")

_MONEY_QUANT = Decimal("0.0001")


# --- Deductibles -------------------------------------------------------------

class DeductibleTerm(BaseModel):
    """One peril's deductible.

    The *basis* differs per peril — fire is a percentage of the loss while
    earthquake is a percentage of the insured amount of the affected item — so
    the basis travels with the number. Unknown extra keys are preserved: lines
    other than property carry their own vocabulary (waiting periods, per-event
    caps) and this must not silently drop them.
    """

    model_config = ConfigDict(extra="allow")

    basis: str | None = None
    pct: Decimal | None = Field(default=None, ge=0)
    min_uf: Decimal | None = Field(default=None, ge=0)
    max_uf: Decimal | None = Field(default=None, ge=0)
    amount_uf: Decimal | None = Field(default=None, ge=0)
    days: int | None = Field(default=None, ge=0)
    notes: str | None = None


# --- Coverages / exclusions --------------------------------------------------

class ProposalCoverageBase(BaseModel):
    kind: CoverageKind
    text: str = Field(min_length=1)
    normalized_code: str | None = Field(default=None, max_length=64)
    sort_order: int = 0


class ProposalCoverageCreate(ProposalCoverageBase):
    pass


class ProposalCoverageUpdate(BaseModel):
    kind: CoverageKind | None = None
    text: str | None = Field(default=None, min_length=1)
    normalized_code: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None


class ProposalCoverageRead(ProposalCoverageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int


# --- Proposal ----------------------------------------------------------------

class ProposalMoneyMixin(BaseModel):
    """The money/rate block shared by create and update."""

    taxable_premium_uf: Decimal | None = Field(default=None, ge=0)
    exempt_premium_uf: Decimal | None = Field(default=None, ge=0)
    net_premium_uf: Decimal | None = Field(default=None, ge=0)
    vat_uf: Decimal | None = Field(default=None, ge=0)
    total_premium_uf: Decimal | None = Field(default=None, ge=0)

    taxable_rate_permille: Decimal | None = Field(default=None, ge=0)
    exempt_rate_permille: Decimal | None = Field(default=None, ge=0)
    comprehensive_rate_permille: Decimal | None = Field(default=None, ge=0)

    commission_pct: Decimal | None = Field(default=None, ge=0, le=100)


class ProposalCreate(ProposalMoneyMixin):
    """Create a proposal.

    ``source_document_id`` is MANDATORY (rule 3): a proposal cannot exist without
    the file it was read from. The insurer must already carry ``rut`` +
    ``cmf_code``; both are checked by the router.
    """

    quote_request_id: int
    insurer_id: int
    source_document_id: int
    origin: ProposalOrigin = ProposalOrigin.EXTERNAL

    modality: str | None = Field(default=None, max_length=255)
    activity_classification: str | None = Field(default=None, max_length=255)

    validity_business_days: int | None = Field(default=None, ge=0)
    coverage_start: date | None = None
    coverage_end: date | None = None
    received_at: date | None = None

    deductibles: dict[str, DeductibleTerm] | None = None
    warranties: str | None = None
    notes: str | None = None

    status: ProposalStatus = ProposalStatus.DRAFT

    # AI provenance. Set when the proposal was pre-filled from an extraction.
    extraction_id: int | None = None
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=100)

    coverages: list[ProposalCoverageCreate] = Field(default_factory=list)

    @field_validator("deductibles", mode="before")
    @classmethod
    def _reject_scalar_deductibles(cls, value: Any) -> Any:
        """Deductibles are keyed by peril and each value is an object."""
        if value is None or isinstance(value, dict):
            return value
        raise ValueError(
            "deductibles must be an object keyed by peril, e.g. "
            '{"fire": {"basis": "loss", "pct": 5, "min_uf": 25}}'
        )


class ProposalUpdate(ProposalMoneyMixin):
    """Partial update. ``quote_request_id`` is immutable — recreate instead.

    Sending ``coverages`` REPLACES the whole set; omitting it leaves the existing
    coverage/exclusion rows untouched.
    """

    insurer_id: int | None = None
    source_document_id: int | None = None
    origin: ProposalOrigin | None = None

    modality: str | None = Field(default=None, max_length=255)
    activity_classification: str | None = Field(default=None, max_length=255)

    validity_business_days: int | None = Field(default=None, ge=0)
    coverage_start: date | None = None
    coverage_end: date | None = None
    received_at: date | None = None

    deductibles: dict[str, DeductibleTerm] | None = None
    warranties: str | None = None
    notes: str | None = None
    status: ProposalStatus | None = None

    extraction_id: int | None = None
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=100)

    coverages: list[ProposalCoverageCreate] | None = None


class ProposalConfirm(BaseModel):
    """Human confirmation of an AI-prefilled proposal (suggest -> confirm -> commit)."""

    is_confirmed: bool = True


class ProposalReject(BaseModel):
    reason: str | None = None


class InsurerRef(BaseModel):
    """Insurer identity as the comparator needs it: rut + cmf_code, never a name match."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    cmf_code: str
    legal_name: str
    trade_name: str | None = None
    is_native: bool


class ProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    quote_request_id: int
    insurer_id: int
    origin: ProposalOrigin
    source_document_id: int

    modality: str | None
    activity_classification: str | None

    taxable_premium_uf: Decimal | None
    exempt_premium_uf: Decimal | None
    net_premium_uf: Decimal | None
    vat_uf: Decimal | None
    total_premium_uf: Decimal | None

    taxable_rate_permille: Decimal | None
    exempt_rate_permille: Decimal | None
    comprehensive_rate_permille: Decimal | None
    commission_pct: Decimal | None

    validity_business_days: int | None
    coverage_start: date | None
    coverage_end: date | None
    received_at: date | None

    deductibles: dict[str, Any] | None
    warranties: str | None
    notes: str | None
    status: ProposalStatus

    extraction_id: int | None
    extraction_confidence: Decimal | None
    is_confirmed: bool
    confirmed_by_id: int | None
    confirmed_at: datetime | None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    coverages: list[ProposalCoverageRead] = Field(default_factory=list)
    insurer: InsurerRef | None = None


class ProposalPage(BaseModel):
    items: list[ProposalRead]
    total: int
    limit: int
    offset: int


class ProposalInsurerCount(BaseModel):
    """One insurer's slice of the proposal flow (the by-insurer leaderboard)."""

    insurer_id: int
    insurer_name: str
    count: int


class ProposalSummary(BaseModel):
    """The proposals board: status split, insurer mix, and the money in play.

    ``total_premium_uf`` and ``avg_rate_permille`` aggregate only proposals
    still in the running (draft / submitted / accepted) — a declined, withdrawn
    or expired offer is not money on the table.
    """

    total: int
    by_status: dict[str, int]
    by_insurer: list[ProposalInsurerCount]
    confirmed_count: int
    total_premium_uf: Decimal
    avg_rate_permille: Decimal | None = None


class ProposalDecisionResult(BaseModel):
    """Outcome of accept/reject — the whole cascade, in one payload."""

    proposal_id: int
    proposal_status: ProposalStatus
    rejected_proposal_ids: list[int] = Field(default_factory=list)
    quote_request_id: int
    quote_request_status: QuoteRequestStatus
    placement_id: int
    placement_status: str


# --- Comparison --------------------------------------------------------------

class ComparisonQuote(BaseModel):
    """The ask the proposals are answering."""

    id: int
    placement_id: int
    insured_object: str | None = None
    declared_value_uf: Decimal | None = None
    currency: str = "UF"
    status: QuoteRequestStatus
    desired_start: date | None = None
    desired_end: date | None = None
    due_at: datetime | None = None
    line_items_total_uf: Decimal = Decimal("0")


class ComparisonColumn(BaseModel):
    """One proposal as a column of the comparison grid."""

    proposal_id: int
    insurer: InsurerRef
    origin: ProposalOrigin
    status: ProposalStatus
    is_confirmed: bool
    source_document_id: int
    extraction_confidence: Decimal | None = None

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

    warranties: str | None = None
    coverage_count: int = 0
    exclusion_count: int = 0


class DeductibleCell(BaseModel):
    proposal_id: int
    # None = this proposal says nothing about the peril, which is itself a finding.
    term: dict[str, Any] | None = None


class DeductibleRow(BaseModel):
    peril: str
    cells: list[DeductibleCell]


class CoverageCell(BaseModel):
    proposal_id: int
    included: bool
    text: str | None = None


class CoverageRow(BaseModel):
    """One aligned coverage (or exclusion) across every proposal of the quote."""

    key: str
    label: str
    kind: CoverageKind
    normalized_code: str | None = None
    cells: list[CoverageCell]


class ComparisonHighlights(BaseModel):
    lowest_total_premium_proposal_id: int | None = None
    lowest_comprehensive_rate_proposal_id: int | None = None
    highest_commission_proposal_id: int | None = None


class ProposalComparison(BaseModel):
    quote: ComparisonQuote
    proposal_count: int
    columns: list[ComparisonColumn]
    perils: list[str]
    deductibles: list[DeductibleRow]
    coverages: list[CoverageRow]
    exclusions: list[CoverageRow]
    highlights: ComparisonHighlights


# --- Money arithmetic --------------------------------------------------------

def _q(value: Decimal) -> Decimal:
    try:
        return Decimal(value).quantize(_MONEY_QUANT)
    except (InvalidOperation, TypeError):  # pragma: no cover - defensive
        return Decimal(value)


def _mismatch(field: str, expected: Decimal, actual: Decimal, rule: str, tolerance: Decimal) -> dict[str, Any]:
    return {
        "field": field,
        "rule": rule,
        "expected": str(_q(expected)),
        "received": str(_q(actual)),
        "difference": str(_q(Decimal(actual) - Decimal(expected))),
        "tolerance": str(tolerance),
    }


def reconcile_money(values: dict[str, Any]) -> tuple[dict[str, Decimal], list[dict[str, Any]]]:
    """Derive the missing premium fields and validate the supplied ones.

    ``values`` is the MERGED state of the proposal (existing row + incoming
    patch), so a partial update is checked against what the row will actually
    look like after the write.

    Returns ``(derived, errors)``: ``derived`` holds fields that were absent and
    could be computed from the invariants, ``errors`` holds one entry per broken
    invariant, ready to be returned as a ``422`` detail.
    """
    taxable = values.get("taxable_premium_uf")
    exempt = values.get("exempt_premium_uf")
    net = values.get("net_premium_uf")
    vat = values.get("vat_uf")
    total = values.get("total_premium_uf")
    taxable_rate = values.get("taxable_rate_permille")
    exempt_rate = values.get("exempt_rate_permille")
    comprehensive_rate = values.get("comprehensive_rate_permille")

    derived: dict[str, Decimal] = {}
    errors: list[dict[str, Any]] = []

    # net = taxable + exempt
    if taxable is not None and exempt is not None:
        expected_net = _q(Decimal(taxable) + Decimal(exempt))
        if net is None:
            net = expected_net
            derived["net_premium_uf"] = expected_net
        elif abs(Decimal(net) - expected_net) > MONEY_TOLERANCE:
            errors.append(
                _mismatch(
                    "net_premium_uf", expected_net, Decimal(net),
                    "net = taxable + exempt", MONEY_TOLERANCE,
                )
            )
    elif net is not None and taxable is not None and exempt is None:
        exempt = _q(Decimal(net) - Decimal(taxable))
        if exempt >= 0:
            derived["exempt_premium_uf"] = exempt
        else:
            errors.append(
                _mismatch(
                    "net_premium_uf", Decimal(taxable), Decimal(net),
                    "net must be >= taxable (exempt cannot be negative)", MONEY_TOLERANCE,
                )
            )
            exempt = None

    # vat = 0.19 * taxable  (never on net — earthquake cover is VAT-exempt)
    if taxable is not None:
        expected_vat = _q(Decimal(taxable) * VAT_RATE)
        if vat is None:
            vat = expected_vat
            derived["vat_uf"] = expected_vat
        elif abs(Decimal(vat) - expected_vat) > MONEY_TOLERANCE:
            errors.append(
                _mismatch(
                    "vat_uf", expected_vat, Decimal(vat),
                    "vat = 0.19 * taxable (not on net)", MONEY_TOLERANCE,
                )
            )

    # total = net + vat
    if net is not None and vat is not None:
        expected_total = _q(Decimal(net) + Decimal(vat))
        if total is None:
            derived["total_premium_uf"] = expected_total
        elif abs(Decimal(total) - expected_total) > MONEY_TOLERANCE:
            errors.append(
                _mismatch(
                    "total_premium_uf", expected_total, Decimal(total),
                    "total = net + vat", MONEY_TOLERANCE,
                )
            )

    # comprehensive_rate = taxable_rate + exempt_rate
    if taxable_rate is not None and exempt_rate is not None:
        expected_rate = _q(Decimal(taxable_rate) + Decimal(exempt_rate))
        if comprehensive_rate is None:
            derived["comprehensive_rate_permille"] = expected_rate
        elif abs(Decimal(comprehensive_rate) - expected_rate) > RATE_TOLERANCE:
            errors.append(
                _mismatch(
                    "comprehensive_rate_permille", expected_rate, Decimal(comprehensive_rate),
                    "comprehensive_rate = taxable_rate + exempt_rate", RATE_TOLERANCE,
                )
            )

    return derived, errors


# --- Coverage alignment key --------------------------------------------------

_NON_WORD = re.compile(r"[^a-z0-9]+")


def coverage_key(normalized_code: str | None, text: str) -> str:
    """Alignment key for the coverage matrix.

    ``normalized_code`` wins when the AI normalisation pass has filled it. Until
    then we fall back to an accent-stripped, punctuation-free slug of the text so
    "Sismo / Terremoto" and "SISMO/TERREMOTO" still line up in the grid.
    """
    if normalized_code:
        return f"code:{normalized_code.strip().lower()}"
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = _NON_WORD.sub("-", folded.lower()).strip("-")
    return f"text:{slug or 'unnamed'}"


__all__ = [
    "VAT_RATE",
    "MONEY_TOLERANCE",
    "RATE_TOLERANCE",
    "DeductibleTerm",
    "ProposalCoverageCreate",
    "ProposalCoverageUpdate",
    "ProposalCoverageRead",
    "ProposalCreate",
    "ProposalUpdate",
    "ProposalConfirm",
    "ProposalReject",
    "ProposalRead",
    "ProposalPage",
    "ProposalInsurerCount",
    "ProposalSummary",
    "ProposalDecisionResult",
    "InsurerRef",
    "ProposalComparison",
    "ComparisonQuote",
    "ComparisonColumn",
    "ComparisonHighlights",
    "DeductibleRow",
    "DeductibleCell",
    "CoverageRow",
    "CoverageCell",
    "reconcile_money",
    "coverage_key",
]
