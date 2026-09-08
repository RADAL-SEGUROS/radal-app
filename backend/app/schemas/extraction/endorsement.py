"""``endorsement`` (08A/08B/09B/09D) — Endoso emitido, insurer -> broker.

Confirming it moves the ``endorsement`` to ``status=issued`` and applies the
deltas to the policy and the collection plan in one transaction.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    AmountRow,
    ClaimRow,
    DeductibleLine,
    ExtractionModel,
    FlexDate,
    PeriodBlock,
    PolicyDateTime,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["EndorsementExtraction", "PledgeStipulation", "RosterChange"]


class PledgeStipulation(ExtractionModel):
    creditor: Str = None
    text: Str = Field(default=None, description="Verbatim stipulation — the wording IS the right")
    amount_uf: UF = None


class RosterChange(ExtractionModel):
    before_count: int | None = None
    after_count: int | None = None
    capital_before_uf: UF = None
    capital_after_uf: UF = None
    note: Str = None


class EndorsementExtraction(RootExtraction):
    endorsement_number: Str = Field(
        default=None, description="Carrier folio, verbatim (e.g. '791237-4')"
    )
    endorsement_document_number: Str = None
    policy_number: Str = None
    insurer: Str = None
    endorsement_type: Str = None
    prior_endorsements: list[Str] = Field(default_factory=list)
    claims_in_period: list[ClaimRow] = Field(default_factory=list)

    policy_period: PeriodBlock = Field(default_factory=PeriodBlock)
    endorsement_effective_at: PolicyDateTime = None
    endorsement_end_at: PolicyDateTime = None
    issue_date: FlexDate = None
    motive_text: Str = Field(default=None, description="Verbatim motive")
    same_terms_statement: Str = None

    amounts_moved: list[AmountRow] = Field(default_factory=list)
    post_endorsement_schedule: list[Row] = Field(default_factory=list)
    premium_movement: list[Row] = Field(
        default_factory=list,
        description="Signed deltas: taxable, exempt, net, vat, total, commission",
    )
    premium_impact_none: bool | None = Field(
        default=None,
        description="True for an administrative endorsement with all-zero movement",
    )
    charging_instruction: Str = None

    deductible_changes: list[DeductibleLine] = Field(default_factory=list)
    guarantee_modifications: list[WarrantyLine] = Field(default_factory=list)
    reinspection_result: Row | None = None
    reversal_right: Str = None
    pledge_clause_stipulations: list[PledgeStipulation] = Field(default_factory=list)
    pledged_partitions: list[Row] = Field(default_factory=list)
    roster_change: RosterChange = Field(default_factory=RosterChange)
    element_change_table: list[Row] = Field(default_factory=list)
    creditor_authorization: Row | None = None
    notary_reference: Row | None = None
    special_conditions: list[Str] = Field(default_factory=list)
    coverage_cessation_statement: Str = None
