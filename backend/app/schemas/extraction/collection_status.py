"""``collection_status`` (09/10 estado de cobranza) — internal.

The living state of the billing: what was charged, what was paid late, whether
art. 528 suspended cover, and what that did to the warranties.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    EventDateTime,
    FlexDate,
    InstallmentRow,
    Int,
    PartyRef,
    PeriodBlock,
    PolicyDateTime,
    RootExtraction,
    Row,
    Str,
)

__all__ = ["CollectionStatusExtraction", "CoverageTermination", "Art528Event"]


class CoverageTermination(ExtractionModel):
    """Cover ended for non-payment — a datetime, because the hour matters."""

    terminated_at: PolicyDateTime = None
    rehabilitated_at: PolicyDateTime = None
    days_without_cover: Int = None
    rehabilitation_cost_uf: UF = None
    text: Str = Field(default=None, description="Verbatim termination wording")


class Art528Event(ExtractionModel):
    """One step of the art. 528 CCom non-payment ladder."""

    occurred_at: EventDateTime = None
    event: Str = None
    detail: Str = None
    installment_number: Int = None


class CollectionStatusExtraction(RootExtraction):
    document_variant: Str = Field(
        default=None, description="plan de pago / estado de cobranza / cartola"
    )
    as_of_date: FlexDate = None
    policy_number: Str = None
    insurer: Str = None
    account_name: Str = None
    insured: PartyRef = Field(default_factory=PartyRef)
    policy_period: PeriodBlock = Field(default_factory=PeriodBlock)

    original_gross_premium_uf: UF = None
    payment_mode: Str = None
    charge_account: Str = None
    pledge_creditor: Row | None = None

    premium_composition: list[Row] = Field(
        default_factory=list,
        description="Signed rows: base premium plus every endorsement delta",
    )
    total_period_gross_uf: UF = None
    installments: list[InstallmentRow] = Field(default_factory=list)

    situation_summary: Row | None = None
    incidents: list[Row] = Field(default_factory=list)
    coverage_termination: CoverageTermination = Field(default_factory=CoverageTermination)
    art528_events: list[Art528Event] = Field(default_factory=list)
    commission: list[Row] = Field(default_factory=list)
    warranty_status: list[Row] = Field(default_factory=list)
    active_alerts: list[Str] = Field(default_factory=list)
    aggregate_limit_control: list[Row] = Field(default_factory=list)
    roster_and_accumulation_control: list[Row] = Field(default_factory=list)
    engineering_plan_control: list[Row] = Field(default_factory=list)
    verification_cycle_milestones: list[Row] = Field(default_factory=list)
    management_note: Str = None
