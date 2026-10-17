"""Payment-provider jobs (v12, 02 §10) — owner: lane B3.

* ``payments.sync`` (``rate(30 minutes)``): ``GET /receivables`` for the items
  of every open checkout intent, through the webhook's idempotent core. The
  slot period is ``minute``: two triggers in the same minute run once; the
  scheduler fires every 30 minutes.
* ``payments.payouts`` (daily): payouts (``hasPayout=true``) →
  ``provider_payout`` and ``settled`` evidence.

Both skip (``{"skipped": True}``) when no provider account is active, and never
reach the network in tests (``fake_provider``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.jobs import register_job
from app.services.payments.sync import run_for_accounts, sync_account, sync_payouts


@register_job("payments.sync", period="minute")
def payments_sync(db: Session, now: datetime) -> dict[str, Any]:
    return run_for_accounts(db, sync_account, now=now)


@register_job("payments.payouts", period="day")
def payments_payouts(db: Session, now: datetime) -> dict[str, Any]:
    return run_for_accounts(db, sync_payouts, now=now)
