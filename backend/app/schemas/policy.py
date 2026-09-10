"""Pydantic v2 schemas for ``policy``, ``warranty`` and the mirror-diff.

A policy inherits the proposal's money shape, so it reuses ``reconcile_money``
from ``app.schemas.proposal`` — there is one implementation of the Chilean
premium arithmetic in this codebase and this is not a second one.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import WarrantySource, WarrantyStatus
from app.models.policy import PolicyStatus


# --- Policy --------------------------------------------------------------------

class PolicyMoneyMixin(BaseModel):
    insured_amount_uf: Decimal | None = Field(default=None, ge=0)
    taxable_premium_uf: Decimal | None = Field(default=None, ge=0)
    exempt_premium_uf: Decimal | None = Field(default=None, ge=0)
    net_premium_uf: Decimal | None = Field(default=None, ge=0)
    vat_uf: Decimal | None = Field(default=None, ge=0)
    total_premium_uf: Decimal | None = Field(default=None, ge=0)
    commission_pct: Decimal | None = Field(default=None, ge=0, le=100)


class PolicyCreate(PolicyMoneyMixin):
    model_config = ConfigDict(extra="forbid")

    client_id: int | None = Field(default=None, gt=0)
    insurer_id: int = Field(gt=0)
    asset_id: int | None = Field(default=None, gt=0)
    placement_id: int | None = Field(default=None, gt=0)
    proposal_id: int | None = Field(default=None, gt=0)
    insurance_line_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    source_document_id: int | None = Field(default=None, gt=0)
    renews_policy_id: int | None = Field(default=None, gt=0)

    policy_number: str = Field(min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    period_start_at: datetime | None = None
    period_end_at: datetime | None = None
    issued_at: date | None = None
    status: PolicyStatus = PolicyStatus.DRAFT

    cover_mode: str | None = Field(default=None, max_length=64)
    cmf_policy_code: str | None = Field(default=None, max_length=32)
    insured_amount_semantics: str | None = Field(default=None, max_length=255)
    aggregate_limit_uf: Decimal | None = Field(default=None, ge=0)
    average_rate_permille: Decimal | None = Field(default=None, ge=0)
    indemnity_limit: str | None = None

    deductibles: dict[str, Any] | None = None
    notes: str | None = None


class PolicyUpdate(PolicyMoneyMixin):
    model_config = ConfigDict(extra="forbid")

    insurer_id: int | None = Field(default=None, gt=0)
    asset_id: int | None = Field(default=None, gt=0)
    placement_id: int | None = Field(default=None, gt=0)
    insurance_line_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    source_document_id: int | None = Field(default=None, gt=0)
    renews_policy_id: int | None = Field(default=None, gt=0)

    policy_number: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    period_start_at: datetime | None = None
    period_end_at: datetime | None = None
    issued_at: date | None = None
    status: PolicyStatus | None = None

    cover_mode: str | None = Field(default=None, max_length=64)
    cmf_policy_code: str | None = Field(default=None, max_length=32)
    insured_amount_semantics: str | None = Field(default=None, max_length=255)
    aggregate_limit_uf: Decimal | None = Field(default=None, ge=0)
    average_rate_permille: Decimal | None = Field(default=None, ge=0)
    indemnity_limit: str | None = None

    deductibles: dict[str, Any] | None = None
    notes: str | None = None


class PolicyUploadRequest(BaseModel):
    """``POST /policies/upload`` — validate-then-dynamic policy ingestion.

    Point at an already-filed ``document``; the server runs the context-aware
    POLICY extraction, checks the fixed minimal core and (unless ``override``)
    refuses a file that does not look like a policy with a 422 ``not_a_policy``.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: int = Field(gt=0)
    #: Attach the document to this account folder when it carries none yet.
    case_file_id: int | None = Field(default=None, gt=0)
    #: Force the commit even when the fixed core is incomplete (the broker's
    #: confirmed decision — "yes, register it anyway").
    override: bool = False


class PolicyUploadResponse(BaseModel):
    """The committed policy plus the core verdict and provenance."""

    policy: "PolicyRead"
    extraction_id: int
    is_core_valid: bool
    core_validation: dict[str, Any] | None = None
    overridden: bool = False
    warnings: list[str] = Field(default_factory=list)


class PolicyFromProposal(BaseModel):
    """Build the draft policy from the accepted proposal — the mirror baseline."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: int = Field(gt=0)
    policy_number: str | None = Field(default=None, max_length=64)
    source_document_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    period_start_at: datetime | None = None
    period_end_at: datetime | None = None


class PolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    client_id: int
    asset_id: int | None = None
    placement_id: int | None = None
    proposal_id: int | None = None
    insurer_id: int
    insurance_line_id: int | None = None
    case_file_id: int | None = None
    source_document_id: int | None = None
    renews_policy_id: int | None = None

    policy_number: str
    start_date: date | None = None
    end_date: date | None = None
    period_start_at: datetime | None = None
    period_end_at: datetime | None = None
    issued_at: date | None = None
    status: PolicyStatus

    cover_mode: str | None = None
    cmf_policy_code: str | None = None
    insured_amount_semantics: str | None = None
    aggregate_limit_uf: Decimal | None = None
    average_rate_permille: Decimal | None = None
    indemnity_limit: str | None = None

    insured_amount_uf: Decimal | None = None
    taxable_premium_uf: Decimal | None = None
    exempt_premium_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    total_premium_uf: Decimal | None = None
    commission_pct: Decimal | None = None

    deductibles: dict[str, Any] | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # v8 dynamic policy: the FULL confirmed parse (vehicles, drivers, workers,
    # sublimits, clauses…) plus the fixed-core verdict. Typed money/vigencia
    # columns above stay the source; ``payload`` is the remainder + a snapshot.
    payload: dict[str, Any] | None = None
    is_core_valid: bool | None = None
    core_validation: dict[str, Any] | None = None
    extraction_id: int | None = None

    # Denormalised for the list view.
    insurer_name: str | None = None
    client_legal_name: str | None = None
    endorsements_count: int = 0
    warranties_count: int = 0
    claims_count: int = 0


class PolicyPage(BaseModel):
    items: list[PolicyRead]
    total: int
    limit: int
    offset: int


# ``PolicyUploadResponse`` forward-references ``PolicyRead`` (defined above it).
PolicyUploadResponse.model_rebuild()


class PolicySummary(BaseModel):
    """The portfolio board: status split, live premium, and the renewal clock.

    ``total_premium_uf`` sums only ACTIVE policies — that is the premium the
    broker is actually administering — and ``expiring_within_60_days`` counts
    active policies whose ``end_date`` falls inside the next 60 days.
    """

    total: int
    by_status: dict[str, int]
    active_count: int
    total_premium_uf: Decimal
    expiring_within_60_days: int


# --- Mirror-diff ----------------------------------------------------------------

class MirrorDiffRowRead(BaseModel):
    """One field where the issued policy departs from the issuance proposal."""

    path: str
    expected: Any = None
    found: Any = None
    severity: str
    suggested_endorsement_kind: str | None = None


class MirrorDiffRead(BaseModel):
    policy_id: int
    proposal_extraction_id: int | None = None
    policy_extraction_id: int | None = None
    # Which payload served each side: "confirmed" (a human signed off — the
    # spec §7.6 input) or "latest_succeeded" (documented fallback while the
    # review is pending). None when that side is missing entirely.
    proposal_source: str | None = None
    policy_source: str | None = None
    missing_sources: list[str] = Field(default_factory=list)
    rows: list[MirrorDiffRowRead] = Field(default_factory=list)


class MirrorDiffQueue(BaseModel):
    """Turn selected diff rows into ``endorsement(status=draft)``."""

    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(
        default_factory=list,
        description="Diff paths to queue. Empty = every row of the current diff.",
    )
    case_file_id: int | None = Field(default=None, gt=0)


class MirrorDiffQueueResult(BaseModel):
    policy_id: int
    queued: int
    endorsement_ids: list[int] = Field(default_factory=list)


# --- Warranties -----------------------------------------------------------------

class WarrantyBase(BaseModel):
    code: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, max_length=255)
    requirement: str | None = None
    source: WarrantySource = WarrantySource.UNDERWRITING_WARRANTY
    category: str | None = Field(default=None, max_length=4)
    deadline_days: int | None = Field(default=None, ge=0)
    due_date: date | None = None
    is_permanent: bool = False
    is_suspensive: bool = False
    status: WarrantyStatus = WarrantyStatus.PENDING
    completed_on: date | None = None
    verification: str | None = None
    budget_uf: Decimal | None = Field(default=None, ge=0)
    actual_cost_uf: Decimal | None = Field(default=None, ge=0)
    evidence_document_id: int | None = Field(default=None, gt=0)
    sort_order: int = 0
    case_file_id: int | None = Field(default=None, gt=0)


class WarrantyCreate(WarrantyBase):
    model_config = ConfigDict(extra="forbid")


class WarrantyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, max_length=255)
    requirement: str | None = None
    source: WarrantySource | None = None
    category: str | None = Field(default=None, max_length=4)
    deadline_days: int | None = Field(default=None, ge=0)
    due_date: date | None = None
    is_permanent: bool | None = None
    is_suspensive: bool | None = None
    status: WarrantyStatus | None = None
    completed_on: date | None = None
    verification: str | None = None
    budget_uf: Decimal | None = Field(default=None, ge=0)
    actual_cost_uf: Decimal | None = Field(default=None, ge=0)
    evidence_document_id: int | None = Field(default=None, gt=0)
    sort_order: int | None = None
    case_file_id: int | None = Field(default=None, gt=0)


class WarrantyRead(WarrantyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    policy_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "PolicyCreate",
    "PolicyUpdate",
    "PolicyFromProposal",
    "PolicyUploadRequest",
    "PolicyUploadResponse",
    "PolicyRead",
    "PolicyPage",
    "PolicySummary",
    "MirrorDiffRowRead",
    "MirrorDiffRead",
    "MirrorDiffQueue",
    "MirrorDiffQueueResult",
    "WarrantyCreate",
    "WarrantyUpdate",
    "WarrantyRead",
]
