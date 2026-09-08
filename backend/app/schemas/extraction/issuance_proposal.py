"""``issuance_proposal`` (07) — Propuesta de emisión, broker -> winning insurer.

This is the MIRROR BASELINE: the policy the insurer issues is later diffed
field-by-field against this document (spec §6.4). Everything it states is
therefore recorded as stated, and never merged with the quotation it came from.
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
    RootExtraction,
    Row,
    SignerBlock,
    Str,
    WarrantyLine,
)

__all__ = ["IssuanceProposalExtraction", "CommissionBlock", "PaymentPlanBlock"]


class CommissionBlock(ExtractionModel):
    pct: Pct = None
    amount_uf: UF = None
    note: Str = None


class PaymentPlanBlock(ExtractionModel):
    payment_mode: Str = None
    installment_count: int | None = None
    first_due_date: FlexDate = None
    monthly_interest_rate_pct: Pct = None
    text: Str = Field(default=None, description="Verbatim payment clause")


class IssuanceProposalExtraction(RootExtraction):
    document_date: FlexDate = None
    document_city: Str = None
    addressee_insurer: InsurerRefX = Field(default_factory=InsurerRefX)
    accepted_quotation_number: Str = None
    accepted_quotation_date: FlexDate = None
    mirror_validation_clause: Str = Field(
        default=None,
        description="The clause binding the policy to mirror this proposal — verbatim",
    )

    policyholder: PartyRef = Field(default_factory=PartyRef)
    beneficiaries: list[Str] = Field(default_factory=list)
    secured_creditor_amount_uf: UF = None
    broker: PartyRef = Field(default_factory=PartyRef)

    insurance_line: Str = None
    cover_mode: Str = None
    replaced_policy: Str = None
    period_start_at: PolicyDateTime = None
    period_end_at: PolicyDateTime = None
    indemnity_limit: Str = None
    indemnity_basis: Str = None
    territory: Str = None

    insured_subject_matter: list[Str] = Field(default_factory=list)
    insured_values_by_partida: list[AmountRow] = Field(default_factory=list)
    total_insured_amount_uf: UF = None
    sublimits: list[CoverageLine] = Field(default_factory=list)
    coverages_to_issue: list[CoverageLine] = Field(default_factory=list)
    deductibles_to_issue: list[DeductibleLine] = Field(default_factory=list)
    exclusions_to_issue: list[ExclusionLine] = Field(default_factory=list)
    particular_clauses: list[Str] = Field(default_factory=list)
    warranties: list[WarrantyLine] = Field(default_factory=list)
    causality_clause: Str = None
    locations: list[LocationRow] = Field(default_factory=list)

    premium_by_item: list[AmountRow] = Field(default_factory=list)
    premium: PremiumBlock = Field(
        default_factory=PremiumBlock,
        description="net = taxable + exempt; vat = 0.19 x taxable; total = net + vat",
    )
    commission: CommissionBlock = Field(default_factory=CommissionBlock)
    payment_plan: PaymentPlanBlock = Field(default_factory=PaymentPlanBlock)
    instalments: list[InstallmentRow] = Field(default_factory=list)
    prior_policy_refund_uf: UF = None

    insured_declarations: list[Str] = Field(default_factory=list)
    accompanying_documents: list[Str] = Field(default_factory=list)
    issuance_deadline: FlexDate = None
    signer: SignerBlock = Field(default_factory=SignerBlock)
    extra_tables: list[Row] = Field(default_factory=list)
