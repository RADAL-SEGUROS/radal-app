"""Pydantic v2 schemas for ``quote_request`` and its ``quote_line_item`` children.

The one hard invariant carried here (docs/v2-data-modeling-decisions.md §5):

    quote_request.declared_value_uf == SUM(quote_line_item.value_uf)

It is checked by :func:`check_declared_value` and surfaced as a precise ``422``
by the router, because the sum can also drift through the nested line-item
endpoints (add / update / delete), not only on create.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Priority
from app.models.placement import PlacementStatus
from app.models.quote import QuoteRequestStatus
from app.schemas.analytics import GroupedSummary

# UF is stored Numeric(14,4); source documents round to 2 decimals, so a cent of
# slack keeps honest data from tripping the invariant.
UF_TOLERANCE = Decimal("0.01")


# --- Line items --------------------------------------------------------------

class QuoteLineItemBase(BaseModel):
    """One declared-value line item (partida): building, machinery, stock, ..."""

    name: str = Field(min_length=1, max_length=255)
    value_uf: Decimal = Field(ge=0)
    detail: str | None = None
    sort_order: int = 0


class QuoteLineItemCreate(QuoteLineItemBase):
    pass


class QuoteLineItemUpdate(BaseModel):
    """Partial update — only the fields actually sent are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    value_uf: Decimal | None = Field(default=None, ge=0)
    detail: str | None = None
    sort_order: int | None = None


class QuoteLineItemRead(QuoteLineItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    quote_request_id: int


# --- Quote request -----------------------------------------------------------

class QuoteRequestBase(BaseModel):
    insured_object: str | None = None
    declared_value_uf: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="UF", max_length=8)
    requested_coverages: str | None = None
    desired_start: date | None = None
    desired_end: date | None = None
    due_at: datetime | None = None
    priority: Priority = Priority.NORMAL
    recipient_insurer_ids: list[int] | None = None


class QuoteRequestCreate(QuoteRequestBase):
    """Create a quote request against one of the broker's own placements."""

    placement_id: int
    status: QuoteRequestStatus = QuoteRequestStatus.DRAFT
    line_items: list[QuoteLineItemCreate] = Field(default_factory=list)


class QuoteRequestUpdate(BaseModel):
    """Partial update. ``placement_id`` is immutable — recreate instead.

    Sending ``line_items`` REPLACES the whole set (the child rows are owned by
    the quote request); omitting it leaves the existing items untouched.
    """

    insured_object: str | None = None
    declared_value_uf: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=8)
    requested_coverages: str | None = None
    desired_start: date | None = None
    desired_end: date | None = None
    due_at: datetime | None = None
    priority: Priority | None = None
    status: QuoteRequestStatus | None = None
    recipient_insurer_ids: list[int] | None = None
    line_items: list[QuoteLineItemCreate] | None = None


class QuoteRequestSend(BaseModel):
    """Mark a draft quote request as sent to a set of insurers."""

    recipient_insurer_ids: list[int] = Field(min_length=1)
    due_at: datetime | None = None


class PlacementRef(BaseModel):
    """Just enough placement context for a quote list row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    asset_id: int
    insurance_line_id: int
    period: str | None = None
    status: PlacementStatus


class QuoteRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_id: int
    placement_id: int
    insured_object: str | None
    declared_value_uf: Decimal | None
    currency: str
    requested_coverages: str | None
    desired_start: date | None
    desired_end: date | None
    sent_at: datetime | None
    due_at: datetime | None
    priority: Priority
    status: QuoteRequestStatus
    recipient_insurer_ids: list[Any] | None
    created_by_id: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    line_items: list[QuoteLineItemRead] = Field(default_factory=list)
    # Derived, never stored: the sum the invariant is checked against.
    line_items_total_uf: Decimal = Decimal("0")
    proposal_count: int = 0
    placement: PlacementRef | None = None


class QuoteRequestSummary(BaseModel):
    """The quotes board: totals, the status split and the two clocks."""

    total: int
    by_status: dict[str, int]
    sent_last_30d: int
    overdue: int
    grouped: GroupedSummary | None = Field(
        default=None,
        description="Buckets listos para el gráfico cuando se pide ?group_by=.",
    )


class QuoteRequestPage(BaseModel):
    items: list[QuoteRequestRead]
    total: int
    limit: int
    offset: int


# --- The invariant -----------------------------------------------------------

def line_items_total(values: list[Decimal | None]) -> Decimal:
    """Sum of line-item values, treating a NULL value as zero."""
    return sum((v for v in values if v is not None), Decimal("0"))


def check_declared_value(
    declared_value_uf: Decimal | None, items_total: Decimal, item_count: int
) -> dict[str, Any] | None:
    """Validate ``declared_value_uf == SUM(line_items.value_uf)``.

    Returns ``None`` when the quote request is consistent, or a ready-to-return
    ``422`` detail payload when it is not. A quote request with NO line items is
    free to declare any value (the itemised breakdown is optional); as soon as
    there is at least one item the declared value must exist and match.
    """
    if item_count == 0:
        return None
    if declared_value_uf is None:
        return {
            "code": "declared_value_required",
            "message": (
                "declared_value_uf is required once the quote request has line "
                "items, and must equal their sum."
            ),
            "field": "declared_value_uf",
            "line_items_total_uf": str(items_total),
            "line_item_count": item_count,
        }
    difference = Decimal(declared_value_uf) - items_total
    if abs(difference) > UF_TOLERANCE:
        return {
            "code": "declared_value_mismatch",
            "message": (
                f"declared_value_uf ({declared_value_uf}) must equal the sum of "
                f"the {item_count} line items ({items_total})."
            ),
            "field": "declared_value_uf",
            "declared_value_uf": str(declared_value_uf),
            "line_items_total_uf": str(items_total),
            "difference_uf": str(difference),
            "tolerance_uf": str(UF_TOLERANCE),
            "line_item_count": item_count,
        }
    return None


__all__ = [
    "UF_TOLERANCE",
    "QuoteLineItemCreate",
    "QuoteLineItemUpdate",
    "QuoteLineItemRead",
    "QuoteRequestCreate",
    "QuoteRequestUpdate",
    "QuoteRequestSend",
    "QuoteRequestRead",
    "QuoteRequestSummary",
    "QuoteRequestPage",
    "PlacementRef",
    "line_items_total",
    "check_declared_value",
]
