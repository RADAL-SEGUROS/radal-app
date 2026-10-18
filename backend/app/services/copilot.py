"""The copilot home (v12 copilot pass) — ``GET /ai/copilot/home``.

The broker's home is the agent chat. Above the composer it shows a greeting,
a handful of live indicators about the book and the common queries ranked by
what the data says needs attention. This module only ASSEMBLES: every number
comes from the service that already owns it, so a card on the home always
matches the list its link opens.

* ``overdue_installments`` — :func:`services.collection.board.summary_cards`
  (the Cobranza board's ``overdue`` card), narrowed to the caller's book for
  ``scope=mine`` exactly like the board's ``owner=me``.
* ``pending_evidence`` — the same cards' ``pending_evidence`` (the evidence
  inbox is a TEAM inbox: broker-wide in either scope, like its page).
* ``todo_notifications`` — the caller's own open *Por hacer* rows, the same
  predicate ``GET /notifications`` uses (``new`` + ``seen``).
* ``expiring_policies_30`` / ``active_policies`` — the Pólizas cards' helpers
  (:func:`api.routers.policies.count_expiring` and ``_summary_cards``), with
  the policies list's ``owner=me`` clause for ``scope=mine``.

Scope: ``all`` for admins (``Settings.Manage``), ``mine`` otherwise. An
indicator whose module the caller cannot view is omitted (never a 403 on the
home). Suggestion keys and indicator keys are a FIXED set the SPA maps to i18n.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import resolve_role, user_has_permission
from app.models.enums import NotificationStatus, NotificationTray
from app.models.notification import Notification
from app.models.user import User

__all__ = [
    "INDICATOR_KEYS",
    "SUGGESTION_KEYS",
    "MAX_SUGGESTIONS",
    "MIN_SUGGESTIONS",
    "copilot_home",
    "home_scope",
]

Scope = Literal["mine", "all"]

INDICATOR_KEYS = (
    "overdue_installments",
    "pending_evidence",
    "todo_notifications",
    "expiring_policies_30",
    "active_policies",
)

#: ``key -> skill`` (the skill the SPA loads on the new thread; ``None`` = none).
SUGGESTION_SKILLS: dict[str, str | None] = {
    "overdue_review": "collections_review",
    "pending_evidence_review": "collections_review",
    "register_policy": "policy_intake",
    "portfolio_summary": "portfolio_analysis",
    "missing_files": "file_check",
    "renewals_upcoming": "portfolio_analysis",
    "ask_insurer_status": "collections_review",
}
SUGGESTION_KEYS = tuple(SUGGESTION_SKILLS)
MAX_SUGGESTIONS = 6
MIN_SUGGESTIONS = 4

EXPIRING_WINDOW_DAYS = 30


def home_scope(user: User) -> Scope:
    """Admins (``Settings.Manage``) see the whole broker; everyone else their book."""
    return "all" if user_has_permission(user, "Settings", "Manage") else "mine"


def _first_name(user: User) -> str:
    parts = (user.full_name or "").split()
    return parts[0] if parts else ""


def _num(value: Any) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01")))


@dataclass
class _Numbers:
    overdue: int | None = None
    overdue_uf: float | None = None
    pending_evidence: int | None = None
    todo: int = 0
    expiring_30: int | None = None
    active: int | None = None
    active_uf: float | None = None
    without_source_document: int | None = None
    no_plan: int | None = None


def _collections_numbers(db: Session, broker_id: int, user: User, scope: Scope, out: _Numbers) -> None:
    from app.services.collection import board, clock

    cards = board.summary_cards(
        db,
        broker_id,
        today=clock.business_today(),
        owner_user_id=user.id if scope == "mine" else None,
    )
    out.overdue = int(cards["overdue"]["count"])
    out.overdue_uf = _num(cards["overdue"]["uf"])
    out.pending_evidence = int(cards["pending_evidence"])


def _policies_numbers(db: Session, broker_id: int, user: User, scope: Scope, out: _Numbers) -> None:
    from app.api.routers.policies import _summary_cards, count_expiring, owner_me_clause
    from app.models.policy import Policy
    from app.services import policy_registry as registry

    scoped = [Policy.broker_id == broker_id]
    if scope == "mine":
        scoped.append(owner_me_clause(broker_id, user.id))
    today = registry.today_santiago()
    cards = _summary_cards(db, broker_id, scoped, today)
    out.active = int(cards["active_count"])
    out.active_uf = _num(cards["active_premium_uf"])
    out.without_source_document = int(cards["without_source_document"])
    out.no_plan = int(cards["no_plan"])
    out.expiring_30 = count_expiring(db, scoped, today, EXPIRING_WINDOW_DAYS)


def _todo_count(db: Session, broker_id: int, user: User) -> int:
    """The caller's open Por hacer rows — ``GET /notifications?tray=todo``'s total."""
    return int(
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.broker_id == broker_id,
                Notification.user_id == user.id,
                Notification.tray == NotificationTray.TODO,
                Notification.status != NotificationStatus.CLOSED,
            )
        )
        or 0
    )


def _indicator(
    key: str,
    value: float | int,
    *,
    link: str,
    unit: str | None = "count",
    secondary: float | int | None = None,
    tone: str = "neutral",
) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "secondary": secondary,
        "unit": unit,
        "tone": tone,
        "link": link,
    }


def _indicators(n: _Numbers, scope: Scope) -> list[dict[str, Any]]:
    mine = scope == "mine"
    out: list[dict[str, Any]] = []
    if n.overdue is not None:
        out.append(
            _indicator(
                "overdue_installments",
                n.overdue,
                secondary=n.overdue_uf,
                tone="danger" if n.overdue else "ok",
                # The SPA's Cobranza board reads ``tab`` (bucket) and ``owner``.
                link="/collections?tab=overdue" + ("&owner=me" if mine else ""),
            )
        )
    if n.pending_evidence is not None:
        out.append(
            _indicator(
                "pending_evidence",
                n.pending_evidence,
                tone="warn" if n.pending_evidence else "ok",
                link="/collections/evidence",
            )
        )
    out.append(
        _indicator(
            "todo_notifications",
            n.todo,
            tone="warn" if n.todo else "ok",
            link="/notifications?tray=todo",
        )
    )
    if n.expiring_30 is not None:
        out.append(
            _indicator(
                "expiring_policies_30",
                n.expiring_30,
                tone="warn" if n.expiring_30 else "neutral",
                # No end-date filter on the Pólizas list: it opens the active ones.
                link="/policies?status=active" + ("&mine=1" if mine else ""),
            )
        )
    if n.active is not None:
        out.append(
            _indicator(
                "active_policies",
                n.active,
                secondary=n.active_uf,
                tone="neutral",
                link="/policies?status=active" + ("&mine=1" if mine else ""),
            )
        )
    return out


def _suggestions(n: _Numbers) -> list[dict[str, Any]]:
    """Ranked by what the data says, then a stable default order; 4..6 items."""
    overdue = n.overdue or 0
    evidence = n.pending_evidence or 0
    expiring = n.expiring_30 or 0
    gaps = (n.without_source_document or 0) + (n.no_plan or 0)
    active = n.active or 0
    score = {
        "overdue_review": 100 + overdue if overdue else -1,
        "pending_evidence_review": 90 + evidence if evidence else -1,
        "renewals_upcoming": 80 + expiring if expiring else 5,
        "missing_files": 70 + gaps if gaps else 6,
        "ask_insurer_status": 60 if overdue else 2,
        "register_policy": 50 if not active else 8,
        "portfolio_summary": 40 if active else 7,
    }
    default_order = {key: index for index, key in enumerate(SUGGESTION_KEYS)}
    ranked = sorted(score, key=lambda key: (-score[key], default_order[key]))
    picked = [key for key in ranked if score[key] >= 0][:MAX_SUGGESTIONS]
    for key in ranked:  # the floor: never fewer than four
        if len(picked) >= MIN_SUGGESTIONS:
            break
        if key not in picked:
            picked.append(key)
    return [{"key": key, "skill": SUGGESTION_SKILLS[key]} for key in picked]


def copilot_home(db: Session, *, broker_id: int, user: User) -> dict[str, Any]:
    scope = home_scope(user)
    numbers = _Numbers(todo=_todo_count(db, broker_id, user))
    if user_has_permission(user, "Collections", "View"):
        _collections_numbers(db, broker_id, user, scope, numbers)
    if user_has_permission(user, "Policies", "View"):
        _policies_numbers(db, broker_id, user, scope, numbers)
    return {
        "first_name": _first_name(user),
        "role": resolve_role(user) or "",
        "scope": scope,
        "indicators": _indicators(numbers, scope),
        "suggestions": _suggestions(numbers),
    }
