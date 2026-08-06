"""``broker`` — THE TENANT.

Radal is the platform (software provider); the broker (corredora) is the
customer and the tenant. Every workspace table carries ``broker_id`` and every
query is scoped to the authenticated user's broker.

Identity is the **RUT** (mod-11 validated, stored normalised ``BODY-DV``).
``cmf_code`` is the CMF broker registry number.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.activity import Activity, Note
    from app.models.asset import Asset
    from app.models.client import Client
    from app.models.document import Document
    from app.models.insurance_line import InsuranceLine
    from app.models.user import User


class BrokerStatus(StrEnum):
    ACTIVE = "active"
    ONBOARDING = "onboarding"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class Broker(Base, TimestampMixin):
    """An insurance brokerage. The tenant boundary of the whole application."""

    __tablename__ = "broker"
    __table_args__ = (Index("ix_broker_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Identity (RUT is canonical, mod-11 validated, normalised BODY-DV) ---
    rut: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(255))

    # --- CMF registry -------------------------------------------------------
    # `registry_note` documents real edge cases (e.g. a broker shown as
    # eliminated in the CMF registry but operating) so operations can be gated
    # later without a migration.
    cmf_code: Mapped[str | None] = mapped_column(String(32), index=True)
    cmf_status: Mapped[str | None] = mapped_column(String(255))
    cmf_registration_date: Mapped[date | None] = mapped_column(Date)
    registry_note: Mapped[str | None] = mapped_column(Text)

    # --- Appointment document metadata (the deed naming the legal rep) ------
    appointment_doc_type: Mapped[str | None] = mapped_column(String(255))
    appointment_doc_date: Mapped[date | None] = mapped_column(Date)

    # --- Contact ------------------------------------------------------------
    address: Mapped[str | None] = mapped_column(String(255))
    commune: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))

    # Deterministic media route `media/broker/{id}/logo.webp`. This is NOT a
    # document: `document` owns uploaded files, `media/` owns derived 512x512
    # WEBP avatars/logos written by app.services.media.
    logo_key: Mapped[str | None] = mapped_column(String(512))

    status: Mapped[BrokerStatus] = mapped_column(
        sql_enum(BrokerStatus), default=BrokerStatus.ACTIVE, nullable=False
    )

    # --- Relationships ------------------------------------------------------
    users: Mapped[list["User"]] = relationship(
        back_populates="broker",
        foreign_keys="User.broker_id",
        cascade="all, delete-orphan",
    )
    clients: Mapped[list["Client"]] = relationship(
        back_populates="broker", cascade="all, delete-orphan"
    )
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="broker", cascade="all, delete-orphan"
    )
    insurance_lines: Mapped[list["InsuranceLine"]] = relationship(back_populates="broker")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="broker", cascade="all, delete-orphan"
    )
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="broker", cascade="all, delete-orphan"
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="broker", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Broker id={self.id} rut={self.rut!r}>"
