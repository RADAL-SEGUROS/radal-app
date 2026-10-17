"""v12 shared service: the bell (app.services.notifications + /notifications).

Covers the one-open-row rule (escalation in place, quiet refresh, dismissal
sticks), default recipients, policy recipients, close/expire, and the router's
owner-only scope: another user's row — same broker or another tenant — is a
404 on every path; a platform user (no broker) is 403.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import NotificationKind, NotificationStatus, NotificationTray, UserType
from app.models.notification import Notification
from app.models.user import User
from app.services.notifications import (
    close_for_origin,
    expire_fyi,
    notify,
    recipients_for_policy,
)
from tests.conftest import API, _password_hash, auth_headers, make_case_file, make_policy

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _notify(db, tenant, **kw):
    fields = {
        "broker_id": tenant.broker_id,
        "user_ids": None,
        "kind": NotificationKind.INSTALLMENT_DUE,
        "tray": NotificationTray.TODO,
        "origin_type": "installment",
        "origin_id": 101,
        "now": T0,
    }
    fields.update(kw)
    rows = notify(db, **fields)
    db.commit()
    return rows


def _rows(db, **filters):
    stmt = select(Notification)
    for key, value in filters.items():
        stmt = stmt.where(getattr(Notification, key) == value)
    return db.scalars(stmt.order_by(Notification.id)).all()


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _extra_user(db, tenant, role, label, *, active=True):
    user = User(
        broker_id=tenant.broker_id,
        email=f"{label}@{tenant.broker.trade_name.lower()}.cl",
        hashed_password=_password_hash(),
        full_name=label,
        user_type=UserType.BROKER,
        role=role,
        is_active=active,
    )
    db.add(user)
    db.commit()
    return user


# --- service: recipients ---------------------------------------------------------

def test_default_recipients_are_active_broker_admins_only(db, world):
    second = _extra_user(db, world.a, "broker_admin", "admin2")
    _extra_user(db, world.a, "broker_admin", "admin-off", active=False)
    rows = _notify(db, world.a)
    assert sorted(r.user_id for r in rows) == sorted([world.a.admin.id, second.id])
    assert all(r.broker_id == world.a.broker_id for r in rows)
    assert all(r.status == NotificationStatus.NEW for r in rows)
    # executives and broker B never hear about it
    assert not _rows(db, user_id=world.a.executive.id)
    assert not _rows(db, broker_id=world.b.broker_id)


def test_explicit_recipients_from_another_tenant_are_dropped(db, world):
    rows = _notify(
        db, world.a,
        user_ids=[world.a.executive.id, world.b.admin.id, world.a.executive.id],
    )
    assert [r.user_id for r in rows] == [world.a.executive.id]
    assert _rows(db, user_id=world.b.admin.id) == []
    assert _notify(db, world.a, user_ids=[], origin_id=202) == []


def test_bad_kind_or_overlong_fields_raise(db, world):
    with pytest.raises(ValueError):
        notify(db, broker_id=world.a.broker_id, user_ids=None, kind="nope",
               tray="todo", origin_type="installment", origin_id=1)
    with pytest.raises(ValueError):
        notify(db, broker_id=world.a.broker_id, user_ids=None, kind="installment_due",
               tray="todo", origin_type="x" * 33, origin_id=1)
    with pytest.raises(ValueError):
        notify(db, broker_id=world.a.broker_id, user_ids=None, kind="installment_due",
               tray="todo", origin_type="installment", origin_id=1, level="x" * 17)


def test_params_are_made_json_safe(db, world):
    rows = _notify(db, world.a, params={"amount_uf": Decimal("12.5000"), "due": T0.date()})
    db.expire_all()
    stored = db.get(Notification, rows[0].id)
    assert stored.params == {"amount_uf": "12.5000", "due": "2026-10-01"}


# --- service: one open row per (user, origin) -------------------------------------

def test_escalation_updates_in_place_and_resets_to_new(db, world):
    first = _notify(db, world.a, level="-5", params={"n": 1}, link_path="/collections?i=101")[0]
    first.status = NotificationStatus.SEEN
    db.commit()

    later = T0 + timedelta(days=8)
    second = _notify(
        db, world.a, kind=NotificationKind.INSTALLMENT_OVERDUE, level="+3",
        params={"n": 2}, link_path="/collections?i=101&x=1", now=later,
    )[0]
    assert second.id == first.id
    rows = _rows(db, user_id=world.a.admin.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == NotificationKind.INSTALLMENT_OVERDUE
    assert row.level == "+3"
    assert row.params == {"n": 2}
    assert row.link_path == "/collections?i=101&x=1"
    assert row.status == NotificationStatus.NEW
    assert _aware(row.updated_at) == later

    # a tray change is also material (todo -> fyi)
    third = _notify(db, world.a, kind=NotificationKind.INSTALLMENT_OVERDUE, level="+3",
                    tray=NotificationTray.FYI, now=later)[0]
    assert third.id == first.id and third.tray == NotificationTray.FYI


def test_same_alert_again_refreshes_quietly(db, world):
    row = _notify(db, world.a, level="+3", params={"days_late": 3})[0]
    row.status = NotificationStatus.SEEN
    db.commit()
    db.refresh(row)
    raised_at = _aware(row.updated_at)

    again = _notify(db, world.a, level="+3", params={"days_late": 4},
                    now=T0 + timedelta(days=1))[0]
    assert again.id == row.id
    db.expire_all()
    stored = db.get(Notification, row.id)
    assert stored.params == {"days_late": 4}
    assert stored.status == NotificationStatus.SEEN       # not re-lit
    assert _aware(stored.updated_at) == raised_at          # not reordered
    assert len(_rows(db, user_id=world.a.admin.id)) == 1

    # renotify=True forces it back to new
    _notify(db, world.a, level="+3", renotify=True, now=T0 + timedelta(days=2))
    db.expire_all()
    assert db.get(Notification, row.id).status == NotificationStatus.NEW


def test_dismissed_alert_is_not_resurrected_but_a_new_level_is(db, world):
    row = _notify(db, world.a, kind=NotificationKind.INSTALLMENT_OVERDUE, level="+3")[0]
    row.status = NotificationStatus.CLOSED
    row.closed_at = T0
    db.commit()

    assert _notify(db, world.a, kind=NotificationKind.INSTALLMENT_OVERDUE, level="+3") == []
    assert len(_rows(db, user_id=world.a.admin.id)) == 1

    fresh = _notify(db, world.a, kind=NotificationKind.INSTALLMENT_OVERDUE, level="+10")
    assert len(fresh) == 1 and fresh[0].id != row.id
    open_rows = [r for r in _rows(db, user_id=world.a.admin.id) if r.status != NotificationStatus.CLOSED]
    assert len(open_rows) == 1

    # renotify reopens even the identical alert — the SAME row, so the history
    # never shows two identical "Cuota vencida" entries (review v12 #5).
    close_for_origin(db, broker_id=world.a.broker_id, origin_type="installment", origin_id=101)
    db.commit()
    reopened = _notify(db, world.a, kind=NotificationKind.INSTALLMENT_OVERDUE,
                       level="+10", renotify=True)
    assert len(reopened) == 1 and reopened[0].id == fresh[0].id
    assert reopened[0].status == NotificationStatus.NEW and reopened[0].closed_at is None
    assert len(_rows(db, user_id=world.a.admin.id)) == 2


def test_uniqueness_is_per_user_and_per_origin(db, world):
    _notify(db, world.a, user_ids=[world.a.admin.id, world.a.executive.id])
    _notify(db, world.a, user_ids=[world.a.admin.id, world.a.executive.id], level="-5")
    _notify(db, world.a, user_ids=[world.a.admin.id], origin_id=102)
    _notify(db, world.a, user_ids=[world.a.admin.id], origin_type="status_inquiry")
    assert len(_rows(db, user_id=world.a.executive.id)) == 1
    assert len(_rows(db, user_id=world.a.admin.id)) == 3


def test_same_origin_ids_in_two_tenants_never_collide(db, world):
    a = _notify(db, world.a)[0]
    b = _notify(db, world.b)[0]
    assert a.id != b.id
    assert close_for_origin(db, broker_id=world.a.broker_id,
                            origin_type="installment", origin_id=101) == 1
    db.commit()
    db.expire_all()
    assert db.get(Notification, b.id).status == NotificationStatus.NEW


def test_close_for_origin_filters_by_kind(db, world):
    _notify(db, world.a, user_ids=[world.a.admin.id, world.a.executive.id],
            kind=NotificationKind.EVIDENCE_PENDING)
    _notify(db, world.a, user_ids=[world.a.admin.id], origin_id=999)
    assert close_for_origin(db, broker_id=world.a.broker_id, origin_type="installment",
                            origin_id=101, kinds=[NotificationKind.PAYMENT_RECEIVED]) == 0
    closed = close_for_origin(db, broker_id=world.a.broker_id, origin_type="installment",
                              origin_id=101, kinds=["evidence_pending"], now=T0)
    db.commit()
    assert closed == 2
    for row in _rows(db, origin_id=101):
        assert row.status == NotificationStatus.CLOSED and _aware(row.closed_at) == T0
    assert _rows(db, origin_id=999)[0].status == NotificationStatus.NEW
    assert close_for_origin(db, broker_id=world.a.broker_id,
                            origin_type="installment", origin_id=101) == 0


def test_expire_fyi_closes_seen_or_old_fyi_only(db, world):
    old = _notify(db, world.a, tray=NotificationTray.FYI, origin_id=1, now=T0)[0]
    seen = _notify(db, world.a, tray=NotificationTray.FYI, origin_id=2, now=T0 + timedelta(days=6))[0]
    seen.status = NotificationStatus.SEEN
    db.commit()
    young = _notify(db, world.a, tray=NotificationTray.FYI, origin_id=3, now=T0 + timedelta(days=6))[0]
    old_todo = _notify(db, world.a, tray=NotificationTray.TODO, origin_id=4, now=T0)[0]

    now = T0 + timedelta(days=7, hours=1)
    assert expire_fyi(db, now=now) == 2
    db.commit()
    db.expire_all()
    assert db.get(Notification, old.id).status == NotificationStatus.CLOSED
    assert db.get(Notification, seen.id).status == NotificationStatus.CLOSED
    assert db.get(Notification, young.id).status == NotificationStatus.NEW
    assert db.get(Notification, old_todo.id).status == NotificationStatus.NEW
    assert expire_fyi(db, now=now) == 0


def test_recipients_for_policy(db, world):
    policy = make_policy(db, world.a, world.insurer)
    db.commit()
    assert recipients_for_policy(db, policy) == [world.a.admin.id]

    case = make_case_file(db, world.a, owner_user_id=world.a.executive.id)
    policy.case_file_id = case.id
    db.commit()
    assert recipients_for_policy(db, policy) == [world.a.executive.id]

    world.a.executive.is_active = False
    db.commit()
    assert recipients_for_policy(db, policy) == [world.a.admin.id]

    # an owner from another broker never counts
    world.a.executive.is_active = True
    case.owner_user_id = world.b.admin.id
    db.commit()
    assert recipients_for_policy(db, policy) == [world.a.admin.id]


# --- router -------------------------------------------------------------------------

def test_list_count_seen_close_flow(client, db, world, headers_a):
    _notify(db, world.a, origin_id=1, now=T0)
    _notify(db, world.a, origin_id=2, now=T0 + timedelta(hours=1))
    _notify(db, world.a, origin_id=3, tray=NotificationTray.FYI, now=T0 + timedelta(hours=2))

    res = client.get(f"{API}/notifications", headers=headers_a)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3
    assert [i["origin_id"] for i in body["items"]] == [3, 2, 1]   # newest first
    assert {"broker_id", "user_id"}.isdisjoint(body["items"][0])

    todo = client.get(f"{API}/notifications", params={"tray": "todo"}, headers=headers_a).json()
    assert [i["origin_id"] for i in todo["items"]] == [2, 1]
    page = client.get(f"{API}/notifications", params={"limit": 1, "offset": 1}, headers=headers_a).json()
    assert page["total"] == 3 and [i["origin_id"] for i in page["items"]] == [2]

    assert client.get(f"{API}/notifications/count", headers=headers_a).json() == {
        "todo_new": 2, "fyi_new": 1,
    }

    target = body["items"][2]["id"]  # origin 1, the oldest
    seen = client.post(f"{API}/notifications/{target}/seen", headers=headers_a)
    assert seen.status_code == 200 and seen.json()["status"] == "seen"
    # seen does not reorder the bell
    again = client.get(f"{API}/notifications", headers=headers_a).json()
    assert [i["origin_id"] for i in again["items"]] == [3, 2, 1]
    assert client.get(f"{API}/notifications/count", headers=headers_a).json()["todo_new"] == 1

    closed = client.post(f"{API}/notifications/{target}/close", headers=headers_a)
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed" and closed.json()["closed_at"]
    # idempotent; seen on a closed row keeps it closed
    assert client.post(f"{API}/notifications/{target}/close", headers=headers_a).status_code == 200
    assert client.post(f"{API}/notifications/{target}/seen", headers=headers_a).json()["status"] == "closed"

    open_list = client.get(f"{API}/notifications", headers=headers_a).json()
    assert [i["origin_id"] for i in open_list["items"]] == [3, 2]
    history = client.get(f"{API}/notifications", params={"status": "closed"}, headers=headers_a).json()
    assert [i["id"] for i in history["items"]] == [target]

    # a dismissed alert is not raised again at the same level
    assert _notify(db, world.a, origin_id=1) == []


def test_other_users_and_tenants_rows_are_404(client, db, world, headers_a, headers_a_exec, headers_b):
    mine = _notify(db, world.a, user_ids=[world.a.admin.id])[0]
    theirs = _notify(db, world.b, user_ids=[world.b.admin.id])[0]

    # same broker, another user
    assert client.get(f"{API}/notifications", headers=headers_a_exec).json()["total"] == 0
    assert client.get(f"{API}/notifications/count", headers=headers_a_exec).json() == {
        "todo_new": 0, "fyi_new": 0,
    }
    for action in ("seen", "close"):
        assert client.post(f"{API}/notifications/{mine.id}/{action}", headers=headers_a_exec).status_code == 404
        # another tenant
        assert client.post(f"{API}/notifications/{theirs.id}/{action}", headers=headers_a).status_code == 404
        assert client.post(f"{API}/notifications/999999/{action}", headers=headers_a).status_code == 404

    listed_a = client.get(f"{API}/notifications", headers=headers_a).json()
    assert [i["id"] for i in listed_a["items"]] == [mine.id]
    listed_b = client.get(f"{API}/notifications", headers=headers_b).json()
    assert [i["id"] for i in listed_b["items"]] == [theirs.id]
    db.expire_all()
    assert db.get(Notification, theirs.id).status == NotificationStatus.NEW


def test_platform_user_and_anonymous_are_refused(client, db, world):
    platform = User(
        broker_id=None,
        email="ops@radal.cl",
        hashed_password=_password_hash(),
        full_name="Radal Ops",
        user_type=UserType.PLATFORM,
        role="platform_admin",
        is_active=True,
    )
    db.add(platform)
    db.commit()
    headers = auth_headers(client, platform.email)
    assert client.get(f"{API}/notifications", headers=headers).status_code == 403
    assert client.get(f"{API}/notifications/count", headers=headers).status_code == 403
    assert client.post(f"{API}/notifications/1/seen", headers=headers).status_code == 403
    assert client.get(f"{API}/notifications").status_code == 401


def test_x_radal_token_header_is_accepted(client, world, headers_a):
    token = headers_a["Authorization"].split(" ", 1)[1]
    res = client.get(f"{API}/notifications/count", headers={"X-Radal-Token": token})
    assert res.status_code == 200


def test_bad_query_values_are_422(client, world, headers_a):
    assert client.get(f"{API}/notifications", params={"tray": "nope"}, headers=headers_a).status_code == 422
    assert client.get(f"{API}/notifications", params={"limit": 0}, headers=headers_a).status_code == 422
