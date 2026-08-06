"""Pydantic v2 schemas for the insurer catalog (insurer + native profile + contacts).

Design notes
------------
- ``insurer`` is a CANONICAL, cross-broker table: identity is **rut + cmf_code**,
  both mandatory and both normalized (RUT mod-11 ``BODY-DV``, CMF uppercased).
  Names are display data only and are NEVER used for matching (see
  ``docs/v2-architecture.md`` §4.3).
- ``native_insurer_profile`` holds the COMMERCIAL relationship between Radal and
  a partner insurer. It only ever exists when ``insurer.is_native`` is true, and
  it is only ever serialized for native insurers — :class:`InsurerRead` drops it
  otherwise, so an external (broker-supplied) company can never leak commercial
  terms, SLA or platform notes.
- ``insurer_contact`` carries two nullable scoping FKs. ``broker_id`` NULL means
  "global default" and ``insurance_line_id`` NULL means "all lines". Resolution
  order is (broker+line) -> (broker) -> (line) -> global.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.insurer import InsurerStatus
from app.services.identifiers import (
    InvalidCmf,
    InvalidRut,
    validate_codigo_cmf,
    validate_rut,
)

# Which fallback tier answered a contact resolution request.
ContactMatchLevel = Literal["broker_line", "broker", "line", "global"]

# Where a contact lives: this broker's private override, or the global default.
ContactScope = Literal["broker", "global"]


# --- Identifier validation helpers -------------------------------------------

def _normalize_rut(value: str) -> str:
    """Validate mod-11 and return the canonical ``BODY-DV`` form."""
    try:
        return validate_rut(value)
    except InvalidRut as exc:  # -> 422 via pydantic
        raise ValueError(str(exc)) from exc


def _normalize_cmf_code(value: str) -> str:
    """Validate the CMF code format and uppercase it.

    ``TBD`` is rejected here: an insurer without a real CMF code cannot back a
    valid proposal (``docs/v2-architecture.md`` §4.3).
    """
    try:
        return validate_codigo_cmf(value, allow_tbd=False)
    except InvalidCmf as exc:  # -> 422 via pydantic
        raise ValueError(str(exc)) from exc


# --- Native insurer profile ---------------------------------------------------

class NativeInsurerProfileWrite(BaseModel):
    """Commercial terms for a Radal partner insurer. Platform-managed."""

    model_config = ConfigDict(extra="forbid")

    commercial_agreement: str | None = None
    onboarded_at: datetime | None = None
    managed_by_user_id: int | None = None
    priority: int = Field(default=100, ge=0, le=9999)
    sla_hours: int | None = Field(default=None, ge=0, le=8760)
    notes: str | None = None


class NativeInsurerProfileUpdate(BaseModel):
    """Partial update of the commercial profile — every field optional."""

    model_config = ConfigDict(extra="forbid")

    commercial_agreement: str | None = None
    onboarded_at: datetime | None = None
    managed_by_user_id: int | None = None
    priority: int | None = Field(default=None, ge=0, le=9999)
    sla_hours: int | None = Field(default=None, ge=0, le=8760)
    notes: str | None = None


class NativeInsurerProfileRead(BaseModel):
    """Serialized ONLY when ``insurer.is_native`` is true."""

    model_config = ConfigDict(from_attributes=True)

    insurer_id: int
    commercial_agreement: str | None = None
    onboarded_at: datetime | None = None
    managed_by_user_id: int | None = None
    priority: int = 100
    sla_hours: int | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- Insurer ------------------------------------------------------------------

class InsurerCreate(BaseModel):
    """Create a canonical insurer. ``rut`` + ``cmf_code`` are the identity."""

    model_config = ConfigDict(extra="forbid")

    rut: str
    cmf_code: str
    legal_name: str = Field(min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    status: InsurerStatus = InsurerStatus.ACTIVE
    cmf_status: str | None = Field(default=None, max_length=255)
    payment_url: str | None = Field(default=None, max_length=512)
    # Only the platform may flip this on (enforced in the router).
    is_native: bool = False
    # Ignored unless is_native is true AND the caller is a platform user.
    native_profile: NativeInsurerProfileWrite | None = None

    @field_validator("rut")
    @classmethod
    def _validate_rut(cls, value: str) -> str:
        return _normalize_rut(value)

    @field_validator("cmf_code")
    @classmethod
    def _validate_cmf(cls, value: str) -> str:
        return _normalize_cmf_code(value)


class InsurerUpdate(BaseModel):
    """Partial update. Identity fields are platform-only (enforced in the router)."""

    model_config = ConfigDict(extra="forbid")

    rut: str | None = None
    cmf_code: str | None = None
    legal_name: str | None = Field(default=None, min_length=1, max_length=255)
    trade_name: str | None = Field(default=None, max_length=255)
    status: InsurerStatus | None = None
    cmf_status: str | None = Field(default=None, max_length=255)
    payment_url: str | None = Field(default=None, max_length=512)
    is_native: bool | None = None
    native_profile: NativeInsurerProfileUpdate | None = None

    @field_validator("rut")
    @classmethod
    def _validate_rut(cls, value: str | None) -> str | None:
        return _normalize_rut(value) if value is not None else None

    @field_validator("cmf_code")
    @classmethod
    def _validate_cmf(cls, value: str | None) -> str | None:
        return _normalize_cmf_code(value) if value is not None else None


class InsurerRead(BaseModel):
    """An insurer as seen by the requesting broker.

    ``native_profile`` is populated only for native insurers; ``can_edit`` tells
    the UI whether to render the edit control enabled (no dead buttons).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    rut: str
    cmf_code: str
    legal_name: str
    trade_name: str | None = None
    is_native: bool
    status: InsurerStatus
    cmf_status: str | None = None
    payment_url: str | None = None
    logo_key: str | None = None
    created_by_broker_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    native_profile: NativeInsurerProfileRead | None = None
    can_edit: bool = False


class InsurerListResponse(BaseModel):
    """Offset-paginated insurer list."""

    items: list[InsurerRead]
    total: int
    limit: int
    offset: int


# --- Matching / dedup ---------------------------------------------------------

class InsurerMatchRequest(BaseModel):
    """Input for ``find_or_create_insurer`` — matching uses rut/cmf_code ONLY.

    ``legal_name`` is carried for display when a NEW row has to be created; it is
    never used to match an existing insurer (OCR yields "HDI Seguros S.A." /
    "HDI SEGUROS SA" / "H.D.I.").
    """

    model_config = ConfigDict(extra="forbid")

    rut: str | None = None
    cmf_code: str | None = None
    legal_name: str | None = Field(default=None, max_length=255)


class InsurerMatchResult(BaseModel):
    """Outcome of a match/dedup call."""

    insurer: InsurerRead
    created: bool
    # Which identifier resolved the match; None when the insurer was created.
    matched_on: Literal["rut", "cmf_code"] | None = None


# --- Insurer contacts ---------------------------------------------------------

class InsurerContactCreate(BaseModel):
    """Create a contact.

    ``scope='broker'`` (default) binds the contact to the caller's broker;
    ``scope='global'`` creates a platform-wide default and is platform-only.
    ``insurance_line_id`` NULL means the contact serves all lines.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, max_length=255)
    is_primary: bool = False
    insurance_line_id: int | None = None
    scope: ContactScope = "broker"


class InsurerContactUpdate(BaseModel):
    """Partial update of a contact. Scope (broker) cannot be changed."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, max_length=255)
    is_primary: bool | None = None
    insurance_line_id: int | None = None


class InsurerContactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    insurer_id: int
    broker_id: int | None = None
    insurance_line_id: int | None = None
    name: str
    email: str | None = None
    phone: str | None = None
    role: str | None = None
    is_primary: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Derived, for the UI: "global" when broker_id is NULL.
    scope: ContactScope = "broker"
    can_edit: bool = False


class ResolvedContact(BaseModel):
    """The effective contact for (insurer, broker, line) + which tier answered."""

    insurer_id: int
    broker_id: int
    insurance_line_id: int | None = None
    match_level: ContactMatchLevel
    contact: InsurerContactRead


# --- Recommendations ----------------------------------------------------------

class InsurerRecommendation(BaseModel):
    """A native insurer suggested for a given insurance line."""

    insurer: InsurerRead
    priority: int = 100
    sla_hours: int | None = None
    onboarded_at: datetime | None = None
    # True when a contact exists that is specific to the requested line.
    has_line_contact: bool = False
    contact: ResolvedContact | None = None
    reason: str


class InsurerRecommendationResponse(BaseModel):
    insurance_line_id: int | None = None
    insurance_line_name: str | None = None
    items: list[InsurerRecommendation]


__all__ = [
    "ContactMatchLevel",
    "ContactScope",
    "NativeInsurerProfileWrite",
    "NativeInsurerProfileUpdate",
    "NativeInsurerProfileRead",
    "InsurerCreate",
    "InsurerUpdate",
    "InsurerRead",
    "InsurerListResponse",
    "InsurerMatchRequest",
    "InsurerMatchResult",
    "InsurerContactCreate",
    "InsurerContactUpdate",
    "InsurerContactRead",
    "ResolvedContact",
    "InsurerRecommendation",
    "InsurerRecommendationResponse",
]
