"""Pydantic v2 schemas for ``offering`` — the shareable package sent to the insured.

Two read shapes on purpose:
  * ``OfferingRead`` — the broker's internal view (ids, token, audit fields).
  * ``OfferingPublicRead`` — what the unauthenticated share link exposes. It
    deliberately omits ``broker_id`` and every internal id, and carries only the
    headline figures of the recommended proposal.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.offering import OfferingChannel, OfferingStatus


class OfferingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_request_id: int = Field(gt=0)
    # Optional at creation, but required before the offering can be sent.
    selected_proposal_id: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None


class OfferingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_proposal_id: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None
    status: OfferingStatus | None = None


class OfferingSend(BaseModel):
    """Record how the offering left the building. Sending itself is manual."""

    model_config = ConfigDict(extra="forbid")

    channel: OfferingChannel
    sent_at: datetime | None = None


class OfferingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    quote_request_id: int
    selected_proposal_id: int | None = None
    share_token: str
    pdf_document_id: int | None = None
    sent_via: OfferingChannel | None = None
    status: OfferingStatus
    sent_at: datetime | None = None
    viewed_at: datetime | None = None
    expires_at: datetime | None = None
    created_by_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Derived, not stored: the public link the broker copies into WhatsApp/email.
    share_url: str | None = None


class OfferingListResponse(BaseModel):
    total: int
    items: list[OfferingRead]


class OfferingPublicProposal(BaseModel):
    """Headline figures of the recommended proposal, for the public page."""

    insurer_name: str | None = None
    insurer_cmf_code: str | None = None
    modality: str | None = None
    total_premium_uf: Decimal | None = None
    net_premium_uf: Decimal | None = None
    vat_uf: Decimal | None = None
    comprehensive_rate_permille: Decimal | None = None
    validity_business_days: int | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None


class OfferingPublicRead(BaseModel):
    """The unauthenticated share-link view. No broker/internal ids, no commission."""

    share_token: str
    status: OfferingStatus
    sent_at: datetime | None = None
    viewed_at: datetime | None = None
    expires_at: datetime | None = None
    broker_name: str | None = None
    insured_object: str | None = None
    declared_value_uf: Decimal | None = None
    proposal: OfferingPublicProposal | None = None
    pdf_url: str | None = None


__all__ = [
    "OfferingCreate",
    "OfferingUpdate",
    "OfferingSend",
    "OfferingRead",
    "OfferingListResponse",
    "OfferingPublicProposal",
    "OfferingPublicRead",
]
