"""Pydantic v2 schemas for the navigator — the two sidebars' data.

Contract: docs/v3-groups-accounts-spec.md §4.2, reproduced node for node.

* ``NavigatorResponse`` — ``GET /navigator`` (the main rail): up to 8 groups
  ordered by ``latest_period_start`` desc, the 5 most recently updated visible
  account/renewal cases, and ``record_folders`` — the SERVER-OWNED mapping
  from the team's ANTECEDENTES leaves (MONTOS, SINIESTRALIDAD, INFORME,
  CUESTIONARIO, SLIP DE T&C) onto ``DocumentCategory``. The UI never
  hardcodes that mapping.
* ``GroupTree`` — ``GET /account-groups/{id}/tree`` (the contextual rail):
  vigencia -> ramo -> account / policies (with post-sale children by kind) /
  renewal. Vigencia is a LABEL over per-account full dates (spec §1), so every
  ramo and policy node carries its own ``period_start``/``period_end``.

Every count is over cases visible to the caller; child nodes under a policy
are filtered by the caller's ``Endorsements|Collections|Claims View`` grants.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.account_group import AccountGroupStatus
from app.models.document import DocumentCategory
from app.models.enums import CaseFileKind, CaseFileStatus, CaseOrigin, CaseStage
from app.models.policy import PolicyStatus

#: The five ANTECEDENTES folder keys, in the Excel's order (G8-G12).
RecordFolderKey = Literal["amounts", "loss_history", "report", "questionnaire", "slip"]

#: Why a renewal cannot be started from a ramo node (``null`` when allowed).
RenewalReason = Literal["account_not_active", "already_renewed", "period_open"]


# --- Record folders -----------------------------------------------------------

class RecordFolder(BaseModel):
    key: RecordFolderKey
    categories: list[DocumentCategory]


#: The server-owned ANTECEDENTES mapping (spec §4.2). ``services/navigator.py``
#: returns exactly this; ``record_counts`` on an account node is keyed by
#: ``RecordFolder.key``. Every category listed exists in ``DocumentCategory``.
RECORD_FOLDERS: tuple[RecordFolder, ...] = (
    RecordFolder(
        key="amounts",
        categories=[
            DocumentCategory.INSURED_VALUES_SCHEDULE,
            DocumentCategory.INSURED_AMOUNTS,
            DocumentCategory.VALUATION,
        ],
    ),
    RecordFolder(
        key="loss_history",
        categories=[DocumentCategory.LOSS_HISTORY, DocumentCategory.CLAIMS_HISTORY],
    ),
    RecordFolder(
        key="report",
        categories=[
            DocumentCategory.INSPECTION_REPORT,
            DocumentCategory.RISK_ENGINEERING_PLAN,
        ],
    ),
    RecordFolder(
        key="questionnaire",
        categories=[
            DocumentCategory.BUSINESS_QUESTIONNAIRE,
            DocumentCategory.PROSPECT_REQUEST,
        ],
    ),
    RecordFolder(
        key="slip",
        categories=[DocumentCategory.TECHNICAL_BRIEF, DocumentCategory.SUBMISSION_LETTER],
    ),
)

#: ``DocumentCategory -> folder key`` — the inverse, for counting.
RECORD_FOLDER_BY_CATEGORY: dict[DocumentCategory, str] = {
    category: folder.key for folder in RECORD_FOLDERS for category in folder.categories
}


# --- GET /navigator -----------------------------------------------------------

class NavigatorPeriod(BaseModel):
    label: str
    accounts_count: int = 0


class NavigatorGroup(BaseModel):
    id: int
    name: str
    slug: str
    accounts_count: int = 0
    open_count: int = 0
    latest_period_label: str | None = None
    latest_period_start: date | None = None
    #: Up to 3 periods, newest first.
    periods: list[NavigatorPeriod] = Field(default_factory=list)


class NavigatorRecentCase(BaseModel):
    case_file_id: int
    reference: str | None = None
    title: str
    group_id: int | None = None
    period_label: str | None = None
    line_name: str | None = None
    stage: CaseStage
    origin: CaseOrigin


class NavigatorResponse(BaseModel):
    groups: list[NavigatorGroup] = Field(default_factory=list)
    recent: list[NavigatorRecentCase] = Field(default_factory=list)
    ungrouped_clients_count: int = 0
    record_folders: list[RecordFolder] = Field(default_factory=lambda: list(RECORD_FOLDERS))


# --- GET /account-groups/{id}/tree -------------------------------------------

class TreeClient(BaseModel):
    id: int
    rut: str
    legal_name: str


class TreeGroup(BaseModel):
    id: int
    name: str
    status: AccountGroupStatus
    clients: list[TreeClient] = Field(default_factory=list)


class TreeAccountNode(BaseModel):
    """The ramo's account folder (``case_file(kind=account|renewal)``)."""

    case_file_id: int
    reference: str | None = None
    kind: CaseFileKind
    stage: CaseStage
    status: CaseFileStatus
    origin: CaseOrigin
    origin_case_file_id: int | None = None
    #: The folder whose ``origin_case_file_id`` points here (its renewal).
    renewed_by_case_file_id: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    #: True once the folder has a stage event beyond ``intake`` (rule 1).
    period_locked: bool = False
    #: ``account_client`` rows ∪ ``placement.case_file_id`` rows (rule 6).
    client_ids: list[int] = Field(default_factory=list)
    placement_ids: list[int] = Field(default_factory=list)
    documents_count: int = 0
    #: Keyed by ``RecordFolder.key``; every key present, zero when empty.
    record_counts: dict[str, int] = Field(default_factory=dict)
    quotes_count: int = 0
    proposals_count: int = 0
    packs_count: int = 0
    notes_count: int = 0


class TreePostSaleNode(BaseModel):
    """One post-sale child case (endorsement / collection / claim) of a policy."""

    case_file_id: int
    reference: str | None = None
    sequence_no: int = 1
    version: int = 1
    stage: CaseStage
    status: CaseFileStatus
    #: ``endorsement.effective_at`` for endorsement children; null otherwise.
    effective_at: datetime | None = None
    #: Shared by the N endorsements of one prórroga; null otherwise.
    batch_key: str | None = None


class TreePolicyChildren(BaseModel):
    endorsement: list[TreePostSaleNode] = Field(default_factory=list)
    collection: list[TreePostSaleNode] = Field(default_factory=list)
    claim: list[TreePostSaleNode] = Field(default_factory=list)


class TreePolicyNode(BaseModel):
    id: int
    policy_number: str
    insurer_name: str | None = None
    status: PolicyStatus
    start_date: date | None = None
    end_date: date | None = None
    #: Set when a ``period_extension`` endorsement moved ``policy.end_date``
    #: past the account's ``period_end`` ("prorrogada hasta …").
    extended_end_date: date | None = None
    children: TreePolicyChildren = Field(default_factory=TreePolicyChildren)


class TreeRenewal(BaseModel):
    """The RENOVACIÓN leaf: a link when a renewal folder exists, otherwise
    whether ``POST /case-files/{id}/renew`` would be accepted and why not."""

    case_file_id: int | None = None
    allowed: bool = False
    reason: RenewalReason | None = None


class TreeLineNode(BaseModel):
    insurance_line_id: int
    name: str
    requires_inspection: bool = False
    account: TreeAccountNode
    policies: list[TreePolicyNode] = Field(default_factory=list)
    renewal: TreeRenewal = Field(default_factory=TreeRenewal)


class TreePeriodNode(BaseModel):
    """One vigencia: a label grouping accounts; ``start``/``end`` are the
    extremes of its accounts' full dates (they may differ per ramo)."""

    label: str
    start: date | None = None
    end: date | None = None
    is_latest: bool = False
    lines: list[TreeLineNode] = Field(default_factory=list)


class GroupTree(BaseModel):
    group: TreeGroup
    #: ``period_start`` desc; the first one is ``is_latest``.
    periods: list[TreePeriodNode] = Field(default_factory=list)


__all__ = [
    "RecordFolderKey",
    "RenewalReason",
    "RecordFolder",
    "RECORD_FOLDERS",
    "RECORD_FOLDER_BY_CATEGORY",
    "NavigatorPeriod",
    "NavigatorGroup",
    "NavigatorRecentCase",
    "NavigatorResponse",
    "TreeClient",
    "TreeGroup",
    "TreeAccountNode",
    "TreePostSaleNode",
    "TreePolicyChildren",
    "TreePolicyNode",
    "TreeRenewal",
    "TreeLineNode",
    "TreePeriodNode",
    "GroupTree",
]
