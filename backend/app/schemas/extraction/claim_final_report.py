"""``claim_final_report`` (12/13 informe final de liquidación) — adjuster -> parties.

Closes the claim: the ruling, the final figures per partida, the deductibles
applied and the loss ratio the renewal will be argued on.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    EventDateTime,
    FlexDate,
    Int,
    Pct,
    PersonRef,
    RootExtraction,
    Row,
    Str,
)
from app.schemas.extraction.claim_preliminary_report import DamageQuantificationRow

__all__ = ["ClaimFinalReportExtraction", "SettlementBlock", "ProportionalRule"]


class SettlementBlock(ExtractionModel):
    gross_loss_uf: UF = None
    deductible_uf: UF = None
    indemnity_uf: UF = None
    paid_uf: UF = None
    payment_date: FlexDate = None
    beneficiary: Str = None
    text: Str = Field(default=None, description="Verbatim settlement wording")


class ProportionalRule(ExtractionModel):
    """Regla proporcional: applied when the sum insured was short."""

    applies: bool | None = None
    factor: Pct = None
    insured_amount_uf: UF = None
    value_at_risk_uf: UF = None
    text: Str = None


class ClaimFinalReportExtraction(RootExtraction):
    claim_number: Str = None
    adjuster: PersonRef = Field(default_factory=PersonRef)
    report_date: FlexDate = None
    report_place: Str = None
    occurrence_date: EventDateTime = None
    notice_date: EventDateTime = None
    days_since_notice: Int = None

    coverage_ruling: Str = Field(
        default=None, description="pending | covered | partially_covered | rejected"
    )
    covered_coverages: list[Str] = Field(default_factory=list)
    exclusion_ruling: Str = None
    warranty_ruling: Str = None

    final_material_damage: list[DamageQuantificationRow] = Field(default_factory=list)
    total_material_damage_uf: UF = None
    proportional_rule: ProportionalRule = Field(default_factory=ProportionalRule)
    bi_determination: Row | None = None
    deductibles_applied: list[Row] = Field(default_factory=list)
    limit_verification: list[Row] = Field(default_factory=list)
    settlement: SettlementBlock = Field(default_factory=SettlementBlock)
    ap_settlement: list[Row] = Field(default_factory=list)
    medical_commission: list[Row] = Field(default_factory=list)
    accumulation_application: Row | None = None
    rc_claim_closures: list[Row] = Field(default_factory=list)
    recall_costs: list[Row] = Field(default_factory=list)
    rc_settlement: Row | None = None
    aggregate_effect: Row | None = None
    insurer_total_cost: Row | None = None
    recovery: Row | None = None
    account_loss_ratio: Row | None = None
    risk_engineering_effect: list[Row] = Field(default_factory=list)
    prior_program_counterfactual: list[Row] = Field(
        default_factory=list,
        description="What the OLD programme would have paid — the renewal argument",
    )
    alternatives_counterfactual: list[Row] = Field(default_factory=list)
    subrogation: Row | None = None
    conclusions: list[Str] = Field(default_factory=list)
    liquidation_duration_days: Int = None
    loss_ratio_pct: Pct = None
