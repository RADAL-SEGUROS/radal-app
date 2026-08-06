"""Pydantic v2 schemas for authentication and the effective permission matrix.

Token transport note: the frontend sends the access token in ``Authorization:
Bearer`` locally and in ``X-Radal-Token`` behind CloudFront (the OAC reuses
``Authorization`` for its own SigV4 signature). Both are accepted by
``app.api.deps.get_current_user`` — see that module.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.user import UserRead


def _normalize_email(value: str) -> str:
    return value.strip().lower()


# --- Requests ----------------------------------------------------------------


class LoginRequest(BaseModel):
    """JSON login (not OAuth2 form-encoded)."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    _norm_email = field_validator("email")(_normalize_email)


class RefreshRequest(BaseModel):
    """Exchange a valid refresh token for a fresh token pair (rotation)."""

    refresh_token: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """Self-service password change for the authenticated user."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# --- Responses ---------------------------------------------------------------


class TokenPair(BaseModel):
    """Access + refresh tokens. ``expires_in`` is the access token TTL (seconds)."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class OrganizationSummary(BaseModel):
    """The user's home organization, whichever family they belong to.

    ``type`` mirrors ``user.user_type`` (broker | insurer | insured). Platform
    users have no home organization and get ``null``.
    """

    model_config = ConfigDict(from_attributes=True)

    type: str
    id: int
    legal_name: str
    trade_name: str | None = None
    logo_key: str | None = None
    status: str | None = None


class LoginResponse(TokenPair):
    """Login returns the token pair plus the profile, so the SPA boots in one call."""

    user: UserRead
    organization: OrganizationSummary | None = None


class MeResponse(BaseModel):
    """``GET /auth/me`` — the authenticated profile and its organization."""

    user: UserRead
    organization: OrganizationSummary | None = None


class PermissionsResponse(BaseModel):
    """``GET /auth/permissions`` — the effective RBAC matrix, for UI gating.

    ``matrix`` keeps the raw grants ("yes" | "no" | "partial") so the UI can
    render a partial state; ``allowed`` is the coarse boolean the UI uses to
    show / disable a control. Tenant scoping is NOT expressed here — it is
    always applied on top, server-side.
    """

    user_id: int
    user_type: str | None = None
    role: str | None = None
    modules: list[str]
    actions: list[str]
    grants: dict[str, str]
    matrix: dict[str, dict[str, str]]
    allowed: dict[str, dict[str, bool]]


class MessageResponse(BaseModel):
    """Trivial acknowledgement payload."""

    detail: str


__all__ = [
    "ChangePasswordRequest",
    "LoginRequest",
    "LoginResponse",
    "MeResponse",
    "MessageResponse",
    "OrganizationSummary",
    "PermissionsResponse",
    "RefreshRequest",
    "TokenPair",
]
