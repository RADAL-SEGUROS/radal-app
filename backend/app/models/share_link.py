"""``share_link`` — public, WhatsApp-ready links for people who do not log in (v12).

Insureds and insurers will not have accounts for weeks/months (product decision
§1.7), so a share link IS their access: the ``token`` is the credential
(``secrets.token_urlsafe(24)``), exactly like ``offering``'s public route. The
public projection is narrow by audience and never returns other policies,
document keys, internal notes or commission.

``entity_type``/``entity_id`` is a polymorphic pointer (policy, plan, cuota,
case file, status inquiry); the router resolves it with the link's
``broker_id``, so a link can never reach another tenant's row. A revoked or
expired link is a 404, indistinguishable from an unknown token.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_class import Base, TimestampMixin
from app.models.enums import ShareAudience, ShareEntity, sql_enum
from app.models.types import JSONType


class ShareLink(Base, TimestampMixin):
    """One public link to one entity, for one audience."""

    __tablename__ = "share_link"
    __table_args__ = (
        Index("ix_share_link_broker_entity", "broker_id", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    broker_id: Mapped[int] = mapped_column(
        ForeignKey("broker.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    entity_type: Mapped[ShareEntity] = mapped_column(sql_enum(ShareEntity), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    audience: Mapped[ShareAudience] = mapped_column(sql_enum(ShareAudience), nullable=False)
    # {can_pay, can_upload_receipt, can_report_status, show_documents}
    permissions: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    label: Mapped[str | None] = mapped_column(String(160))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ShareLink id={self.id} {self.entity_type}:{self.entity_id} {self.audience}>"


__all__ = ["ShareLink"]
