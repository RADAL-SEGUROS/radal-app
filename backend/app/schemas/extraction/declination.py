"""``declination`` (03A/03B/03C) — Declinación, insurer -> broker.

A "no" is a first-class market signal: it becomes a ``proposal`` with
``status=rejected`` and ``outcome=declined``, with the reasons preserved
verbatim so the next round can answer them one by one.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    InsurerRefX,
    Int,
    PartyRef,
    RootExtraction,
    Str,
)

__all__ = ["DeclinationExtraction", "DeclinationReason", "ExistingPolicyRef"]


class DeclinationReason(ExtractionModel):
    n: Int = None
    reason: Str = None
    rationale: Str = Field(default=None, description="Verbatim rationale")


class ExistingPolicyRef(ExtractionModel):
    number: Str = None
    insurance_line: Str = None
    expiry_date: FlexDate = None
    note: Str = None


class DeclinationExtraction(RootExtraction):
    pronouncement: Str = Field(
        default=None, description="The verdict wording, verbatim"
    )
    pronouncement_date: FlexDate = None
    insurer: InsurerRefX = Field(default_factory=InsurerRefX)
    insured: PartyRef = Field(default_factory=PartyRef)

    submission_reference_date: FlexDate = None
    insurance_line: Str = None
    requested_amount_uf: UF = None
    existing_policy_with_this_insurer: ExistingPolicyRef = Field(
        default_factory=ExistingPolicyRef
    )

    declination_reasons: list[DeclinationReason] = Field(default_factory=list)
    renewal_offer_on_existing_terms: Str = None
    non_renewal: bool | None = None
    final_remarks: Str = None
