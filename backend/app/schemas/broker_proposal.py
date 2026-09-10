"""Schemas for the outbound broker propuesta (v8).

TERMINOLOGY TRAP: ``Proposal`` is the insurer's INBOUND offer. ``BrokerProposal``
is the DIFFERENT, outbound artifact the broker assembles SOLELY from a comparison
snapshot, ratifies, and sends. See ``app.models.broker_proposal``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.broker_proposal import BrokerProposalStatus


class BrokerProposalCreate(BaseModel):
    """Mint a propuesta from a comparison and its chosen winning offer."""

    comparison_id: int
    winning_proposal_id: int
    # An optional broker note about why this offer won; stored as a ``note`` on
    # the resulting artifact (no dedicated column — reuse the note system).
    winner_note: str | None = None


class BrokerProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    case_file_id: int | None = None
    comparison_id: int | None = None
    winning_proposal_id: int | None = None

    content_hash: str | None = None
    payload: dict[str, Any] | None = None
    pdf_document_id: int | None = None

    status: BrokerProposalStatus
    is_ratified: bool = False
    ratified_at: datetime | None = None
    ratified_by_id: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrokerProposalRatify(BaseModel):
    """Manual ratification of the propuesta."""

    note: str | None = None


__all__ = [
    "BrokerProposalCreate",
    "BrokerProposalRead",
    "BrokerProposalRatify",
]
