"""Schemas for the agent skills endpoints (v12, 02-backend-api §9 / 05-agent §3).

Kept out of ``app.schemas.ai`` on purpose: the thread / message shapes there
must stay byte-identical; skills are additive and live on their own routes.
Titles and descriptions are Spanish — they are LLM / user copy defined in
``app.services.agent_skills``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillRead(BaseModel):
    key: str
    title: str
    description: str
    tools: list[str] = Field(default_factory=list)
    #: Spanish keywords the UI matches while the user types (suggested skill).
    triggers: list[str] = Field(default_factory=list)
    version: int = 1


class ThreadSkillsUpdate(BaseModel):
    """``POST /ai/threads/{id}/skills`` — REPLACES the thread's active skills."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list, max_length=12)


class ThreadSkillsRead(BaseModel):
    thread_id: int
    active_skills: list[SkillRead] = Field(default_factory=list)


__all__ = ["SkillRead", "ThreadSkillsUpdate", "ThreadSkillsRead"]
