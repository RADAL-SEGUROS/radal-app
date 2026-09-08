"""``technical_recommendation`` (07R) — Recomendación técnica, broker -> insured.

The insured-facing document. Signing it is the orden de colocación that moves
the case to ``insured_decision`` -> ``proposal_issued``.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    PartyRef,
    Pct,
    PersonRef,
    PolicyDateTime,
    RootExtraction,
    Row,
    SignerBlock,
    Str,
)

__all__ = ["TechnicalRecommendationExtraction", "AlternativeOffer"]


class AlternativeOffer(ExtractionModel):
    insurer: Str = None
    total_premium_uf: UF = None
    why_not_recommended: Str = None


class TechnicalRecommendationExtraction(RootExtraction):
    document_date: FlexDate = None
    insured: PartyRef = Field(default_factory=PartyRef)
    insured_representative: PersonRef = Field(default_factory=PersonRef)

    proposed_insurer: Str = None
    quotation_number: Str = None
    insured_amount_uf: UF = None
    period_start_at: PolicyDateTime = None
    period_end_at: PolicyDateTime = None
    gross_annual_premium_uf: UF = None
    payment_form: Str = None

    narrative_origin: Str = Field(
        default=None, description="Why the account went to market — verbatim"
    )
    market_process_narrative: Str = Field(
        default=None, description="How the market responded — verbatim"
    )
    program_summary: list[Row] = Field(default_factory=list)
    gap_resolution: list[Row] = Field(default_factory=list)
    cost_benefit_comparison: list[Row] = Field(default_factory=list)
    premium_increase_uf: UF = None
    premium_increase_pct: Pct = None

    insured_obligations: list[Str] = Field(default_factory=list)
    deductible_reduction_path: Str = None
    alternatives: list[AlternativeOffer] = Field(default_factory=list)
    placement_order_text: Str = Field(
        default=None, description="The orden de colocación wording — verbatim"
    )
    signature_blocks: list[SignerBlock] = Field(default_factory=list)
