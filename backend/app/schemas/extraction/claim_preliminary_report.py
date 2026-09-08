"""``claim_preliminary_report`` (11/12 pre-informe) — adjuster -> parties.

The adjuster's first ruling. Damage quantification is a per-partida table
(notified -> determined) that becomes ``claim_item`` rows.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    EventDateTime,
    ExtractionModel,
    FlexDate,
    PersonRef,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["ClaimPreliminaryReportExtraction", "DamageQuantificationRow"]


class DamageQuantificationRow(ExtractionModel):
    """One partida: what was notified vs what the adjuster determined."""

    kind: Str = Field(
        default=None,
        description="material_damage | business_interruption | expense | liability |"
        " personal_accident | recovery",
    )
    item: Str = None
    basis: Str = Field(default=None, description="Verbatim basis of the figure")
    notified_uf: UF = None
    determined_uf: UF = None
    damage_uf: UF = None
    deductible_uf: UF = None
    indemnity_uf: UF = None
    note: Str = None


class ClaimPreliminaryReportExtraction(RootExtraction):
    claim_number: Str = None
    policy_number: Str = None
    adjuster: PersonRef = Field(
        default_factory=PersonRef,
        description="name, company and CMF registry number",
    )
    appointment_date: FlexDate = None
    first_inspection_date: EventDateTime = None
    report_date: FlexDate = None
    legal_basis: Str = None
    experts_ordered: list[Str] = Field(default_factory=list)

    coverage_verification: list[Row] = Field(default_factory=list)
    preliminary_ruling: Str = Field(
        default=None, description="pending | covered | partially_covered | rejected"
    )
    warranty_analysis: list[WarrantyLine] = Field(default_factory=list)
    warranty_extension: Row | None = None
    cause_expertise: Row | None = None

    damage_quantification: list[DamageQuantificationRow] = Field(default_factory=list)
    bi_methodology: Row | None = None
    preliminary_settlement: list[Row] = Field(default_factory=list)
    advance_payment_recommendation: Row | None = None
    pledge_creditor_application: Row | None = None
    coverage_termination_effect: Row | None = None
    hourly_franchise_analysis: Row | None = Field(
        default=None,
        description="The 4-hour franchise wording decided coverage in the corpus",
    )
    total_loss_analysis: Row | None = None
    pending_diligences: list[Str] = Field(default_factory=list)
