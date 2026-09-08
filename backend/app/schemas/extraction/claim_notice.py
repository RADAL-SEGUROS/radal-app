"""``claim_notice`` (10/11 denuncio) — broker -> insurer.

The occurrence is a DATETIME: the hourly franchise on business interruption and
the notice deadline both count from the hour, not the day.
"""
from __future__ import annotations

from pydantic import Field

from app.schemas.extraction.common import (
    UF,
    AmountRow,
    EventDateTime,
    ExtractionModel,
    Int,
    LocationRow,
    PartyRef,
    PersonRef,
    RootExtraction,
    Row,
    Str,
)

__all__ = ["ClaimNoticeExtraction", "TimelineEntry", "DeclaredDamage"]


class TimelineEntry(ExtractionModel):
    occurred_at: EventDateTime = None
    event: Str = None
    source: Str = None


class DeclaredDamage(AmountRow):
    coverage: Str = None
    location: Str = None


class ClaimNoticeExtraction(RootExtraction):
    claim_number: Str = None
    policy_number: Str = None
    insurer: Str = None
    insured: PartyRef = Field(default_factory=PartyRef)
    affected_location: LocationRow | None = None

    occurrence_at: EventDateTime = None
    notice_at: EventDateTime = None
    knowledge_date: EventDateTime = None
    notice_deadline_days: Int = None
    claim_modality: Str = None

    narrative_text: Str = Field(default=None, description="Verbatim narrative")
    timeline: list[TimelineEntry] = Field(default_factory=list)
    probable_cause_text: Str = None
    mitigating_elements: list[Str] = Field(default_factory=list)

    declared_damages: list[DeclaredDamage] = Field(default_factory=list)
    subtotal_material_damage_uf: UF = None
    debris_removal_uf: UF = None
    professional_fees_uf: UF = None
    total_estimated_uf: UF = None

    operational_situation: Row | None = None
    invoked_coverages: list[Str] = Field(
        default_factory=list,
        description="Resolve each to the policy's coverage_item number",
    )
    affected_workers: list[Row] = Field(default_factory=list)
    accumulation_analysis: Row | None = None
    claimants: list[Row] = Field(default_factory=list)
    product_recall: Row | None = None
    vehicle: Row | None = None
    driver: PersonRef | None = None
    third_parties: list[Row] = Field(default_factory=list)
    police_report: Row | None = None
    mitigation_measures: list[Str] = Field(default_factory=list)
    attachments: list[Str] = Field(default_factory=list)
    broker_requests: list[Str] = Field(default_factory=list)
