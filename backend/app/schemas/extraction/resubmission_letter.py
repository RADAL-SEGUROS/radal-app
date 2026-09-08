"""``resubmission_letter`` (03F) — Carta de re-remisión, broker -> insurers.

Round two. On confirm it bumps ``quote_request.round_no`` and records each
insurer's round-one answer as a ``proposal.outcome``.
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

__all__ = ["ResubmissionLetterExtraction", "RoundOneResponse", "PerInsurerAsk"]


class RoundOneResponse(ExtractionModel):
    insurer: Str = None
    response: Str = Field(
        default=None, description="quoted | declined | conditional | no_response"
    )
    grounds: Str = Field(default=None, description="Verbatim grounds given")


class PerInsurerAsk(ExtractionModel):
    insurer: Str = None
    ask: Str = None


class ResubmissionLetterExtraction(RootExtraction):
    letter_date: FlexDate = None
    round_number: Int = None
    insured: PartyRef = Field(default_factory=PartyRef)
    insurance_line: Str = None

    proposed_insured_amount_uf: UF = None
    requested_inception_date: FlexDate = None
    quote_deadline: FlexDate = None
    addressee_insurers: list[InsurerRefX] = Field(default_factory=list)

    round_one_responses: list[RoundOneResponse] = Field(default_factory=list)
    first_round_dispatch_date: FlexDate = None
    new_evidence: list[Str] = Field(default_factory=list)
    per_insurer_ask: list[PerInsurerAsk] = Field(default_factory=list)
    deductible_structure_requested: Str = Field(
        default=None, description="Verbatim deductible ask"
    )
    award_criteria: list[Str] = Field(default_factory=list)
    closing_argument: Str = None
