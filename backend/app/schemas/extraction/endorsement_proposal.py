"""``endorsement_proposal`` (07A/07B/09C) — Propuesta de endoso, broker -> insurer.

Confirming it creates an ``endorsement`` with ``status=proposed``. The premium
deltas are SIGN-PRESERVING and obey the same invariant as a proposal:
``net_delta = taxable_delta + exempt_delta``, ``vat_delta = 0.19 x taxable_delta``,
``total_delta = net_delta + vat_delta``. An administrative endorsement is
all-zero — valid, and it must not trip anything.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    AmountRow,
    ExtractionModel,
    FlexDate,
    Int,
    LocationRow,
    PartyRef,
    PartyRef as PolicyholderRef,
    PeriodBlock,
    PersonRef,
    PolicyDateTime,
    RootExtraction,
    Row,
    Str,
)

__all__ = ["EndorsementProposalExtraction", "PledgeUpdate", "PolicyholderChange"]


class PledgeUpdate(ExtractionModel):
    creditor: Str = None
    rut: Str = None
    amount_uf: UF = None
    stipulation: Str = Field(default=None, description="Verbatim pledge stipulation")


class PolicyholderChange(ExtractionModel):
    from_party: PolicyholderRef = Field(default_factory=PolicyholderRef)
    to_party: PolicyholderRef = Field(default_factory=PolicyholderRef)
    reason: Str = None


class EndorsementProposalExtraction(RootExtraction):
    proposed_endorsement_number: Str = None
    endorsement_type: Str = Field(
        default=None,
        description="The capitalised verb is the signal: INCLUYE / EXCLUYE / AUMENTA…",
    )
    addressed_to: Str = None
    issuer_broker: PartyRef = Field(default_factory=PartyRef)
    proposal_date: FlexDate = None
    proposal_place: Str = None
    signer: PersonRef = Field(default_factory=PersonRef)

    policy_number: Str = None
    insured: PartyRef = Field(default_factory=PartyRef)
    policy_period: PeriodBlock = Field(default_factory=PeriodBlock)
    contractual_basis: Str = Field(
        default=None, description="The clause number this endorsement rests on"
    )

    requested_effective_at: PolicyDateTime = None
    requested_end_at: PolicyDateTime = None
    motive_text: Str = Field(default=None, description="Verbatim motive")

    state_before: Row | None = None
    added_location: LocationRow | None = None
    added_vehicle: Row | None = None
    added_workers: list[Row] = Field(default_factory=list)
    added_insured: PartyRef | None = None
    item_amounts: list[AmountRow] = Field(default_factory=list)
    bi_change_uf: UF = None
    total_added_uf: UF = None
    total_removed_uf: UF = None
    effect_table: list[Row] = Field(
        default_factory=list, description="before / after / delta rows"
    )

    endorsement_days: Int = None
    unexpired_days: Int = None
    rates_applied: Row | None = None
    additional_net_taxable_uf: UF = None
    additional_net_exempt_uf: UF = None
    additional_net_premium_uf: UF = None
    vat_uf: UF = None
    additional_gross_premium_uf: UF = None
    broker_commission_delta_uf: UF = None
    charging_instruction: Str = None

    reinstatement_calculation: Row | None = None
    pledge_update: PledgeUpdate = Field(default_factory=PledgeUpdate)
    policyholder_change: PolicyholderChange = Field(default_factory=PolicyholderChange)
    attachments: list[Str] = Field(default_factory=list)
    statements: list[Str] = Field(default_factory=list)
    broker_observation: Str = None
