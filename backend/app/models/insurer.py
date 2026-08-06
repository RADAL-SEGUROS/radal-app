"""``insurer`` (+ native profile + contacts) — CANONICAL, cross-broker.

One identity per insurance company, keyed by **rut + cmf_code** (both unique).
``is_native`` splits Radal commercial partners (rich profile, recommended,
per-broker contacts) from external companies discovered on an uploaded
proposal. Matching an extraction to an insurer is done by **normalised cmf_code
or rut only — never by name** (OCR yields "HDI Seguros S.A." / "HDI SEGUROS SA"
/ "H.D.I."). No match -> create with ``is_native=False``.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.insurance_line import InsuranceLine
    from app.models.policy import CoinsuranceShare, Policy
    from app.models.proposal import Proposal
    from app.models.user import User


class InsurerStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEREGISTERED = "deregistered"


class Insurer(Base, TimestampMixin):
    """An insurance company. Native (Radal partner) or external (broker-supplied)."""

    __tablename__ = "insurer"
    __table_args__ = (Index("ix_insurer_native_status", "is_native", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity = rut + cmf_code. BOTH are mandatory for a proposal to be valid.
    rut: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    cmf_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trade_name: Mapped[str | None] = mapped_column(String(255))

    is_native: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    status: Mapped[InsurerStatus] = mapped_column(
        sql_enum(InsurerStatus), default=InsurerStatus.ACTIVE, nullable=False
    )
    # Free-text CMF registry wording, e.g. "Compañía de Seguros Generales con
    # Registro Vigente" (data, mirrored verbatim from the CMF).
    cmf_status: Mapped[str | None] = mapped_column(String(255))

    payment_url: Mapped[str | None] = mapped_column(String(512))
    # Deterministic media route `media/insurer/{id}/logo.webp` (see broker.logo_key).
    logo_key: Mapped[str | None] = mapped_column(String(512))

    # NULL when seeded by Radal from the official CMF list; set when a broker
    # created the row by uploading an external proposal.
    created_by_broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="SET NULL"), index=True
    )

    # --- Relationships ------------------------------------------------------
    created_by_broker: Mapped["Broker | None"] = relationship(
        foreign_keys=[created_by_broker_id]
    )
    native_profile: Mapped["NativeInsurerProfile | None"] = relationship(
        back_populates="insurer", uselist=False, cascade="all, delete-orphan"
    )
    contacts: Mapped[list["InsurerContact"]] = relationship(
        back_populates="insurer", cascade="all, delete-orphan"
    )
    users: Mapped[list["User"]] = relationship(
        back_populates="insurer", foreign_keys="User.insurer_id"
    )
    proposals: Mapped[list["Proposal"]] = relationship(back_populates="insurer")
    policies: Mapped[list["Policy"]] = relationship(back_populates="insurer")
    coinsurance_shares: Mapped[list["CoinsuranceShare"]] = relationship(
        back_populates="insurer"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Insurer id={self.id} cmf_code={self.cmf_code!r} native={self.is_native}>"


class NativeInsurerProfile(Base, TimestampMixin):
    """1:1 extension that exists ONLY when ``insurer.is_native`` is true."""

    __tablename__ = "native_insurer_profile"

    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="CASCADE"), primary_key=True
    )
    commercial_agreement: Mapped[str | None] = mapped_column(Text)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    managed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    # Ordering hint for the "recommended insurers" list (lower = shown first).
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    sla_hours: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    insurer: Mapped["Insurer"] = relationship(back_populates="native_profile")
    managed_by: Mapped["User | None"] = relationship(foreign_keys=[managed_by_user_id])


class InsurerContact(Base, TimestampMixin):
    """Who to route a quote request to at an insurer.

    Both scoping FKs are nullable and NULL means "fallback":
    ``broker_id`` NULL = global default, ``insurance_line_id`` NULL = all lines.
    Lookup order: (broker+line) -> (broker) -> (line) -> global.
    """

    __tablename__ = "insurer_contact"
    __table_args__ = (
        Index("ix_insurer_contact_lookup", "insurer_id", "broker_id", "insurance_line_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), index=True
    )
    insurance_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="SET NULL"), index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    # Job title at the insurer (e.g. "Suscripción Property Corporativo").
    role: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    insurer: Mapped["Insurer"] = relationship(back_populates="contacts")
    broker: Mapped["Broker | None"] = relationship(foreign_keys=[broker_id])
    insurance_line: Mapped["InsuranceLine | None"] = relationship(
        foreign_keys=[insurance_line_id]
    )


__all__ = ["Insurer", "InsurerStatus", "NativeInsurerProfile", "InsurerContact"]
