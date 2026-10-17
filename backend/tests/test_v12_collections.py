"""v12 cobranza core API (lane B2, docs/v12/02-backend-api.md §4, 03 §1).

The board (buckets, filters, live state), the cards, payment writes (partial,
tolerance, CLP/USD conversion, reversal, insurer confirmation), the legacy PATCH
conversion, promises, debit rejections, the resolver endpoint, plan start /
PATCH, settings and notice templates — plus tenant isolation and RBAC.
``today`` is pinned through ``app.services.collection.clock.business_today``.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.activity import Note
from app.models.collection import CollectionInstallment
from app.models.enums import (
    EntityType,
    EvidenceLevel,
    InstallmentStatus,
    NotificationKind,
    NotificationStatus,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
)
from app.models.notification import Notification
from app.models.payment import Payment
from app.services import fx
from tests.conftest import API, auth_headers, ensure_role_user
from tests.fakes_v12 import make_collection_settings, make_payment, make_policy_with_plan

TODAY = date(2026, 2, 12)


@pytest.fixture()
def today(monkeypatch):
    from app.services.collection import clock

    pinned = {"value": TODAY}
    monkeypatch.setattr(clock, "business_today", lambda now=None: pinned["value"])
    return pinned


@pytest.fixture()
def board(db, world):
    """Broker A: one plan with a paid, an overdue and an upcoming cuota; one all-future plan.

    Broker B: a mirror plan (isolation).
    """
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-A-1")
    first, second, third = plan.installments  # due 2026-01-05, 02-05, 03-05
    make_payment(db, first, paid_on=date(2026, 1, 5))
    other_policy, other_plan = make_policy_with_plan(
        db,
        world.a,
        world.insurer,
        policy_number="POL-A-2",
        start=date(2026, 3, 1),
        payment_mode=PaymentMode.DIRECT_DEBIT,
    )
    b_policy, b_plan = make_policy_with_plan(db, world.b, world.insurer, policy_number="POL-B-1")
    db.commit()
    return {
        "policy": policy,
        "plan": plan,
        "paid": first,
        "overdue": second,
        "upcoming": third,
        "other_plan": other_plan,
        "b_plan": b_plan,
        "b_inst": b_plan.installments[1],
    }


def _ids(body):
    return [row["id"] for row in body["items"]]


# --- the board -------------------------------------------------------------------


def test_board_buckets_and_live_state(client, headers_a, board, today):
    body = client.get(f"{API}/collections/installments", headers=headers_a).json()
    assert body["as_of"] == "2026-02-12"
    by_id = {row["id"]: row for row in body["items"]}
    assert by_id[board["paid"].id]["state"] == "paid"
    assert by_id[board["overdue"].id]["state"] == "overdue"
    assert by_id[board["overdue"].id]["days_late"] == 7
    assert by_id[board["upcoming"].id]["state"] == "upcoming"
    # Never another tenant's cuota.
    assert board["b_inst"].id not in by_id

    def bucket(name):
        return set(
            _ids(client.get(
                f"{API}/collections/installments", params={"bucket": name}, headers=headers_a
            ).json())
        )

    assert bucket("settled") == {board["paid"].id}
    assert bucket("overdue") == {board["overdue"].id}
    # Due 2026-03-05 is within 30 days; the other plan's 2026-03-01 cuota too.
    assert board["upcoming"].id in bucket("upcoming")
    assert board["other_plan"].installments[0].id in bucket("upcoming")
    # "Al día": only plans with nothing overdue.
    current = bucket("current")
    assert {i.id for i in board["other_plan"].installments} == current
    assert body["counts"]["overdue"] == 1
    assert body["counts"]["settled"] == 1

    row = by_id[board["overdue"].id]
    assert row["level"] == "assisted"
    assert "consulta a" in row["level_reason"]
    assert row["policy_number"] == "POL-A-1"
    assert row["insurer_name"] == "HDI Seguros S.A."
    assert row["insured_name"] == "Asegurado Alfa S.A."
    assert Decimal(row["balance_uf"]) == Decimal(str(board["overdue"].gross_amount_uf))


def test_board_filters(client, db, headers_a, board, today, world):
    def ids(**params):
        return set(_ids(client.get(
            f"{API}/collections/installments", params=params, headers=headers_a
        ).json()))

    plan_ids = {i.id for i in board["plan"].installments}
    other_ids = {i.id for i in board["other_plan"].installments}
    assert ids(q="pol-a-2") == other_ids
    assert ids(q="alfa") == plan_ids | other_ids
    assert ids(method="direct_debit") == other_ids
    # A RUT matches dotted or not — it is stored BODY-DV (QA v12: dotted gave 0).
    rut = world.a.client.insured.rut
    body, dv = rut.split("-")
    dotted = f"{int(body):,}".replace(",", ".") + f"-{dv}"
    assert ids(q=rut) == plan_ids | other_ids
    assert ids(q=dotted) == plan_ids | other_ids
    assert ids(insurer_id=world.insurer.id) == plan_ids | other_ids
    assert ids(insurer_id=world.insurer_no_cmf.id) == set()
    assert ids(level="assisted") == plan_ids | other_ids
    assert ids(level="native") == set()
    assert ids(owner="me") == set()
    assert ids(plan_id=board["plan"].id) == plan_ids
    # Paging
    page = client.get(
        f"{API}/collections/installments", params={"limit": 2, "offset": 0}, headers=headers_a
    ).json()
    assert page["total"] == 6 and len(page["items"]) == 2


def test_board_balance_clp_uses_todays_uf(client, db, headers_a, board, today):
    fx.upsert_rates(db, [(TODAY, Decimal("39000"), Decimal("950"), "test")])
    db.commit()
    body = client.get(
        f"{API}/collections/installments", params={"bucket": "overdue"}, headers=headers_a
    ).json()
    row = body["items"][0]
    assert Decimal(body["uf_rate"]) == Decimal("39000")
    assert row["balance_clp"] == fx.uf_to_clp(Decimal(row["balance_uf"]), Decimal("39000"))


def test_summary_cards_and_legacy_fields(client, db, headers_a, board, today):
    response = client.get(f"{API}/collections/summary", headers=headers_a)
    assert response.status_code == 200, response.text  # not captured by /{plan_id}
    body = response.json()
    assert body["entity"] == "collections"  # the legacy generic part is kept
    assert body["total"] == 2
    assert body["overdue"]["count"] == 1
    assert Decimal(body["overdue"]["uf"]) == Decimal(str(board["overdue"].gross_amount_uf))
    # 2026-03-01 (other plan) is 17 days away: not within 7, within 30 (+ 03-05).
    assert body["due_7d"]["count"] == 0
    assert body["due_30d"]["count"] == 2
    assert body["unconfirmed_payments"] == 1  # the declared payment of cuota 1
    assert body["pending_evidence"] == 0
    assert body["plans_missing"] == 0
    assert Decimal(body["collected_month_uf"]) == Decimal("0")  # January payment


def test_summary_counts_plans_missing_and_month_collections(
    client, db, headers_a, board, today, world
):
    from tests.conftest import make_policy

    make_policy(db, world.a, world.insurer, policy_number="POL-NO-PLAN")
    make_payment(db, board["overdue"], amount_uf="10", paid_on=date(2026, 2, 10))
    db.commit()
    body = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert body["plans_missing"] == 1
    assert Decimal(body["collected_month_uf"]) == Decimal("10.0000")


# --- payments --------------------------------------------------------------------


def test_partial_then_full_payment(client, db, headers_a, board, today, world):
    inst = board["overdue"]
    gross = Decimal(str(inst.gross_amount_uf))
    first = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": "20", "currency": "UF", "paid_on": "2026-02-08"},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["state"]["state"] == "overdue"
    assert "partial" in body["state"]["flags"]
    assert body["payment"]["source"] == "manual"
    assert body["payment"]["evidence_level"] == "declared"
    assert Decimal(body["state"]["balance_uf"]) == gross - 20

    second = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={
            "amount": str(gross - 20),
            "currency": "UF",
            "paid_on": "2026-02-10",
            "confirmed_by_insurer": True,
        },
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["state"]["state"] == "paid"
    assert body["status"] == "paid_late"
    assert body["state"]["days_late"] == 5
    assert body["payment"]["evidence_level"] == "insurer_confirmed"

    db.expire_all()
    row = db.get(CollectionInstallment, inst.id)
    assert row.status == InstallmentStatus.PAID_LATE
    assert row.paid_on == date(2026, 2, 10)

    # Already paid -> a further payment is refused with a Spanish reason.
    again = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": "1", "currency": "UF", "paid_on": "2026-02-11"},
    )
    assert again.status_code == 422
    assert "pagada" in again.json()["detail"]


def test_uf_payment_within_tolerance_is_paid(client, headers_a, board, today):
    inst = board["overdue"]
    short = Decimal(str(inst.gross_amount_uf)) - Decimal("0.005")
    body = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": str(short), "currency": "UF", "paid_on": "2026-02-12"},
    ).json()
    assert body["state"]["state"] == "paid"


def test_clp_payment_converts_with_the_uf_of_the_day(client, db, headers_a, board, today):
    inst = board["upcoming"]  # 46.3334 UF
    fx.upsert_rates(db, [(date(2026, 2, 11), Decimal("39000.00"), None, "test")])
    db.commit()
    # Half the cuota in pesos.
    half_clp = 900_000
    body = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": half_clp, "currency": "CLP", "paid_on": "2026-02-11"},
    ).json()
    assert body["payment"]["currency"] == "CLP"
    assert Decimal(body["payment"]["uf_rate"]) == Decimal("39000.0000")
    assert Decimal(body["payment"]["amount_uf"]) == fx.clp_to_uf(half_clp, Decimal("39000"))
    assert body["state"]["state"] == "upcoming"
    assert "partial" in body["state"]["flags"]

    # The rest, 500 pesos short: within tolerance_clp (1000) -> settles exactly.
    balance = Decimal(body["state"]["balance_uf"])
    rest_clp = fx.uf_to_clp(balance, Decimal("39000")) - 500
    body = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": rest_clp, "currency": "CLP", "paid_on": "2026-02-11"},
    ).json()
    assert body["state"]["state"] == "paid"
    assert Decimal(body["payment"]["amount_uf"]) == balance
    assert Decimal(body["payment"]["amount"]) == Decimal(rest_clp)  # verbatim


def test_clp_payment_without_rate_is_422(client, headers_a, board, today):
    inst = board["upcoming"]
    response = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": 100000, "currency": "CLP", "paid_on": "2026-02-11"},
    )
    assert response.status_code == 422
    assert "valor de la UF" in response.json()["detail"]
    decimals = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": "100.5", "currency": "CLP", "paid_on": "2026-02-11"},
    )
    assert decimals.status_code == 422
    assert "sin decimales" in decimals.json()["detail"]


def test_usd_payment_converts_through_pesos(client, db, headers_a, board, today):
    inst = board["upcoming"]
    fx.upsert_rates(db, [(date(2026, 2, 11), Decimal("39000"), Decimal("975"), "test")])
    db.commit()
    body = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": "400", "currency": "USD", "paid_on": "2026-02-11"},
    ).json()
    # 400 USD * 975 / 39000 = 10 UF
    assert Decimal(body["payment"]["amount_uf"]) == Decimal("10.0000")


def test_payment_validation(client, headers_a, board, today):
    inst = board["upcoming"]
    future = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": "1", "currency": "UF", "paid_on": "2026-02-13"},
    )
    assert future.status_code == 422
    assert "futura" in future.json()["detail"]
    zero = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": "0", "currency": "UF", "paid_on": "2026-02-12"},
    )
    assert zero.status_code == 422


def test_receipt_of_another_tenant_is_404(client, headers_a, board, today, world):
    response = client.post(
        f"{API}/collections/installments/{board['upcoming'].id}/payments",
        headers=headers_a,
        json={
            "amount": "1",
            "currency": "UF",
            "paid_on": "2026-02-12",
            "receipt_document_id": world.b.document.id,
        },
    )
    assert response.status_code == 404


def test_reverse_and_confirm(client, db, headers_a, board, today):
    inst = board["overdue"]
    created = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": str(inst.gross_amount_uf), "currency": "UF", "paid_on": "2026-02-10"},
    ).json()
    payment_id = created["payment"]["id"]
    assert created["state"]["state"] == "paid"
    assert "unconfirmed" in created["state"]["flags"]

    confirmed = client.post(f"{API}/collections/payments/{payment_id}/confirm", headers=headers_a)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["payment"]["evidence_level"] == "insurer_confirmed"
    assert confirmed.json()["payment"]["confirmed_by_insurer"] is True
    assert "unconfirmed" not in confirmed.json()["state"]["flags"]

    reversed_ = client.post(
        f"{API}/collections/payments/{payment_id}/reverse",
        headers=headers_a,
        json={"reason": "error", "note": "Se registró en la cuota equivocada"},
    )
    assert reversed_.status_code == 200, reversed_.text
    body = reversed_.json()
    assert body["payment"]["status"] == "reversed"
    assert body["payment"]["reversal_reason"] == "error"
    assert body["state"]["state"] == "overdue"
    assert body["status"] == "overdue"
    # Never deleted.
    db.expire_all()
    assert db.get(Payment, payment_id) is not None

    again = client.post(
        f"{API}/collections/payments/{payment_id}/reverse",
        headers=headers_a,
        json={"reason": "error"},
    )
    assert again.status_code == 409
    confirm_reversed = client.post(
        f"{API}/collections/payments/{payment_id}/confirm", headers=headers_a
    )
    assert confirm_reversed.status_code == 422

    listed = client.get(
        f"{API}/collections/installments/{inst.id}/payments", headers=headers_a
    ).json()
    assert [p["status"] for p in listed] == ["reversed"]


def test_payment_closes_the_cuota_alert_and_reversal_reraises_it(
    client, db, headers_a, board, today, world
):
    from app.jobs import run_job
    from datetime import datetime, timezone

    run_job(db, "collection.clock", datetime(2026, 2, 12, 15, tzinfo=timezone.utc))
    inst = board["overdue"]

    def open_alerts():
        db.expire_all()
        return db.scalars(
            select(Notification).where(
                Notification.origin_type == "installment",
                Notification.origin_id == inst.id,
                Notification.status != NotificationStatus.CLOSED,
            )
        ).all()

    alerts = open_alerts()
    assert [a.kind for a in alerts] == [NotificationKind.INSTALLMENT_OVERDUE]
    assert alerts[0].user_id == world.a.admin.id

    paid = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": str(inst.gross_amount_uf), "currency": "UF", "paid_on": "2026-02-12"},
    ).json()
    assert open_alerts() == []

    client.post(
        f"{API}/collections/payments/{paid['payment']['id']}/reverse",
        headers=headers_a,
        json={"reason": "direct_debit_rejected"},
    )
    alerts = open_alerts()
    assert len(alerts) == 1 and alerts[0].status == NotificationStatus.NEW


# --- legacy PATCH conversion -------------------------------------------------------


def test_legacy_patch_paid_on_becomes_a_declared_payment(client, db, headers_a, board, today):
    plan = board["plan"]
    inst = board["upcoming"]
    response = client.patch(
        f"{API}/collections/{plan.id}/installments/{inst.number}",
        headers=headers_a,
        # Exactly what the legacy ledger UI sends.
        json={"paid_on": "2026-03-10", "status": "paid", "days_late": 0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "paid_late"  # derived from the payment: 5 days late
    assert body["days_late"] == 5

    db.expire_all()
    payments = db.scalars(select(Payment).where(Payment.installment_id == inst.id)).all()
    assert len(payments) == 1
    assert payments[0].source == PaymentSource.MANUAL
    assert payments[0].evidence_level == EvidenceLevel.DECLARED
    assert payments[0].amount_uf == inst.gross_amount_uf

    # A new date reverses and re-registers; the same date is a no-op.
    client.patch(
        f"{API}/collections/{plan.id}/installments/{inst.number}",
        headers=headers_a,
        json={"paid_on": "2026-03-05"},
    )
    client.patch(
        f"{API}/collections/{plan.id}/installments/{inst.number}",
        headers=headers_a,
        json={"paid_on": "2026-03-05"},
    )
    db.expire_all()
    payments = db.scalars(
        select(Payment).where(Payment.installment_id == inst.id).order_by(Payment.id)
    ).all()
    assert [p.status for p in payments] == [PaymentStatus.REVERSED, PaymentStatus.ACTIVE]
    assert db.get(CollectionInstallment, inst.id).status == InstallmentStatus.PAID

    # paid_on = null reopens the cuota (reversal, never delete).
    reopened = client.patch(
        f"{API}/collections/{plan.id}/installments/{inst.number}",
        headers=headers_a,
        json={"paid_on": None},
    ).json()
    assert reopened["status"] == "pending"
    assert reopened["paid_on"] is None
    db.expire_all()
    assert all(
        p.status == PaymentStatus.REVERSED
        for p in db.scalars(select(Payment).where(Payment.installment_id == inst.id))
    )

    # The board (live state) and the cache agree.
    rows = client.get(
        f"{API}/collections/installments", params={"plan_id": plan.id}, headers=headers_a
    ).json()["items"]
    assert {r["id"]: r["state"] for r in rows}[inst.id] == "upcoming"


def test_legacy_status_write_contradicting_payments_is_422(
    client, db, headers_a, board, today
):
    plan = board["plan"]
    paid = board["paid"]  # has an active payment
    response = client.patch(
        f"{API}/collections/{plan.id}/installments/{paid.number}",
        headers=headers_a,
        json={"status": "pending"},
    )
    assert response.status_code == 422
    # Without payments the legacy direct write still works.
    ok = client.patch(
        f"{API}/collections/{plan.id}/installments/{board['upcoming'].number}",
        headers=headers_a,
        json={"status": "credited"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "credited"


def test_replace_installments_refused_once_payments_exist(client, headers_a, board, today):
    plan = board["plan"]
    response = client.put(
        f"{API}/collections/{plan.id}/installments",
        headers=headers_a,
        json={"installments": [{"number": 1, "gross_amount_uf": "139"}]},
    )
    assert response.status_code == 422
    assert "pagos registrados" in response.json()["detail"]


# --- promise, debit rejection, mode -----------------------------------------------


def test_promise_creates_a_note_and_reads_promised(client, db, headers_a, board, today):
    inst = board["overdue"]
    response = client.post(
        f"{API}/collections/installments/{inst.id}/promise",
        headers=headers_a,
        json={"promise_until": "2026-02-19", "note": "Llamó el asegurado; paga el viernes"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"]["state"] == "promised"
    assert body["promise_until"] == "2026-02-19"
    note = db.get(Note, body["note_id"])
    assert note.entity_type == EntityType.INSTALLMENT
    assert note.entity_id == inst.id
    assert note.follow_up_on == date(2026, 2, 19)
    assert note.broker_id == board["plan"].broker_id

    rows = client.get(
        f"{API}/collections/installments", params={"bucket": "overdue"}, headers=headers_a
    ).json()["items"]
    assert rows[0]["last_note"]["body"] == "Llamó el asegurado; paga el viernes"
    assert rows[0]["promise_until"] == "2026-02-19"

    past = client.post(
        f"{API}/collections/installments/{inst.id}/promise",
        headers=headers_a,
        json={"promise_until": "2026-02-11"},
    )
    assert past.status_code == 422
    paid = client.post(
        f"{API}/collections/installments/{board['paid'].id}/promise",
        headers=headers_a,
        json={"promise_until": "2026-02-19"},
    )
    assert paid.status_code == 422


def test_promise_on_a_cuota_not_yet_due_stays_upcoming(client, db, headers_a, board, today):
    """A promise before the due date never makes the cuota read "vencida"."""
    inst = board["upcoming"]  # due 2026-03-05; today 2026-02-12
    response = client.post(
        f"{API}/collections/installments/{inst.id}/promise",
        headers=headers_a,
        json={"promise_until": "2026-02-20"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"]["state"] == "upcoming"
    assert body["status"] == "pending"
    assert body["promise_until"] == "2026-02-20"
    assert body["note_id"] is not None

    summary = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert summary["overdue"]["count"] == 1  # only the genuinely overdue cuota
    assert Decimal(summary["overdue"]["uf"]) == Decimal(str(board["overdue"].gross_amount_uf))

    def bucket(name):
        return set(_ids(client.get(
            f"{API}/collections/installments", params={"bucket": name}, headers=headers_a
        ).json()))

    assert inst.id not in bucket("overdue")
    assert inst.id in bucket("upcoming")

    # Once it is late, a promise that still holds reads "promised" (en acuerdo).
    today["value"] = date(2026, 3, 6)
    late = client.post(
        f"{API}/collections/installments/{inst.id}/promise",
        headers=headers_a,
        json={"promise_until": "2026-03-09"},
    )
    assert late.status_code == 200, late.text
    assert late.json()["state"]["state"] == "promised"
    assert late.json()["status"] == "overdue"
    assert inst.id in bucket("overdue")


def test_terminate_then_rehabilitate_reopens_the_voided_cuotas(
    client, db, headers_a, board, today
):
    """Art. 528: a rehabilitated plan's cuotas come back to cobranza."""
    from app.services.collection.clock import run_collection_clock
    from datetime import datetime, timezone

    plan, overdue, upcoming = board["plan"], board["overdue"], board["upcoming"]
    run_collection_clock(db, now=datetime(2026, 2, 12, 15, tzinfo=timezone.utc))

    def open_alerts(inst_id):
        db.expire_all()
        return db.scalars(
            select(Notification).where(
                Notification.origin_type == "installment",
                Notification.origin_id == inst_id,
                Notification.status != NotificationStatus.CLOSED,
            )
        ).all()

    assert len(open_alerts(overdue.id)) == 1  # +3 overdue alert

    terminated = client.patch(
        f"{API}/collections/plans/{plan.id}",
        headers=headers_a,
        json={"status": "terminated", "terminated_at": "2026-01-20T00:00:00"},
    )
    assert terminated.status_code == 200, terminated.text
    # Voided and its alert closed at once — no clock run needed.
    db.expire_all()
    assert db.get(CollectionInstallment, overdue.id).status == InstallmentStatus.CANCELLED
    assert db.get(CollectionInstallment, upcoming.id).status == InstallmentStatus.CANCELLED
    assert db.get(CollectionInstallment, board["paid"].id).status == InstallmentStatus.PAID
    assert open_alerts(overdue.id) == []

    # The clock keeps them voided while the plan is terminated.
    run_collection_clock(db, now=datetime(2026, 2, 12, 16, tzinfo=timezone.utc))
    db.expire_all()
    assert db.get(CollectionInstallment, overdue.id).status == InstallmentStatus.CANCELLED

    rehabilitated = client.patch(
        f"{API}/collections/{plan.id}",  # the legacy PATCH runs the same path
        headers=headers_a,
        json={"status": "rehabilitated", "terminated_at": None,
              "rehabilitated_at": "2026-02-10T00:00:00"},
    )
    assert rehabilitated.status_code == 200, rehabilitated.text
    rows = {
        row["id"]: row
        for row in client.get(f"{API}/collections/installments", headers=headers_a).json()["items"]
    }
    assert rows[overdue.id]["state"] == "overdue"
    assert Decimal(rows[overdue.id]["balance_uf"]) == Decimal(str(overdue.gross_amount_uf))
    assert rows[upcoming.id]["state"] == "upcoming"
    assert rows[board["paid"].id]["state"] == "paid"
    summary = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert summary["overdue"]["count"] == 1
    assert len(open_alerts(overdue.id)) == 1  # what is owed is raised again


def test_rehabilitation_keeps_cuotas_cancelled_for_another_reason(
    client, db, headers_a, board, today
):
    """Only the cuotas the OLD termination voided are reopened."""
    plan, overdue, upcoming = board["plan"], board["overdue"], board["upcoming"]
    # A legacy cancelled cuota due BEFORE the termination date (not voided by it).
    db.get(CollectionInstallment, overdue.id).status = InstallmentStatus.CANCELLED
    db.commit()
    client.patch(
        f"{API}/collections/plans/{plan.id}",
        headers=headers_a,
        json={"status": "terminated", "terminated_at": "2026-02-20T00:00:00"},
    )
    db.expire_all()
    assert db.get(CollectionInstallment, upcoming.id).status == InstallmentStatus.CANCELLED
    response = client.patch(
        f"{API}/collections/plans/{plan.id}",
        headers=headers_a,
        json={"status": "rehabilitated", "rehabilitated_at": "2026-02-25T00:00:00"},
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(CollectionInstallment, upcoming.id).status == InstallmentStatus.PENDING
    assert db.get(CollectionInstallment, overdue.id).status == InstallmentStatus.CANCELLED


def test_termination_never_erases_a_legacy_paid_cuota(client, db, headers_a, board, today):
    """A pre-v12 paid cuota (no payment rows) survives terminate -> rehabilitate."""
    plan, upcoming = board["plan"], board["upcoming"]
    row = db.get(CollectionInstallment, upcoming.id)
    row.status = InstallmentStatus.PAID
    row.paid_on = date(2026, 2, 1)
    db.commit()
    client.patch(
        f"{API}/collections/plans/{plan.id}",
        headers=headers_a,
        json={"status": "terminated", "terminated_at": "2026-01-20T00:00:00"},
    )

    def state():
        items = client.get(f"{API}/collections/installments", headers=headers_a).json()["items"]
        return {r["id"]: r for r in items}[upcoming.id]["state"]

    assert state() == "voided"
    db.expire_all()
    assert db.get(CollectionInstallment, upcoming.id).status == InstallmentStatus.PAID
    client.patch(
        f"{API}/collections/plans/{plan.id}",
        headers=headers_a,
        json={"status": "rehabilitated", "terminated_at": None},
    )
    assert state() == "paid"
    db.expire_all()
    assert db.get(CollectionInstallment, upcoming.id).paid_on == date(2026, 2, 1)


def test_direct_debit_rejected_flags_and_alerts(client, db, headers_a, board, today, world):
    inst = board["upcoming"]
    response = client.post(
        f"{API}/collections/installments/{inst.id}/direct-debit-rejected",
        headers=headers_a,
        json={"rejected_on": "2026-02-10"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "direct_debit_rejected" in body["state"]["flags"]
    assert body["direct_debit_rejected_at"] == "2026-02-10"
    db.expire_all()
    alert = db.scalar(
        select(Notification).where(
            Notification.origin_type == "installment", Notification.origin_id == inst.id
        )
    )
    assert alert.kind == NotificationKind.DIRECT_DEBIT_REJECTED
    assert alert.user_id == world.a.admin.id
    future = client.post(
        f"{API}/collections/installments/{inst.id}/direct-debit-rejected",
        headers=headers_a,
        json={"rejected_on": "2026-03-18"},
    )
    assert future.status_code == 422


def test_mode_endpoint(client, headers_a, board, today):
    body = client.get(
        f"{API}/collections/installments/{board['overdue'].id}/mode", headers=headers_a
    ).json()
    assert body["level"] == "assisted"
    assert body["channels"] == []
    assert "templates_unapproved" in body["missing"]
    assert "onboarding" not in str(body)


# --- plans -------------------------------------------------------------------------


def test_plan_patch_and_start(client, headers_a, board, today):
    plan = board["plan"]
    patched = client.patch(
        f"{API}/collections/plans/{plan.id}",
        headers=headers_a,
        json={
            "level_preference": "record",
            "notices_paused_until": "2026-03-01",
            "notices_pause_reason": "Asegurado en viaje",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["level_preference"] == "record"
    assert patched.json()["notices_pause_reason"] == "Asegurado en viaje"
    mode = client.get(
        f"{API}/collections/installments/{board['overdue'].id}/mode", headers=headers_a
    ).json()
    assert mode["level"] == "record"

    refused = client.post(
        f"{API}/collections/plans/{plan.id}/start",
        headers=headers_a,
        json={"level_preference": "record"},
    )
    assert refused.status_code == 422

    started = client.post(f"{API}/collections/plans/{plan.id}/start", headers=headers_a)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["notices_enabled"] is True
    assert body["level_preference"] == "auto"
    assert body["notices_paused_until"] is None

    # The legacy PATCH accepts the same fields.
    legacy = client.patch(
        f"{API}/collections/{plan.id}", headers=headers_a, json={"notices_enabled": False}
    )
    assert legacy.json()["notices_enabled"] is False


def test_plan_create_accepts_source_and_closes_plan_missing(
    client, db, headers_a, world, today
):
    from datetime import datetime, timezone

    from app.jobs import run_job
    from tests.conftest import make_policy

    policy = make_policy(
        db, world.a, world.insurer, policy_number="POL-MISSING",
        start_date=date(2026, 1, 1), end_date=date(2027, 1, 1),
    )
    db.commit()
    run_job(db, "collection.clock", datetime(2026, 2, 12, 15, tzinfo=timezone.utc))
    open_missing = lambda: db.scalars(  # noqa: E731
        select(Notification).where(
            Notification.kind == NotificationKind.PLAN_MISSING,
            Notification.origin_id == policy.id,
            Notification.status != NotificationStatus.CLOSED,
        )
    ).all()
    assert len(open_missing()) == 1

    created = client.post(
        f"{API}/collections",
        headers=headers_a,
        json={
            "policy_id": policy.id,
            "source": "manual",
            "origin": "policy",
            "installments": [{"number": 1, "gross_amount_uf": "139", "due_date": "2026-03-01"}],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "manual"
    assert created.json()["origin"] == "policy"
    db.expire_all()
    assert open_missing() == []


def test_literal_paths_are_never_captured_by_the_plan_routes(client, headers_a, board, today):
    # Lane B4 mounts /collections/evidence and /collections/notices on another
    # router; the legacy /{plan_id} routes must not answer them with a 422.
    for path in ("evidence", "notices", "inquiries"):
        response = client.get(f"{API}/collections/{path}", headers=headers_a)
        assert response.status_code != 422, (path, response.text)
    assert client.get(f"{API}/collections/{board['plan'].id}", headers=headers_a).status_code == 200


# --- settings & templates -----------------------------------------------------------


def test_settings_lazy_create_and_update(client, db, headers_a, world, today):
    body = client.get(f"{API}/collections/settings", headers=headers_a).json()
    assert body["cadence_days"] == [-15, -5, 0, 3, 10]
    assert Decimal(body["tolerance_uf"]) == Decimal("0.009")
    assert body["tolerance_clp"] == 1000
    assert body["notices_enabled"] is False
    assert body["native_collection_enabled"] is True

    updated = client.put(
        f"{API}/collections/settings",
        headers=headers_a,
        json={"cadence_days": [10, -3, 0, 5], "tolerance_clp": 2000, "auto_status_inquiries": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["cadence_days"] == [-3, 0, 5, 10]
    assert updated.json()["tolerance_clp"] == 2000

    # The master switch needs approved templates first.
    refused = client.put(
        f"{API}/collections/settings", headers=headers_a, json={"notices_enabled": True}
    )
    assert refused.status_code == 422
    assert "plantillas" in refused.json()["detail"]
    # Sending from a broker account needs the account.
    no_account = client.put(
        f"{API}/collections/settings", headers=headers_a, json={"default_sender": "broker_account"}
    )
    assert no_account.status_code == 422
    bad = client.put(
        f"{API}/collections/settings", headers=headers_a, json={"cadence_days": [1, 1]}
    )
    assert bad.status_code == 422


def test_settings_foreign_mail_account_is_404(client, db, headers_a, world, today):
    from app.models.enums import MailProvider
    from app.models.mail import MailAccount

    account = MailAccount(
        broker_id=world.b.broker_id, provider=MailProvider.CUSTOM, address="cobranza@beta.cl"
    )
    db.add(account)
    db.commit()
    response = client.put(
        f"{API}/collections/settings",
        headers=headers_a,
        json={"default_sender": "broker_account", "sender_mail_account_id": account.id},
    )
    assert response.status_code == 404


def test_templates_defaults_edit_and_approve(client, db, headers_a, world, today):
    body = client.get(f"{API}/collections/templates", headers=headers_a).json()
    assert body["approved"] is False
    assert all(item["is_default"] for item in body["items"])
    kinds = {(i["kind"], i["cadence_level"]) for i in body["items"]}
    assert ("payer_reminder", -5) in kinds
    assert ("insurer_status_inquiry", None) in kinds
    assert "policy_number" in body["variables"]

    unknown = client.put(
        f"{API}/collections/templates",
        headers=headers_a,
        json={"templates": [{
            "kind": "payer_reminder", "cadence_level": -5,
            "subject": "Hola {{nombre}}", "body": "x",
        }]},
    )
    assert unknown.status_code == 422
    assert "{{nombre}}" in unknown.json()["detail"]

    no_token = client.put(
        f"{API}/collections/templates",
        headers=headers_a,
        json={"templates": [{
            "kind": "insurer_status_inquiry", "subject": "Consulta {{policy_number}}", "body": "x",
        }]},
    )
    assert no_token.status_code == 422
    assert "RADAL" in no_token.json()["detail"]

    approved = client.post(f"{API}/collections/templates/approve", headers=headers_a).json()
    assert approved["approved"] is True
    assert all(not item["is_default"] and item["approved_at"] for item in approved["items"])

    # The master switch may now be turned on.
    on = client.put(
        f"{API}/collections/settings", headers=headers_a, json={"notices_enabled": True}
    )
    assert on.status_code == 200, on.text

    edited = client.put(
        f"{API}/collections/templates",
        headers=headers_a,
        json={"templates": [{
            "kind": "payer_reminder", "cadence_level": -5,
            "subject": "Tu cuota {{installment_number}} vence el {{due_date}}",
            "body": "Hola {{insured_name}}: paga aquí {{share_url}}",
        }]},
    ).json()
    assert edited["approved"] is False  # a content change withdraws the approval
    reminder = next(
        i for i in edited["items"] if i["kind"] == "payer_reminder" and i["cadence_level"] == -5
    )
    assert reminder["subject"].startswith("Tu cuota")
    assert reminder["approved_at"] is None


def test_templates_and_settings_are_per_broker(client, headers_a, headers_b, world, today):
    client.post(f"{API}/collections/templates/approve", headers=headers_a)
    assert client.get(f"{API}/collections/templates", headers=headers_b).json()["approved"] is False
    client.put(f"{API}/collections/settings", headers=headers_a, json={"tolerance_clp": 5})
    assert client.get(f"{API}/collections/settings", headers=headers_b).json()["tolerance_clp"] == 1000


# --- isolation & RBAC -----------------------------------------------------------------


def test_another_tenants_rows_are_404_everywhere(client, db, headers_a, board, today):
    b_inst = board["b_inst"]
    b_payment = make_payment(db, board["b_plan"].installments[0], paid_on=date(2026, 1, 5))
    db.commit()
    paths = [
        ("get", f"/collections/installments/{b_inst.id}/mode", None),
        ("get", f"/collections/installments/{b_inst.id}/payments", None),
        ("post", f"/collections/installments/{b_inst.id}/payments",
         {"amount": "1", "currency": "UF", "paid_on": "2026-02-12"}),
        ("post", f"/collections/installments/{b_inst.id}/promise", {"promise_until": "2026-02-19"}),
        ("post", f"/collections/installments/{b_inst.id}/direct-debit-rejected",
         {"rejected_on": "2026-02-12"}),
        ("post", f"/collections/payments/{b_payment.id}/reverse", {"reason": "error"}),
        ("post", f"/collections/payments/{b_payment.id}/confirm", None),
        ("patch", f"/collections/plans/{board['b_plan'].id}", {"notices_enabled": True}),
        ("post", f"/collections/plans/{board['b_plan'].id}/start", None),
        ("patch", f"/collections/{board['b_plan'].id}/installments/1", {"paid_on": "2026-01-05"}),
    ]
    for method, path, body in paths:
        kwargs = {"headers": headers_a}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(f"{API}{path}", **kwargs)
        assert response.status_code == 404, (path, response.status_code, response.text)
    rows = client.get(
        f"{API}/collections/installments", params={"plan_id": board["b_plan"].id}, headers=headers_a
    ).json()
    assert rows["total"] == 0
    db.expire_all()
    assert db.get(Payment, b_payment.id).status == PaymentStatus.ACTIVE


def test_executive_may_register_payments_inspector_may_not_see(
    client, db, headers_a_exec, headers_a_inspector, headers_a_tech, board, today
):
    inst = board["upcoming"]
    created = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a_exec,
        json={"amount": "5", "currency": "UF", "paid_on": "2026-02-12"},
    )
    assert created.status_code == 201, created.text
    assert client.post(
        f"{API}/collections/installments/{inst.id}/promise",
        headers=headers_a_exec,
        json={"promise_until": "2026-02-19"},
    ).status_code == 200
    # ...but cobranza settings are Settings.Manage (admin only).
    assert client.put(
        f"{API}/collections/settings", headers=headers_a_exec, json={"tolerance_clp": 1}
    ).status_code == 403
    assert client.get(f"{API}/collections/settings", headers=headers_a_exec).status_code == 200

    assert client.get(f"{API}/collections/installments", headers=headers_a_inspector).status_code == 403
    assert client.get(f"{API}/collections/summary", headers=headers_a_inspector).status_code == 403
    # The technician reads, never writes.
    assert client.get(f"{API}/collections/installments", headers=headers_a_tech).status_code == 200
    assert client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a_tech,
        json={"amount": "5", "currency": "UF", "paid_on": "2026-02-12"},
    ).status_code == 403


def test_collections_role_may_not_edit_settings(client, db, world, board, today):
    user = ensure_role_user(db, world.a, "broker_collections", "cobranza")
    headers = auth_headers(client, user.email)
    assert client.get(f"{API}/collections/installments", headers=headers).status_code == 200
    assert client.put(
        f"{API}/collections/templates/approve", headers=headers
    ).status_code in (403, 405)
    assert client.post(f"{API}/collections/templates/approve", headers=headers).status_code == 403


def test_settings_tolerance_feeds_the_state(client, db, headers_a, board, world, today):
    make_collection_settings(db, world.a.broker_id, tolerance_uf=Decimal("1.0000"))
    db.commit()
    inst = board["upcoming"]
    body = client.post(
        f"{API}/collections/installments/{inst.id}/payments",
        headers=headers_a,
        json={"amount": str(Decimal(str(inst.gross_amount_uf)) - Decimal("0.5")),
              "currency": "UF", "paid_on": "2026-02-12"},
    ).json()
    assert body["state"]["state"] == "paid"
