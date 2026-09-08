"""``policy`` (08) — La póliza emitida, insurer -> broker.

Confirming it writes the ``policy`` plus its locations, coverage items,
warranties and collection plan — and runs the MIRROR DIFF against the confirmed
``issuance_proposal``. The two documents disagree in the corpus (the real
Southbridge policy says 6 months where the mirror says 12) and that disagreement
is the product: it is surfaced as a review item, never reconciled away.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    AmountRow,
    CoverageLine,
    DeductibleLine,
    ExclusionLine,
    ExtractionModel,
    FlexDate,
    InstallmentRow,
    InsurerRefX,
    LocationRow,
    PartyRef,
    Pct,
    PolicyDateTime,
    PremiumBlock,
    Rate,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["PolicyExtraction", "ReinstatementBlock", "PledgeCreditorRow"]


class ReinstatementBlock(ExtractionModel):
    text: Str = Field(default=None, description="Verbatim reinstatement clause")
    is_automatic: bool | None = None
    premium_uf: UF = None
    limit_uf: UF = None


class PledgeCreditorRow(ExtractionModel):
    name: Str = None
    rut: Str = None
    amount_uf: UF = None
    stipulation: Str = Field(
        default=None, description="Verbatim pledge stipulation — the wording IS the right"
    )


class PolicyExtraction(RootExtraction):
    policy_number: Str = None
    renews_policy_number: Str = None
    insurer: InsurerRefX = Field(default_factory=InsurerRefX)
    line_of_business: Str = None
    coverage_modality: Str = None
    policyholder: PartyRef = Field(default_factory=PartyRef)
    insured: PartyRef = Field(default_factory=PartyRef)
    broker: PartyRef = Field(
        default_factory=PartyRef,
        description="The broker named on the policy — TEXT ONLY, never a tenant",
    )

    cmf_policy_code: Str = None
    cmf_additional_clause_codes: list[Str] = Field(default_factory=list)
    market_clause_codes: list[Str] = Field(default_factory=list)

    period_start_at: PolicyDateTime = None
    period_end_at: PolicyDateTime = None
    currency: Str = None
    issue_date: FlexDate = None
    issue_place: Str = None
    source_proposal_date: FlexDate = None
    source_quote_reference: Str = None
    mirror_issuance_statement: Str = Field(
        default=None, description="Verbatim 'se emite en los términos de…' clause"
    )

    locations: list[LocationRow] = Field(default_factory=list)
    insured_amounts: list[AmountRow] = Field(default_factory=list)
    total_insured_amount_uf: UF = None
    valuation_basis: Str = None
    indemnity_limit: Str = None
    aggregate_limit_uf: UF = None
    reinstatement: ReinstatementBlock = Field(default_factory=ReinstatementBlock)

    vehicles: list[Row] = Field(default_factory=list)
    drivers: list[Row] = Field(default_factory=list)
    workers: list[Row] = Field(default_factory=list)
    ap_plans: list[Row] = Field(default_factory=list)
    accumulation_per_event_uf: UF = None
    accumulation_per_period_uf: UF = None
    pledge_creditor: list[PledgeCreditorRow] = Field(default_factory=list)

    coverages: list[CoverageLine] = Field(default_factory=list)
    sublimits: list[CoverageLine] = Field(default_factory=list)
    deductibles: list[DeductibleLine] = Field(default_factory=list)
    exclusions: list[ExclusionLine] = Field(default_factory=list)
    particular_clauses: list[Str] = Field(default_factory=list)
    warranties: list[WarrantyLine] = Field(default_factory=list)

    premium_lines: list[AmountRow] = Field(default_factory=list)
    premium: PremiumBlock = Field(default_factory=PremiumBlock)
    commission_pct: Pct = None
    commission_uf: UF = None
    average_rate_permille: Rate = None
    payment_mode: Str = None
    installments: list[InstallmentRow] = Field(default_factory=list)
