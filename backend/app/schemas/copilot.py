"""``GET /ai/copilot/home`` — the broker's copilot home (v12 copilot pass).

Keys are a FIXED English set the SPA maps to i18n labels; numbers are plain
JSON numbers (UF rounded to 2 decimals for display — the money of record stays
in the owning endpoints).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

IndicatorKey = Literal[
    "overdue_installments",
    "pending_evidence",
    "todo_notifications",
    "expiring_policies_30",
    "active_policies",
]
SuggestionKey = Literal[
    "overdue_review",
    "pending_evidence_review",
    "register_policy",
    "portfolio_summary",
    "missing_files",
    "renewals_upcoming",
    "ask_insurer_status",
]


class CopilotIndicator(BaseModel):
    key: IndicatorKey
    value: int | float
    secondary: int | float | None = None
    unit: Literal["count", "uf"] | None = "count"
    tone: Literal["neutral", "warn", "danger", "ok"] = "neutral"
    #: In-app SPA path the card opens (carries ``owner=me`` / ``mine=1`` for scope=mine).
    link: str


class CopilotSuggestion(BaseModel):
    key: SuggestionKey
    #: The agent skill to load on the new thread, or ``None``.
    skill: str | None = None


class CopilotHome(BaseModel):
    first_name: str
    #: RBAC role key (``broker_admin``, ``broker_executive``, …).
    role: str
    #: ``mine`` = the caller's own book (executives); ``all`` = the whole broker (Settings.Manage).
    scope: Literal["mine", "all"]
    indicators: list[CopilotIndicator]
    suggestions: list[CopilotSuggestion]
