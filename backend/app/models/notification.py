"""``notification`` — the bell (v12). WORKSPACE table.

Two trays: ``todo`` (needs the user to act: overdue cuota, evidence to confirm,
termination risk) and ``fyi`` (heads-ups that expire). Rule, enforced in
``app.services.notifications`` rather than by a constraint (a partial unique
index is not portable across SQLite/MySQL): at most ONE non-closed row per
``(user_id, origin_type, origin_id)`` — escalation UPDATES that row (kind,
level, tray, params) and resets it to ``new``, so the bell never stacks five
rows about the same cuota.

``origin_type``/``origin_id`` are a plain polymorphic pointer (``installment``,
``status_inquiry``, ``portal_search`` …); ``params`` is the render data the
frontend formats (policy number, insurer, amount, due date) — JSON, never
filtered on.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin
from app.models.enums import (
    NotificationKind,
    NotificationStatus,
    NotificationTray,
    sql_enum,
)
from app.models.types import JSONType


class Notification(Base, TimestampMixin):
    """One bell entry for one user."""

    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notification_broker_user_status", "broker_id", "user_id", "status"),
        Index("ix_notification_user_origin", "user_id", "origin_type", "origin_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tray: Mapped[NotificationTray] = mapped_column(sql_enum(NotificationTray), nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(sql_enum(NotificationKind), nullable=False)
    # Cadence label, e.g. "-5", "+10", "term".
    level: Mapped[str | None] = mapped_column(String(16))
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_id: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # SPA path to open, e.g. "/collections?installment=12".
    link_path: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[NotificationStatus] = mapped_column(
        sql_enum(NotificationStatus), default=NotificationStatus.NEW, nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Notification id={self.id} user={self.user_id} {self.kind} {self.status}>"


__all__ = ["Notification"]
