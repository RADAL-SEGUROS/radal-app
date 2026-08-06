"""``insured_account_request`` — the manual claim on a RUT.

There is no self-service code handoff any more. An insured (or a broker acting
for one) requests an account against a RUT; the **Radal platform team approves
it manually**. Eligibility rule (enforced in the service layer): the RUT must
have minimum history — at least one awarded placement.

This table is cross-broker like ``insured`` itself: ``broker_id`` only records
*who asked on whose behalf*, it is not a tenancy boundary.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.insured import Insured
    from app.models.user import User


class AccountRequestOrigin(StrEnum):
    SELF = "self"
    BROKER = "broker"


class AccountRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class InsuredAccountRequest(Base, TimestampMixin):
    """A pending/approved/rejected claim on an insured RUT."""

    __tablename__ = "insured_account_request"
    __table_args__ = (
        Index("ix_insured_account_request_status", "status", "created_at"),
        Index("ix_insured_account_request_rut", "rut"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Normalised BODY-DV RUT. Not a FK: a claim may arrive before the insured
    # record exists, and is linked once matched.
    rut: Mapped[str] = mapped_column(String(20), nullable=False)
    insured_id: Mapped[int | None] = mapped_column(
        ForeignKey("insured.id", ondelete="SET NULL"), index=True
    )
    requested_email: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[AccountRequestOrigin] = mapped_column(
        sql_enum(AccountRequestOrigin), default=AccountRequestOrigin.SELF, nullable=False
    )
    # Which broker raised it (informational; NOT a tenancy boundary).
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[AccountRequestStatus] = mapped_column(
        sql_enum(AccountRequestStatus), default=AccountRequestStatus.PENDING, nullable=False
    )
    # Platform reviewer (a `platform` user).
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    # The account actually provisioned on approval.
    created_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    insured: Mapped["Insured | None"] = relationship(back_populates="account_requests")
    broker: Mapped["Broker | None"] = relationship(foreign_keys=[broker_id])
    reviewed_by: Mapped["User | None"] = relationship(foreign_keys=[reviewed_by_id])
    created_user: Mapped["User | None"] = relationship(foreign_keys=[created_user_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InsuredAccountRequest id={self.id} rut={self.rut!r} status={self.status}>"


__all__ = ["InsuredAccountRequest", "AccountRequestOrigin", "AccountRequestStatus"]
