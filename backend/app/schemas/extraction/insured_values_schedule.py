"""``insured_values_schedule`` (00C) — Montos asegurados, broker internal -> insurers.

An xlsx of value matrices. The partida names are NOT a fixed vocabulary, so the
matrix is a list of open rows rather than a closed set of columns.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    Int,
    Pct,
    RootExtraction,
    Row,
    Str,
)

__all__ = ["InsuredValuesScheduleExtraction", "BusinessInterruptionBlock", "Underinsurance"]


class BusinessInterruptionBlock(ExtractionModel):
    uf: UF = None
    basis: Str = Field(default=None, description="Verbatim basis wording")
    months: Int = Field(default=None, description="Indemnity period in months")
    deductible: Str = Field(default=None, description="Verbatim BI deductible / franchise")


class Underinsurance(ExtractionModel):
    amount_uf: UF = None
    pct: Pct = None
    note: Str = None


class InsuredValuesScheduleExtraction(RootExtraction):
    sheet_titles: list[Str] = Field(default_factory=list)
    insurance_line: Str = None
    currency: Str = Field(default=None, description="UF unless the sheet says otherwise")
    valuation_basis: Str = Field(
        default=None, description="Reposición / valor real — verbatim"
    )
    valuation_date: FlexDate = None

    value_matrix: list[Row] = Field(
        default_factory=list,
        description="One row per partida: label = partida, amount_uf = its value",
    )
    total_physical_assets_uf: UF = None
    business_interruption: BusinessInterruptionBlock = Field(
        default_factory=BusinessInterruptionBlock
    )
    program_total_insured_value_uf: UF = None

    asset_inventory: list[Row] = Field(default_factory=list)
    stock_by_month: list[Row] = Field(default_factory=list)
    current_vs_valued_comparison: list[Row] = Field(default_factory=list)
    underinsurance: Underinsurance = Field(default_factory=Underinsurance)
    proportional_rule_factor: Pct = None

    fleet_schedule: list[Row] = Field(default_factory=list)
    liability_scenarios: list[Row] = Field(default_factory=list)
    limit_dimensioning_conclusion: Row | None = None
    personnel_capitals: list[Row] = Field(default_factory=list)
    cumulus_analysis: list[Row] = Field(default_factory=list)
    secured_creditor_amount_uf: UF = None
