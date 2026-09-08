"""``prospect_request`` (00A) — Solicitud del Prospecto, insured -> broker.

The letter that opens the expediente. It carries the identity of the insured,
who signed it, what happened that made them shop the market, and what they are
asking the broker to do.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    FlexDate,
    Int,
    PartyRef,
    PersonRef,
    RootExtraction,
    Str,
)
from app.schemas.extraction.common import ExtractionModel

__all__ = ["ProspectRequestExtraction", "CurrentPolicyRef", "TriggerEvent"]


class CurrentPolicyRef(ExtractionModel):
    """The policy in force today, as the prospect describes it."""

    number: Str = None
    insurer: Str = None
    endorsement_number: Str = None
    expiry_date: FlexDate = None
    insured_amount_uf: UF = None


class TriggerEvent(ExtractionModel):
    """What made them move: a loss, a rate increase, a declination."""

    description: Str = None
    date: FlexDate = None
    loss_uf: UF = None
    outcome: Str = None


class ProspectRequestExtraction(RootExtraction):
    letter_date: FlexDate = None
    letter_city: Str = None
    received_by_broker_date: FlexDate = None

    insured: PartyRef = Field(
        default_factory=PartyRef,
        description="legal_name, rut and trade_name of the prospect",
    )
    signer: PersonRef = Field(default_factory=PersonRef)
    broker_legal_name: Str = None

    insurance_line_hint: Str = Field(
        default=None, description="The line they asked about, in the letter's own words"
    )
    current_policy: CurrentPolicyRef = Field(default_factory=CurrentPolicyRef)
    current_incumbent_broker: Str = Field(
        default=None,
        description="Prior broker named in the letter — TEXT ONLY, never a tenant",
    )
    declared_insured_amount_uf: UF = None

    trigger_event: TriggerEvent = Field(default_factory=TriggerEvent)
    concerns: list[Str] = Field(default_factory=list)
    requests: list[Str] = Field(default_factory=list)
    minimum_insurers_requested: Int = None
    new_locations_declared: list[Str] = Field(default_factory=list)
    attachments: list[Str] = Field(default_factory=list)
    site_visit_offered: bool | None = None
