"""Pydantic v2 schemas for ``account_group`` — the broker-private Group.

Contract: docs/v3-groups-accounts-spec.md §4.1. A group carries no money and
no stage (rule 7); everything money- or stage-shaped lives on the account
folder (``case_file(kind=account|renewal)``) and is only *counted* here.

Naming note: the group timeline entry is ``GroupTimelineEntry`` so it cannot
be confused with ``app.schemas.case_file.TimelineEntry`` (the per-case
bitácora, which carries ``user_id``/``meta`` and is ordered oldest-first).
The group timeline is merged over every visible case of the group and is
sorted DESCENDING before truncation.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.account_group import AccountGroupIconKind, AccountGroupStatus
from app.models.client import ClientStatus
from app.schemas.document import DocumentDownload


# --- Group avatar (v8) --------------------------------------------------------

class AccountGroupIconInput(BaseModel):
    """The optional group avatar on create/update.

    ``emoji``/``glyph`` carry the character or curated glyph name in ``value``;
    ``image`` points at an uploaded ``document`` via ``document_id`` (rule 8: the
    S3 key lives only on the document row)."""

    model_config = ConfigDict(extra="forbid")

    kind: AccountGroupIconKind
    value: str | None = Field(default=None, max_length=64)
    document_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _shape(self) -> "AccountGroupIconInput":
        if self.kind is AccountGroupIconKind.IMAGE:
            if self.document_id is None:
                raise ValueError("an image icon requires document_id")
        elif not (self.value or "").strip():
            raise ValueError(f"a {self.kind.value} icon requires value")
        return self


class AccountGroupIconRead(BaseModel):
    """The resolved avatar: ``url`` is a scoped link for an image, else null."""

    kind: AccountGroupIconKind
    value: str | None = None
    url: str | None = None

#: The kinds a group timeline row can carry (spec §4.1).
GroupTimelineKind = Literal[
    "stage", "activity", "note", "policy", "endorsement", "claim", "collection"
]


# --- Write models -------------------------------------------------------------

class AccountGroupCreate(BaseModel):
    """``POST /account-groups`` — the server derives ``slug`` from ``name``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    notes: str | None = None
    #: Optional group avatar (emoji / curated glyph / uploaded image).
    icon: AccountGroupIconInput | None = None
    #: Existing clients of the caller's broker to attach on creation.
    client_ids: list[int] | None = None

    @field_validator("client_ids")
    @classmethod
    def _unique_client_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(cid <= 0 for cid in value):
            raise ValueError("client_ids must be positive integers")
        if len(set(value)) != len(value):
            raise ValueError("client_ids must not contain duplicates")
        return value


class AccountGroupUpdate(BaseModel):
    """``PATCH /account-groups/{id}``. Archiving DETACHES, never cascades."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: AccountGroupStatus | None = None
    notes: str | None = None
    #: Set/replace the group avatar. ``None`` leaves it untouched.
    icon: AccountGroupIconInput | None = None


class AccountGroupAttachClient(BaseModel):
    """``POST /account-groups/{id}/clients`` — 422 ``client_in_other_group``."""

    model_config = ConfigDict(extra="forbid")

    client_id: int = Field(gt=0)


#: Short alias matching the spec §8 wording ("AttachClient").
AttachClient = AccountGroupAttachClient


class ArchiveCreate(BaseModel):
    """``POST /account-groups/{id}/archives`` — whole group when ``period_label`` is null."""

    model_config = ConfigDict(extra="forbid")

    period_label: str | None = Field(default=None, min_length=1, max_length=32)


# --- Read models --------------------------------------------------------------

class GroupClientRef(BaseModel):
    """The derived primary RUT: the contratante of the latest ``period_start``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    legal_name: str


class AccountGroupClientRead(BaseModel):
    """One member client, as listed on the group detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    legal_name: str
    trade_name: str | None = None
    status: ClientStatus


class AccountGroupRead(BaseModel):
    """List row. Every count is over cases VISIBLE to the caller."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    status: AccountGroupStatus
    icon: AccountGroupIconRead | None = None
    primary_client: GroupClientRef | None = None
    clients_count: int = 0
    accounts_count: int = 0
    open_count: int = 0
    latest_period_label: str | None = None
    latest_period_start: date | None = None
    updated_at: datetime | None = None


class AccountGroupDetail(AccountGroupRead):
    notes: str | None = None
    clients: list[AccountGroupClientRead] = Field(default_factory=list)


class AccountGroupPage(BaseModel):
    """The ``?page/?page_size`` envelope (same family as clients/cases)."""

    items: list[AccountGroupRead]
    total: int
    page: int
    page_size: int
    pages: int


class GroupTimelineEntry(BaseModel):
    """One merged row of the group timeline (stage events, activity, notes,
    policies, endorsements, claims, collections), newest FIRST."""

    kind: GroupTimelineKind
    occurred_at: datetime
    case_file_id: int | None = None
    policy_id: int | None = None
    #: The human identifier only (policy number, endorsement folio, claim
    #: number). NEVER prose: rule 1 keeps Spanish out of the API, and rule 4
    #: means the label around it is the UI's ``t()`` call on ``kind``.
    title: str
    #: Free text the user themself wrote (a note body, a stage note). Already
    #: in the user's own words, so it is passed through untouched.
    detail: str | None = None
    #: The enum VALUE behind ``detail`` (``policy.status``, ``endorsement.kind``,
    #: ``claim.status``, ``collection_plan.status``) for the UI to translate. A
    #: raw token here is deliberate — the label lives in the locale files.
    detail_token: str | None = None
    #: Stage rows only: the two ``CaseStage`` tokens of the transition.
    from_stage: str | None = None
    to_stage: str | None = None


#: Spec §8 wording ("TimelineEntry + page"); the canonical name is the explicit one.
TimelineEntry = GroupTimelineEntry


class GroupTimelinePage(BaseModel):
    """``GET /account-groups/{id}/timeline?limit=50&before=<iso>``.

    ``next_before`` is the ``occurred_at`` of the last entry returned, or
    ``None`` when the page was not full — pass it back as ``before`` to
    continue; there is no offset pagination on a merged timeline.
    """

    entries: list[GroupTimelineEntry]
    next_before: datetime | None = None


class ArchiveResponse(BaseModel):
    """The generated ZIP is a ``document(entity_type=account_group,
    category=archive_pack)`` row — the key lives only there (rule 8)."""

    document_id: int
    download: DocumentDownload


__all__ = [
    "GroupTimelineKind",
    "AccountGroupIconInput",
    "AccountGroupIconRead",
    "AccountGroupCreate",
    "AccountGroupUpdate",
    "AccountGroupAttachClient",
    "AttachClient",
    "ArchiveCreate",
    "GroupClientRef",
    "AccountGroupClientRead",
    "AccountGroupRead",
    "AccountGroupDetail",
    "AccountGroupPage",
    "GroupTimelineEntry",
    "TimelineEntry",
    "GroupTimelinePage",
    "ArchiveResponse",
]
