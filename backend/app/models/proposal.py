"""``proposal`` — THE CENTER OF GRAVITY, plus its coverage/exclusion children.

Hard rules (docs/v2-architecture.md §4.3):
  * A proposal CANNOT exist without its source document -> ``source_document_id``
    is NOT NULL. The file is mandatory context for the AI and for the audit trail.
  * Its insurer must carry both ``rut`` and ``cmf_code`` (enforced at the service
    layer; the insurer table already makes both NOT NULL + unique).
  * AI pre-fills, the broker CONFIRMS before commit (``is_confirmed``). The
    extraction and its confidence are retained forever for traceability.

Money is COLUMNS, not JSON (docs/v2-data-modeling-decisions.md §2) — it is the
comparator's core and must be sortable/filterable/aggregatable in SQL. The
Chilean invariants, enforced in the service layer:

    net   = taxable + exempt          # earthquake cover is VAT-exempt
    vat   = 0.19 * taxable            # on the TAXABLE part, NOT on net
    total = net + vat
    comprehensive_rate = taxable_rate + exempt_rate

Deductibles are JSON keyed by peril (§3) because perils differ per line
(property: fire/earthquake; transport, fleet and cyber have entirely different
ones), and because the *basis* differs per peril — fire is a % of the loss while
earthquake is a % of the insured amount of the affected item:

    {"fire":       {"basis": "loss",           "pct": 5, "min_uf": 25},
     "earthquake": {"basis": "insured_amount", "pct": 1, "min_uf": 300},
     "other":      {"basis": "fixed",                    "min_uf": 15}}
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
from app.models.enums import CoverageKind, ProposalOutcome, StrEnum, sql_enum
from app.models.types import PCT, RATE, UF, JSONType

if TYPE_CHECKING:
    from app.models.ai import Extraction
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.document import Document
    from app.models.insurer import Insurer
    from app.models.policy import Policy
    from app.models.quote import QuoteRequest
    from app.models.user import User


class ProposalOrigin(StrEnum):
    """Where the proposal came from: a Radal-native insurer or an external one."""

    NATIVE = "native"
    EXTERNAL = "external"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class Proposal(Base, TimestampMixin):
    """One insurer's offer against one quote request, standardised for comparison."""

    __tablename__ = "proposal"
    __table_args__ = (
        Index("ix_proposal_broker_status", "broker_id", "status"),
        Index("ix_proposal_quote_insurer", "quote_request_id", "insurer_id"),
        Index("ix_proposal_broker_origin", "broker_id", "origin"),
        Index("ix_proposal_broker_confirmed", "broker_id", "is_confirmed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_request_id: Mapped[int] = mapped_column(
        ForeignKey("quote_request.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurer_id: Mapped[int] = mapped_column(
        ForeignKey("insurer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    origin: Mapped[ProposalOrigin] = mapped_column(
        sql_enum(ProposalOrigin), default=ProposalOrigin.EXTERNAL, nullable=False
    )
    # ``use_alter``: case_file reaches proposal transitively (case_file -> policy
    # -> proposal), so this back-pointer closes a genuine FK cycle. See
    # ``document.case_file_id`` for the same treatment.
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "case_file.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_proposal_case_file",
        ),
        index=True,
    )

    # MANDATORY source file. A proposal without its document cannot exist.
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("document.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Cover modality, e.g. "Todo Riesgo de daño físico".
    modality: Mapped[str | None] = mapped_column(String(255))
    # Insurer's activity classification of the risk, e.g. "10310 — ...".
    activity_classification: Mapped[str | None] = mapped_column(String(255))

    # --- Money (UF) — COLUMNS, see module docstring -------------------------
    taxable_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    exempt_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    net_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    vat_uf: Mapped[Decimal | None] = mapped_column(UF)
    total_premium_uf: Mapped[Decimal | None] = mapped_column(UF, index=True)

    # --- Rates (per mille) --------------------------------------------------
    taxable_rate_permille: Mapped[Decimal | None] = mapped_column(RATE)
    exempt_rate_permille: Mapped[Decimal | None] = mapped_column(RATE)
    comprehensive_rate_permille: Mapped[Decimal | None] = mapped_column(RATE)

    # Broker commission, 0-100. Kept separable so it can be hidden from insured
    # users later without a migration.
    commission_pct: Mapped[Decimal | None] = mapped_column(PCT)

    # --- Validity & cover period -------------------------------------------
    validity_business_days: Mapped[int | None] = mapped_column(Integer)
    coverage_start: Mapped[date | None] = mapped_column(Date)
    coverage_end: Mapped[date | None] = mapped_column(Date)
    received_at: Mapped[date | None] = mapped_column(Date)

    # --- Structured terms ---------------------------------------------------
    deductibles: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # Conditions the insured must keep for cover to hold (garantías).
    warranties: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    status: Mapped[ProposalStatus] = mapped_column(
        sql_enum(ProposalStatus), default=ProposalStatus.DRAFT, nullable=False
    )
    # How the insurer ANSWERED, which is not the same axis as ``status``: a
    # declination and a conditional pronouncement are both real answers that
    # never become an offer. NULL until the answer is filed.
    outcome: Mapped[ProposalOutcome | None] = mapped_column(sql_enum(ProposalOutcome))

    # Carrier's own quotation folio, verbatim.
    quotation_number: Mapped[str | None] = mapped_column(String(64), index=True)
    # Cover modality as quoted, e.g. "Todo Riesgo" vs "Riesgos Nombrados".
    cover_mode: Mapped[str | None] = mapped_column(String(64))

    # --- AI prose: suggest -> human edit -> confirm --------------------------
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_summary_model: Mapped[str | None] = mapped_column(String(120))
    ai_summary_prompt_version: Mapped[str | None] = mapped_column(String(64))
    is_summary_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

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
    quote_request: Mapped["QuoteRequest"] = relationship(back_populates="proposals")
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    insurer: Mapped["Insurer"] = relationship(back_populates="proposals")
    source_document: Mapped["Document"] = relationship(foreign_keys=[source_document_id])
    extraction: Mapped["Extraction | None"] = relationship(
        back_populates="proposals", foreign_keys=[extraction_id]
    )
    confirmed_by: Mapped["User | None"] = relationship(foreign_keys=[confirmed_by_id])
    coverages: Mapped[list["ProposalCoverage"]] = relationship(
        back_populates="proposal",
        cascade="all, delete-orphan",
        order_by="ProposalCoverage.sort_order",
    )
    policy: Mapped["Policy | None"] = relationship(back_populates="proposal", uselist=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Proposal id={self.id} insurer_id={self.insurer_id} status={self.status}>"


class ProposalCoverage(Base, TimestampMixin):
    """One covered item or one exclusion of a proposal.

    A CHILD TABLE rather than a JSON array (docs §4): the comparator must ALIGN
    items across competing proposals ("who covers earthquake?") — that is a join,
    not a blob. ``normalized_code`` starts NULL and is filled by AI normalisation
    later, which would be impossible to add cleanly inside a JSON array.
    """

    __tablename__ = "proposal_coverage"
    __table_args__ = (
        Index("ix_proposal_coverage_kind", "proposal_id", "kind", "sort_order"),
        Index("ix_proposal_coverage_normalized", "normalized_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposal.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[CoverageKind] = mapped_column(sql_enum(CoverageKind), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Cross-proposal alignment key, populated by AI normalisation. NULL until then.
    normalized_code: Mapped[str | None] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    proposal: Mapped["Proposal"] = relationship(back_populates="coverages")


__all__ = [
    "Proposal",
    "ProposalOrigin",
    "ProposalStatus",
    "ProposalOutcome",
    "ProposalCoverage",
]
