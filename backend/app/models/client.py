"""``client`` — the broker <-> insured relationship plus broker-PRIVATE CRM data.

A broker asserting a client is **never blocked**: the row is created immediately
against the canonical ``insured`` (matched by RUT) and audited. Confidentiality
comes from tenant scoping, not from a gate on the relationship.

``internal_notes`` is deliberately kept separable from the insured record so
insured-facing field visibility can be gated later without a migration.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.account_group import AccountGroup
    from app.models.asset import Asset
    from app.models.broker import Broker
    from app.models.insured import Insured
    from app.models.placement import Placement
    from app.models.policy import Claim, Policy
    from app.models.user import User


class ClientStatus(StrEnum):
    PROSPECT = "prospect"
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Client(Base, TimestampMixin):
    """One broker's view of one insured."""

    __tablename__ = "client"
    __table_args__ = (
        UniqueConstraint("broker_id", "insured_id", name="uq_client_broker_insured"),
        Index("ix_client_broker_status", "broker_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insured_id: Mapped[int] = mapped_column(
        ForeignKey("insured.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # A broker x RUT belongs to AT MOST ONE broker-private group (v3). SET
    # NULL: archiving/deleting the group detaches the client, never cascades.
    account_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_group.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[ClientStatus] = mapped_column(
        sql_enum(ClientStatus), default=ClientStatus.PROSPECT, nullable=False
    )
    account_manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    # Where the lead came from (referral, inbound, campaign, ...).
    source: Mapped[str | None] = mapped_column(String(120))
    # Economic sector as the broker classifies it (their own CRM taxonomy).
    sector: Mapped[str | None] = mapped_column(String(255))
    since: Mapped[date | None] = mapped_column(Date)

    # Broker-private day-to-day contact (may differ from the insured's legal rep).
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(64))

    # BROKER-PRIVATE. Never exposed to insured/insurer users.
    internal_notes: Mapped[str | None] = mapped_column(Text)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship(back_populates="clients")
    insured: Mapped["Insured"] = relationship(back_populates="clients")
    account_group: Mapped["AccountGroup | None"] = relationship(
        back_populates="clients", foreign_keys=[account_group_id]
    )
    account_manager: Mapped["User | None"] = relationship(foreign_keys=[account_manager_id])
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    placements: Mapped[list["Placement"]] = relationship(back_populates="client")
    policies: Mapped[list["Policy"]] = relationship(back_populates="client")
    claims: Mapped[list["Claim"]] = relationship(back_populates="client")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Client id={self.id} broker_id={self.broker_id} insured_id={self.insured_id}>"
