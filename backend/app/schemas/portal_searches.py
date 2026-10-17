"""Pydantic v2 schemas for ``/portal-searches`` (v12 02 §8, 06-rpa).

Secrets are write-only: :class:`CredentialWrite` takes the password,
:class:`CredentialRead` only says ``has_secret``. Run responses carry the
SANITISED step log and never ``error_detail`` (internal) or a quote's raw cells.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CredentialStatus, PortalErrorKind, PortalRunStatus
from app.schemas.utc import UtcDatetime


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class SubtypeRef(BaseModel):
    id: int | None = None
    code: str
    name: str | None = None


class InputField(BaseModel):
    name: str
    type: str
    required: bool = True
    options: list[str] | None = None


class CredentialRead(BaseModel):
    portal: str
    username: str | None = None
    has_secret: bool = False
    status: CredentialStatus
    last_verified_at: UtcDatetime | None = None
    last_error: str | None = None
    updated_at: UtcDatetime | None = None

    @field_validator("last_verified_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class CredentialWrite(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    #: Omit to keep the stored password (e.g. only the username changed).
    password: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("username")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("username is blank")
        return value


class PortalRead(BaseModel):
    key: str
    name: str
    version: str | None = None
    #: ``available`` | ``maintenance``
    status: str
    #: The ONE Spanish status to show (review v12 #16: the card said "En
    #: mantención" AND "no está habilitada en este ambiente"): ``Disponible`` ·
    #: ``No disponible en este ambiente`` · ``En configuración`` ·
    #: ``En mantención`` · ``Pronto``. ``maintenance_reason`` is its one reason.
    status_label: str = "Disponible"
    maintenance_reason: str | None = None
    #: rpa_disabled | not_configured | layout_changed | coming_soon | None
    maintenance_code: str | None = None
    #: for ``layout_changed``: ``global`` (every broker) | ``broker`` (only the caller's broker)
    maintenance_scope: str | None = None
    #: True for a portal shown as "pronto" (no connector yet).
    coming_soon: bool = False
    supported_subtypes: list[str] = []
    subtypes: list[SubtypeRef] = []
    requires_credentials: bool = False
    has_credentials: bool = False
    credential: CredentialRead | None = None
    input_fields: list[InputField] = []


class RunCreate(BaseModel):
    portal: str = Field(min_length=1, max_length=32)
    insurance_subtype_id: int
    case_file_id: int | None = None
    input: dict[str, Any]


class StepRead(BaseModel):
    name: str
    #: Spanish label (``selectors/<portal>.json``), else ``Paso N`` — never the id.
    label: str = ""
    op: str | None = None
    ok: bool
    detail: str | None = None
    at: str | None = None
    elapsed_ms: int | None = None


class QuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    insurer_id: int | None = None
    insurer_name: str | None = None
    plan_name: str | None = None
    premium_uf: Decimal | None = None
    premium_clp: int | None = None
    deductible: str | None = None
    coverages: list[Any] | dict[str, Any] | None = None
    proposal_id: int | None = None


class Alternative(BaseModel):
    """What the user can do next (06 §4): retry · upload a PDF quote · other portals."""

    #: retry | upload_quote_pdf | other_portal
    kind: str
    available: bool
    reason: str | None = None
    portal: str | None = None
    portal_name: str | None = None
    case_file_id: int | None = None


class RunSummary(BaseModel):
    id: int
    portal: str
    portal_name: str
    status: PortalRunStatus
    error_kind: PortalErrorKind | None = None
    #: = ``error_kind`` (the frontend's i18n key); ``None`` unless failed.
    user_message_key: str | None = None
    case_file_id: int | None = None
    insurance_subtype_id: int | None = None
    insurance_subtype_code: str | None = None
    requested_by_user_id: int | None = None
    input: dict[str, Any] = {}
    quote_count: int = 0
    connector_version: str | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    can_retry: bool = False
    can_cancel: bool = False
    #: ``queued`` but the portal cannot run here (RPA disabled / maintenance):
    #: the UI shows "No se ejecutará" + the reason, never "En cola" forever.
    will_not_run: bool = False
    will_not_run_reason: str | None = None
    insurance_subtype_name: str | None = None
    #: ``input`` formatted for people (dates dd-mm-aaaa, RUT with dots, Spanish
    #: option values). ``input`` itself stays raw for retries.
    input_display: dict[str, str] = {}

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class RunRead(RunSummary):
    steps: list[StepRead] = []
    quotes: list[QuoteRead] = []
    screenshot_document_id: int | None = None
    screenshot_url: str | None = None
    alternatives: list[Alternative] = []


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


__all__ = [
    "Alternative",
    "CredentialRead",
    "CredentialWrite",
    "InputField",
    "PortalRead",
    "QuoteRead",
    "RunCreate",
    "RunPage",
    "RunRead",
    "RunSummary",
    "StepRead",
    "SubtypeRef",
]
