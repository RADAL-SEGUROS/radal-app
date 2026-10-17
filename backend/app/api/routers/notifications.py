"""``/notifications`` — the bell: the current user's to-do / fyi trays (v12, 02 §4).

Owner: lane **B2** (collections core); built in phase 2 (shared services) with
``app.services.notifications``, which is the only writer of the table.

Scope: a notification belongs to ONE user of ONE broker. Every query filters
``broker_id == get_current_broker_id`` AND ``user_id == current_user.id``, so
another user's row — same broker or not — is a **404** on every path. Platform
users carry no broker: ``get_current_broker_id`` answers 403, like every other
broker endpoint. There is no module gate: the bell is personal, any
authenticated broker user reads and acts on their own rows.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_current_user, get_db
from app.core.permissions import has_permission, resolve_role
from app.models.enums import NotificationStatus, NotificationTray
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notifications import NotificationCount, NotificationPage, NotificationRead
from app.services.notifications import _aware_utc, close_notification, dedupe_by_origin, mark_seen

router = APIRouter(prefix="/notifications", tags=["notifications"])

Scope = Literal["mine", "all"]

SCOPE_ALL_FORBIDDEN = (
    "Solo un administrador de la corredora puede ver las notificaciones de todo el equipo."
)


def _check_scope(scope: str, user: User) -> None:
    """``all`` = every open row of the broker — broker admins (Settings.Manage) only."""
    if scope == "all" and not has_permission(resolve_role(user), "Settings", "Manage"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SCOPE_ALL_FORBIDDEN)


def _team_rows(db: Session, *, broker_id: int, tray: NotificationTray | None) -> list[Notification]:
    """Every non-closed row of the broker, ONE per origin (most severe, then newest)."""
    stmt = select(Notification).where(
        Notification.broker_id == broker_id,
        Notification.status != NotificationStatus.CLOSED,
    )
    if tray is not None:
        stmt = stmt.where(Notification.tray == tray)
    rows = dedupe_by_origin(db.scalars(stmt).all())
    rows.sort(key=lambda r: (_aware_utc(r.updated_at), r.id), reverse=True)
    return rows


def _names(db: Session, user_ids: set[int], broker_id: int) -> dict[int, str]:
    if not user_ids:
        return {}
    return {
        uid: (name or "")
        for uid, name in db.execute(
            select(User.id, User.full_name).where(
                User.id.in_(user_ids), User.broker_id == broker_id
            )
        ).all()
    }


def _mine(stmt, *, broker_id: int, user: User):  # noqa: ANN001
    return stmt.where(Notification.broker_id == broker_id, Notification.user_id == user.id)


def _get_mine_or_404(db: Session, notification_id: int, *, broker_id: int, user: User) -> Notification:
    row = db.scalar(
        _mine(select(Notification), broker_id=broker_id, user=user).where(
            Notification.id == notification_id
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notificación no encontrada")
    return row


@router.get("", response_model=NotificationPage)
def list_notifications(
    tray: NotificationTray | None = Query(default=None),
    status_filter: NotificationStatus | None = Query(default=None, alias="status"),
    scope: Scope = Query(default="mine"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NotificationPage:
    """The current user's notifications, last raised first.

    Without ``status`` the open rows (``new`` + ``seen``) are listed; pass
    ``status=closed`` for the history.

    ``scope=all`` (broker admins only — 403 otherwise) is the team view: every
    NON-closed row of the broker, de-duplicated per origin (the most severe
    row, then the newest), each with ``recipient_name``. It ignores ``status``.
    Acting on a colleague's row (seen / close) stays theirs: those paths are
    scoped to the caller's own rows.
    """
    _check_scope(scope, current_user)
    if scope == "all":
        rows = _team_rows(db, broker_id=broker_id, tray=tray)
        page = rows[offset : offset + limit]
        names = _names(db, {r.user_id for r in page}, broker_id)
        return NotificationPage(
            items=[
                NotificationRead.model_validate(row).model_copy(
                    update={"recipient_name": names.get(row.user_id) or None}
                )
                for row in page
            ],
            total=len(rows),
            limit=limit,
            offset=offset,
        )
    stmt = _mine(select(Notification), broker_id=broker_id, user=current_user)
    if tray is not None:
        stmt = stmt.where(Notification.tray == tray)
    if status_filter is not None:
        stmt = stmt.where(Notification.status == status_filter)
    else:
        stmt = stmt.where(Notification.status != NotificationStatus.CLOSED)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Notification.updated_at.desc(), Notification.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return NotificationPage(
        items=[NotificationRead.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/count", response_model=NotificationCount)
def count_notifications(
    scope: Scope = Query(default="mine"),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NotificationCount:
    """Unseen rows per tray (the bell badge).

    ``scope=all`` (admins): the team view's open rows per tray, one per origin
    — ``new`` is not meaningful across users there, so every non-closed row
    counts.
    """
    _check_scope(scope, current_user)
    if scope == "all":
        team = _team_rows(db, broker_id=broker_id, tray=None)
        return NotificationCount(
            todo_new=sum(1 for r in team if r.tray == NotificationTray.TODO),
            fyi_new=sum(1 for r in team if r.tray == NotificationTray.FYI),
        )
    rows = db.execute(
        _mine(
            select(Notification.tray, func.count(Notification.id)),
            broker_id=broker_id,
            user=current_user,
        )
        .where(Notification.status == NotificationStatus.NEW)
        .group_by(Notification.tray)
    ).all()
    counts = {NotificationTray(tray): int(n) for tray, n in rows}
    return NotificationCount(
        todo_new=counts.get(NotificationTray.TODO, 0),
        fyi_new=counts.get(NotificationTray.FYI, 0),
    )


@router.post("/{notification_id}/seen", response_model=NotificationRead)
def mark_notification_seen(
    notification_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NotificationRead:
    """``new`` -> ``seen`` (idempotent; a closed row stays closed)."""
    row = _get_mine_or_404(db, notification_id, broker_id=broker_id, user=current_user)
    mark_seen(db, row)
    db.commit()
    db.refresh(row)
    return NotificationRead.model_validate(row)


@router.post("/{notification_id}/close", response_model=NotificationRead)
def close_one_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(get_current_user),
) -> NotificationRead:
    """Dismiss the row (idempotent). The same alert at the same level is not raised again."""
    row = _get_mine_or_404(db, notification_id, broker_id=broker_id, user=current_user)
    close_notification(db, row)
    db.commit()
    db.refresh(row)
    return NotificationRead.model_validate(row)
