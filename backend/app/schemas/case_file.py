"""Pydantic v2 schemas for ``case_file`` — the expediente and its timeline."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.account_client import AccountClientRole
from app.models.document import DocumentCategory
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    CaseOrigin,
    CaseSection,
    CaseStage,
    ClaimRuling,
)
from app.models.policy import ClaimStatus
from app.schemas.document import DocumentRead
from app.schemas.policy import PolicyRead
from app.schemas.analytics import GroupedSummary


def _check_period_order(start: date | None, end: date | None) -> None:
    """A vigencia runs forward; ``end == start`` is as wrong as ``end < start``."""
    if start is not None and end is not None and end <= start:
        raise ValueError("period_end must be after period_start")


def _unique_positive_ids(value: list[int] | None, name: str) -> list[int] | None:
    if value is None:
        return None
    if any(item <= 0 for item in value):
        raise ValueError(f"{name} must be positive integers")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")
    return value


# --- Write models -------------------------------------------------------------

class CaseFileCreate(BaseModel):
    """Open an expediente.

    The server assigns ``reference``, ``sequence_no`` and ``version``; a client
    may not spoof any of the three.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CaseFileKind = CaseFileKind.ACCOUNT
    client_id: int | None = Field(default=None, gt=0)
    placement_id: int | None = Field(default=None, gt=0)
    policy_id: int | None = Field(default=None, gt=0)
    parent_case_file_id: int | None = Field(default=None, gt=0)
    insurance_line_id: int | None = Field(default=None, gt=0)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    stage: CaseStage | None = None
    status: CaseFileStatus = CaseFileStatus.OPEN
    owner_user_id: int | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    summary: str | None = None
    meta: dict[str, Any] | None = None

    # --- Groups & accounts (spec v3 §4.3) -----------------------------------
    # The AUTHORITATIVE validity period of the folder. The router requires
    # both for ``kind=account`` (422); post-sale kinds inherit them from
    # their policy's account, so they stay optional at the schema level.
    period_start: date | None = None
    period_end: date | None = None
    # Grouping label only; the server derives ``f"{start.year}-{end.year}"``
    # when omitted.
    period_label: str | None = Field(default=None, min_length=1, max_length=32)
    # Default: the client's group. Members must belong to it (422
    # ``client_not_in_group``).
    account_group_id: int | None = Field(default=None, gt=0)
    # Extra RUTs of the account -> ``account_client`` rows; the contratante
    # (``client_id``) is always the primary member and need not be repeated.
    client_ids: list[int] | None = None
    # ``kind=renewal`` is accepted WITHOUT ``policy_id`` when this is given:
    # the folder is a sibling in time of the source, never a child of it.
    origin_case_file_id: int | None = Field(default=None, gt=0)

    @field_validator("client_ids")
    @classmethod
    def _client_ids_unique(cls, value: list[int] | None) -> list[int] | None:
        return _unique_positive_ids(value, "client_ids")

    @model_validator(mode="after")
    def _period_order(self) -> "CaseFileCreate":
        _check_period_order(self.period_start, self.period_end)
        return self


class CaseFileUpdate(BaseModel):
    """Patch the human-editable fields. ``stage`` moves only via /transition."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    status: CaseFileStatus | None = None
    owner_user_id: int | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    summary: str | None = None
    meta: dict[str, Any] | None = None
    insurance_line_id: int | None = Field(default=None, gt=0)
    policy_id: int | None = Field(default=None, gt=0)
    parent_case_file_id: int | None = Field(default=None, gt=0)


class CaseFileTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_stage: CaseStage
    note: str | None = None


class CaseFileVersionCreate(BaseModel):
    """Clone the case at ``version + 1``, superseding the current row."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)


class CaseRenewBody(BaseModel):
    """``POST /case-files/{id}/renew`` — invoked on the ACCOUNT folder, never
    on a policy (rule 4). Opens a ``kind=renewal, origin=renewal`` sibling in
    the next vigencia under the same group with cloned placements.

    ``copy_sections`` lists the sub-expediente sections whose documents are
    copied into the new folder as NEW ``document`` rows on NEW storage keys
    (never two rows on one key). Defaults to the root (00A antecedentes).
    """

    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    period_label: str | None = Field(default=None, min_length=1, max_length=32)
    copy_sections: list[CaseSection] = Field(
        default_factory=lambda: [CaseSection.ROOT_PROSPECT]
    )
    #: Override the member set; omitted = copy the source's ``account_client`` rows.
    client_ids: list[int] | None = None

    @field_validator("client_ids")
    @classmethod
    def _client_ids_unique(cls, value: list[int] | None) -> list[int] | None:
        return _unique_positive_ids(value, "client_ids")

    @field_validator("copy_sections")
    @classmethod
    def _sections_unique(cls, value: list[CaseSection]) -> list[CaseSection]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _period_order(self) -> "CaseRenewBody":
        _check_period_order(self.period_start, self.period_end)
        return self


class CaseReperiodBody(BaseModel):
    """``POST /case-files/{id}/reperiod`` — the ONLY door for a vigencia date
    change once the folder is ``period_locked`` (rule 1). Opens a sibling
    ``kind=account, origin=period_change`` folder and closes the source with
    ``meta.closed_reason="period_change"``.
    """

    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _period_order(self) -> "CaseReperiodBody":
        _check_period_order(self.period_start, self.period_end)
        return self


class CaseClientAttach(BaseModel):
    """``POST /case-files/{id}/clients`` — add a RUT to the account.

    422 ``client_not_in_group`` when the client's group differs from the
    folder's; the contratante (``case_file.client_id``) cannot be removed.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: int = Field(gt=0)
    role: AccountClientRole = AccountClientRole.INSURED


# --- Read models --------------------------------------------------------------

class CaseFileRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reference: str | None = None
    title: str
    kind: CaseFileKind
    stage: CaseStage
    status: CaseFileStatus
    sequence_no: int
    version: int
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    # Sibling-in-time context for children / versions / origin chains.
    origin: CaseOrigin = CaseOrigin.NEW
    period_label: str | None = None


class StageEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_file_id: int
    from_stage: CaseStage | None = None
    to_stage: CaseStage
    occurred_at: datetime
    user_id: int | None = None
    note: str | None = None
    meta: dict[str, Any] | None = None


class CaseFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    client_id: int
    placement_id: int | None = None
    policy_id: int | None = None
    parent_case_file_id: int | None = None
    supersedes_case_file_id: int | None = None
    insurance_line_id: int | None = None

    kind: CaseFileKind
    stage: CaseStage
    status: CaseFileStatus
    reference: str | None = None
    title: str
    sequence_no: int
    version: int
    owner_user_id: int | None = None
    opened_at: datetime | None = None
    due_at: datetime | None = None
    closed_at: datetime | None = None
    summary: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Denormalised for the list view — filled by the router.
    client_legal_name: str | None = None
    client_rut: str | None = None
    insurance_line_name: str | None = None
    documents_count: int = 0

    # --- Groups & accounts (spec v3 §4.3, last bullet) ----------------------
    # Column-backed (read straight off the ORM row).
    account_group_id: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    period_label: str | None = None
    origin: CaseOrigin = CaseOrigin.NEW
    origin_case_file_id: int | None = None
    # Filled by the router: the group's broker-private name; whether the
    # period is frozen (a stage event beyond ``intake`` exists — rule 1); and
    # the member RUTs = ``account_client`` rows ∪ ``placement.case_file_id``
    # rows (rule 6), the contratante first.
    account_group_name: str | None = None
    period_locked: bool = False
    client_ids: list[int] = Field(default_factory=list)


class CaseFileListItem(CaseFileRead):
    pass


class CaseFilePage(BaseModel):
    items: list[CaseFileListItem]
    total: int
    page: int
    page_size: int
    pages: int


class SectionCount(BaseModel):
    section: CaseSection | None = None
    count: int


class CaseFileDetail(CaseFileRead):
    """The expediente page payload: case + refs + counts + children + timeline tip."""

    placement_status: str | None = None
    policy_number: str | None = None
    period: str | None = None
    documents_by_section: list[SectionCount] = Field(default_factory=list)
    children: list[CaseFileRef] = Field(default_factory=list)
    versions: list[CaseFileRef] = Field(default_factory=list)
    latest_stage_event: StageEventRead | None = None
    proposals_count: int = 0
    quote_requests_count: int = 0
    packs_count: int = 0
    notes_count: int = 0


class TransitionOptionRead(BaseModel):
    to_stage: CaseStage
    allowed: bool
    reason: str | None = None


class CaseFileTransitions(BaseModel):
    id: int
    kind: CaseFileKind
    stage: CaseStage
    options: list[TransitionOptionRead]
    is_terminal: bool


class CaseFileSummary(BaseModel):
    """The pipeline board: counts by kind and by stage."""

    total: int
    open: int
    by_kind: dict[str, int]
    by_stage: dict[str, int]
    by_status: dict[str, int]
    overdue: int
    grouped: GroupedSummary | None = Field(
        default=None,
        description="Buckets listos para el gráfico cuando se pide ?group_by=.",
    )


class CaseDocumentRead(BaseModel):
    """One filed document, with its Spanish label and a download URL."""

    id: int
    original_name: str
    category: DocumentCategory
    category_label: str
    section: CaseSection | None = None
    document_code: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    uploaded_by_id: int | None = None
    created_at: datetime | None = None
    url: str
    expires_in_seconds: int | None = None


class CaseDocumentSection(BaseModel):
    section: CaseSection | None = None
    label: str
    documents: list[CaseDocumentRead]


class CaseDocumentGroups(BaseModel):
    case_file_id: int
    total: int
    sections: list[CaseDocumentSection]


class TimelineEntry(BaseModel):
    """One merged row of stage events + activity + notes, newest last."""

    kind: str  # "stage" | "activity" | "note"
    occurred_at: datetime
    #: Identifier or action key only — never prose (rule 1). The label around
    #: it is the UI's ``t()`` call; see ``GroupTimelineEntry`` for the twin.
    title: str
    #: Free text the user wrote themself (a note body, a stage note).
    detail: str | None = None
    #: Enum token behind ``detail`` when there is one, for the UI to translate.
    detail_token: str | None = None
    #: Stage rows only: the two ``CaseStage`` tokens of the transition.
    from_stage: str | None = None
    to_stage: str | None = None
    user_id: int | None = None
    meta: dict[str, Any] | None = None


class CaseFileTimeline(BaseModel):
    case_file_id: int
    entries: list[TimelineEntry]


# --- History (GET /case-files/{id}/history) -----------------------------------

class OriginChainEntry(BaseModel):
    """One folder of the origin chain, oldest first: new -> renewal -> …"""

    case_file_id: int
    reference: str | None = None
    period_label: str | None = None
    origin: CaseOrigin
    stage: CaseStage


class ClaimSummary(BaseModel):
    """The prior vigencia's claims, as the renewal screen needs them —
    a projection of ``ClaimRead`` without the item tables."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    policy_id: int | None = None
    case_file_id: int | None = None
    claim_number: str | None = None
    kind: str | None = None
    status: ClaimStatus
    coverage_ruling: ClaimRuling
    event_date: date | None = None
    reported_date: date | None = None
    estimated_amount_uf: Decimal | None = None
    settled_amount_uf: Decimal | None = None
    paid_amount_uf: Decimal | None = None
    deductible_uf: Decimal | None = None


class CasePriorContext(BaseModel):
    """Read-only context from the folder this one was renewed / re-perioded
    from. ``records`` is keyed by the navigator's ``RecordFolder.key``
    (``amounts``, ``loss_history``, ``report``, ``questionnaire``, ``slip``)."""

    case_file_id: int
    records: dict[str, list[DocumentRead]] = Field(default_factory=dict)
    policies: list[PolicyRead] = Field(default_factory=list)
    claims: list[ClaimSummary] = Field(default_factory=list)
    loss_ratio_pct: Decimal | None = None


class CaseHistoryResponse(BaseModel):
    """``GET /case-files/{id}/history`` — the origin chain plus the immediate
    prior folder's records, policies and claims. ``prior`` is ``null`` for a
    folder with ``origin=new`` ("Sin vigencia anterior")."""

    case_file_id: int
    origin_chain: list[OriginChainEntry] = Field(default_factory=list)
    prior: CasePriorContext | None = None


class PendingAction(BaseModel):
    """One actionable gap in the account, for the overview Journey.

    ``code`` is an English token, ``severity`` is ``info | warning | blocker``,
    ``tab`` names the desk that owns the fix, ``reason`` is the human sentence,
    and ``count`` is how many items the gap covers (1 for a singleton).
    """

    code: str
    severity: str
    tab: str
    reason: str
    count: int = 1


class PendingActionsResponse(BaseModel):
    """``GET /case-files/{id}/pending-actions`` — the typed gap list."""

    case_file_id: int
    actions: list[PendingAction] = Field(default_factory=list)


__all__ = [
    "PendingAction",
    "PendingActionsResponse",
    "CaseFileCreate",
    "CaseFileUpdate",
    "CaseFileTransition",
    "CaseFileVersionCreate",
    "CaseRenewBody",
    "CaseReperiodBody",
    "CaseClientAttach",
    "OriginChainEntry",
    "ClaimSummary",
    "CasePriorContext",
    "CaseHistoryResponse",
    "CaseFileRef",
    "CaseFileRead",
    "CaseFileListItem",
    "CaseFilePage",
    "CaseFileDetail",
    "CaseFileSummary",
    "CaseFileTransitions",
    "TransitionOptionRead",
    "StageEventRead",
    "SectionCount",
    "CaseDocumentRead",
    "CaseDocumentSection",
    "CaseDocumentGroups",
    "TimelineEntry",
    "CaseFileTimeline",
]
