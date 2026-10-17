"""Portal search RPA — credentials, runs and the quotes they bring back (v12).

docs/v12/06-rpa.md is the contract. Playwright connectors (HDI first) log in to
an insurer's portal with the BROKER's credentials, quote, and return
``portal_quote`` rows. Portals change without notice, so every failure carries
a typed ``error_kind`` (the user-facing message key) plus an internal
``error_detail``; the step log is JSON (heterogeneous, rendered, never queried)
and a failure screenshot is a ``document`` (rule 8).

``portal_credential`` passwords are ENCRYPTED (``app.core.crypto``) and
write-only. A quote the broker keeps becomes a ``proposal`` (``proposal_id``).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import CredentialStatus, PortalErrorKind, PortalRunStatus, sql_enum
from app.models.types import UF, JSONType


class PortalCredential(Base, TimestampMixin):
    """The broker's login to one insurer portal."""

    __tablename__ = "portal_credential"
    __table_args__ = (
        UniqueConstraint("broker_id", "portal", name="uq_portal_credential_broker_portal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Connector key, e.g. "hdi".
    portal: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CredentialStatus] = mapped_column(
        sql_enum(CredentialStatus), default=CredentialStatus.UNVERIFIED, nullable=False
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class PortalSearchRun(Base, TimestampMixin):
    """One quoting run against one portal."""

    __tablename__ = "portal_search_run"
    __table_args__ = (Index("ix_portal_search_run_broker_status", "broker_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    portal: Mapped[str] = mapped_column(String(32), nullable=False)
    case_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("case_file.id", ondelete="SET NULL"), index=True
    )
    insurance_subtype_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurance_subtype.id", ondelete="SET NULL"), index=True
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    # The connector's input form (vehicle, insured, period …).
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[PortalRunStatus] = mapped_column(
        sql_enum(PortalRunStatus), default=PortalRunStatus.QUEUED, nullable=False
    )
    error_kind: Mapped[PortalErrorKind | None] = mapped_column(sql_enum(PortalErrorKind))
    # Technical, internal — never shown verbatim to the broker.
    error_detail: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[list[Any] | None] = mapped_column(JSONType)
    screenshot_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )
    connector_version: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    quotes: Mapped[list["PortalQuote"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="PortalQuote.id"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PortalSearchRun id={self.id} {self.portal} {self.status}>"


class PortalQuote(Base, TimestampMixin):
    """One plan/price a portal returned."""

    __tablename__ = "portal_quote"

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("portal_search_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    insurer_id: Mapped[int | None] = mapped_column(
        ForeignKey("insurer.id", ondelete="SET NULL"), index=True
    )
    plan_name: Mapped[str | None] = mapped_column(String(255))
    premium_uf: Mapped[Decimal | None] = mapped_column(UF)
    premium_clp: Mapped[int | None] = mapped_column(Integer)
    deductible: Mapped[str | None] = mapped_column(String(255))
    coverages: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONType)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # Set when the broker keeps the quote as a cotización.
    proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposal.id", ondelete="SET NULL"), index=True
    )

    run: Mapped["PortalSearchRun"] = relationship(back_populates="quotes")


__all__ = ["PortalCredential", "PortalSearchRun", "PortalQuote"]
