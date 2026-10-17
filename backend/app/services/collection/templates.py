"""Payer / insurer notice templates: effective set, edits, approval (v12, 01 §3).

The EFFECTIVE template for a ``(kind, cadence_level, channel)`` slot is the
broker's own ``notice_template`` row when it has one, else the Spanish default
from ``app/db/seed_assets/notice_templates_es.json`` (data, not code). Broker
rows that match no default slot are kept too (a broker may add e.g. a ``-15``
reminder).

Approval is of CONTENT and PER TEMPLATE (review v12 #14):
:func:`approve_templates` materialises the defaults it approves into the
broker's own rows (so what was approved can never change under it when the seed
file changes) and stamps ``approved_at`` on those rows plus
``collection_settings.notices_approved_at`` (= "the broker approved something").
A content edit through :func:`upsert_templates` withdraws the approval of THAT
template only; the job sends a slot only when its template is approved — nothing
is sent to payers on wording the broker has not approved (03 §7).

Lane B4 renders/sends with :func:`effective_template` (exact slot, else the
kind's ``cadence_level = NULL`` row/default, else ``None``).
Flushes only; the caller commits.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base_class import utcnow
from app.models.collection_settings import NoticeTemplate
from app.models.enums import NoticeChannel, NoticeKind
from app.services.collection.settings import get_or_create_settings

SEED_PATH = Path(__file__).resolve().parents[2] / "db" / "seed_assets" / "notice_templates_es.json"

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
INQUIRY_TOKEN_MARK = "[RADAL-{{inquiry_token}}]"


class TemplateError(ValueError):
    """A template edit the API refuses (422). ``detail`` is Spanish."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class EffectiveTemplate:
    kind: str
    cadence_level: int | None
    channel: str
    subject: str
    body: str
    is_active: bool
    is_default: bool
    id: int | None = None
    approved_at: datetime | None = None
    approved_by_user_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "cadence_level": self.cadence_level,
            "channel": self.channel,
            "subject": self.subject,
            "body": self.body,
            "is_active": self.is_active,
            "is_default": self.is_default,
            "approved_at": self.approved_at,
            "approved_by_user_id": self.approved_by_user_id,
        }


def _val(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


@lru_cache(maxsize=1)
def _seed() -> dict[str, Any]:
    with SEED_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def template_variables() -> list[str]:
    """The placeholders a template may use (from the seed file)."""
    return list(_seed().get("variables") or [])


def default_templates() -> list[dict[str, Any]]:
    """The seed defaults, as plain dicts (copies)."""
    out = []
    for item in _seed().get("templates") or []:
        out.append(
            {
                "kind": item["kind"],
                "cadence_level": item.get("cadence_level"),
                "channel": item.get("channel") or NoticeChannel.EMAIL.value,
                "subject": item["subject"],
                "body": item["body"],
            }
        )
    return out


def _slot(kind: Any, cadence_level: Any, channel: Any) -> tuple[str, int | None, str]:
    return (
        str(_val(kind)),
        int(cadence_level) if cadence_level is not None else None,
        str(_val(channel) or NoticeChannel.EMAIL.value),
    )


def broker_template_rows(db: Session, broker_id: int) -> list[NoticeTemplate]:
    return list(
        db.scalars(
            select(NoticeTemplate)
            .where(NoticeTemplate.broker_id == broker_id)
            .order_by(NoticeTemplate.id)
        )
    )


def _sort_key(item: EffectiveTemplate) -> tuple:
    kinds = [k.value for k in NoticeKind]
    return (
        kinds.index(item.kind) if item.kind in kinds else 99,
        item.cadence_level if item.cadence_level is not None else 10_000,
        item.channel,
    )


def effective_templates(db: Session, broker_id: int) -> list[EffectiveTemplate]:
    """Every effective template of the broker (own rows win over defaults)."""
    rows = broker_template_rows(db, broker_id)
    by_slot: dict[tuple, NoticeTemplate] = {}
    for row in rows:
        by_slot[_slot(row.kind, row.cadence_level, row.channel)] = row  # newest id wins

    out: list[EffectiveTemplate] = []
    seen: set[tuple] = set()
    for item in default_templates():
        slot = _slot(item["kind"], item["cadence_level"], item["channel"])
        seen.add(slot)
        row = by_slot.get(slot)
        out.append(_from_row(row) if row is not None else _from_default(item))
    for slot, row in by_slot.items():
        if slot not in seen:
            out.append(_from_row(row))
    out.sort(key=_sort_key)
    return out


def _from_row(row: NoticeTemplate) -> EffectiveTemplate:
    return EffectiveTemplate(
        id=row.id,
        kind=str(_val(row.kind)),
        cadence_level=row.cadence_level,
        channel=str(_val(row.channel)),
        subject=row.subject,
        body=row.body,
        is_active=bool(row.is_active),
        is_default=False,
        approved_at=row.approved_at,
        approved_by_user_id=row.approved_by_user_id,
    )


def _from_default(item: dict[str, Any]) -> EffectiveTemplate:
    return EffectiveTemplate(
        kind=item["kind"],
        cadence_level=item["cadence_level"],
        channel=item["channel"],
        subject=item["subject"],
        body=item["body"],
        is_active=True,
        is_default=True,
    )


def effective_template(
    db: Session,
    broker_id: int,
    kind: NoticeKind | str,
    cadence_level: int | None = None,
    channel: NoticeChannel | str = NoticeChannel.EMAIL,
) -> EffectiveTemplate | None:
    """The template to render for one slot: exact level, else the kind's NULL level."""
    wanted = _slot(kind, cadence_level, channel)
    fallback = _slot(kind, None, channel)
    items = {(_slot(t.kind, t.cadence_level, t.channel)): t for t in effective_templates(db, broker_id)}
    found = items.get(wanted) or items.get(fallback)
    if found is None or not found.is_active:
        return None
    return found


def validate_template(kind: str, subject: str, body: str) -> None:
    """422-worthy problems: unknown placeholders, the inquiry token removed."""
    allowed = set(template_variables())
    unknown = sorted(
        {name for text in (subject, body) for name in PLACEHOLDER_RE.findall(text or "")}
        - allowed
    )
    if unknown:
        raise TemplateError(
            "Variables desconocidas en la plantilla: "
            + ", ".join(f"{{{{{name}}}}}" for name in unknown)
            + ". Usa solo: "
            + ", ".join(sorted(allowed))
            + "."
        )
    if kind == NoticeKind.INSURER_STATUS_INQUIRY.value:
        compact = re.sub(r"\s+", "", subject or "")
        if INQUIRY_TOKEN_MARK not in compact:
            raise TemplateError(
                "El asunto de la consulta a la compañía debe conservar "
                f"{INQUIRY_TOKEN_MARK}: así Radal reconoce la respuesta."
            )


def clear_approval(db: Session, broker_id: int) -> None:
    """Withdraw EVERY approval of the broker (kept for callers that reset all)."""
    settings_row = get_or_create_settings(db, broker_id)
    settings_row.notices_approved_at = None
    settings_row.notices_approved_by_user_id = None
    for row in broker_template_rows(db, broker_id):
        row.approved_at = None
        row.approved_by_user_id = None


def _sync_settings_approval(db: Session, broker_id: int) -> None:
    """``collection_settings.notices_approved_at`` = "some template is approved".

    Cleared only when no approved template remains, so editing ONE template
    never switches the whole broker's reminders off (review v12 #14).
    """
    settings_row = get_or_create_settings(db, broker_id)
    if not any(row.approved_at is not None for row in broker_template_rows(db, broker_id)):
        settings_row.notices_approved_at = None
        settings_row.notices_approved_by_user_id = None


def is_approved(template: "EffectiveTemplate") -> bool:
    """A template sends from the job only once approved (a seed default never is)."""
    return not template.is_default and template.approved_at is not None


def upsert_templates(
    db: Session, broker_id: int, items: Iterable[dict[str, Any]]
) -> tuple[int, bool]:
    """Write the broker's own rows for each slot. Returns ``(written, changed)``.

    Validates every item BEFORE writing any. A content change (subject, body,
    active flag) withdraws the approval of THAT template only — the others keep
    sending (review v12 #14). ``changed`` is True when any approval was withdrawn.
    """
    items = list(items)
    for item in items:
        validate_template(str(_val(item["kind"])), item["subject"], item["body"])

    existing = {
        _slot(row.kind, row.cadence_level, row.channel): row
        for row in broker_template_rows(db, broker_id)
    }
    defaults = {
        _slot(d["kind"], d["cadence_level"], d["channel"]): d for d in default_templates()
    }
    changed = False
    written = 0
    for item in items:
        slot = _slot(item["kind"], item.get("cadence_level"), item.get("channel"))
        subject = item["subject"]
        body = item["body"]
        active = bool(item.get("is_active", True))
        row = existing.get(slot)
        if row is None:
            default = defaults.get(slot)
            same_as_default = (
                default is not None
                and default["subject"] == subject
                and default["body"] == body
                and active
            )
            row = NoticeTemplate(
                broker_id=broker_id,
                kind=NoticeKind(slot[0]),
                cadence_level=slot[1],
                channel=NoticeChannel(slot[2]),
                subject=subject,
                body=body,
                is_active=active,
            )
            db.add(row)
            existing[slot] = row
            # A brand-new row is never approved; it "withdraws" an approval only
            # in the sense that its slot now waits for one.
            changed = changed or not same_as_default
        else:
            if row.subject != subject or row.body != body or bool(row.is_active) != active:
                changed = True
                row.approved_at = None
                row.approved_by_user_id = None
            row.subject = subject
            row.body = body
            row.is_active = active
        written += 1
    db.flush()
    if changed:
        _sync_settings_approval(db, broker_id)
        db.flush()
    return written, changed


def approve_templates(
    db: Session,
    broker_id: int,
    *,
    user_id: int | None,
    now: datetime | None = None,
    slots: Iterable[tuple[Any, Any, Any]] | None = None,
):
    """Freeze templates as the broker's rows and stamp their approval.

    ``slots`` = ``[(kind, cadence_level, channel), …]`` approves just those
    (a default slot is materialised first); ``None`` approves the whole
    effective set. Unknown slots raise :class:`TemplateError`.
    """
    moment = now or utcnow()
    existing = {
        _slot(row.kind, row.cadence_level, row.channel): row
        for row in broker_template_rows(db, broker_id)
    }
    wanted: set[tuple] | None = None
    if slots is not None:
        wanted = {_slot(k, c, ch) for k, c, ch in slots}
        known = set(existing) | {
            _slot(d["kind"], d["cadence_level"], d["channel"]) for d in default_templates()
        }
        unknown = wanted - known
        if unknown:
            raise TemplateError("Esa plantilla no existe: revisa el tipo y el día del aviso.")
    for item in default_templates():
        slot = _slot(item["kind"], item["cadence_level"], item["channel"])
        if slot in existing:
            continue
        if wanted is not None and slot not in wanted:
            continue
        row = NoticeTemplate(
            broker_id=broker_id,
            kind=NoticeKind(slot[0]),
            cadence_level=slot[1],
            channel=NoticeChannel(slot[2]),
            subject=item["subject"],
            body=item["body"],
            is_active=True,
        )
        db.add(row)
        existing[slot] = row
    for slot, row in existing.items():
        if wanted is not None and slot not in wanted:
            continue
        row.approved_at = moment
        row.approved_by_user_id = user_id
    settings_row = get_or_create_settings(db, broker_id)
    settings_row.notices_approved_at = moment
    settings_row.notices_approved_by_user_id = user_id
    db.flush()
    return settings_row


__all__ = [
    "EffectiveTemplate",
    "TemplateError",
    "approve_templates",
    "clear_approval",
    "default_templates",
    "effective_template",
    "effective_templates",
    "is_approved",
    "template_variables",
    "upsert_templates",
    "validate_template",
]
