"""``policy_party`` — who is who on a policy (v12). WORKSPACE table.

A CHILD TABLE, not JSON on ``policy``: cobranza filters on it (the payer's
email decides whether assisted collection can remind anyone), the share link
resolves its recipient from it and ``collection_notice.recipient_party_id``
points at a row. Business rule (service layer): the payer defaults to the
policyholder, the policyholder defaults to the insured.

``client_id`` is set when the RUT is one of the broker's own clients; parties
that are not (a beneficiary bank, a leasing company) stay plain data.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import PartyRole, sql_enum
from app.models.types import PCT

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.policy import Policy


class PolicyParty(Base, TimestampMixin):
    """One role-holder of a policy (insured, policyholder, payer, …)."""

    __tablename__ = "policy_party"
    __table_args__ = (Index("ix_policy_party_broker_policy", "broker_id", "policy_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[PartyRole] = mapped_column(sql_enum(PartyRole), nullable=False)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("client.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Normalised ``BODY-DV``.
    rut: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    # hipotecario / prendario / leasing / endosatario / otro — free data.
    beneficiary_kind: Mapped[str | None] = mapped_column(String(32))
    share_pct: Mapped[Decimal | None] = mapped_column(PCT)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_collections: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    policy: Mapped["Policy"] = relationship(back_populates="parties")
    client: Mapped["Client | None"] = relationship(foreign_keys=[client_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PolicyParty policy={self.policy_id} role={self.role} name={self.name!r}>"


__all__ = ["PolicyParty"]
