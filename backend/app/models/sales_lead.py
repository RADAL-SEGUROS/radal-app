"""``sales_lead`` — the data-point-only lead, before there is an insured RUT.

TABLE NAME IS ``sales_lead``, NOT ``lead``. ``LEAD`` is a MySQL 8 reserved word
(the window function), so a bare ``lead`` table would depend on the dialect
quoting it correctly. Verified against
``sqlalchemy.dialects.mysql.base.RESERVED_WORDS_MYSQL``.

A lead is deliberately NOT a ``client``: it exists before identity is known. No
files, no placement — a tracked data point with notes and one follow-up date.
Advancing it (``POST /leads/{id}/convert``) creates the insured + client +
placement + ``case_file(kind=account, stage=intake)`` in one transaction and
fills the two conversion FKs.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import LeadStatus, sql_enum
from app.models.types import UF

if TYPE_CHECKING:
    from app.models.account_group import AccountGroup
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.client import Client
    from app.models.insurance_line import InsuranceLine
    from app.models.user import User


class SalesLead(Base, TimestampMixin):
    """A commercial data point the broker is tracking towards a first case."""

    __tablename__ = "sales_lead"
    __table_args__ = (
        Index("ix_sales_lead_broker_status", "broker_id", "status"),
        Index("ix_sales_lead_broker_followup", "broker_id", "follow_up_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Trade name as the broker heard it.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Normalized BODY-DV when supplied; mod-11 validated. NEVER required — a
    # broker is never blocked from tracking a prospect.
    rut: Mapped[str | None] = mapped_column(String(16), index=True)

    contact_name: Mapped[str | None] = mapped_column(String(160))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    # referral, inbound, campaign, ...
    source: Mapped[str | None] = mapped_column(String(80))

    insurance_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_line.id", ondelete="SET NULL"), index=True
    )
    estimated_premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    status: Mapped[LeadStatus] = mapped_column(
        sql_enum(LeadStatus), default=LeadStatus.NEW, nullable=False
    )
    # The single date the team asked for.
    follow_up_on: Mapped[date | None] = mapped_column(Date)

    # --- Groups & accounts (v3) ---------------------------------------------
    # "crear grupo -> crear cuenta" starts at the lead: the target group and
    # the intended vigencia are forwarded by ``LeadConvert`` into the account
    # folder. SET NULL: the group may be archived before conversion.
    account_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_group.id", ondelete="SET NULL"), index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)

    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    lost_reason: Mapped[str | None] = mapped_column(String(255))

    # Filled on conversion.
    converted_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("client.id", ondelete="SET NULL"), index=True
    )
    converted_case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )

    summary: Mapped[str | None] = mapped_column(Text)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    insurance_line: Mapped["InsuranceLine | None"] = relationship(
        foreign_keys=[insurance_line_id]
    )
    account_group: Mapped["AccountGroup | None"] = relationship(
        foreign_keys=[account_group_id]
    )
    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_user_id])
    converted_client: Mapped["Client | None"] = relationship(
        foreign_keys=[converted_client_id]
    )
    converted_case_file: Mapped["CaseFile | None"] = relationship(
        foreign_keys=[converted_case_file_id]
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SalesLead id={self.id} name={self.name!r} status={self.status}>"


__all__ = ["SalesLead"]
