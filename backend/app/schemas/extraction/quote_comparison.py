"""``quote_comparison`` (06) — Comparativo de cotizaciones, broker -> insured.

The broker's own comparison document. It seeds the comparison pack's summary
and the per-proposal AI summaries; the recommended offer is highlighted but
never auto-accepted.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    PartyRef,
    PersonRef,
    Pct,
    RootExtraction,
    Row,
    Str,
)

__all__ = ["QuoteComparisonExtraction", "ComparedOffer", "RecommendedOffer"]


class ComparedOffer(ExtractionModel):
    insurer: Str = None
    quotation_number: Str = None
    total_premium_uf: UF = None
    net_premium_uf: UF = None
    insured_amount_uf: UF = None
    rate: Pct = None
    deductible_summary: Str = None
    note: Str = None


class RecommendedOffer(ExtractionModel):
    insurer: Str = None
    quotation_number: Str = None
    total_premium_uf: UF = None
    reason: Str = None


class QuoteComparisonExtraction(RootExtraction):
    comparison_date: FlexDate = None
    insured: PartyRef = Field(default_factory=PartyRef)
    insurance_line: Str = None
    quoted_period: Str = None

    offers_compared: list[ComparedOffer] = Field(default_factory=list)
    methodological_warning: Str = Field(
        default=None,
        description="The caveat about comparing unlike offers — verbatim",
    )
    economic_summary: list[Row] = Field(default_factory=list)
    central_finding: Str = None
    gap_resolution_matrix: list[Row] = Field(default_factory=list)
    coverage_comparison: list[Row] = Field(default_factory=list)
    deductible_comparison: list[Row] = Field(default_factory=list)
    scenario_simulation: list[Row] = Field(default_factory=list)
    cover_mode_comparison: list[Row] = Field(default_factory=list)
    weighted_evaluation: list[Row] = Field(default_factory=list)

    recommended_offer: RecommendedOffer = Field(default_factory=RecommendedOffer)
    recommendation_rationale: list[Str] = Field(default_factory=list)
    pre_issuance_actions: list[Str] = Field(default_factory=list)
    broker_signer: PersonRef = Field(default_factory=PersonRef)
