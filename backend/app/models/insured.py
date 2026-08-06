"""``insured`` — CANONICAL, cross-broker. No ``broker_id``.

The insured (asegurado) is identified by its **RUT**, globally unique. Two
brokers naming the same company converge on one row; each broker's private view
of that relationship lives in ``client`` (broker_id + insured_id), which is what
tenant scoping filters on. The insured itself is never broker-owned.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import PersonType, sql_enum

if TYPE_CHECKING:
    from app.models.access import InsuredAccountRequest
    from app.models.client import Client
    from app.models.user import User


class Insured(Base, TimestampMixin):
    """A company or person that buys insurance. Canonical across all brokers."""

    __tablename__ = "insured"

    id: Mapped[int] = mapped_column(primary_key=True)

    # RUT is the identity: mod-11 validated, stored normalised BODY-DV.
    rut: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    person_type: Mapped[PersonType] = mapped_column(
        sql_enum(PersonType), default=PersonType.LEGAL, nullable=False
    )
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trade_name: Mapped[str | None] = mapped_column(String(255))
    # Economic activity / SII code description.
    tax_activity: Mapped[str | None] = mapped_column(Text)

    # --- Contact ------------------------------------------------------------
    contact_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(255))
    commune: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))

    # Deterministic media route `media/insured/{id}/logo.webp` (see broker.logo_key).
    logo_key: Mapped[str | None] = mapped_column(String(512))

    # --- Relationships ------------------------------------------------------
    clients: Mapped[list["Client"]] = relationship(back_populates="insured")
    users: Mapped[list["User"]] = relationship(
        back_populates="insured", foreign_keys="User.insured_id"
    )
    account_requests: Mapped[list["InsuredAccountRequest"]] = relationship(
        back_populates="insured"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Insured id={self.id} rut={self.rut!r}>"
