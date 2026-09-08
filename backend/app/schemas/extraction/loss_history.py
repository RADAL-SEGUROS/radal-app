"""``loss_history`` (00D) — Certificado de siniestralidad, insurer -> broker.

Historical claims with the loss ratio the underwriter will price on. The claims
are recorded as reported by THIS certificate; they are never reconciled against
the claims the app already knows about (spec §3.2 rule 4).
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    ClaimRow,
    ExtractionModel,
    FlexDate,
    Pct,
    PartyRef,
    RootExtraction,
    Str,
)

__all__ = ["LossHistoryExtraction", "LossTotals"]


class LossTotals(ExtractionModel):
    reported_uf: UF = None
    indemnified_uf: UF = None
    rejected_uf: UF = None
    recoveries_uf: UF = None
    reserves_uf: UF = None
    claim_count: int | None = None


class LossHistoryExtraction(RootExtraction):
    insured: PartyRef = Field(default_factory=PartyRef)
    insurance_line: Str = None
    periods_covered: list[Str] = Field(default_factory=list)
    years_covered: Str = None
    issuing_insurer: Str = None
    issue_date: FlexDate = None
    source_system: Str = None

    claims: list[ClaimRow] = Field(default_factory=list)
    totals: LossTotals = Field(default_factory=LossTotals)

    accumulated_gross_premium_uf: UF = None
    loss_ratio_pct: Pct = None
    claim_frequency: Pct = None
    average_severity_uf: UF = None
    broker_observation: Str = None
