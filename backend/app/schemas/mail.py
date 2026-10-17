"""Pydantic v2 schemas for ``/mail`` — broker mailboxes and the mail log (v12, 02 §6).

Passwords are WRITE-ONLY: requests may carry ``password``; responses only ever
expose ``has_secret``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    InboundClass,
    MailAccountStatus,
    MailDirection,
    MailMessageStatus,
    MailProvider,
    MailSecurity,
)
from app.schemas.utc import UtcDatetime


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class MailAccountCreate(BaseModel):
    provider: MailProvider = MailProvider.CUSTOM
    address: str = Field(min_length=3, max_length=255)
    display_name: str | None = Field(default=None, max_length=160)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: MailSecurity | None = MailSecurity.SSL
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: MailSecurity | None = MailSecurity.SSL
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=512)
    send_enabled: bool = True
    receive_enabled: bool = True
    owner: Literal["me", "shared"] = "me"

    @field_validator("address")
    @classmethod
    def _address(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Correo inválido")
        return value


class MailAccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: MailSecurity | None = None
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: MailSecurity | None = None
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=512)
    send_enabled: bool | None = None
    receive_enabled: bool | None = None
    paused: bool | None = None


class MailAccountRead(BaseModel):
    """A mailbox. ``id`` is ``None`` for the virtual Radal system sender entry."""

    id: int | None = None
    kind: Literal["radal_system", "custom", "google", "microsoft"]
    provider: MailProvider
    address: str
    display_name: str | None = None
    owner: Literal["me", "shared", "other", "radal"] = "shared"
    owner_user_id: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: MailSecurity | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security: MailSecurity | None = None
    username: str | None = None
    has_secret: bool = False
    send_enabled: bool = True
    receive_enabled: bool = True
    status: MailAccountStatus
    last_sync_at: UtcDatetime | None = None
    last_error: str | None = None
    read_only: bool = False
    #: True when sending is live (``MAIL_ENABLED``); otherwise mail is only recorded.
    mail_enabled: bool = False
    can_manage: bool = False

    @field_validator("last_sync_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class MailProviderOption(BaseModel):
    """A connectable provider for the "Conectar casilla" picker (no dead buttons)."""

    provider: MailProvider
    available: bool
    reason: str | None = None


class MailAccountList(BaseModel):
    items: list[MailAccountRead]
    providers: list[MailProviderOption]


class MailCheckRead(BaseModel):
    smtp_ok: bool | None = None
    imap_ok: bool | None = None
    smtp_error: str | None = None
    imap_error: str | None = None
    checked_at: UtcDatetime | None = None
    ok: bool = False


class MailAccountTestResult(BaseModel):
    account: MailAccountRead
    check: MailCheckRead


class MailMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mail_account_id: int | None = None
    direction: MailDirection
    status: MailMessageStatus
    classification: InboundClass | None = None
    thread_token: str | None = None
    message_id_header: str | None = None
    in_reply_to: str | None = None
    from_address: str | None = None
    to_addresses: list[Any] | None = None
    cc_addresses: list[Any] | None = None
    subject: str | None = None
    body_text: str | None = None
    occurred_at: datetime | None = None
    document_id: int | None = None
    extraction_id: int | None = None
    error: str | None = None
    created_at: UtcDatetime

    @field_validator("occurred_at", "created_at")
    @classmethod
    def _tz(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class MailMessagePage(BaseModel):
    items: list[MailMessageRead]
    total: int
    limit: int
    offset: int


__all__ = [
    "MailAccountCreate",
    "MailAccountList",
    "MailAccountRead",
    "MailAccountTestResult",
    "MailAccountUpdate",
    "MailCheckRead",
    "MailMessagePage",
    "MailMessageRead",
    "MailProviderOption",
]
