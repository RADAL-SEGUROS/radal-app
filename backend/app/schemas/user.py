"""Pydantic v2 schemas for the ``user`` resource.

All identifiers are English. Roles are validated against the single source of
truth for the RBAC vocabulary — ``app.core.roles_config.ROLES`` — so an unknown
role fails as a 422 at the edge instead of reaching the database.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core import roles_config

# --- Shared field validators -------------------------------------------------


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _validate_role(value: str) -> str:
    role = value.strip()
    if role not in roles_config.ROLES:
        valid = ", ".join(sorted(roles_config.ROLES))
        raise ValueError(f"unknown role {role!r}; expected one of: {valid}")
    return role


# --- Read models -------------------------------------------------------------


class UserSummary(BaseModel):
    """Compact user reference, safe to embed in any other payload."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: str
    user_type: str
    is_active: bool
    avatar_key: str | None = None


class UserRead(UserSummary):
    """Full user record. Never carries ``hashed_password``."""

    model_config = ConfigDict(from_attributes=True)

    job_title: str | None = None
    phone: str | None = None
    broker_id: int | None = None
    insurer_id: int | None = None
    insured_id: int | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserListResponse(BaseModel):
    """Paginated user list."""

    items: list[UserRead]
    total: int
    limit: int
    offset: int


class AssignableRole(BaseModel):
    """A role the current actor is allowed to grant — powers the invite form."""

    role: str
    user_type: str


class AssignableRolesResponse(BaseModel):
    items: list[AssignableRole]


# --- Write models ------------------------------------------------------------


class UserCreate(BaseModel):
    """Invite / create a user inside the caller's organization.

    ``password`` is optional: when omitted the API generates a temporary one and
    returns it ONCE in ``UserInviteResponse.temporary_password`` (there is no
    outbound email delivery in this pass — see the router docstring).

    ``broker_id`` / ``insurer_id`` / ``insured_id`` are honoured ONLY for a
    platform user creating a user in another organization; for every other actor
    the home organization is forced from the authenticated user.
    """

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    role: str
    password: str | None = Field(default=None, min_length=8, max_length=128)
    job_title: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    is_active: bool = True

    broker_id: int | None = None
    insurer_id: int | None = None
    insured_id: int | None = None

    _norm_email = field_validator("email")(_normalize_email)
    _norm_role = field_validator("role")(_validate_role)


class UserUpdate(BaseModel):
    """Editable profile fields. Role and status have their own endpoints."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)


class UserRoleUpdate(BaseModel):
    """Change a user's RBAC role (within their own actor family)."""

    role: str

    _norm_role = field_validator("role")(_validate_role)


class UserStatusUpdate(BaseModel):
    """Activate / deactivate a user."""

    is_active: bool


class UserInviteResponse(BaseModel):
    """Result of creating a user.

    ``temporary_password`` is populated only when the API generated one; it is
    shown exactly once and never stored in clear text.
    """

    user: UserRead
    temporary_password: str | None = None


__all__ = [
    "AssignableRole",
    "AssignableRolesResponse",
    "UserCreate",
    "UserInviteResponse",
    "UserListResponse",
    "UserRead",
    "UserRoleUpdate",
    "UserStatusUpdate",
    "UserSummary",
    "UserUpdate",
]
