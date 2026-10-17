"""Shared helpers for checkout intents and provider charges (v12, 03 §5-§6).

* :func:`santiago_today` — business dates are America/Santiago (00 §3);
* :func:`charges_by_item` / :func:`provider_paid_item_ids` — which cuotas of an
  intent were actually charged through the provider (charge state 2 with an
  ACTIVE ``payment``);
* :func:`rollup_intent` — the intent's status from its items (all paid → paid,
  some → partially_paid; a reversal walks it back);
* :func:`find_charge` — THE charge identity: ``(account, external_installment_id)``
  first (OUR id, a SIGNED webhook field), Forpay's ``internalId`` only as a
  fallback — ``internalId`` is not signed, so it can never mint a second
  charge (and so a second payment) for one cuota;
* :func:`mark_charge_paid_outside` — the charge marker left when Radal tells
  the provider a cuota was paid by another path, so the provider's echo of
  that state-2 never becomes a provider payment.

Every function flushes at most; the caller commits.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import IntentStatus, PaymentStatus
from app.models.payment import Payment, PaymentIntent, PaymentIntentItem
from app.models.provider import ProviderCharge
from app.services.payments.providers.base import (
    STATE_IN_PROCESS,
    STATE_IN_VALIDATION,
    STATE_PAID,
)

logger = logging.getLogger(__name__)

#: ``provider_charge.raw`` key set when Radal reported the cuota paid outside the provider.
MARKED_PAID_KEY = "radal_marked_paid"

BUSINESS_TZ_NAME = "America/Santiago"

#: An intent whose link may still charge someone.
OPEN_INTENT_STATUSES: tuple[IntentStatus, ...] = (
    IntentStatus.CREATED,
    IntentStatus.PENDING,
    IntentStatus.PARTIALLY_PAID,
)


def _business_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(BUSINESS_TZ_NAME)
    except Exception:  # noqa: BLE001 - no tzdata: UTC is still deterministic
        return timezone.utc


def as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def santiago_today(now: datetime) -> date:
    """The America/Santiago calendar date of ``now`` (naive = UTC)."""
    return as_aware(now).astimezone(_business_tz()).date()


def santiago_date(moment: datetime | None) -> date | None:
    if moment is None:
        return None
    return santiago_today(moment)


def charges_by_item(db: Session, intent: PaymentIntent) -> dict[int, ProviderCharge]:
    """``{item.id: charge}`` for the items the provider has told us about."""
    items = list(intent.items)
    if not items:
        return {}
    by_ext = {item.external_installment_id: item.id for item in items}
    stmt = select(ProviderCharge).where(
        ProviderCharge.broker_id == intent.broker_id,
        ProviderCharge.external_installment_id.in_(list(by_ext)),
    )
    if intent.provider_account_id is not None:
        stmt = stmt.where(ProviderCharge.provider_account_id == intent.provider_account_id)
    out: dict[int, ProviderCharge] = {}
    for charge in db.scalars(stmt):
        item_id = by_ext.get(charge.external_installment_id)
        if item_id is not None:
            # Prefer a paid charge when the provider reported more than one.
            if item_id not in out or charge.state == STATE_PAID:
                out[item_id] = charge
    return out


def _active_payment_charge_ids(db: Session, charge_ids: Iterable[int]) -> set[int]:
    ids = [c for c in charge_ids if c is not None]
    if not ids:
        return set()
    return set(
        db.scalars(
            select(Payment.provider_charge_id).where(
                Payment.provider_charge_id.in_(ids),
                Payment.status == PaymentStatus.ACTIVE,
            )
        )
    )


def provider_paid_item_ids(db: Session, intent: PaymentIntent) -> set[int]:
    """Items charged through the provider (charge state 2 + an ACTIVE payment)."""
    charges = charges_by_item(db, intent)
    paid_charges = _active_payment_charge_ids(
        db, (c.id for c in charges.values() if c.state == STATE_PAID)
    )
    return {item_id for item_id, charge in charges.items() if charge.id in paid_charges}


def rollup_intent(db: Session, intent: PaymentIntent) -> IntentStatus:
    """Set ``intent.status`` from its items. Truth wins over a local cancel."""
    db.flush()
    items = list(intent.items)
    paid = provider_paid_item_ids(db, intent)
    if items and len(paid) == len(items):
        intent.status = IntentStatus.PAID
    elif paid:
        intent.status = IntentStatus.PARTIALLY_PAID
    elif intent.status in (IntentStatus.PAID, IntentStatus.PARTIALLY_PAID):
        # Every provider payment was reversed: the link is back to pending.
        intent.status = IntentStatus.PENDING
    elif intent.status == IntentStatus.CREATED:
        charges = charges_by_item(db, intent).values()
        if any(c.state in (STATE_IN_PROCESS, STATE_IN_VALIDATION) for c in charges):
            intent.status = IntentStatus.PENDING
    return intent.status


def find_charge(
    db: Session, account_id: int | None, *, ext: str | None, pid: str | None = None
) -> ProviderCharge | None:
    """The charge of one provider cuota: by OUR external id first, else by Forpay's id.

    The external id (``installment.id``) is signed; ``internalId`` is not and a
    receivable may omit it, so the external id is the identity. ``pid`` is only
    consulted when nothing matches ``ext``; the caller must check that a charge
    found that way does not belong to another external id.
    """
    if account_id is None:
        return None
    if ext:
        charge = db.scalars(
            select(ProviderCharge)
            .where(
                ProviderCharge.provider_account_id == account_id,
                ProviderCharge.external_installment_id == ext[:64],
            )
            .order_by(ProviderCharge.id)
        ).first()
        if charge is not None:
            return charge
    if pid:
        return db.scalar(
            select(ProviderCharge).where(
                ProviderCharge.provider_account_id == account_id,
                ProviderCharge.provider_installment_id == pid[:64],
            )
        )
    return None


def is_marked_paid_outside(charge: ProviderCharge) -> bool:
    return isinstance(charge.raw, dict) and bool(charge.raw.get(MARKED_PAID_KEY))


def mark_charge_paid_outside(
    db: Session, intent: PaymentIntent, item: PaymentIntentItem, *, now: datetime
) -> ProviderCharge | None:
    """Record that Radal told the provider this item is paid (state 2, no payment).

    Upserts the item's charge with :data:`MARKED_PAID_KEY`; the webhook core then
    mirrors the provider's echo without creating a ``payment``. Flushes only.
    """
    if intent.provider_account_id is None or not item.external_installment_id:
        return None
    ext = item.external_installment_id[:64]
    charge = find_charge(db, intent.provider_account_id, ext=ext)
    if charge is None:
        charge = ProviderCharge(
            broker_id=item.broker_id,
            provider_account_id=intent.provider_account_id,
            intent_id=intent.id,
            installment_id=item.installment_id,
            provider_mandate_id=intent.provider_mandate_id,
            # Placeholder until the provider tells us its internalId.
            provider_installment_id=ext,
            external_installment_id=ext,
        )
        db.add(charge)
    charge.state = STATE_PAID
    charge.raw = {**(charge.raw or {}), MARKED_PAID_KEY: as_aware(now).isoformat()}
    db.flush()
    return charge


def open_items_for_installment(
    db: Session, installment_id: int, *, broker_id: int, exclude_intent_id: int | None = None
) -> list[PaymentIntentItem]:
    """Items of OPEN intents that include this cuota."""
    stmt = (
        select(PaymentIntentItem)
        .join(PaymentIntent, PaymentIntent.id == PaymentIntentItem.intent_id)
        .where(
            PaymentIntentItem.broker_id == broker_id,
            PaymentIntentItem.installment_id == installment_id,
            PaymentIntent.broker_id == broker_id,
            PaymentIntent.status.in_(OPEN_INTENT_STATUSES),
        )
        .order_by(PaymentIntentItem.id)
    )
    if exclude_intent_id is not None:
        stmt = stmt.where(PaymentIntent.id != exclude_intent_id)
    return list(db.scalars(stmt))


__all__ = [
    "MARKED_PAID_KEY",
    "OPEN_INTENT_STATUSES",
    "as_aware",
    "charges_by_item",
    "find_charge",
    "is_marked_paid_outside",
    "mark_charge_paid_outside",
    "open_items_for_installment",
    "provider_paid_item_ids",
    "rollup_intent",
    "santiago_date",
    "santiago_today",
]
