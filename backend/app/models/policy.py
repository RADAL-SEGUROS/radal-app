"""Post-sale: ``policy`` and its children, plus ``claim``.

A policy is created ONLY after a proposal is accepted, so it inherits the same
money shape as the proposal (taxable / exempt / net / VAT / total in UF).
Post-sale depth is deliberately shallow in this pass — the UI renders these
sections visibly disabled ("pronto") rather than as dead controls — but the
model is complete so nothing needs a migration when it is switched on.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.enums import (
    ClaimItemKind,
    ClaimRuling,
    CoverageKind,
    StrEnum,
    sql_enum,
)
from app.models.base_class import Base, TimestampMixin
from app.models.types import PCT, RATE, UF, JSONType

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.client import Client
    from app.models.collection import CollectionPlan
    from app.models.ai import Extraction
    from app.models.document import Document
    from app.models.endorsement import Endorsement
    from app.models.insurance_line import InsuranceLine
    from app.models.insurer import Insurer
    from app.models.placement import Placement
    from app.models.proposal import Proposal
    from app.models.warranty import Warranty


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
    # The account expediente that produced the policy.
    # ``use_alter``: policy <-> case_file is a genuine FK cycle (see
    # ``document.case_file_id`` for the same treatment).
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "case_file.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_policy_case_file",
        ),
        index=True,
    )
    # The 08 the policy was read from — rule 8: FK to document, never a key.
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    # Renewal chain: the policy this one renews.
    renews_policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy.id", ondelete="SET NULL"), index=True
    )

    policy_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # The 12:00 convention is contractual. ``start_date``/``end_date`` stay and
    # are kept in sync by the service layer — nothing is dropped.
    period_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[date | None] = mapped_column(Date)
    status: Mapped[PolicyStatus] = mapped_column(
        sql_enum(PolicyStatus), default=PolicyStatus.DRAFT, nullable=False
    )

    # --- As-issued technical identity ---------------------------------------
    # "Todo Riesgo" vs "Riesgos Nombrados", verbatim from the carrier.
    cover_mode: Mapped[str | None] = mapped_column(String(64))
    # CMF-registered policy-conditions code (POL / CAD).
    cmf_policy_code: Mapped[str | None] = mapped_column(String(32))
    # How the insured amount is to be read (first loss, total value, ...).
    insured_amount_semantics: Mapped[str | None] = mapped_column(String(255))
    aggregate_limit_uf: Mapped[Decimal | None] = mapped_column(UF)
    average_rate_permille: Mapped[Decimal | None] = mapped_column(RATE)
    # Prose: in the corpus the exact wording IS the limit.
    indemnity_limit: Mapped[str | None] = mapped_column(Text)

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

    # --- v8 policy repurpose: full dynamic payload + fixed-core verdict --------
    # Everything the typed committer does NOT promote to a column lands here —
    # the same columns-plus-JSON-tail split as ``asset`` (decision #6). The typed
    # money/vigencia columns above stay the SINGLE source for their values;
    # ``payload`` never re-stores them (no double-sourcing).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # The fixed minimal-core verdict (corredor / datos asegurado / vigencia /
    # desglose de prima present). NULLable: NULL = not yet validated; the detail
    # of which core fields are present/missing lives in ``core_validation``.
    is_core_valid: Mapped[bool | None] = mapped_column(Boolean)
    core_validation: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # The dynamic parse this policy's payload was read from (mirrors
    # ``proposal.extraction_id``).
    extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )

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
    # The account case that produced this policy.
    case_file: Mapped["CaseFile | None"] = relationship(
        "CaseFile", foreign_keys=[case_file_id]
    )
    # The post-sale sub-funnel: endorsement / collection / claim / renewal cases.
    case_files: Mapped[list["CaseFile"]] = relationship(
        "CaseFile",
        foreign_keys="CaseFile.policy_id",
        back_populates="policy",
        order_by="(CaseFile.kind, CaseFile.sequence_no, CaseFile.version)",
    )
    source_document: Mapped["Document | None"] = relationship(
        foreign_keys=[source_document_id]
    )
    extraction: Mapped["Extraction | None"] = relationship(foreign_keys=[extraction_id])
    renews_policy: Mapped["Policy | None"] = relationship(
        "Policy", remote_side=[id], foreign_keys=[renews_policy_id]
    )
    endorsements: Mapped[list["Endorsement"]] = relationship(
        "Endorsement",
        foreign_keys="Endorsement.policy_id",
        back_populates="policy",
        order_by="Endorsement.sequence_no",
    )
    collection_plans: Mapped[list["CollectionPlan"]] = relationship(
        "CollectionPlan",
        foreign_keys="CollectionPlan.policy_id",
        back_populates="policy",
        cascade="all, delete-orphan",
    )
    warranties: Mapped[list["Warranty"]] = relationship(
        "Warranty",
        foreign_keys="Warranty.policy_id",
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="Warranty.sort_order",
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
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )

    claim_number: Mapped[str | None] = mapped_column(String(64), index=True)
    # Line the loss was reported under (insurer's own wording, e.g. "Incendio").
    kind: Mapped[str | None] = mapped_column(String(160))
    event_date: Mapped[date | None] = mapped_column(Date)
    reported_date: Mapped[date | None] = mapped_column(Date)
    # Hour-level, because the hourly franchise (4-hour BI waiting period) is
    # contractual. The existing Dates stay and are kept in sync.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Contractual notice window from knowledge of the loss.
    notice_deadline_days: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(
        sql_enum(ClaimStatus), default=ClaimStatus.REPORTED, nullable=False
    )

    # --- Adjuster (liquidador) and ruling -----------------------------------
    adjuster_name: Mapped[str | None] = mapped_column(String(160))
    # CMF registry number of the adjuster.
    adjuster_registry: Mapped[str | None] = mapped_column(String(32))
    coverage_ruling: Mapped[ClaimRuling] = mapped_column(
        sql_enum(ClaimRuling), default=ClaimRuling.PENDING, nullable=False
    )
    deductible_uf: Mapped[Decimal | None] = mapped_column(UF)
    loss_ratio_pct: Mapped[Decimal | None] = mapped_column(PCT)

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
    case_file: Mapped["CaseFile | None"] = relationship(
        "CaseFile", foreign_keys=[case_file_id]
    )
    items: Mapped[list["ClaimItem"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="ClaimItem.sort_order",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Claim id={self.id} number={self.claim_number!r}>"


class ClaimItem(Base, TimestampMixin):
    """One partida of a loss, as the adjuster quantified it.

    A CHILD TABLE, not JSON: the adjuster's tables are 1:N and every column is
    summed (notified -> determined -> damage -> deductible -> indemnity), which
    is exactly what the final report reconciles.
    """

    __tablename__ = "claim_item"
    __table_args__ = (
        Index("ix_claim_item_claim_order", "claim_id", "sort_order"),
        Index("ix_claim_item_broker_kind", "broker_id", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claim.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[ClaimItemKind] = mapped_column(
        sql_enum(ClaimItemKind), default=ClaimItemKind.MATERIAL_DAMAGE, nullable=False
    )
    item: Mapped[str | None] = mapped_column(String(255))
    # How the figure was arrived at — prose survives verbatim.
    basis: Mapped[str | None] = mapped_column(Text)

    notified_uf: Mapped[Decimal | None] = mapped_column(UF)
    determined_uf: Mapped[Decimal | None] = mapped_column(UF)
    damage_uf: Mapped[Decimal | None] = mapped_column(UF)
    deductible_uf: Mapped[Decimal | None] = mapped_column(UF)
    indemnity_uf: Mapped[Decimal | None] = mapped_column(UF)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    broker: Mapped["Broker"] = relationship()
    claim: Mapped["Claim"] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ClaimItem id={self.id} claim={self.claim_id} kind={self.kind}>"


__all__ = [
    "Policy",
    "PolicyStatus",
    "CoinsuranceShare",
    "PolicyLocation",
    "CoverageItem",
    "Claim",
    "ClaimStatus",
    "ClaimItem",
    "ClaimItemKind",
    "ClaimRuling",
]
