"""Agent chat sessions (v12 copilot pass) — the list behind the copilot sub-sidebar.

A *session* is an ``agent_thread`` the caller owns. No schema change: the model
already carries ``title``, ``is_archived`` and ``last_message_at``. This module
owns the read projection (title fallback, message count, plain-text preview),
the list query (archived / search / paging) and the auto-title rule shared by
every message path (``/threads/{id}/messages``, ``/threads/{id}/stream`` and
``/ai/agent/messages``).

Scope is always ``broker_id`` + ``user_id``: a session is private to the person
who opened it, and another user's (or tenant's) session is a 404 on the
session endpoints.

No AI call happens here — the title is the first user message, trimmed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.ai import AgentMessage, AgentRole, AgentThread

__all__ = [
    "AUTO_TITLE_MAX_CHARS",
    "PREVIEW_MAX_CHARS",
    "SessionStats",
    "auto_title",
    "fallback_title",
    "display_title",
    "plain_preview",
    "session_stats",
    "list_sessions",
    "get_own_thread",
]

AUTO_TITLE_MAX_CHARS = 60
PREVIEW_MAX_CHARS = 120
ELLIPSIS = "…"
BUSINESS_TZ = "America/Santiago"

#: Messages a person sees in the transcript (tool rows and empty tool-call
#: carriers are plumbing, not conversation).
_VISIBLE_ROLES = (AgentRole.USER, AgentRole.ASSISTANT)


def _visible_clause():
    return and_(
        AgentMessage.role.in_(_VISIBLE_ROLES),
        AgentMessage.content.is_not(None),
        AgentMessage.content != "",
    )


def _cut(text: str, limit: int) -> str:
    """``text`` within ``limit`` chars INCLUDING the ellipsis, cut on a word."""
    if len(text) <= limit:
        return text
    room = max(limit - len(ELLIPSIS), 1)
    head = text[:room]
    # Prefer a word boundary unless that throws away most of the text.
    if not text[room].isspace():
        space = head.rfind(" ")
        if space >= room // 2:
            head = head[:space]
    return head.rstrip(" ,.;:-–—") + ELLIPSIS


def auto_title(content: str, limit: int = AUTO_TITLE_MAX_CHARS) -> str | None:
    """The session title from its first user message (whitespace collapsed)."""
    text = " ".join(str(content or "").split())
    if not text:
        return None
    return _cut(text, limit)


def _local(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:  # SQLite hands naive UTC back
        value = value.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return value.astimezone(ZoneInfo(BUSINESS_TZ))
    except Exception:  # noqa: BLE001 - no tzdata: UTC is still a sane day
        return value.astimezone(timezone.utc)


def fallback_title(thread: AgentThread) -> str:
    """``Conversación del dd-mm`` (Santiago day the session was opened)."""
    return f"Conversación del {_local(thread.created_at).strftime('%d-%m')}"


def display_title(thread: AgentThread) -> str:
    """Never null in a response: the stored title, else the dated fallback."""
    title = (thread.title or "").strip()
    return title or fallback_title(thread)


_FENCE = re.compile(r"```[a-zA-Z0-9_-]*")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LINE_MARK = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|~~|`)")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:-]*\|[\s|:-]*$", re.MULTILINE)


def plain_preview(content: str | None, limit: int = PREVIEW_MAX_CHARS) -> str | None:
    """The first ``limit`` chars of a message as plain text (markdown stripped)."""
    if not content:
        return None
    text = _FENCE.sub(" ", content)
    text = _LINK.sub(r"\1", text)
    text = _TABLE_RULE.sub(" ", text)
    text = _LINE_MARK.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = text.replace("|", " ")
    text = re.sub(r"(?<!\w)\*(\S[^*]*?)\*(?!\w)", r"\1", text)
    text = " ".join(text.split())
    if not text:
        return None
    return _cut(text, limit)


@dataclass(frozen=True)
class SessionStats:
    message_count: int = 0
    preview: str | None = None


def session_stats(db: Session, thread_ids: Iterable[int]) -> dict[int, SessionStats]:
    """``thread_id -> (visible message count, latest visible message preview)``.

    Two grouped queries for any page size — never one per thread.
    """
    ids = sorted({int(i) for i in thread_ids})
    if not ids:
        return {}
    visible = _visible_clause()
    counts = dict(
        db.execute(
            select(AgentMessage.thread_id, func.count(AgentMessage.id))
            .where(AgentMessage.thread_id.in_(ids), visible)
            .group_by(AgentMessage.thread_id)
        ).all()
    )
    latest_ids = [
        row[0]
        for row in db.execute(
            select(func.max(AgentMessage.id))
            .where(AgentMessage.thread_id.in_(ids), visible)
            .group_by(AgentMessage.thread_id)
        ).all()
        if row[0] is not None
    ]
    previews: dict[int, str | None] = {}
    if latest_ids:
        for thread_id, content in db.execute(
            select(AgentMessage.thread_id, AgentMessage.content).where(
                AgentMessage.id.in_(latest_ids)
            )
        ).all():
            previews[int(thread_id)] = plain_preview(content)
    return {
        i: SessionStats(message_count=int(counts.get(i, 0)), preview=previews.get(i))
        for i in ids
    }


def _own(broker_id: int, user_id: int):
    return select(AgentThread).where(
        AgentThread.broker_id == broker_id, AgentThread.user_id == user_id
    )


def get_own_thread(
    db: Session, thread_id: int, *, broker_id: int, user_id: int
) -> AgentThread | None:
    """The caller's own thread (archived or not), or ``None`` — never someone else's."""
    return db.scalar(_own(broker_id, user_id).where(AgentThread.id == thread_id))


def list_sessions(
    db: Session,
    *,
    broker_id: int,
    user_id: int,
    archived: bool = False,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AgentThread], int]:
    """The caller's sessions, most recent activity first.

    Only threads with at least one message are listed (the SPA opens a thread
    on first send, so an empty one is an abandoned draft). ``q`` matches the
    title or the text of any visible message, case-insensitively. Order:
    ``last_message_at`` desc with NULLs last (portable — MySQL has no
    ``NULLS LAST``), then ``created_at`` desc, then ``id`` desc.
    """
    has_messages = (
        select(AgentMessage.id).where(AgentMessage.thread_id == AgentThread.id).exists()
    )
    stmt = _own(broker_id, user_id).where(
        AgentThread.is_archived.is_(bool(archived)), has_messages
    )
    needle = (q or "").strip()
    if needle:
        text_hit = (
            select(AgentMessage.id)
            .where(
                AgentMessage.thread_id == AgentThread.id,
                _visible_clause(),
                AgentMessage.content.icontains(needle, autoescape=True),
            )
            .exists()
        )
        stmt = stmt.where(or_(AgentThread.title.icontains(needle, autoescape=True), text_hit))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.scalars(
        stmt.order_by(
            AgentThread.last_message_at.is_(None),
            AgentThread.last_message_at.desc(),
            AgentThread.created_at.desc(),
            AgentThread.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return list(rows), total
