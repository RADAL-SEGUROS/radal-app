"""``insurer_quotation`` (03/04/05/05B) — the insurer's quotation to the broker.

Also the target of the DEPRECATED ``proposal`` category: the registry maps both
to this spec, which is what keeps ``pages/proposals/upload.tsx`` and the existing
proposal tests working unchanged.

That legacy contract is why the money and cover-period fields carry the
``proposal``-shaped names (``taxable_premium_uf``, ``coverage_start`` …) as the
CANONICAL names, with the spec's names accepted as validation aliases. The
service converts a payload of this shape into the legacy ``ProposalSuggestion``
without a lossy remap.
"""
from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator

from app.schemas.extraction.common import (
    UF,
    AmountRow,
    CoverageLine,
    DeductibleLine,
    ExclusionLine,
    FlexDate,
    InsurerRefX,
    Int,
    PartyRef,
    PolicyDateTime,
    Pct,
    Rate,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["InsurerQuotationExtraction", "QuotationCoverage"]


class QuotationCoverage(CoverageLine):
    """A coverage line as quoted: what was asked for vs what is offered.

    ``text`` is the legacy field the proposal comparator aligns on; it is
    back-filled from the name and the offered condition when the model only
    reported the columns.
    """

    kind: str = Field(default="coverage", description="coverage | exclusion")
    sort_order: Int = None

    @model_validator(mode="after")
    def _fill_text(self) -> "QuotationCoverage":
        if not self.text:
            parts = [p for p in (self.name, self.offered_condition or self.requested_limit) if p]
            if parts:
                object.__setattr__(self, "text", " — ".join(parts))
        return self


class InsurerQuotationExtraction(RootExtraction):
    """One insurer quotation (cotización) as issued to the broker."""

    quotation_number: Str = Field(
        default=None, description="Número de cotización, verbatim"
    )
    issue_date: FlexDate = None
    received_at: FlexDate = Field(
        default=None, description="Date the broker received it, when stated"
    )

    insurer: InsurerRefX = Field(
        default_factory=InsurerRefX,
        description="The issuing company: rut and CMF code matter, the name never matches",
    )
    policyholder: PartyRef = Field(default_factory=PartyRef)

    insurance_line: Str = None
    # ``modality`` is the legacy column name for the cover mode.
    modality: Str = Field(
        default=None,
        validation_alias=AliasChoices("modality", "cover_mode", "coverage_modality"),
        description="Cobertura: todo riesgo / nominada",
    )
    activity_classification: Str = None
    policy_conditions_code: Str = Field(
        default=None, description="POL/CAD código de condiciones CMF"
    )
    indemnity_limit: Str = Field(default=None, description="Verbatim limit wording")

    # --- Cover period: datetimes are the contract, dates are the legacy columns
    period_start_at: PolicyDateTime = None
    period_end_at: PolicyDateTime = None
    coverage_start: FlexDate = None
    coverage_end: FlexDate = None
    validity_business_days: Int = Field(
        default=None,
        validation_alias=AliasChoices("validity_business_days", "validity_days"),
        description="Vigencia de la oferta en días hábiles",
    )

    # --- Insured values ------------------------------------------------------
    accepted_values_by_location: list[Row] = Field(default_factory=list)
    total_accepted_uf: UF = None

    # --- Terms ---------------------------------------------------------------
    coverages: list[QuotationCoverage] = Field(default_factory=list)
    sublimit_application_rules: list[Str] = Field(default_factory=list)
    deductibles: list[DeductibleLine] = Field(default_factory=list)
    dual_deductible: Row | None = Field(
        default=None, description="The two-tier deductible mechanism, when offered"
    )
    exclusions: list[ExclusionLine] = Field(default_factory=list)
    warranties: list[WarrantyLine] = Field(default_factory=list)

    # --- Money (UF) — net = taxable + exempt; vat = 0.19 x taxable ------------
    premium_by_item: list[AmountRow] = Field(default_factory=list)
    taxable_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("taxable_premium_uf", "net_premium_taxable_uf"),
    )
    exempt_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("exempt_premium_uf", "net_premium_exempt_uf"),
    )
    net_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("net_premium_uf", "net_premium_total_uf"),
    )
    vat_uf: UF = None
    total_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("total_premium_uf", "gross_premium_uf"),
    )

    taxable_rate_permille: Rate = None
    exempt_rate_permille: Rate = None
    comprehensive_rate_permille: Rate = Field(
        default=None,
        validation_alias=AliasChoices("comprehensive_rate_permille", "average_rate"),
    )
    commission_pct: Pct = Field(
        default=None,
        validation_alias=AliasChoices("commission_pct", "broker_commission_pct"),
    )
    commission_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("commission_uf", "broker_commission_uf"),
    )

    # --- Underwriting --------------------------------------------------------
    inspection_acceptance: Str = None
    underwriting_observations: list[Str] = Field(default_factory=list)
    payment_plan_offered: Str = None
    notes: Str = None

    @model_validator(mode="after")
    def _mirror_period(self) -> "InsurerQuotationExtraction":
        """Keep the datetime pair and the legacy date pair in step.

        Both are recorded because the contract is a datetime (noon convention)
        while ``proposal.coverage_start`` / ``coverage_end`` are Date columns.
        """
        if self.coverage_start is None and self.period_start_at is not None:
            object.__setattr__(self, "coverage_start", self.period_start_at.date())
        if self.coverage_end is None and self.period_end_at is not None:
            object.__setattr__(self, "coverage_end", self.period_end_at.date())
        return self
