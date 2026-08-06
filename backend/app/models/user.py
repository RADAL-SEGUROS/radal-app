"""``user`` — one table for every actor family.

``user_type`` selects the family (platform | broker | insurer | insured) and
``role`` is the RBAC key looked up in ``app.core.roles_config.ROLES``. Exactly
one of ``broker_id`` / ``insurer_id`` / ``insured_id`` is set (none of them for
a platform user) — enforced in the service layer, not by a DB constraint, so a
user can be re-homed without a migration.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import UserType, sql_enum

if TYPE_CHECKING:
    from app.models.ai import AgentThread
    from app.models.broker import Broker
    from app.models.insured import Insured
    from app.models.insurer import Insurer


class User(Base, TimestampMixin):
    """An authenticated person. Email is the login and is globally unique."""

    __tablename__ = "user"
    __table_args__ = (
        Index("ix_user_broker_active", "broker_id", "is_active"),
        Index("ix_user_type_role", "user_type", "role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Home organisation: exactly one of these (or none for platform) -----
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), index=True
    )
    insurer_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurer.id", ondelete="CASCADE"), index=True
    )
    insured_id: Mapped[int | None] = mapped_column(
        ForeignKey("insured.id", ondelete="CASCADE"), index=True
    )

    # --- Credentials & profile ---------------------------------------------
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))

    # --- Access -------------------------------------------------------------
    user_type: Mapped[UserType] = mapped_column(sql_enum(UserType), nullable=False, index=True)
    # RBAC key, validated against app.core.roles_config.ROLES (kept a plain
    # string so the matrix stays the single source of truth for the vocabulary).
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Deterministic media route `media/user/{id}/avatar.webp` (see broker.logo_key).
    avatar_key: Mapped[str | None] = mapped_column(String(512))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker | None"] = relationship(
        back_populates="users", foreign_keys=[broker_id]
    )
    insurer: Mapped["Insurer | None"] = relationship(
        back_populates="users", foreign_keys=[insurer_id]
    )
    insured: Mapped["Insured | None"] = relationship(
        back_populates="users", foreign_keys=[insured_id]
    )
    agent_threads: Mapped[list["AgentThread"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
