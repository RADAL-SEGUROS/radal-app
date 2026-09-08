"""``account_client`` — RUT membership of an account folder.

An account (``case_file(kind=account|renewal)``) is one insurance line x one
validity period x N RUTs. Membership = ``account_client`` rows UNION the
``placement.case_file_id`` rows; ``case_file.client_id`` stays the contratante.
This table exists so a RUT can be a member WITHOUT inventing an asset
(``placement.asset_id`` is NOT NULL).

No FK cycle: ``case_file`` never points back here, so no ``use_alter`` is
needed and the migrator's create-table path can emit it as-is.

Service rule (not a DB constraint, because it spans two tables):
``client.account_group_id == case_file.account_group_id`` or 422
``client_not_in_group``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.client import Client


class AccountClientRole(StrEnum):
    POLICYHOLDER = "policyholder"
    INSURED = "insured"


class AccountClient(Base, TimestampMixin):
    """One RUT on one account folder."""

    __tablename__ = "account_client"
    __table_args__ = (
        Index("ix_account_client_case_client", "case_file_id", "client_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The account folder.
    case_file_id: Mapped[int] = mapped_column(
        ForeignKey("case_file.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[AccountClientRole] = mapped_column(
        sql_enum(AccountClientRole), default=AccountClientRole.INSURED, nullable=False
    )
    # The contratante's row (mirrors ``case_file.client_id``).
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    case_file: Mapped["CaseFile"] = relationship(back_populates="account_clients")
    client: Mapped["Client"] = relationship(foreign_keys=[client_id])

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AccountClient case={self.case_file_id} client={self.client_id} "
            f"role={self.role} primary={self.is_primary}>"
        )


__all__ = ["AccountClient", "AccountClientRole"]
