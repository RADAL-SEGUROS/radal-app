"""v12 lane B2 jobs: ``collection.clock``, ``fx.daily``, ``notifications.expire``.

* the clock refreshes the cache and walks a cuota through the cadence
  ``[-15, -5, 0, 3, 10]`` -> termination risk, escalating ONE row in place,
  idempotent per slot and per day; paid closes; promise broken; plan missing
  (re-notified after 7 days); native collection available once per flip;
* ``fx.daily`` with the fetch monkeypatched (no network): rows upserted, a
  failure is recorded ``failed`` and never escapes;
* ``notifications.expire`` closes stale fyi rows;
* isolation: a broker's alerts reach only its own users.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.jobs import JOB_PERIODS, load_jobs, run_job, trigger_job
from app.jobs import collections as collection_jobs
from app.models.collection import CollectionInstallment
from app.models.currency import CurrencyRate
from app.models.enums import (
    ChannelStatus,
    InstallmentStatus,
    JobStatus,
    NotificationKind,
    NotificationStatus,
    NotificationTray,
)
from app.models.notification import Notification
from app.services.notifications import mark_seen
from tests.conftest import API, make_case_file, make_policy
from tests.fakes_v12 import (
    make_broker_override,
    make_native_channel,
    make_payment,
    make_policy_with_plan,
    make_provider_account,
)

DUE = date(2026, 3, 10)


def at(day: date, hour: int = 15) -> datetime:
    """A UTC instant that is still ``day`` in Santiago (15:00 UTC = 12:00 CLT)."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def alerts(db, *, origin_type="installment", origin_id=None, open_only=True):
    db.expire_all()
    stmt = select(Notification).where(Notification.origin_type == origin_type)
    if origin_id is not None:
        stmt = stmt.where(Notification.origin_id == origin_id)
    if open_only:
        stmt = stmt.where(Notification.status != NotificationStatus.CLOSED)
    return db.scalars(stmt.order_by(Notification.id)).all()


@pytest.fixture()
def cuota(db, world):
    """Broker A: one policy with a single cuota due 2026-03-10 (plus one in B)."""
    policy, plan = make_policy_with_plan(
        db, world.a, world.insurer, n_installments=1, start=DUE, policy_number="POL-CLOCK"
    )
    make_policy_with_plan(
        db, world.b, world.insurer, n_installments=1, start=DUE, policy_number="POL-CLOCK-B"
    )
    db.commit()
    return plan.installments[0]


def test_lane_jobs_are_registered_as_daily():
    jobs = load_jobs()
    for name in ("fx.daily", "collection.clock", "notifications.expire"):
        assert name in jobs
        assert JOB_PERIODS[name] == "day"


# --- the clock: cadence escalation -------------------------------------------------


def test_clock_walks_the_cadence_escalating_one_row(db, world, cuota):
    seen: list[tuple] = []
    for offset in (-20, -15, -14, -5, 0, 3, 10, 11):
        run_job(db, "collection.clock", at(DUE + timedelta(days=offset)))
        rows = alerts(db, origin_id=cuota.id)
        assert len(rows) <= 1, offset  # one open row per (user, origin)
        seen.append((offset, rows[0].kind, rows[0].tray, rows[0].level) if rows else (offset, None))

    assert seen[0] == (-20, None)
    assert seen[1] == (-15, NotificationKind.INSTALLMENT_DUE, NotificationTray.FYI, "-15")
    assert seen[2] == (-14, NotificationKind.INSTALLMENT_DUE, NotificationTray.FYI, "-15")
    assert seen[3] == (-5, NotificationKind.INSTALLMENT_DUE, NotificationTray.FYI, "-5")
    assert seen[4] == (0, NotificationKind.INSTALLMENT_DUE, NotificationTray.FYI, "0")
    assert seen[5] == (3, NotificationKind.INSTALLMENT_OVERDUE, NotificationTray.TODO, "+3")
    assert seen[6] == (10, NotificationKind.INSTALLMENT_OVERDUE, NotificationTray.TODO, "+10")
    assert seen[7] == (11, NotificationKind.TERMINATION_RISK, NotificationTray.TODO, "term")

    every = alerts(db, origin_id=cuota.id, open_only=False)
    assert len(every) == 1  # escalated in place, never a second row
    assert every[0].user_id == world.a.admin.id
    assert every[0].params["policy_number"] == "POL-CLOCK"
    assert every[0].link_path == f"/collections/{cuota.collection_plan_id}"
    # The cache moved too.
    assert db.get(CollectionInstallment, cuota.id).status == InstallmentStatus.OVERDUE


def test_same_level_next_day_does_not_relight_the_bell(db, world, cuota):
    run_job(db, "collection.clock", at(DUE + timedelta(days=3)))
    row = alerts(db, origin_id=cuota.id)[0]
    mark_seen(db, row)
    db.commit()
    raised_at = row.updated_at
    run_job(db, "collection.clock", at(DUE + timedelta(days=4)))
    row = alerts(db, origin_id=cuota.id)[0]
    assert row.status == NotificationStatus.SEEN
    assert row.updated_at == raised_at
    # A new level re-lights it.
    run_job(db, "collection.clock", at(DUE + timedelta(days=10)))
    assert alerts(db, origin_id=cuota.id)[0].status == NotificationStatus.NEW


def test_clock_is_idempotent_per_slot(db, world, cuota):
    moment = at(DUE + timedelta(days=3))
    first = trigger_job(db, "collection.clock", moment)
    second = trigger_job(db, "collection.clock", moment + timedelta(hours=2))
    assert first.executed is True and second.executed is False
    assert first.run.status == JobStatus.SUCCEEDED
    assert first.run.stats["refreshed"] >= 2  # both brokers' cuotas
    assert len(alerts(db, origin_id=cuota.id)) == 1


def test_paid_cuota_closes_its_alert(db, world, cuota):
    run_job(db, "collection.clock", at(DUE + timedelta(days=3)))
    assert len(alerts(db, origin_id=cuota.id)) == 1
    make_payment(db, db.get(CollectionInstallment, cuota.id), paid_on=DUE + timedelta(days=4))
    db.commit()
    run_job(db, "collection.clock", at(DUE + timedelta(days=5)))
    assert alerts(db, origin_id=cuota.id) == []
    assert db.get(CollectionInstallment, cuota.id).status == InstallmentStatus.PAID_LATE


def test_promise_broken(db, world, cuota):
    inst = db.get(CollectionInstallment, cuota.id)
    inst.promise_until = DUE + timedelta(days=4)
    db.commit()
    run_job(db, "collection.clock", at(DUE + timedelta(days=4)))
    assert alerts(db, origin_id=cuota.id) == []  # promised: nothing new
    run_job(db, "collection.clock", at(DUE + timedelta(days=5)))
    rows = alerts(db, origin_id=cuota.id)
    assert [(r.kind, r.tray, r.level) for r in rows] == [
        (NotificationKind.PROMISE_BROKEN, NotificationTray.TODO, f"p{(DUE + timedelta(days=4)).isoformat()}")
    ]


def test_a_promise_dated_before_the_due_date_is_not_broken_early(db, world, cuota):
    inst = db.get(CollectionInstallment, cuota.id)
    inst.promise_until = DUE - timedelta(days=10)
    db.commit()
    run_job(db, "collection.clock", at(DUE - timedelta(days=5)))
    rows = alerts(db, origin_id=cuota.id)
    assert [(r.kind, r.level) for r in rows] == [(NotificationKind.INSTALLMENT_DUE, "-5")]
    assert db.get(CollectionInstallment, cuota.id).status == InstallmentStatus.PENDING
    # Once the cuota is late and still unpaid, the earlier promise is broken.
    run_job(db, "collection.clock", at(DUE + timedelta(days=1)))
    rows = alerts(db, origin_id=cuota.id)
    assert [(r.kind, r.level) for r in rows] == [
        (NotificationKind.PROMISE_BROKEN, f"p{(DUE - timedelta(days=10)).isoformat()}")
    ]


def test_alerts_go_to_the_account_owner(db, world, cuota):
    case = make_case_file(db, world.a, owner_user_id=world.a.executive.id)
    plan = db.get(CollectionInstallment, cuota.id).collection_plan
    plan.case_file_id = case.id
    db.commit()
    run_job(db, "collection.clock", at(DUE))
    rows = alerts(db, origin_id=cuota.id)
    assert [r.user_id for r in rows] == [world.a.executive.id]


def test_clock_alerts_stay_inside_their_broker(db, world, cuota):
    run_job(db, "collection.clock", at(DUE + timedelta(days=3)))
    db.expire_all()
    rows = db.scalars(select(Notification)).all()
    by_broker = {}
    for row in rows:
        by_broker.setdefault(row.broker_id, set()).add(row.user_id)
    assert by_broker[world.a.broker_id] == {world.a.admin.id}
    assert by_broker[world.b.broker_id] == {world.b.admin.id}


def test_history_policies_stay_quiet(db, world):
    from app.models.policy import PolicyStatus

    policy, plan = make_policy_with_plan(
        db, world.a, world.insurer, n_installments=1, start=DUE,
        policy_number="POL-OLD", status=PolicyStatus.EXPIRED,
    )
    db.commit()
    run_job(db, "collection.clock", at(DUE + timedelta(days=3)))
    assert alerts(db, origin_id=plan.installments[0].id) == []
    # ...but the cache is still refreshed.
    assert db.get(CollectionInstallment, plan.installments[0].id).status == InstallmentStatus.OVERDUE


# --- plan missing ----------------------------------------------------------------


def test_plan_missing_renotifies_every_7_days(db, world):
    policy = make_policy(
        db, world.a, world.insurer, policy_number="POL-NOPLAN",
        start_date=date(2026, 1, 1), end_date=date(2027, 1, 1),
    )
    db.commit()
    day = date(2026, 3, 1)
    run_job(db, "collection.clock", at(day))
    rows = alerts(db, origin_type="policy", origin_id=policy.id)
    assert [(r.kind, r.tray) for r in rows] == [(NotificationKind.PLAN_MISSING, NotificationTray.TODO)]
    assert rows[0].link_path == f"/policies/{policy.id}"
    # The user closes it; the next day it stays closed.
    from app.services.notifications import close_notification

    close_notification(db, rows[0], now=at(day))
    db.commit()
    run_job(db, "collection.clock", at(day + timedelta(days=1)))
    assert alerts(db, origin_type="policy", origin_id=policy.id) == []
    # Seven days after it was raised, it comes back.
    run_job(db, "collection.clock", at(day + timedelta(days=7)))
    rows = alerts(db, origin_type="policy", origin_id=policy.id)
    assert len(rows) == 1 and rows[0].status == NotificationStatus.NEW


def test_plan_missing_closes_once_a_plan_exists(client, db, world, headers_a):
    policy = make_policy(
        db, world.a, world.insurer, policy_number="POL-LATER",
        start_date=date(2026, 1, 1), end_date=date(2027, 1, 1),
    )
    db.commit()
    run_job(db, "collection.clock", at(date(2026, 3, 1)))
    assert len(alerts(db, origin_type="policy", origin_id=policy.id)) == 1
    # A plan written by a path that does not close the alert (e.g. an import).
    from decimal import Decimal

    from app.models.collection import CollectionInstallment, CollectionPlan

    plan = CollectionPlan(broker_id=world.a.broker_id, policy_id=policy.id)
    db.add(plan)
    db.commit()
    # An EMPTY plan is still "sin plan de pago" (one definition with the Pólizas view).
    run_job(db, "collection.clock", at(date(2026, 3, 2)))
    assert len(alerts(db, origin_type="policy", origin_id=policy.id)) == 1
    plan.installments.append(
        CollectionInstallment(
            broker_id=world.a.broker_id, number=1, due_date=date(2026, 4, 1),
            gross_amount_uf=Decimal("10"),
        )
    )
    db.commit()
    run_job(db, "collection.clock", at(date(2026, 3, 3)))
    assert alerts(db, origin_type="policy", origin_id=policy.id) == []


# --- native collection available -------------------------------------------------


def test_native_collection_available_once_per_flip(db, world, cuota):
    account = make_provider_account(db)
    channel = make_native_channel(db, world.insurer, account, status=ChannelStatus.PENDING)
    db.commit()
    run_job(db, "collection.clock", at(DUE - timedelta(days=30)))
    assert alerts(db, origin_type="insurer") == []

    channel.status = ChannelStatus.ACTIVE
    channel.activated_at = at(DUE - timedelta(days=29))
    db.commit()
    run_job(db, "collection.clock", at(DUE - timedelta(days=29)))
    rows = alerts(db, origin_type="insurer")
    # Both brokers have open cuotas with the insurer: one fyi each, admins only.
    assert sorted((r.broker_id, r.user_id) for r in rows) == sorted(
        [(world.a.broker_id, world.a.admin.id), (world.b.broker_id, world.b.admin.id)]
    )
    assert {r.kind for r in rows} == {NotificationKind.NATIVE_COLLECTION_AVAILABLE}
    assert {r.tray for r in rows} == {NotificationTray.FYI}
    assert "onboarding" not in str(rows[0].params)

    # Once: the next days add nothing; after expiry it never comes back.
    run_job(db, "collection.clock", at(DUE - timedelta(days=28)))
    assert len(alerts(db, origin_type="insurer", open_only=False)) == 2
    run_job(db, "notifications.expire", at(DUE - timedelta(days=20)))
    run_job(db, "collection.clock", at(DUE - timedelta(days=19)))
    assert alerts(db, origin_type="insurer") == []
    assert len(alerts(db, origin_type="insurer", open_only=False)) == 2

    # A re-activation (new date) is a new flip.
    channel.activated_at = at(DUE - timedelta(days=10))
    db.commit()
    run_job(db, "collection.clock", at(DUE - timedelta(days=10)))
    assert len(alerts(db, origin_type="insurer")) == 2


def test_native_available_respects_the_broker_opt_out(db, world, cuota):
    account = make_provider_account(db)
    make_native_channel(db, world.insurer, account)
    make_broker_override(db, world.a.broker_id, world.insurer, native_enabled=False)
    db.commit()
    run_job(db, "collection.clock", at(DUE - timedelta(days=30)))
    rows = alerts(db, origin_type="insurer")
    assert {r.broker_id for r in rows} == {world.b.broker_id}


# --- fx.daily ---------------------------------------------------------------------

MINDICADOR = {
    "version": "1.7.0",
    "fecha": "2026-03-10T03:00:00.000Z",
    "uf": {"codigo": "uf", "fecha": "2026-03-10T03:00:00.000Z", "valor": 39123.45},
    "dolar": {"codigo": "dolar", "fecha": "2026-03-10T03:00:00.000Z", "valor": 951.2},
    "euro": {"codigo": "euro", "fecha": "2026-03-10T03:00:00.000Z", "valor": 1030.1},
}


def test_fx_daily_upserts_the_rates(db, monkeypatch):
    calls = []

    def fake_fetch(url, *, timeout=collection_jobs.FX_TIMEOUT_SECONDS):
        calls.append((url, timeout))
        return MINDICADOR

    monkeypatch.setattr(collection_jobs, "fetch_fx_payload", fake_fetch)
    run = run_job(db, "fx.daily", at(date(2026, 3, 10)))
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["changed"] == 1
    assert calls and calls[0][0] == "http://127.0.0.1:9/no-fx-in-tests"
    rate = db.scalar(select(CurrencyRate).where(CurrencyRate.rate_date == date(2026, 3, 10)))
    assert rate.uf == Decimal("39123.4500")
    assert rate.usd_observed == Decimal("951.2000")
    # Same slot again: no second run.
    assert trigger_job(db, "fx.daily", at(date(2026, 3, 10), 20)).executed is False


def test_fx_daily_failure_is_recorded_and_retriable(db, monkeypatch):
    def broken(url, *, timeout=collection_jobs.FX_TIMEOUT_SECONDS):
        raise collection_jobs.FxFetchError("no se pudo leer la fuente")

    monkeypatch.setattr(collection_jobs, "fetch_fx_payload", broken)
    run = run_job(db, "fx.daily", at(date(2026, 3, 11)))  # never raises
    assert run.status == JobStatus.FAILED
    assert "no se pudo leer" in run.error

    monkeypatch.setattr(collection_jobs, "fetch_fx_payload", lambda url, **kw: MINDICADOR)
    retry = trigger_job(db, "fx.daily", at(date(2026, 3, 11), 18))
    assert retry.executed is True and retry.run.status == JobStatus.SUCCEEDED


def test_fx_daily_real_fetch_fails_fast_in_the_hermetic_env(db):
    # FX_SOURCE_URL points at the discard port: the real httpx path must fail
    # quickly and be recorded, never hang or raise.
    run = run_job(db, "fx.daily", at(date(2026, 3, 12)))
    assert run.status == JobStatus.FAILED
    assert db.scalar(select(func.count()).select_from(CurrencyRate)) == 0


def test_fx_daily_empty_payload_fails(db, monkeypatch):
    monkeypatch.setattr(collection_jobs, "fetch_fx_payload", lambda url, **kw: {"euro": {}})
    run = run_job(db, "fx.daily", at(date(2026, 3, 13)))
    assert run.status == JobStatus.FAILED


def test_fx_daily_blank_source_is_skipped(db, monkeypatch):
    monkeypatch.setattr(collection_jobs.settings, "FX_SOURCE_URL", "")
    run = run_job(db, "fx.daily", at(date(2026, 3, 14)))
    assert run.status == JobStatus.SKIPPED


# --- notifications.expire ------------------------------------------------------------


def test_notifications_expire_closes_stale_fyi(db, world, cuota):
    run_job(db, "collection.clock", at(DUE - timedelta(days=15)))
    assert alerts(db, origin_id=cuota.id)[0].tray == NotificationTray.FYI
    run = run_job(db, "notifications.expire", at(DUE - timedelta(days=7)))
    assert run.status == JobStatus.SUCCEEDED
    assert run.stats["closed"] >= 1
    assert alerts(db, origin_id=cuota.id) == []


# --- the HTTP door ----------------------------------------------------------------


def test_clock_through_the_internal_door(client, db, world, cuota):
    response = client.post(
        f"{API}/internal/jobs/collection.clock",
        headers={"X-Radal-Job-Token": "test-jobs-token"},
        json={"now": at(DUE + timedelta(days=3)).isoformat()},
    )
    assert response.status_code == 200, response.text
    assert len(alerts(db, origin_id=cuota.id)) == 1


def test_a_failing_broker_does_not_stop_the_others(db, world, cuota, monkeypatch):
    from app.services.collection import clock

    real = clock.run_broker_clock

    def flaky(db_, broker_id, *, now):
        if broker_id == world.a.broker_id:
            raise RuntimeError("boom")
        return real(db_, broker_id, now=now)

    monkeypatch.setattr(clock, "run_broker_clock", flaky)
    run = run_job(db, "collection.clock", at(DUE + timedelta(days=3)))
    assert run.status == JobStatus.FAILED
    assert "boom" in run.error
    rows = alerts(db)
    assert {r.broker_id for r in rows} == {world.b.broker_id}
