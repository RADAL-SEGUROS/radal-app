"""``technical_brief`` (01) — Bases técnicas, broker -> insurers.

The document that opens the submission and defines what every insurer is being
asked to quote. Requested coverages and deductibles are kept VERBATIM: the
comparator later aligns offers against exactly this wording.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    CoverageLine,
    DeductibleLine,
    ExclusionLine,
    ExtractionModel,
    FlexDate,
    Int,
    LocationRow,
    PartyRef,
    Pct,
    PolicyDateTime,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["TechnicalBriefExtraction", "GapRow"]


class GapRow(ExtractionModel):
    """A B-n gap between what the insured has and what they need."""

    code: Str = None
    title: Str = None
    severity: Str = None
    detail: Str = None


class TechnicalBriefExtraction(RootExtraction):
    document_date: FlexDate = None
    document_city: Str = None
    insurance_line: Str = None
    cover_mode: Str = None
    alternative_cover_mode_requested: Str = None

    # Datetimes: the noon convention is contractual.
    policy_period_start: PolicyDateTime = None
    policy_period_end: PolicyDateTime = None
    broker_commission_pct: Pct = None

    insured: PartyRef = Field(default_factory=PartyRef)
    locations: list[LocationRow] = Field(default_factory=list)
    insured_values_by_location: list[Row] = Field(default_factory=list)
    total_program_insured_value_uf: UF = None
    indemnity_limit: Str = None
    indemnity_basis: Str = None

    requested_coverages: list[CoverageLine] = Field(
        default_factory=list,
        description="n, name and the requested limit as prose — verbatim",
    )
    requested_deductibles: list[DeductibleLine] = Field(default_factory=list)
    deductible_policy_note: Str = None
    exclusions: list[ExclusionLine] = Field(default_factory=list)
    gaps: list[GapRow] = Field(default_factory=list)
    warranties_accepted: list[WarrantyLine] = Field(default_factory=list)
    causality_clause_requested: Str = None

    quoting_instructions: list[Str] = Field(default_factory=list)
    offer_deadline: FlexDate = None
    award_date_estimated: FlexDate = None
    loss_history_summary: list[Row] = Field(default_factory=list)
    exposure_summary: list[Row] = Field(default_factory=list)
    inspection_folio_reference: Str = None
    annexes: list[Str] = Field(default_factory=list)
    decisive_coverages: list[Str] = Field(
        default_factory=list,
        description="The coverages the award will actually turn on",
    )
    minimum_insurers_requested: Int = None
