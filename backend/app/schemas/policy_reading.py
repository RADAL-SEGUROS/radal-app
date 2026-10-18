"""``TechnicalRead`` — the policy's technical data as a SUGGESTION (fast reading).

docs/v12/11-fast-reading.md: the technical groups run in a second request after
``POST /policies/upload``; their result sits on an ``extraction`` row and is
shown on the policy as "Leído por IA — Revisar y confirmar" until the broker
confirms (``…/confirm``) or discards (``…/discard``) it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TechnicalSection",
    "TechnicalLimits",
    "TechnicalSections",
    "TechnicalCounts",
    "TechnicalRead",
    "TechnicalReadConfirm",
]

TechnicalSection = Literal[
    "items",
    "coverages",
    "deductibles",
    "warranties",
    "exclusions",
    "particular_clauses",
    "limits",
]


class TechnicalLimits(BaseModel):
    indemnity_limit: str | None = None
    aggregate_limit_uf: Any = None
    valuation_basis: str | None = None
    average_rate_permille: Any = None
    cmf_policy_code: str | None = None


class TechnicalSections(BaseModel):
    """Rows are passed through as read (slim dicts; numbers as JSON strings)."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    coverages: list[dict[str, Any]] = Field(default_factory=list)
    deductibles: list[dict[str, Any]] = Field(default_factory=list)
    warranties: list[dict[str, Any]] = Field(default_factory=list)
    exclusions: list[dict[str, Any]] = Field(default_factory=list)
    particular_clauses: list[dict[str, Any]] = Field(default_factory=list)
    limits: TechnicalLimits = Field(default_factory=TechnicalLimits)


class TechnicalCounts(BaseModel):
    items: int = 0
    coverages: int = 0
    deductibles: int = 0
    warranties: int = 0
    exclusions: int = 0
    particular_clauses: int = 0


class TechnicalRead(BaseModel):
    """``GET|POST /policies/{id}/technical-read`` (and the confirm/discard answer)."""

    status: Literal["none", "running", "ready", "confirmed", "failed"]
    extraction_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: Literal["ai_timeout", "ai_unavailable", "ai_parse"] | None = None
    #: Spanish, user-facing (failed reads, or a read with failed sections).
    message: str | None = None
    sections: TechnicalSections | None = None
    counts: TechnicalCounts | None = None
    #: ``{section: Spanish message}`` for the sections whose group failed —
    #: those sections are empty; the rest of the read is usable.
    section_errors: dict[str, str] = Field(default_factory=dict)
    #: Sections committed by the confirm (confirmed reads only).
    confirmed_sections: list[str] = Field(default_factory=list)
    #: On the confirm answer: differences against values the policy already
    #: had (never overwritten) and anything skipped.
    warnings: list[str] = Field(default_factory=list)


class TechnicalReadConfirm(BaseModel):
    """``POST …/technical-read/{extraction_id}/confirm`` — default: every section."""

    model_config = ConfigDict(extra="forbid")

    sections: list[TechnicalSection] | None = None
