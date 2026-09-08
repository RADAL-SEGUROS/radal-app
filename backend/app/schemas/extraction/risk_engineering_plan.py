"""``risk_engineering_plan`` (03E) — Plan de ingeniería de riesgos.

Broker + insured -> insurers. Every measure becomes a ``warranty`` row with
``source=engineering_measure``, a budget and a deadline.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    Int,
    PartyRef,
    Pct,
    RootExtraction,
    Row,
    SignerBlock,
    Str,
    WarrantyLine,
)

__all__ = ["RiskEngineeringPlanExtraction", "MeasureRow", "DeductibleReductionMechanism"]


class MeasureRow(WarrantyLine):
    scope: Str = None
    cost_uf: UF = None
    provider: Str = None
    quote_reference: Str = None


class DeductibleReductionMechanism(ExtractionModel):
    """What the insurer gives back once the plan is verified."""

    text: Str = Field(default=None, description="Verbatim mechanism wording")
    from_deductible: Str = None
    to_deductible: Str = None
    condition: Str = None
    saving_uf: UF = None


class RiskEngineeringPlanExtraction(RootExtraction):
    document_date: FlexDate = None
    insured: PartyRef = Field(default_factory=PartyRef)
    origin_inspection_folio: Str = None

    total_investment_uf: UF = None
    total_duration_days: Int = None
    plan_start_date: FlexDate = None
    score_before: Pct = None
    score_after: Pct = None
    class_before: Str = None
    class_after: Str = None

    measures: list[MeasureRow] = Field(default_factory=list)
    verification_milestones: list[Row] = Field(default_factory=list)
    gantt_by_month: list[Row] = Field(default_factory=list)
    financing: list[Row] = Field(default_factory=list)
    decision_arithmetic: list[Row] = Field(
        default_factory=list, description="Cost of the plan vs the premium it saves"
    )
    deductible_reduction_mechanism: DeductibleReductionMechanism = Field(
        default_factory=DeductibleReductionMechanism
    )
    insured_commitments: list[Str] = Field(default_factory=list)
    signers: list[SignerBlock] = Field(default_factory=list)
