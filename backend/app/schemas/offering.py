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
    # The insured's own choice from the public surface — surfaced to the broker
    # next to ``selected_proposal_id`` (the broker's RECOMMENDATION). The broker
    # picks it up and mints the broker_proposal (which advances the case stage).
    decided_proposal_id: int | None = None
    decided_note: str | None = None
    decided_at: datetime | None = None
    # Derived, not stored: the public link the broker copies into WhatsApp/email.
    share_url: str | None = None


class OfferingListResponse(BaseModel):
    total: int
    items: list[OfferingRead]


class OfferingPublicProposal(BaseModel):
    """Headline figures of a proposal, for the public page.

    ``id`` is the ``proposal.id`` — the insured posts it back to record a choice.
    Commission and every broker-internal field are deliberately absent."""

    id: int | None = None
    insurer_name: str | None = None
    insurer_cmf_code: str | None = None
    modality: str | None = None
    is_recommended: bool = False
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
    # The broker's recommendation (kept for back-compat) plus EVERY open proposal
    # of the quote in clear language, so the insured can compare and choose.
    proposal: OfferingPublicProposal | None = None
    proposals: list[OfferingPublicProposal] = Field(default_factory=list)
    # The insured's recorded choice (once decided).
    decided_proposal_id: int | None = None
    decided_note: str | None = None
    decided_at: datetime | None = None
    pdf_url: str | None = None


class OfferingDecisionRequest(BaseModel):
    """The insured's public choice: which proposal, plus an optional note.

    This is a PUBLIC WRITE path — the narrow projection IS the only guard, so
    nothing here (or in ``OfferingPublicRead``) may carry a broker-internal id."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=2000)


__all__ = [
    "OfferingCreate",
    "OfferingUpdate",
    "OfferingSend",
    "OfferingRead",
    "OfferingListResponse",
    "OfferingPublicProposal",
    "OfferingPublicRead",
    "OfferingDecisionRequest",
]
