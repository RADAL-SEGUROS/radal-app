"""Reconciliation against the provider (v12, 02 §10 ``payments.sync`` / ``payments.payouts``).

Webhooks are best-effort (Forpay's retry policy is not public), so a job re-reads
``GET /receivables``:

* :func:`sync_account` — for every OPEN intent of an account (created in the
  last :data:`SYNC_LOOKBACK_DAYS`), asks for its items by external id and feeds
  each receivable through :func:`~app.services.payments.webhooks.apply_charge_update`
  — the same core as the webhook, so a replay creates nothing.
* :func:`sync_payouts` — receivables with ``hasPayout=true`` in a date window →
  one ``provider_payout`` per ``(account, dispersionId)``, the charge linked to
  it, and its payment's evidence raised to ``settled``.

Both commit per account and record ``last_error`` on the account when the
provider fails, so one broken credential set never stops the others.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EvidenceLevel, PaymentStatus, ProviderAccountStatus
from app.models.payment import Payment, PaymentIntent, PaymentIntentItem
from app.models.provider import ProviderAccount, ProviderPayout
from app.services.payments.intents import OPEN_INTENT_STATUSES, as_aware, find_charge, santiago_today
from app.services.payments.providers import get_provider
from app.services.payments.providers.base import PaymentProviderError, _date, _decimal, _text
from app.services.payments.webhooks import apply_charge_update, receivable_to_event

logger = logging.getLogger(__name__)

SYNC_LOOKBACK_DAYS = 45
PAYOUT_LOOKBACK_DAYS = 35
CHUNK = 100


def _val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def active_accounts(db: Session) -> list[ProviderAccount]:
    return list(
        db.scalars(
            select(ProviderAccount)
            .where(ProviderAccount.status == ProviderAccountStatus.ACTIVE)
            .order_by(ProviderAccount.id)
        )
    )


def _open_external_ids(db: Session, acct: ProviderAccount, *, since: datetime) -> list[str]:
    return list(
        db.scalars(
            select(PaymentIntentItem.external_installment_id)
            .join(PaymentIntent, PaymentIntent.id == PaymentIntentItem.intent_id)
            .where(
                PaymentIntent.provider_account_id == acct.id,
                PaymentIntent.status.in_(OPEN_INTENT_STATUSES),
                PaymentIntent.provider_mandate_id.is_not(None),
                PaymentIntent.created_at >= since,
            )
            .order_by(PaymentIntentItem.id)
        )
    )


def sync_account(db: Session, acct: ProviderAccount, *, now: datetime) -> dict[str, int]:
    """Reconcile one account's open intents. Commits."""
    moment = as_aware(now)
    stats = {"items": 0, "receivables": 0, "payments_created": 0, "reversed": 0, "rejected": 0, "ignored": 0}
    ext_ids = _open_external_ids(db, acct, since=moment - timedelta(days=SYNC_LOOKBACK_DAYS))
    stats["items"] = len(ext_ids)
    if not ext_ids:
        return stats
    provider = get_provider(_val(acct.provider))
    for start in range(0, len(ext_ids), CHUNK):
        chunk = ext_ids[start : start + CHUNK]
        receivables = provider.search_receivables(acct, externalInstallmentId=chunk)
        for rec in receivables:
            stats["receivables"] += 1
            outcome = apply_charge_update(db, acct, receivable_to_event(rec), now=moment)
            stats["payments_created"] += int(outcome.payment_created)
            stats["reversed"] += int(outcome.payment_reversed)
            stats["rejected"] += int(outcome.rejected)
            stats["ignored"] += int(outcome.status.value == "ignored")
        db.commit()
    return stats


def _payout_fields(payout: dict[str, Any]) -> dict[str, Any]:
    account = payout.get("destinationAccount")
    if isinstance(account, dict):
        account = account.get("number") or account.get("accountNumber")
    masked = None
    if account:
        text = str(account)
        masked = ("•••" + text[-4:]) if len(text) > 4 else text
    return {
        "credited_amount": _decimal(payout.get("creditedAmount")),
        "deposit_date": _date(payout.get("companyDepositDate")),
        "destination_account_masked": masked[:64] if masked else None,
        "bank_tx_number": (_text(payout.get("bankTransactionNumber")) or None),
        "status_name": _text(payout.get("customerStatusName") or payout.get("statusName")),
    }


def sync_payouts(db: Session, acct: ProviderAccount, *, now: datetime) -> dict[str, int]:
    """Payouts (abonos) → ``provider_payout`` + ``settled`` evidence. Commits."""
    moment = as_aware(now)
    today = santiago_today(moment)
    stats = {"receivables": 0, "payouts_created": 0, "charges_linked": 0, "payments_settled": 0}
    provider = get_provider(_val(acct.provider))
    receivables = provider.search_receivables(
        acct,
        hasPayout=True,
        paymentDateFrom=today - timedelta(days=PAYOUT_LOOKBACK_DAYS),
        paymentDateTo=today,
    )
    for rec in receivables:
        if not rec.payout:
            continue
        stats["receivables"] += 1
        dispersion = _text(rec.payout.get("dispersionId"))
        if not dispersion:
            continue
        # Make sure the charge (and its payment) exist — the same idempotent core.
        apply_charge_update(db, acct, receivable_to_event(rec), now=moment)
        payout = db.scalar(
            select(ProviderPayout).where(
                ProviderPayout.provider_account_id == acct.id,
                ProviderPayout.dispersion_id == dispersion[:64],
            )
        )
        if payout is None:
            payout = ProviderPayout(provider_account_id=acct.id, dispersion_id=dispersion[:64])
            db.add(payout)
            stats["payouts_created"] += 1
        for key, value in _payout_fields(rec.payout).items():
            if value is not None:
                setattr(payout, key, value)
        payout.raw = rec.payout
        db.flush()
        charge = find_charge(
            db,
            acct.id,
            ext=rec.external_installment_id,
            pid=rec.provider_installment_id or rec.external_installment_id,
        )
        if charge is None:
            continue
        if payout.insurer_id is None and charge.intent_id is not None:
            item = db.scalar(
                select(PaymentIntentItem).where(
                    PaymentIntentItem.external_installment_id == charge.external_installment_id
                )
            )
            if item is not None:
                payout.insurer_id = item.insurer_id
        if charge.payout_id != payout.id:
            charge.payout_id = payout.id
            stats["charges_linked"] += 1
        payment = db.scalar(
            select(Payment).where(
                Payment.provider_charge_id == charge.id, Payment.status == PaymentStatus.ACTIVE
            )
        )
        if payment is not None and payment.evidence_level != EvidenceLevel.SETTLED:
            payment.evidence_level = EvidenceLevel.SETTLED
            stats["payments_settled"] += 1
    db.commit()
    return stats


def run_for_accounts(db: Session, fn, *, now: datetime) -> dict[str, Any]:  # noqa: ANN001
    """Run ``fn(db, acct, now=)`` for every active account; errors per account."""
    accounts = active_accounts(db)
    if not accounts:
        return {"skipped": True, "reason": "no active provider accounts"}
    totals: dict[str, Any] = {"accounts": len(accounts), "errors": []}
    for acct in accounts:
        acct_id = acct.id
        try:
            stats = fn(db, acct, now=now)
        except PaymentProviderError as exc:
            db.rollback()
            acct = db.get(ProviderAccount, acct_id)
            if acct is not None:
                acct.last_error = str(exc)[:2000]
                db.commit()
            totals["errors"].append({"account_id": acct_id, "error": str(exc)[:300]})
            continue
        except Exception as exc:  # noqa: BLE001 - one account's bug must not stop the others
            db.rollback()
            logger.exception("payments job: account %s failed", acct_id)
            totals["errors"].append({"account_id": acct_id, "error": type(exc).__name__})
            continue
        for key, value in stats.items():
            if isinstance(value, (int, Decimal)):
                totals[key] = totals.get(key, 0) + value
    return totals


__all__ = [
    "PAYOUT_LOOKBACK_DAYS",
    "SYNC_LOOKBACK_DAYS",
    "active_accounts",
    "run_for_accounts",
    "sync_account",
    "sync_payouts",
]
