"""``account_group`` — the broker-PRIVATE Group above the expediente.

The broker's daily loop is *open group -> see vigencias -> see ramos -> act*.
A group is the label the broker uses for one commercial relationship
("JO PASTELERÍA" is not the legal "Pacto Food SpA"), and it may span several
RUTs: Grupo Viña Indómita is ONE group over two ``client`` rows.

Shape rules (docs/v3-groups-accounts-spec.md §2.1):
  * TABLE NAME IS ``account_group``, NEVER ``group`` — ``GROUP`` is a MySQL
    reserved word (``test_case_file_tables_are_not_mysql_reserved_words``);
  * a group carries NO money and NO stage — those live on the account folder
    (``case_file(kind=account|renewal)``); deleting/archiving a group only
    detaches (every FK pointing here is ``SET NULL``), never cascades;
  * NO ``primary_client_id`` column: it would need a ``use_alter`` FK, which
    the migrator's create-table path never emits. The primary RUT is DERIVED
    (``client_id`` of the account with the latest ``period_start``);
  * ``slug`` is the importer/backfill idempotency key, unique per broker.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base_class import Base, TimestampMixin
from app.models.enums import StrEnum, sql_enum

if TYPE_CHECKING:
    from app.models.broker import Broker
    from app.models.case_file import CaseFile
    from app.models.client import Client
    from app.models.document import Document


class AccountGroupStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AccountGroupIconKind(StrEnum):
    """How the group's avatar is rendered (v8).

    ``emoji`` -> ``icon_value`` is the emoji character; ``glyph`` -> a curated
    glyph name in ``icon_value``; ``image`` -> ``icon_document_id`` points at an
    uploaded ``document`` (rule 8: the S3 key lives only on the document row).
    """

    EMOJI = "emoji"
    GLYPH = "glyph"
    IMAGE = "image"


class AccountGroup(Base, TimestampMixin):
    """One broker-private commercial group: the folder above the vigencias."""

    __tablename__ = "account_group"
    __table_args__ = (
        Index("ix_account_group_broker_slug", "broker_id", "slug", unique=True),
        Index("ix_account_group_broker_status", "broker_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Broker-private label; never the insured's legal name.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Slugified ``name``; the importer / backfill get-or-create key.
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[AccountGroupStatus] = mapped_column(
        sql_enum(AccountGroupStatus), default=AccountGroupStatus.ACTIVE, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    # --- Group avatar (v8): emoji char / curated glyph name / uploaded image --
    icon_kind: Mapped[AccountGroupIconKind | None] = mapped_column(
        sql_enum(AccountGroupIconKind)
    )
    icon_value: Mapped[str | None] = mapped_column(String(64))
    icon_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document.id", ondelete="SET NULL"), index=True
    )

    # --- Relationships ------------------------------------------------------
    broker: Mapped["Broker"] = relationship()
    icon_document: Mapped["Document | None"] = relationship(foreign_keys=[icon_document_id])
    # A broker x RUT belongs to <= 1 group; detaching is SET NULL, never a delete.
    clients: Mapped[list["Client"]] = relationship(
        back_populates="account_group", passive_deletes=True
    )
    # Every folder of the group (accounts, renewals AND their post-sale
    # children, which inherit ``account_group_id`` at creation).
    case_files: Mapped[list["CaseFile"]] = relationship(
        back_populates="account_group", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AccountGroup id={self.id} broker_id={self.broker_id} slug={self.slug!r}>"


__all__ = ["AccountGroup", "AccountGroupStatus", "AccountGroupIconKind"]
