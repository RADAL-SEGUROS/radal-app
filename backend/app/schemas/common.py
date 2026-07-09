"""Shared/common Pydantic v2 schemas: auth tokens, user, pagination."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.usuario import RolEnum


# --- Auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UsuarioOut(BaseModel):
    """Public shape of a usuario (matches api-contract login embed)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    email: EmailStr
    cargo: str
    rol: RolEnum
    corredora_id: int


class CorredoraSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    logo_url: str | None = None


class Token(BaseModel):
    """access + refresh with the embedded usuario (login response)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


class TokenPair(BaseModel):
    """access + refresh only (refresh response)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeResponse(UsuarioOut):
    """/auth/me: usuario fields + corredora summary."""

    corredora: CorredoraSummary | None = None


# --- Pagination ------------------------------------------------------------
T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=200)


class Page(BaseModel, Generic[T]):
    """Standard list envelope: { items, total, page, page_size }."""

    items: list[T]
    total: int
    page: int = 1
    page_size: int = 25
