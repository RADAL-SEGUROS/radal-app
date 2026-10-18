"""Pydantic v2 schemas for ``policy``, ``warranty`` and the mirror-diff.

A policy inherits the proposal's money shape, so it reuses ``reconcile_money``
from ``app.schemas.proposal`` — there is one implementation of the Chilean
premium arithmetic in this codebase and this is not a second one.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    CaseStage,
    PartyRole,
    PaymentMode,
    PolicyCurrency,
    PolicyOrigin,
    TerminationReason,
    WarrantySource,
    WarrantyStatus,
)
from app.models.policy import PolicyStatus
from app.schemas.analytics import GroupedSummary
from app.schemas.catalog import LineRef, SubtypeRef
from app.schemas.utc import UtcDatetime
from app.schemas.wallclock import WallClockDatetime


def _normalize_optional_rut(value: str | None) -> str | None:
    """mod-11 validate + ``BODY-DV`` (rule 7). Blank -> None. Spanish 422."""
    if value is None or not str(value).strip():
        return None
    from app.services.identifiers import InvalidRut, validate_rut

    try:
        return validate_rut(str(value))
    except InvalidRut as exc:
        raise ValueError(f"RUT inválido: {exc}") from exc


# --- Parties (v12) ----------------------------------------------------------------

class PartyContact(BaseModel):
    """A contratante / pagador typed in the manual form: name + RUT + contact."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    rut: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)

    @field_validator("rut")
    @classmethod
    def _rut(cls, value: str | None) -> str | None:
        return _normalize_optional_rut(value)


class PartyInput(PartyContact):
    """One row of ``PUT /policies/{id}/parties``."""

    role: PartyRole
    client_id: int | None = Field(default=None, gt=0)
    beneficiary_kind: str | None = Field(default=None, max_length=32)
    share_pct: Decimal | None = Field(default=None, ge=0, le=100)
    is_primary: bool = False
    notify_collections: bool = True


class PartiesReplace(BaseModel):
    """Replace the policy's parties wholesale: exactly one ``policyholder``,
    at least one ``insured``, at most one ``payer``."""

    model_config = ConfigDict(extra="forbid")

    parties: list[PartyInput] = Field(min_length=1)


class PartyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: PartyRole
    client_id: int | None = None
    name: str
    rut: str | None = None
    email: str | None = None
    phone: str | None = None
    beneficiary_kind: str | None = None
    share_pct: Decimal | None = None
    is_primary: bool = False
    notify_collections: bool = True


class CollectionSummaryRead(BaseModel):
    """The cobranza line of a policy row: next cuota, arrears and level."""

    plan_id: int | None = None
    state: str = "no_plan"  # no_plan | overdue | due_soon | ok
    next_due_date: date | None = None
    next_due_amount_uf: Decimal | None = None
    overdue_count: int = 0
    overdue_uf: Decimal = Decimal("0")
    level: str | None = None  # native | assisted | record
    level_reason: str | None = None
    installments_count: int = 0
    paid_count: int = 0


class OperabilityRead(BaseModel):
    """One feature of the policy and whether its data is complete (R38)."""

    feature: str
    enabled: bool
    reason: str | None = None
    missing: list[str] = Field(default_factory=list)


class PolicyJourneyRead(BaseModel):
    """Where the policy sits in its colocación: entered vs skipped stages."""

    case_file_id: int | None = None
    group_id: int | None = None
    stage: CaseStage | None = None
    entered_stages: list[CaseStage] = Field(default_factory=list)
    skipped_stages: list[CaseStage] = Field(default_factory=list)


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

    # Required on the legacy path; OPTIONAL on the v12 manual path (vía Manual:
    # "vacío solo en vía Manual") — the server then stores a placeholder.
    policy_number: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    period_start_at: WallClockDatetime | None = None
    period_end_at: WallClockDatetime | None = None
    issued_at: date | None = None
    status: PolicyStatus = PolicyStatus.DRAFT

    # --- v12 manual entry (vía Manual, 02 §2) --------------------------------
    # Giving ``insurance_subtype_id`` (or ``insured_rut``) switches the request
    # to the manual-entry path: five mandatory data, colocación on demand,
    # parties, ``origin=manual``, 409 on a duplicate number.
    insurance_subtype_id: int | None = Field(default=None, gt=0)
    insured_rut: str | None = Field(default=None, max_length=20)
    insured_name: str | None = Field(default=None, min_length=1, max_length=255)
    policyholder: PartyContact | None = None
    payer: PartyContact | None = None
    payment_method: PaymentMode | None = None
    currency: PolicyCurrency = PolicyCurrency.UF
    is_provisional: bool = False

    @field_validator("insured_rut")
    @classmethod
    def _insured_rut(cls, value: str | None) -> str | None:
        return _normalize_optional_rut(value)

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
    period_start_at: WallClockDatetime | None = None
    period_end_at: WallClockDatetime | None = None
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

    # --- v12 ------------------------------------------------------------------
    insurance_subtype_id: int | None = Field(default=None, gt=0)
    is_provisional: bool | None = None
    currency: PolicyCurrency | None = None
    payment_method: PaymentMode | None = None
    terminated_on: date | None = None
    termination_reason: TerminationReason | None = None
    #: "Corregir dato": REQUIRED when the PATCH changes a field that already
    #: holds a value (R19). Filling an empty field ("Completar") needs none.
    correction_reason: str | None = Field(default=None, min_length=1, max_length=1000)


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
    #: v12 "Subir póliza" from the Pólizas list (no folder yet): the insured
    #: (else resolved from the RUT the reader extracts) and the subtipo the
    #: colocación is located/created for (02 §2, colocación on demand).
    client_id: int | None = Field(default=None, gt=0)
    insurance_subtype_id: int | None = Field(default=None, gt=0)
    #: The broker's confirmation (2b.5 "Crear ahí mismo, prellenado") that the
    #: insured RUT the reader extracted, unknown to the workspace, becomes a
    #: new client. Without it an unknown RUT is a 422 ``insured_unresolved``
    #: carrying the suggestion — the reader never creates a client on its own.
    create_client: bool = False
    #: The broker's explicit choice of the issuing insurer when the PDF states
    #: no rut/cmf (real policies print the insurer only by name, and an insurer
    #: is never matched by name). Sent on the retry after a 422
    #: ``insurer_unresolved``; an id the broker cannot see is a 422
    #: ``insurer_not_found``. Ignored when the PDF does state a rut/cmf.
    insurer_id: int | None = Field(default=None, gt=0)


class PolicyUploadResponse(BaseModel):
    """The committed policy plus the core verdict and provenance."""

    policy: "PolicyRead"
    extraction_id: int
    is_core_valid: bool
    core_validation: dict[str, Any] | None = None
    overridden: bool = False
    warnings: list[str] = Field(default_factory=list)
    #: Fast reading (docs/v12/11): the upload ran only the CORE read;
    #: ``{"status": "pending"}`` tells the SPA to fire
    #: ``POST /policies/{id}/technical-read`` next.
    technical_read: dict[str, Any] | None = None


class PolicyFromProposal(BaseModel):
    """Build the draft policy from the accepted proposal — the mirror baseline."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: int = Field(gt=0)
    policy_number: str | None = Field(default=None, max_length=64)
    source_document_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    period_start_at: WallClockDatetime | None = None
    period_end_at: WallClockDatetime | None = None


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
    period_start_at: WallClockDatetime | None = None
    period_end_at: WallClockDatetime | None = None
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
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None

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

    # --- v12: policies are the main view (02 §2) ---------------------------
    # Column-backed.
    insurance_subtype_id: int | None = None
    origin: PolicyOrigin | None = None
    has_source_document: bool = False
    is_provisional: bool = False
    currency: PolicyCurrency = PolicyCurrency.UF
    payment_method: PaymentMode | None = None
    terminated_on: date | None = None
    termination_reason: TerminationReason | None = None
    # Filled by the router.
    computed_status: str | None = None  # upcoming | active | expired | cancelled
    subtype: SubtypeRef | None = None
    line: LineRef | None = None
    insured_name: str | None = None
    insured_rut: str | None = None
    group_id: int | None = None
    has_policy_number: bool = True
    collection_summary: CollectionSummaryRead | None = None
    missing: list[str] = Field(default_factory=list)


class PolicyDetail(PolicyRead):
    """``GET /policies/{id}`` — the ficha: row + parties, "Falta: X", journey."""

    parties: list[PartyRead] = Field(default_factory=list)
    operability: list[OperabilityRead] = Field(default_factory=list)
    journey: PolicyJourneyRead | None = None
    #: Non-blocking advice from the last write (e.g. R5 possible duplicate).
    warnings: list[str] = Field(default_factory=list)


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
    #: v12: policies whose COMPUTED status is ``active`` (dates + cancellation).
    active_count: int
    total_premium_uf: Decimal
    expiring_within_60_days: int
    # --- v12 cards (02 §2) -----------------------------------------------------
    active_premium_uf: Decimal = Decimal("0")
    expiring_90: int = 0
    no_plan: int = 0
    overdue_installments: int = 0
    overdue_uf: Decimal = Decimal("0")
    without_source_document: int = 0
    provisional: int = 0
    grouped: GroupedSummary | None = Field(
        default=None,
        description="Buckets listos para el gráfico cuando se pide ?group_by=.",
    )


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
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


__all__ = [
    "PolicyCreate",
    "PolicyUpdate",
    "PolicyFromProposal",
    "PolicyUploadRequest",
    "PolicyUploadResponse",
    "PolicyRead",
    "PolicyDetail",
    "PolicyPage",
    "PartyContact",
    "PartyInput",
    "PartiesReplace",
    "PartyRead",
    "CollectionSummaryRead",
    "OperabilityRead",
    "PolicyJourneyRead",
    "PolicySummary",
    "MirrorDiffRowRead",
    "MirrorDiffRead",
    "MirrorDiffQueue",
    "MirrorDiffQueueResult",
    "WarrantyCreate",
    "WarrantyUpdate",
    "WarrantyRead",
]
