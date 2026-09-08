"""``broker_closing_note`` (14 nota de cierre) — broker -> insured.

The claim is paid; the renewal conversation starts here. Confirming it seeds a
``case_file(kind=renewal)`` with the dated actions the note asks for.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ClaimRow,
    FlexDate,
    Int,
    PartyRef,
    PersonRef,
    RootExtraction,
    Row,
    Str,
)

__all__ = ["BrokerClosingNoteExtraction"]


class BrokerClosingNoteExtraction(RootExtraction):
    addressed_to: PartyRef = Field(default_factory=PartyRef)
    issuer_broker: PartyRef = Field(default_factory=PartyRef)
    signer: PersonRef = Field(default_factory=PersonRef)
    subject: Str = None
    note_date: FlexDate = None

    claim_number: Str = None
    policy_number: Str = None
    renewal_date: FlexDate = None

    paid_amount_uf: UF = None
    payment_date: FlexDate = None
    days_from_notice_to_payment: Int = None
    deductibles_borne_uf: UF = None
    total_damage_uf: UF = None

    claim_history: list[ClaimRow] = Field(default_factory=list)
    decisive_factors: list[Str] = Field(
        default_factory=list,
        description="What actually decided the outcome — verbatim where possible",
    )
    renewal_context: Row | None = None
    negotiation_position: list[Str] = Field(default_factory=list)
    renewal_actions_requested: list[Str] = Field(default_factory=list)
    closing_message: Str = None
