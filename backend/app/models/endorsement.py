"""``endorsement`` — an amendment to an issued policy.

Money deltas are COLUMNS, not JSON: the shape is fixed in every one of the 14
corpus endorsements and we SUM them into the collection plan. Sign-preserving
invariants, enforced in the schema layer exactly as for a proposal:

    net_delta   = taxable_delta + exempt_delta
    vat_delta   = 0.19 * taxable_delta      # on the TAXABLE part, NOT on net
    total_delta = net_delta + vat_delta

An administrative endorsement (pledge update, policyholder change) is all-zero —
valid, and it must not trip the validator.

``effect`` is JSON because the before/after/delta table's shape varies per kind
(a vehicle inclusion and a roster adjustment share no columns). ``deductibles``
mirrors ``proposal.deductibles``: the replacement set, keyed by peril.

``effective_at`` / ``ends_at`` are DateTime, never bare Date: the noon
convention is contractual in the Chilean market.
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import EndorsementKind, EndorsementStatus, sql_enum
from app.models.types import PCT, UF, JSONType

if TYPE_CHECKING:
    from app.models.ai import Extraction
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.document import Document
    from app.models.policy import Policy
    from app.models.user import User


class Endorsement(Base, TimestampMixin):
    """One endorsement (endoso) proposed to, or issued by, the carrier."""

    __tablename__ = "endorsement"
    __table_args__ = (
        Index("ix_endorsement_broker_status", "broker_id", "status"),
        Index("ix_endorsement_policy_sequence", "policy_id", "sequence_no"),
        Index("ix_endorsement_broker_kind", "broker_id", "kind"),
        Index("ix_endorsement_broker_batch", "broker_id", "batch_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # An endorsement cannot exist without its policy.
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )

    # Carrier folio, verbatim (e.g. "791237-4").
    endorsement_number: Mapped[str | None] = mapped_column(String(64), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    kind: Mapped[EndorsementKind] = mapped_column(
        sql_enum(EndorsementKind), default=EndorsementKind.OTHER, nullable=False
    )
    status: Mapped[EndorsementStatus] = mapped_column(
        sql_enum(EndorsementStatus), default=EndorsementStatus.DRAFT, nullable=False
    )
    # UUID shared by the N endorsements of ONE prórroga (``POST /endorsements/
    # batch``): one row per policy, each still tied to exactly one policy
    # (rule 2). NULL for every single endorsement.
    batch_key: Mapped[str | None] = mapped_column(String(36), index=True)

    # The noon convention is contractual — never a bare Date.
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Can precede effective_at.
    issued_at: Mapped[date | None] = mapped_column(Date)

    # 07A / 07B / 09C.
    proposal_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    # 08A / 08B / 09B / 09D.
    issued_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    # Verbatim; the capitalised verb (INCLUYE / EXCLUYE / AUMENTA) is the signal.
    motive: Mapped[str | None] = mapped_column(Text)
    # Resolves back to a particular clause number.
    contractual_basis: Mapped[str | None] = mapped_column(String(255))

    # --- Money deltas (UF) — COLUMNS, sign-preserving -----------------------
    insured_amount_delta_uf: Mapped[Decimal | None] = mapped_column(UF)
    taxable_premium_delta_uf: Mapped[Decimal | None] = mapped_column(UF)
    exempt_premium_delta_uf: Mapped[Decimal | None] = mapped_column(UF)
    net_premium_delta_uf: Mapped[Decimal | None] = mapped_column(UF)
    vat_delta_uf: Mapped[Decimal | None] = mapped_column(UF)
    total_premium_delta_uf: Mapped[Decimal | None] = mapped_column(UF)
    commission_delta_uf: Mapped[Decimal | None] = mapped_column(UF)

    # Numerator / denominator of the pro-rata delta.
    prorata_days: Mapped[int | None] = mapped_column(Integer)
    unexpired_days: Mapped[int | None] = mapped_column(Integer)

    # The before/after/delta table — shape varies per kind.
    effect: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONType)
    # Replacement deductible set, when the endorsement changes it.
    deductibles: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    # --- AI provenance: suggest -> human confirm -> commit -------------------
    extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction.id", ondelete="SET NULL"), index=True
    )
    extraction_confidence: Mapped[Decimal | None] = mapped_column(PCT)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    policy: Mapped["Policy"] = relationship(
        foreign_keys=[policy_id], back_populates="endorsements"
    )
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    proposal_document: Mapped["Document | None"] = relationship(
        foreign_keys=[proposal_document_id]
    )
    issued_document: Mapped["Document | None"] = relationship(
        foreign_keys=[issued_document_id]
    )
    extraction: Mapped["Extraction | None"] = relationship(foreign_keys=[extraction_id])
    confirmed_by: Mapped["User | None"] = relationship(foreign_keys=[confirmed_by_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Endorsement id={self.id} policy={self.policy_id} "
            f"seq={self.sequence_no} status={self.status}>"
        )


__all__ = ["Endorsement"]
