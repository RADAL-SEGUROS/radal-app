"""The bell: create, escalate, close and expire ``notification`` rows (v12, 01 §3).

Shared service (phase 2): lanes B2 (collection clock), B3 (payments), B4
(inquiries / evidence), B5 (public share reports) and B6 (portal searches) all
raise notifications through :func:`notify` and resolve them through
:func:`close_for_origin`. Nothing else writes this table.

Rules
-----
* **One open row per (user, origin).** At most ONE non-closed row exists per
  ``(user_id, origin_type, origin_id)`` (the model has no partial unique index —
  it is not portable — so this module is the enforcement point). A second
  :func:`notify` for the same origin updates that row in place:

  - a **material** change (``kind``, ``tray`` or ``level`` differs) or
    ``renotify=True`` escalates it: every field is rewritten, ``status`` goes
    back to ``new`` and ``updated_at`` becomes ``now`` (it rises to the top of
    the bell);
  - otherwise (the same alert raised again, e.g. a daily job) only
    ``params`` / ``link_path`` are refreshed, quietly: the status the user left
    it in and its position (``updated_at``) are kept.

* **A dismissal sticks.** When no open row exists but the user's LATEST row for
  that origin is closed with the same ``kind`` and ``level``, the alert is not
  raised again (a daily job would otherwise resurrect what the user just
  closed). A new level (``+3`` → ``+10``), a new kind, or ``renotify=True``
  opens a fresh row.

* **Tenant scope.** Recipients are always users of ``broker_id`` that are
  active; an explicit ``user_ids`` entry from another broker (or inactive) is
  dropped silently, never notified. ``user_ids=None`` means every active
  ``broker_admin`` of the broker.

* ``updated_at`` is the "raised at" sort key of the bell and the age used by
  :func:`expire_fyi`; status-only writes (seen, quiet refresh) preserve it.

Every function flushes only; the CALLER commits.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.models.base_class import utcnow
from app.models.case_file import CaseFile
from app.models.enums import (
    CaseFileKind,
    NotificationKind,
    NotificationStatus,
    NotificationTray,
)
from app.models.notification import Notification
from app.models.policy import Policy
from app.models.user import User

#: ``fyi`` rows older than this (by ``updated_at``) are closed by :func:`expire_fyi`.
FYI_TTL = timedelta(days=7)

BROKER_ADMIN_ROLE = "broker_admin"

_ORIGIN_TYPE_MAX = 32
_LEVEL_MAX = 16
_LINK_PATH_MAX = 255


# --- helpers -----------------------------------------------------------------

def _aware_utc(value: datetime | None) -> datetime:
    """``None`` -> now; naive -> taken as UTC; aware -> converted to UTC."""
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _jsonable(value: Any) -> Any:
    """Make ``params`` JSON-safe: Decimal -> str, date/datetime -> ISO, Enum -> value."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _check_len(name: str, value: str | None, limit: int, *, required: bool = False) -> None:
    if value is None or value == "":
        if required:
            raise ValueError(f"{name} is required")
        return
    if len(value) > limit:
        raise ValueError(f"{name} longer than {limit} characters")


def _quiet_update(db: Session, row: Notification, **values: Any) -> None:
    """UPDATE ``row`` without touching ``updated_at`` (no ``onupdate`` bump).

    Status-only writes (mark seen, a same-level refresh) must not reorder the
    bell nor reset the fyi age, so they go through Core with ``updated_at`` set
    to itself; the in-session object is then synced without a SELECT.
    """
    db.flush()
    db.execute(
        update(Notification)
        .where(Notification.id == row.id)
        .values(**values, updated_at=Notification.updated_at)
        .execution_options(synchronize_session=False)
    )
    for key, value in values.items():
        set_committed_value(row, key, value)


def broker_admin_ids(db: Session, broker_id: int) -> list[int]:
    """Every active ``broker_admin`` of the broker, by id."""
    return list(
        db.scalars(
            select(User.id)
            .where(
                User.broker_id == broker_id,
                User.is_active.is_(True),
                User.role == BROKER_ADMIN_ROLE,
            )
            .order_by(User.id)
        )
    )


def _resolve_recipients(db: Session, broker_id: int, user_ids: Iterable[int] | None) -> list[int]:
    if user_ids is None:
        return broker_admin_ids(db, broker_id)
    wanted: list[int] = []
    for uid in user_ids:
        if uid is None:
            continue
        uid = int(uid)
        if uid not in wanted:
            wanted.append(uid)
    if not wanted:
        return []
    allowed = set(
        db.scalars(
            select(User.id).where(
                User.id.in_(wanted),
                User.broker_id == broker_id,
                User.is_active.is_(True),
            )
        )
    )
    return [uid for uid in wanted if uid in allowed]


# --- public API ----------------------------------------------------------------

def notify(
    db: Session,
    *,
    broker_id: int,
    user_ids: list[int] | None,
    kind: NotificationKind | str,
    tray: NotificationTray | str,
    origin_type: str,
    origin_id: int,
    params: dict | None = None,
    link_path: str | None = None,
    level: str | None = None,
    now: datetime | None = None,
    renotify: bool = False,
) -> list[Notification]:
    """Raise (or escalate) the alert about ``(origin_type, origin_id)`` for each recipient.

    Returns the open rows created or updated, one per recipient, in recipient
    order. A recipient whose identical alert was dismissed (see module doc) is
    skipped and not returned. ``ValueError`` for an unknown kind/tray or an
    over-long ``origin_type`` (32) / ``level`` (16) / ``link_path`` (255).
    Flushes; the caller commits.
    """
    kind = NotificationKind(kind)
    tray = NotificationTray(tray)
    _check_len("origin_type", origin_type, _ORIGIN_TYPE_MAX, required=True)
    _check_len("level", level, _LEVEL_MAX)
    _check_len("link_path", link_path, _LINK_PATH_MAX)
    if origin_id is None:
        raise ValueError("origin_id is required")
    origin_id = int(origin_id)
    level = level or None
    clean_params = _jsonable(params) if params is not None else None
    moment = _aware_utc(now)

    recipients = _resolve_recipients(db, broker_id, user_ids)
    if not recipients:
        return []

    # The latest row per recipient for this origin (open rows win over closed).
    rows = db.scalars(
        select(Notification)
        .where(
            Notification.broker_id == broker_id,
            Notification.user_id.in_(recipients),
            Notification.origin_type == origin_type,
            Notification.origin_id == origin_id,
        )
        .order_by(Notification.id.desc())
    ).all()
    open_by_user: dict[int, Notification] = {}
    latest_by_user: dict[int, Notification] = {}
    for row in rows:
        latest_by_user.setdefault(row.user_id, row)
        if row.status != NotificationStatus.CLOSED:
            # Self-heal: should two open rows ever exist, the newest is kept
            # and the others are closed (the rule is one open row).
            if row.user_id in open_by_user:
                row.status = NotificationStatus.CLOSED
                row.closed_at = moment
            else:
                open_by_user[row.user_id] = row

    out: list[Notification] = []
    for uid in recipients:
        current = open_by_user.get(uid)
        if current is not None:
            material = (
                current.kind != kind
                or current.tray != tray
                or (current.level or None) != level
            )
            if material or renotify:
                current.kind = kind
                current.tray = tray
                current.level = level
                current.params = clean_params
                current.link_path = link_path
                current.status = NotificationStatus.NEW
                current.closed_at = None
                current.updated_at = moment
            else:
                _quiet_update(db, current, params=clean_params, link_path=link_path)
            out.append(current)
            continue

        latest = latest_by_user.get(uid)
        if (
            latest is not None
            and latest.kind == kind
            and (latest.level or None) == level
        ):
            if not renotify:
                continue  # the user (or the system) closed this exact alert already
            # The SAME event raised again (a reversal reopening a cuota, a
            # re-rejected debit): reopen that row instead of stacking an
            # identical twin in the history (review v12 #5 — two "Cuota
            # vencida · póliza 5520-0001").
            latest.tray = tray
            latest.params = clean_params
            latest.link_path = link_path
            latest.status = NotificationStatus.NEW
            latest.closed_at = None
            latest.updated_at = moment
            out.append(latest)
            continue

        row = Notification(
            broker_id=broker_id,
            user_id=uid,
            tray=tray,
            kind=kind,
            level=level,
            origin_type=origin_type,
            origin_id=origin_id,
            params=clean_params,
            link_path=link_path,
            status=NotificationStatus.NEW,
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
        out.append(row)

    db.flush()
    return out


def close_for_origin(
    db: Session,
    *,
    broker_id: int,
    origin_type: str,
    origin_id: int,
    kinds: list[NotificationKind | str] | None = None,
    now: datetime | None = None,
) -> int:
    """Close every open row (all users of the broker) about this origin.

    ``kinds`` narrows it (e.g. close only ``evidence_pending``). Returns the
    number of rows closed. Flushes; the caller commits.
    """
    stmt = select(Notification).where(
        Notification.broker_id == broker_id,
        Notification.origin_type == origin_type,
        Notification.origin_id == int(origin_id),
        Notification.status != NotificationStatus.CLOSED,
    )
    if kinds:
        stmt = stmt.where(Notification.kind.in_([NotificationKind(k) for k in kinds]))
    moment = _aware_utc(now)
    rows = db.scalars(stmt).all()
    for row in rows:
        row.status = NotificationStatus.CLOSED
        row.closed_at = moment
    db.flush()
    return len(rows)


def recipients_for_policy(db: Session, policy: Policy) -> list[int]:
    """Who hears about a policy: its account owner, else the broker admins.

    The account is ``policy.case_file_id`` (walking one ``parent_case_file_id``
    up when that case is a post-sale child). The owner counts only when the
    user is active and belongs to the policy's broker.
    """
    broker_id = policy.broker_id
    case = db.get(CaseFile, policy.case_file_id) if policy.case_file_id else None
    if case is not None and case.broker_id != broker_id:
        case = None
    if (
        case is not None
        and case.kind != CaseFileKind.ACCOUNT
        and case.owner_user_id is None
        and case.parent_case_file_id
    ):
        parent = db.get(CaseFile, case.parent_case_file_id)
        case = parent if parent is not None and parent.broker_id == broker_id else case
    if case is not None and case.owner_user_id:
        owner = db.get(User, case.owner_user_id)
        if owner is not None and owner.is_active and owner.broker_id == broker_id:
            return [owner.id]
    return broker_admin_ids(db, broker_id)


#: Severity of a bell row, for the admin "Todas" view: the most severe row of an
#: origin wins its de-duplication (then the newest).
KIND_SEVERITY: dict[str, int] = {
    NotificationKind.TERMINATION_RISK.value: 100,
    NotificationKind.PROMISE_BROKEN.value: 90,
    NotificationKind.DIRECT_DEBIT_REJECTED.value: 85,
    NotificationKind.INSTALLMENT_OVERDUE.value: 80,
    NotificationKind.EVIDENCE_PENDING.value: 70,
    NotificationKind.PLAN_MISSING.value: 60,
}


def severity(row: Notification) -> tuple[int, int, int]:
    """``(tray, kind, cadence step)`` — bigger is more severe."""
    tray_v = getattr(row.tray, "value", row.tray)
    kind_v = getattr(row.kind, "value", row.kind)
    tray_rank = 1 if tray_v == NotificationTray.TODO.value else 0
    step = 0
    level = (row.level or "").strip()
    if level.lstrip("+-").isdigit():
        step = int(level)
    elif level == "term":
        step = 1000
    return tray_rank, KIND_SEVERITY.get(kind_v, 10), step


def dedupe_by_origin(rows: Iterable[Notification]) -> list[Notification]:
    """One row per ``(origin_type, origin_id)``: the most severe, then the newest."""
    best: dict[tuple[str, int], Notification] = {}
    for row in rows:
        key = (row.origin_type, int(row.origin_id))
        current = best.get(key)
        if current is None or (severity(row), _aware_utc(row.updated_at), row.id) > (
            severity(current),
            _aware_utc(current.updated_at),
            current.id,
        ):
            best[key] = row
    return list(best.values())


def expire_fyi(db: Session, *, now: datetime) -> int:
    """Close ``fyi`` rows the user has seen, or raised more than 7 days ago.

    The age is ``updated_at`` (the last time the alert was raised). Every
    broker at once (this is the ``notifications.expire`` job body). Returns the
    number closed. Flushes; the caller commits.
    """
    moment = _aware_utc(now)
    cutoff = moment - FYI_TTL
    rows = db.scalars(
        select(Notification).where(
            Notification.tray == NotificationTray.FYI,
            Notification.status != NotificationStatus.CLOSED,
            (Notification.status == NotificationStatus.SEEN) | (Notification.updated_at <= cutoff),
        )
    ).all()
    for row in rows:
        row.status = NotificationStatus.CLOSED
        row.closed_at = moment
    db.flush()
    return len(rows)


def mark_seen(db: Session, row: Notification) -> Notification:
    """``new`` -> ``seen`` without reordering the bell; seen/closed rows are left as is."""
    if row.status == NotificationStatus.NEW:
        _quiet_update(db, row, status=NotificationStatus.SEEN)
    return row


def close_notification(db: Session, row: Notification, *, now: datetime | None = None) -> Notification:
    """Close one row (idempotent: an already-closed row keeps its ``closed_at``)."""
    if row.status != NotificationStatus.CLOSED:
        _quiet_update(
            db, row, status=NotificationStatus.CLOSED, closed_at=_aware_utc(now)
        )
    return row


__all__ = [
    "FYI_TTL",
    "KIND_SEVERITY",
    "dedupe_by_origin",
    "severity",
    "broker_admin_ids",
    "close_for_origin",
    "close_notification",
    "expire_fyi",
    "mark_seen",
    "notify",
    "recipients_for_policy",
]
