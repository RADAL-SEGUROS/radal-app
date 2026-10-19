"""The daily cobranza clock (v12, 03 §8) and Santiago's business date.

:func:`run_collection_clock` is the body of the ``collection.clock`` job. For
EVERY broker, deterministic in the injected ``now``:

1. refresh the status cache of every OPEN cuota (cached ``pending`` / ``due`` /
   ``overdue``) — overdue promotion, voiding after a termination;
2. raise / escalate / close each cuota's bell alert
   (:mod:`app.services.collection.alerts`; the notification service keeps one
   open row per user and origin, so a second run the same day changes nothing);
3. ``plan_missing`` (todo) for every active in-force policy without a plan de
   pago, re-notified every 7 days while it stays missing; closed once a plan
   exists;
4. ``native_collection_available`` (fyi, broker admins) once per insurer
   "flip": an insurer with an ACTIVE native channel on which the broker has open
   cuotas. The level is the activation date of the channel, so a later
   re-activation is a new alert; a dismissed / expired one never comes back.

Each broker is committed on its own; a failing broker is rolled back, the rest
still run, and the job reports the failure (so the slot can be retried).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.base_class import utcnow
from app.models.broker import Broker
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.enums import (
    InstallmentStatus,
    NotificationKind,
    NotificationStatus,
    NotificationTray,
)
from app.models.insurer import Insurer
from app.models.insurer_collection import BrokerInsurerCollection
from app.models.notification import Notification
from app.models.policy import Policy
from app.services.collection.alerts import (
    INSTALLMENT_KINDS,
    ORIGIN_INSTALLMENT,
    sync_installment_notifications,
)
from app.services.collection.board import policies_without_plan_stmt
from app.services.collection.past_review import review_cutoffs
from app.services.collection.resolver import active_native_channels, lead_insurer_id
from app.services.collection.settings import settings_or_none
from app.services.collection.status import broker_collection_params, refresh_installment_cache
from app.services.notifications import close_for_origin, notify, recipients_for_policy

logger = logging.getLogger(__name__)

BUSINESS_TZ_NAME = "America/Santiago"
PLAN_MISSING_RENOTIFY = timedelta(days=7)
ORIGIN_POLICY = "policy"
ORIGIN_INSURER = "insurer"

#: Cached statuses the clock refreshes (everything still owed).
OPEN_CACHE = (InstallmentStatus.PENDING, InstallmentStatus.DUE, InstallmentStatus.OVERDUE)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def business_today(now: datetime | None = None) -> date:
    """Today in America/Santiago (UTC if tzdata is missing)."""
    moment = _aware(now or utcnow())
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(BUSINESS_TZ_NAME)).date()
    except Exception:  # noqa: BLE001 - no tzdata: UTC is still a stable date
        return moment.date()


# --- per-broker steps --------------------------------------------------------

def refresh_open_installments(
    db: Session, broker_id: int, *, today: date, now: datetime
) -> dict[str, int]:
    cadence, _tolerance = broker_collection_params(db, broker_id)
    insts = db.scalars(
        select(CollectionInstallment)
        .join(CollectionPlan, CollectionPlan.id == CollectionInstallment.collection_plan_id)
        .where(
            CollectionInstallment.broker_id == broker_id,
            CollectionPlan.broker_id == broker_id,
            CollectionInstallment.status.in_(OPEN_CACHE),
        )
        .options(
            selectinload(CollectionInstallment.payments),
            selectinload(CollectionInstallment.collection_plan).selectinload(CollectionPlan.policy),
        )
        .order_by(CollectionInstallment.id)
    ).all()
    stats = {"refreshed": 0, "alerts_raised": 0, "alerts_closed": 0}
    # Review r2 (D1): past cuotas of a policy loaded late stay quiet until the
    # broker decides (one query per broker, not per cuota).
    cutoffs = review_cutoffs(db, broker_id)
    for inst in insts:
        state = refresh_installment_cache(
            db, inst, today=today, review_cutoff=cutoffs.get(inst.collection_plan_id)
        )
        stats["refreshed"] += 1
        outcome = sync_installment_notifications(
            db, inst, state, today=today, cadence_days=cadence, now=now
        )
        if outcome == "raised":
            stats["alerts_raised"] += 1
        elif outcome == "closed":
            stats["alerts_closed"] += 1

    # Safety net: an open cuota alert whose cuota is no longer open (settled by a
    # path that did not close it, or deleted with a replaced ledger) is closed.
    refreshed = {inst.id for inst in insts if inst.status in OPEN_CACHE}
    stale_origins = {
        origin_id
        for origin_id in db.scalars(
            select(Notification.origin_id).where(
                Notification.broker_id == broker_id,
                Notification.origin_type == ORIGIN_INSTALLMENT,
                Notification.status != NotificationStatus.CLOSED,
            )
        )
        if origin_id not in refreshed
    }
    for origin_id in sorted(stale_origins):
        inst = db.get(CollectionInstallment, origin_id)
        if inst is not None and inst.broker_id == broker_id and inst.status in OPEN_CACHE:
            continue
        stats["alerts_closed"] += close_for_origin(
            db,
            broker_id=broker_id,
            origin_type=ORIGIN_INSTALLMENT,
            origin_id=origin_id,
            kinds=list(INSTALLMENT_KINDS),
            now=now,
        )
    db.flush()
    return stats


def _latest_plan_missing(db: Session, broker_id: int, policy_id: int) -> Notification | None:
    return db.scalar(
        select(Notification)
        .where(
            Notification.broker_id == broker_id,
            Notification.origin_type == ORIGIN_POLICY,
            Notification.origin_id == policy_id,
            Notification.kind == NotificationKind.PLAN_MISSING,
        )
        .order_by(Notification.updated_at.desc(), Notification.id.desc())
    )


def emit_plan_missing(db: Session, broker_id: int, *, today: date, now: datetime) -> dict[str, int]:
    policies = db.scalars(policies_without_plan_stmt(broker_id, today=today).order_by(Policy.id)).all()
    raised = 0
    missing_ids = {p.id for p in policies}
    for policy in policies:
        latest = _latest_plan_missing(db, broker_id, policy.id)
        renotify = latest is not None and (
            _aware(now) - _aware(latest.updated_at) >= PLAN_MISSING_RENOTIFY
        )
        rows = notify(
            db,
            broker_id=broker_id,
            user_ids=recipients_for_policy(db, policy),
            kind=NotificationKind.PLAN_MISSING,
            tray=NotificationTray.TODO,
            origin_type=ORIGIN_POLICY,
            origin_id=policy.id,
            params={
                "policy_id": policy.id,
                "policy_number": policy.policy_number,
                "start_date": policy.start_date,
                "end_date": policy.end_date,
            },
            link_path=f"/policies/{policy.id}",
            now=now,
            renotify=renotify,
        )
        if rows and (latest is None or renotify):
            raised += 1

    # A plan now exists (or the policy is no longer in force): close the alert.
    open_rows = db.scalars(
        select(Notification).where(
            Notification.broker_id == broker_id,
            Notification.origin_type == ORIGIN_POLICY,
            Notification.kind == NotificationKind.PLAN_MISSING,
            Notification.status != NotificationStatus.CLOSED,
        )
    ).all()
    closed = 0
    for origin_id in sorted({row.origin_id for row in open_rows} - missing_ids):
        closed += close_for_origin(
            db,
            broker_id=broker_id,
            origin_type=ORIGIN_POLICY,
            origin_id=origin_id,
            kinds=[NotificationKind.PLAN_MISSING],
            now=now,
        )
    return {"plans_missing": len(policies), "plan_missing_raised": raised, "plan_missing_closed": closed}


def _open_cuota_insurers(db: Session, broker_id: int) -> set[int]:
    policies = db.scalars(
        select(Policy)
        .join(CollectionPlan, CollectionPlan.policy_id == Policy.id)
        .join(CollectionInstallment, CollectionInstallment.collection_plan_id == CollectionPlan.id)
        .where(
            Policy.broker_id == broker_id,
            CollectionPlan.broker_id == broker_id,
            CollectionInstallment.broker_id == broker_id,
            CollectionInstallment.status.in_(OPEN_CACHE),
        )
        .options(selectinload(Policy.coinsurance_shares))
    ).unique().all()
    return {iid for iid in (lead_insurer_id(p) for p in policies) if iid is not None}


def _close_stale_native(db: Session, broker_id: int, *, keep: set[int], now: datetime) -> int:
    """Close "X ya cobra en línea" rows whose insurer no longer qualifies (the
    account failed *Verificar*, was paused, or the broker opted out) — the
    notice must follow the same rule as the cuota's level (review v12 P3)."""
    closed = 0
    stale = {
        row.origin_id
        for row in db.scalars(
            select(Notification).where(
                Notification.broker_id == broker_id,
                Notification.origin_type == ORIGIN_INSURER,
                Notification.kind == NotificationKind.NATIVE_COLLECTION_AVAILABLE,
                Notification.status != NotificationStatus.CLOSED,
            )
        )
    } - keep
    for insurer_id in sorted(stale):
        closed += close_for_origin(
            db,
            broker_id=broker_id,
            origin_type=ORIGIN_INSURER,
            origin_id=insurer_id,
            kinds=[NotificationKind.NATIVE_COLLECTION_AVAILABLE],
            now=now,
        )
    return closed


def emit_native_available(db: Session, broker_id: int, *, now: datetime) -> dict[str, int]:
    settings_row = settings_or_none(db, broker_id)
    if settings_row is not None and not settings_row.native_collection_enabled:
        _close_stale_native(db, broker_id, keep=set(), now=now)
        return {"native_available": 0}
    raised = 0
    eligible: set[int] = set()
    for insurer_id in sorted(_open_cuota_insurers(db, broker_id)):
        override = db.scalar(
            select(BrokerInsurerCollection).where(
                BrokerInsurerCollection.broker_id == broker_id,
                BrokerInsurerCollection.insurer_id == insurer_id,
            )
        )
        if override is not None and override.native_enabled is False:
            continue
        # ACTIVE channels on an ACTIVE *and verified* account only — the same
        # rule that makes a cuota *En línea* (resolver.active_native_channels).
        channels = active_native_channels(db, insurer_id)
        if not channels:
            continue
        eligible.add(insurer_id)
        stamps = [
            _aware(c.activated_at or c.created_at).date() for c in channels if (c.activated_at or c.created_at)
        ]
        level = f"a{min(stamps).isoformat()}" if stamps else "a"
        insurer = db.get(Insurer, insurer_id)
        rows = notify(
            db,
            broker_id=broker_id,
            user_ids=None,
            kind=NotificationKind.NATIVE_COLLECTION_AVAILABLE,
            tray=NotificationTray.FYI,
            origin_type=ORIGIN_INSURER,
            origin_id=insurer_id,
            params={
                "insurer_id": insurer_id,
                "insurer_name": (insurer.trade_name or insurer.legal_name) if insurer else None,
                "channels": sorted({c.channel.value for c in channels}),
            },
            link_path="/collections",
            level=level,
            now=now,
        )
        raised += len(rows)
    _close_stale_native(db, broker_id, keep=eligible, now=now)
    return {"native_available": raised}


def run_broker_clock(db: Session, broker_id: int, *, now: datetime) -> dict[str, Any]:
    today = business_today(now)
    stats: dict[str, Any] = {}
    stats.update(refresh_open_installments(db, broker_id, today=today, now=now))
    stats.update(emit_plan_missing(db, broker_id, today=today, now=now))
    stats.update(emit_native_available(db, broker_id, now=now))
    return stats


def run_collection_clock(db: Session, *, now: datetime) -> dict[str, Any]:
    """The ``collection.clock`` body. Raises at the END if any broker failed."""
    moment = _aware(now)
    totals: dict[str, Any] = {"today": business_today(moment).isoformat(), "brokers": 0}
    errors: list[str] = []
    for broker_id in db.scalars(select(Broker.id).order_by(Broker.id)).all():
        try:
            stats = run_broker_clock(db, broker_id, now=moment)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - one broker must not stop the others
            db.rollback()
            logger.exception("collection.clock failed for broker %s", broker_id)
            errors.append(f"broker {broker_id}: {type(exc).__name__}: {exc}"[:500])
            continue
        totals["brokers"] += 1
        for key, value in stats.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    if errors:
        totals["errors"] = errors
        raise RuntimeError("; ".join(errors))
    return totals


__all__ = [
    "OPEN_CACHE",
    "business_today",
    "emit_native_available",
    "emit_plan_missing",
    "refresh_open_installments",
    "run_broker_clock",
    "run_collection_clock",
]
