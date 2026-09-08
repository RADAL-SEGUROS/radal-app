"""``inspection_report`` (00E) — Informe de inspección de riesgo, third party -> broker.

Scores become ``inspection`` columns, the checklist becomes JSON, boundaries a
child table, and every category-A recommendation becomes a ``warranty`` row.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    Int,
    Pct,
    PersonRef,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["InspectionReportExtraction", "ModuleScore", "RecommendationRow"]


class ModuleScore(ExtractionModel):
    module: Str = None
    score: Pct = None
    weight_pct: Pct = None
    weighted_score: Pct = None
    comment: Str = None


class RecommendationRow(WarrantyLine):
    """One recommendation: the code threads through the entire case file."""

    type: Str = Field(default=None, description="Inmediata / programada / permanente")
    cost_uf: UF = None
    deadline: Str = Field(default=None, description="Verbatim deadline wording")


class InspectionReportExtraction(RootExtraction):
    report_folio: Str = None
    inspection_company: Str = None
    requesting_party: Str = None
    scope: Str = None
    visit_date: FlexDate = None
    report_date: FlexDate = None
    inspector: PersonRef = Field(default_factory=PersonRef)
    reference_standards: list[Str] = Field(default_factory=list)

    executive_summary: Str = None
    module_scores: list[ModuleScore] = Field(default_factory=list)
    weighted_technical_score: Pct = None
    risk_class: Str = None
    verdict: Str = None

    construction_by_location: list[Row] = Field(default_factory=list)
    fire_protection_matrix: list[Row] = Field(default_factory=list)
    combustible_load: list[Row] = Field(default_factory=list)
    theft_protection_matrix: list[Row] = Field(default_factory=list)
    exposure_scenarios: list[Row] = Field(default_factory=list)
    mfl_uf: UF = Field(default=None, description="Maximum foreseeable loss")
    pml_uf: UF = Field(default=None, description="Probable maximum loss")
    regulatory_compliance: list[Row] = Field(default_factory=list)

    recommendations: list[RecommendationRow] = Field(default_factory=list)
    reinspection_days: Int = None
    projected_score_after_recommendations: Pct = None
    inspector_conclusion: Str = None
