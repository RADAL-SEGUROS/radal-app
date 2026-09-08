"""``conditional_pronouncement`` (03D) — Pronunciamiento condicionado.

Insurer -> broker: "we will quote, but only if…". Confirming it sets
``proposal.outcome=conditional`` and creates the measures as ``warranty`` rows,
split between pre-quotation and post-issuance.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    FlexDate,
    InsurerRefX,
    Int,
    PartyRef,
    RootExtraction,
    Str,
    WarrantyLine,
)

__all__ = ["ConditionalPronouncementExtraction"]


class ConditionalPronouncementExtraction(RootExtraction):
    pronouncement: Str = Field(default=None, description="Verbatim verdict")
    pronouncement_date: FlexDate = None
    insurer: InsurerRefX = Field(default_factory=InsurerRefX)
    insured: PartyRef = Field(default_factory=PartyRef)
    requested_amount_uf: UF = None
    rationale: Str = None

    pre_quotation_measures: list[WarrantyLine] = Field(
        default_factory=list, description="Must be met BEFORE a quotation is issued"
    )
    post_issuance_warranty_measures: list[WarrantyLine] = Field(
        default_factory=list, description="Warranties that attach to the policy"
    )
    coverage_anticipations: list[Str] = Field(default_factory=list)
    deductible_mechanism_response: Str = None
    anticipated_conditions: list[Str] = Field(default_factory=list)
    inspection_folio_accepted: Str = None
    reinspection_required: bool | None = None
    quoting_sla_business_days: Int = None
