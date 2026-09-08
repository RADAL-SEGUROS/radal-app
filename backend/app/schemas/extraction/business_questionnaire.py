"""``business_questionnaire`` (00B) — Ficha del negocio, insured -> broker.

The master data sheet: who the company is, where it operates, what it owns and
who drives / works there. Ten attributes are promoted to ``asset`` columns on
confirm; everything type-specific stays in ``asset.attributes`` JSON.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    Int,
    LocationRow,
    PartyRef,
    PersonRef,
    RootExtraction,
    Row,
    Str,
)

__all__ = [
    "BusinessQuestionnaireExtraction",
    "FleetRow",
    "DriverRow",
    "EmployeeRow",
    "QuestionAnswer",
]


class FleetRow(ExtractionModel):
    number: Int = None
    plate: Str = None
    vin: Str = Field(default=None, description="Chassis / VIN, verbatim — never reconciled")
    make: Str = None
    model: Str = None
    year: Int = None
    use: Str = None
    value_uf: UF = None


class DriverRow(ExtractionModel):
    name: Str = None
    rut: Str = None
    licence_class: Str = None
    licence_expiry: FlexDate = None
    birth_date: FlexDate = None
    note: Str = None


class EmployeeRow(ExtractionModel):
    name: Str = None
    rut: Str = None
    role: Str = None
    monthly_income_uf: UF = None
    capital_uf: UF = None


class QuestionAnswer(ExtractionModel):
    """One open question and its answer, both verbatim."""

    question: Str = None
    answer: Str = None
    section: Str = None


class BusinessQuestionnaireExtraction(RootExtraction):
    form_date: FlexDate = None
    insured: PartyRef = Field(default_factory=PartyRef)
    commercial_account_name: Str = Field(
        default=None,
        description="The commercial account, which may differ from the legal name",
    )
    business_activity: Str = None
    economic_activity_code: Str = None
    commercial_address: Str = None
    legal_representative: PersonRef = Field(default_factory=PersonRef)
    policy_contact: PersonRef = Field(default_factory=PersonRef)

    company_age_years: Int = None
    employee_count: Int = None
    annual_revenue_uf: UF = None
    contribution_margin_plus_fixed_costs_uf: UF = Field(
        default=None,
        description="Margen de contribución + costos fijos — the BI basis",
    )

    locations: list[LocationRow] = Field(default_factory=list)
    construction_and_protection: list[Row] = Field(default_factory=list)
    fleet: list[FleetRow] = Field(default_factory=list)
    authorized_drivers: list[DriverRow] = Field(default_factory=list)
    employee_roster: list[EmployeeRow] = Field(default_factory=list)
    risk_profiles: list[Row] = Field(default_factory=list)
    questionnaire: list[QuestionAnswer] = Field(default_factory=list)

    third_party_goods_uf: UF = None
    pledges_leasing_creditors: list[Str] = Field(default_factory=list)
    declaration_text: Str = Field(default=None, description="Verbatim declaration clause")
    signed_by: PersonRef = Field(default_factory=PersonRef)
