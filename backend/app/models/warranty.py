"""``warranty`` — the R-n / G-n / M-n obligation tracker.

The single highest-value post-sale structure in the corpus: the same code
threads through inspection recommendation -> policy warranty -> collection
tracker -> claim notice -> adjuster report -> endorsement. Keeping it as rows
(never JSON on the policy) is what lets the app answer "was R-1 met before the
loss?", which is exactly what decided coverage in the demo files.

``is_suspensive`` marks an obligation whose breach suspends cover;
``category`` is the inspection's A/B/C/D letter, kept as the source's own token.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import WarrantySource, WarrantyStatus, sql_enum
from app.models.types import UF

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.document import Document
    from app.models.policy import Policy


class Warranty(Base, TimestampMixin):
    """One condition the insured must keep for cover to hold."""

    __tablename__ = "warranty"
    __table_args__ = (
        Index("ix_warranty_policy_code", "policy_id", "code"),
        Index("ix_warranty_broker_status", "broker_id", "status"),
        Index("ix_warranty_broker_due", "broker_id", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policy.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )

    # "R-1", "G-4", "M-5" — the thread that runs through every post-sale doc.
    code: Mapped[str | None] = mapped_column(String(16), index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    # Verbatim wording: in the corpus, the exact text IS the obligation.
    requirement: Mapped[str | None] = mapped_column(Text)

    source: Mapped[WarrantySource] = mapped_column(
        sql_enum(WarrantySource),
        default=WarrantySource.UNDERWRITING_WARRANTY,
        nullable=False,
    )
    # The inspection's A/B/C/D letter.
    category: Mapped[str | None] = mapped_column(String(4))

    deadline_days: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    is_permanent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_suspensive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[WarrantyStatus] = mapped_column(
        sql_enum(WarrantyStatus), default=WarrantyStatus.PENDING, nullable=False
    )
    completed_on: Mapped[date | None] = mapped_column(Date)
    verification: Mapped[str | None] = mapped_column(Text)
    budget_uf: Mapped[Decimal | None] = mapped_column(UF)
    actual_cost_uf: Mapped[Decimal | None] = mapped_column(UF)

    evidence_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    broker: Mapped["Broker"] = relationship()
    policy: Mapped["Policy"] = relationship(
        foreign_keys=[policy_id], back_populates="warranties"
    )
    case_file: Mapped["CaseFile | None"] = relationship(foreign_keys=[case_file_id])
    evidence_document: Mapped["Document | None"] = relationship(
        foreign_keys=[evidence_document_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Warranty id={self.id} code={self.code!r} status={self.status}>"


__all__ = ["Warranty"]
