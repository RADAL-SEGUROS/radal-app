"""``compliance_notice`` (09A) — Aviso de cumplimiento de garantías, broker -> insurer.

"The measures are done, come and re-inspect." Confirming it closes the matching
``warranty`` rows and opens the expected ``endorsement(status=draft)`` that
should follow (typically a deductible reduction).
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    FlexDate,
    PartyRef,
    Pct,
    PersonRef,
    RootExtraction,
    Row,
    Str,
    WarrantyLine,
)

__all__ = ["ComplianceNoticeExtraction"]


class ComplianceNoticeExtraction(RootExtraction):
    addressed_to: Str = None
    issuer_broker: PartyRef = Field(default_factory=PartyRef)
    policy_number: Str = None
    insured: PartyRef = Field(default_factory=PartyRef)
    subject: Str = None
    contractual_basis: Str = None
    notice_date: FlexDate = None
    reinspection_deadline: FlexDate = None

    measures_completed: list[WarrantyLine] = Field(default_factory=list)
    full_plan_status: list[Row] = Field(default_factory=list)
    total_budget_uf: UF = None
    total_actual_uf: UF = None
    overrun_pct: Pct = None

    requests: list[Str] = Field(default_factory=list)
    signer: PersonRef = Field(default_factory=PersonRef)
