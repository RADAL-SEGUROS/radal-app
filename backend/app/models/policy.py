"""Post-sale: ``policy`` and its children, plus ``claim``.

A policy is created ONLY after a proposal is accepted, so it inherits the same
money shape as the proposal (taxable / exempt / net / VAT / total in UF).
Post-sale depth is deliberately shallow in this pass — the UI renders these
sections visibly disabled ("pronto") rather than as dead controls — but the
model is complete so nothing needs a migration when it is switched on.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import CoverageKind, StrEnum, sql_enum
from app.models.types import PCT, UF, JSONType

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.broker import Broker
    from app.models.client import Client
    from app.models.insurance_line import InsuranceLine
    from app.models.insurer import Insurer
    from app.models.placement import Placement
    from app.models.proposal import Proposal


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    RENEWED = "renewed"


class ClaimStatus(StrEnum):
    REPORTED = "reported"
    UNDER_REVIEW = "under_review"
    SETTLED = "settled"
    PAID = "paid"
    REJECTED = "rejected"
    CLOSED = "closed"


class Policy(Base, TimestampMixin):
    """An issued policy. ``policy_number`` is unique PER BROKER, never globally."""

    __tablename__ = "policy"
    __table_args__ = (
        UniqueConstraint("broker_id", "policy_number", name="uq_policy_broker_number"),
        Index("ix_policy_broker_status", "broker_id", "status"),
        Index("ix_policy_broker_end", "broker_id", "end_date"),
        Index("ix_policy_broker_client", "broker_id", "client_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset.id", ondelete="SET NULL"), index=True
    )
    placement_id: Mapped[int | None] = mapped_column(
        ForeignKey("placement.id", ondelete="SET NULL"), index=True
    )
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True, unique=True
    )
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    insurance_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="SET NULL"), index=True
    )

    policy_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[PolicyStatus] = mapped_column(
        sql_enum(PolicyStatus), default=PolicyStatus.DRAFT, nullable=False
    )

    # --- Money (UF), same invariants as the proposal ------------------------
    insured_amount_uf: Mapped[Decimal | None] = mapped_column(UF)
    taxable_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    exempt_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    net_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    vat_uf: Mapped[Decimal | None] = mapped_column(UF)
    total_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    commission_pct: Mapped[Decimal | None] = mapped_column(PCT)

    deductibles: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    notes: Mapped[str | None] = mapped_column(Text)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    client: Mapped["Client"] = relationship(back_populates="policies")
    asset: Mapped["Asset | None"] = relationship(foreign_keys=[asset_id])
    placement: Mapped["Placement | None"] = relationship(back_populates="policies")
    proposal: Mapped["Proposal | None"] = relationship(back_populates="policy")
    insurer: Mapped["Insurer"] = relationship(back_populates="policies")
    insurance_line: Mapped["InsuranceLine | None"] = relationship(
        foreign_keys=[insurance_line_id]
    )
    coinsurance_shares: Mapped[list["CoinsuranceShare"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )
    locations: Mapped[list["PolicyLocation"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )
    coverage_items: Mapped[list["CoverageItem"]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="CoverageItem.sort_order",
    )
    claims: Mapped[list["Claim"]] = relationship(back_populates="policy")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Policy id={self.id} number={self.policy_number!r}>"


class CoinsuranceShare(Base, TimestampMixin):
    """How a co-insured policy is split between companies. Percentages sum to 100."""

    __tablename__ = "coinsurance_share"
    __table_args__ = (
        UniqueConstraint("policy_id", "insurer_id", name="uq_coinsurance_policy_insurer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    percentage: Mapped[Decimal] = mapped_column(PCT, nullable=False)

    policy: Mapped["Policy"] = relationship(back_populates="coinsurance_shares")
    insurer: Mapped["Insurer"] = relationship(back_populates="coinsurance_shares")


class PolicyLocation(Base, TimestampMixin):
    """One insured location of a policy, with its share of the insured amount."""

    __tablename__ = "policy_location"
    __table_args__ = (Index("ix_policy_location_policy", "policy_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    commune: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    insured_amount_uf: Mapped[Decimal | None] = mapped_column(UF)
    percentage: Mapped[Decimal | None] = mapped_column(PCT)

    policy: Mapped["Policy"] = relationship(back_populates="locations")


class CoverageItem(Base, TimestampMixin):
    """A coverage or exclusion as finally written into the issued policy."""

    __tablename__ = "coverage_item"
    __table_args__ = (Index("ix_coverage_item_policy_kind", "policy_id", "kind", "sort_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[CoverageKind] = mapped_column(sql_enum(CoverageKind), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    policy: Mapped["Policy"] = relationship(back_populates="coverage_items")


class Claim(Base, TimestampMixin):
    """A loss reported against a policy. Out of scope this pass; modelled in full."""

    __tablename__ = "claim"
    __table_args__ = (
        Index("ix_claim_broker_status", "broker_id", "status"),
        Index("ix_claim_broker_event", "broker_id", "event_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy.id", ondelete="SET NULL"), index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("asset.id", ondelete="SET NULL"), index=True
    )

    claim_number: Mapped[str | None] = mapped_column(String(64), index=True)
    # Line the loss was reported under (insurer's own wording, e.g. "Incendio").
    kind: Mapped[str | None] = mapped_column(String(160))
    event_date: Mapped[date | None] = mapped_column(Date)
    reported_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(
        sql_enum(ClaimStatus), default=ClaimStatus.REPORTED, nullable=False
    )

    estimated_amount_uf: Mapped[Decimal | None] = mapped_column(UF)
    settled_amount_uf: Mapped[Decimal | None] = mapped_column(UF)
    paid_amount_uf: Mapped[Decimal | None] = mapped_column(UF)
    recovery_uf: Mapped[Decimal | None] = mapped_column(UF)
    reserve_uf: Mapped[Decimal | None] = mapped_column(UF)
    cost_uf: Mapped[Decimal | None] = mapped_column(UF)
    # Participation flags as reported by the insurer, e.g. ["C","A","B"].
    participation: Mapped[list[Any] | None] = mapped_column(JSONType)

    broker: Mapped["Broker"] = relationship()
    policy: Mapped["Policy | None"] = relationship(back_populates="claims")
    client: Mapped["Client"] = relationship(back_populates="claims")
    asset: Mapped["Asset | None"] = relationship(foreign_keys=[asset_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Claim id={self.id} number={self.claim_number!r}>"


__all__ = [
    "Policy",
    "PolicyStatus",
    "CoinsuranceShare",
    "PolicyLocation",
    "CoverageItem",
    "Claim",
    "ClaimStatus",
]
