"""Pydantic v2 schemas for ``client`` (the broker <-> insured relationship).

Two records are involved on every write:

- ``insured`` — CANONICAL and cross-broker, keyed by a mod-11 validated RUT.
- ``client``  — the broker-private CRM row that points at it.

Creating a client therefore takes a **RUT**, not an ``insured_id``: the router
normalizes it, finds-or-creates the canonical insured, and links the broker's
own row. The broker is never blocked and never needs an access code.

The insured-side fields on :class:`ClientCreate` are prefixed ``insured_*`` to
keep them clearly separate from the broker-private ``contact_*`` fields, which
describe the day-to-day counterpart the broker actually talks to.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.client import ClientStatus
from app.models.enums import PersonType
from app.services.identifiers import validate_rut

# --- Shared aliases ----------------------------------------------------------

Name = Annotated[str, Field(min_length=1, max_length=255)]
ShortText = Annotated[str, Field(max_length=255)]
Phone = Annotated[str, Field(max_length=64)]
Commune = Annotated[str, Field(max_length=120)]


class UserSummary(BaseModel):
    """The slice of a user any list/detail view needs (e.g. account manager)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    job_title: str | None = None
    avatar_key: str | None = None


class InsuredSummary(BaseModel):
    """The canonical insured, as embedded in every client payload."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    person_type: PersonType
    legal_name: str
    trade_name: str | None = None
    tax_activity: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    commune: str | None = None
    region: str | None = None
    logo_key: str | None = None


# --- Write payloads ----------------------------------------------------------


class ClientCreate(BaseModel):
    """Create a client from a RUT.

    ``legal_name`` is required only when the RUT is unknown to the platform
    (i.e. the canonical insured has to be created). When the insured already
    exists, any ``insured_*`` field that is currently empty on the canonical row
    is filled in from this payload; existing values are never overwritten,
    because that row is shared with other brokers.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Canonical insured identity ----------------------------------------
    rut: str = Field(description="Chilean RUT; mod-11 validated, stored BODY-DV")
    legal_name: Name | None = None
    person_type: PersonType = PersonType.LEGAL
    trade_name: ShortText | None = None
    tax_activity: str | None = None

    insured_contact_name: ShortText | None = None
    insured_email: EmailStr | None = None
    insured_phone: Phone | None = None
    insured_address: ShortText | None = None
    insured_commune: Commune | None = None
    insured_region: Commune | None = None

    # --- Broker-private CRM -------------------------------------------------
    status: ClientStatus = ClientStatus.PROSPECT
    account_manager_id: int | None = None
    source: Annotated[str, Field(max_length=120)] | None = None
    sector: ShortText | None = None
    since: date | None = None
    contact_name: ShortText | None = None
    contact_email: EmailStr | None = None
    contact_phone: Phone | None = None
    internal_notes: str | None = None

    @field_validator("rut")
    @classmethod
    def _normalize_rut(cls, value: str) -> str:
        # Raises InvalidRut (a ValueError) -> FastAPI answers 422.
        return validate_rut(value)


class ClientUpdate(BaseModel):
    """Patch the broker-private side of a client.

    The canonical insured is deliberately NOT patchable here: it is shared
    across brokers, so editing it is a separate, explicit operation.
    """

    model_config = ConfigDict(extra="forbid")

    status: ClientStatus | None = None
    account_manager_id: int | None = None
    source: Annotated[str, Field(max_length=120)] | None = None
    sector: ShortText | None = None
    since: date | None = None
    contact_name: ShortText | None = None
    contact_email: EmailStr | None = None
    contact_phone: Phone | None = None
    internal_notes: str | None = None


# --- Read payloads -----------------------------------------------------------


class ClientListItem(BaseModel):
    """One row of the clients list. Excludes ``internal_notes`` (detail only)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    insured_id: int
    status: ClientStatus
    source: str | None = None
    sector: str | None = None
    since: date | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    insured: InsuredSummary
    account_manager: UserSummary | None = None

    assets_count: int = 0
    placements_count: int = 0
    active_placements_count: int = 0


class ClientRead(ClientListItem):
    """Full client detail, including the broker-private notes."""

    internal_notes: str | None = None
    policies_count: int = 0


class ClientPage(BaseModel):
    """A page of clients plus the counters the list header shows."""

    items: list[ClientListItem]
    total: int
    page: int
    page_size: int
    pages: int


class ClientSummary(BaseModel):
    """Aggregate counters for the clients list view."""

    total: int
    by_status: dict[str, int]
    with_active_placements: int
    unassigned: int = Field(description="Clients with no account manager")


__all__ = [
    "UserSummary",
    "InsuredSummary",
    "ClientCreate",
    "ClientUpdate",
    "ClientListItem",
    "ClientRead",
    "ClientPage",
    "ClientSummary",
]
