"""v12 cross-lane wiring (integration pass).

Each test pins one seam between two lanes so a later edit on either side can't
silently break the other:

* B2 ledger -> B3 ``mark_paid_outside``: a non-provider payment that settles a
  cuota tells the provider, so an open Forpay link can't charge it twice.
* B4 evidence/notices are also served at the 02 §6 ``/collections/*`` paths.
* B2 cuota gestión notes go through ``/notes`` (``installment`` / ``payment``).
* B1 policy PATCH of ``terminated_on`` -> B2 ``reconcile_plan_installments``.
* B1 ``no_plan`` and B2 ``plans_missing`` share one definition.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.enums import InstallmentStatus, IntentStatus
from app.models.payment import PaymentIntent
from tests.conftest import API, make_policy
from tests.fakes_v12 import make_policy_with_plan
from tests.test_v12_payments import create_intent, installments, native_policy


def test_manual_payment_settling_a_cuota_reports_paid_outside(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    intent, _ = create_intent(db, world.a, [first.id])
    ext = intent.items[0].external_installment_id
    gross = Decimal(str(first.gross_amount_uf))

    res = client.post(
        f"{API}/collections/installments/{first.id}/payments",
        headers=headers_a,
        json={"amount": str(gross), "currency": "UF", "paid_on": date.today().isoformat()},
    )
    assert res.status_code == 201, res.text
    assert res.json()["state"]["state"] == "paid"
    (call,) = fake_provider.calls_to("set_installment_state")
    assert call["external_ids"] == [ext] and call["state"] == "paid"
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.CANCELLED


def test_partial_manual_payment_supersedes_the_link(client, db, world, headers_a, fake_provider):
    """The open link froze the FULL balance: left open, the payer would be overcharged."""
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    intent, _ = create_intent(db, world.a, [first.id])
    ext = intent.items[0].external_installment_id
    res = client.post(
        f"{API}/collections/installments/{first.id}/payments",
        headers=headers_a,
        json={"amount": "0.5", "currency": "UF", "paid_on": date.today().isoformat()},
    )
    assert res.status_code == 201, res.text
    calls = fake_provider.calls_to("set_installment_state")
    assert calls and calls[-1]["state"] == "annulled" and calls[-1]["external_ids"] == [ext]
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.CANCELLED


def test_evidence_and_notices_answer_on_both_doors(client, db, world, headers_a, headers_b):
    for path in ("evidence", "notices"):
        a = client.get(f"{API}/collections/{path}", headers=headers_a)
        b = client.get(f"{API}/inquiries/{path}", headers=headers_a)
        assert a.status_code == b.status_code == 200, (path, a.text)
        assert a.json() == b.json()
    # Same 404 semantics for another tenant's / an unknown row.
    res = client.post(f"{API}/collections/evidence/999999/reject", headers=headers_b, json={})
    assert res.status_code in (404, 422)
    # The /collections int routes still win for numeric ids.
    assert client.get(f"{API}/collections/999999", headers=headers_a).status_code == 404


def test_cuota_notes_go_through_notes(client, db, world, headers_a, headers_b):
    _, plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-NOTE")
    db.commit()
    inst = plan.installments[0]
    body = {"entity_type": "installment", "entity_id": inst.id, "body": "Promete pagar el viernes"}
    res = client.post(f"{API}/notes", headers=headers_a, json=body)
    assert res.status_code == 201, res.text
    listed = client.get(
        f"{API}/notes", headers=headers_a, params={"entity_type": "installment", "entity_id": inst.id}
    )
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    # Another tenant's cuota is a 404, never a 403.
    assert client.post(f"{API}/notes", headers=headers_b, json=body).status_code == 404


def test_policy_termination_voids_and_reopens_cuotas(client, db, world, headers_a):
    policy, plan = make_policy_with_plan(
        db, world.a, world.insurer, n_installments=3, start=date(2026, 1, 5), policy_number="POL-TERM"
    )
    db.commit()
    url = f"{API}/policies/{policy.id}"

    res = client.patch(url, headers=headers_a, json={"terminated_on": "2026-01-20"})
    assert res.status_code == 200, res.text
    db.expire_all()
    statuses = [
        i.status for i in sorted(db.get(type(plan), plan.id).installments, key=lambda i: i.number)
    ]
    assert statuses[1:] == [InstallmentStatus.CANCELLED, InstallmentStatus.CANCELLED]

    # Undoing it (Art. 528 rehabilitation) is a correction and reopens them.
    res = client.patch(
        url, headers=headers_a, json={"terminated_on": None, "correction_reason": "Rehabilitada"}
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    reopened = [
        db.get(CollectionInstallment, i.id).status for i in plan.installments if i.number > 1
    ]
    assert InstallmentStatus.CANCELLED not in reopened


def test_no_plan_is_one_definition(client, db, world, headers_a):
    today = date.today()
    make_policy(
        db, world.a, world.insurer, policy_number="POL-SIN-PLAN",
        start_date=today - timedelta(days=10), end_date=today + timedelta(days=300),
    )
    make_policy(
        db, world.a, world.insurer, policy_number="POL-FUTURA",
        start_date=today + timedelta(days=20), end_date=today + timedelta(days=385),
    )
    make_policy_with_plan(
        db, world.a, world.insurer, start=today, policy_number="POL-CON-PLAN",
        start_date=today - timedelta(days=5), end_date=today + timedelta(days=360),
    )
    # An EMPTY plan (no cuotas) is still "sin plan de pago" on both views.
    empty = make_policy(
        db, world.a, world.insurer, policy_number="POL-PLAN-VACIO",
        start_date=today - timedelta(days=5), end_date=today + timedelta(days=360),
    )
    db.add(CollectionPlan(broker_id=world.a.broker_id, policy_id=empty.id))
    db.commit()
    policies = client.get(f"{API}/policies/summary", headers=headers_a).json()
    collections = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert policies["no_plan"] == collections["plans_missing"] == 3
    listed = client.get(f"{API}/policies", headers=headers_a, params={"collection": "no_plan"}).json()
    assert {row["policy_number"] for row in listed["items"]} == {
        "POL-SIN-PLAN", "POL-FUTURA", "POL-PLAN-VACIO",
    }


def test_plan_create_answers_on_the_contract_path(client, db, world, headers_a):
    policy = make_policy(db, world.a, world.insurer, policy_number="POL-PLANS-PATH")
    db.commit()
    res = client.post(f"{API}/collections/plans", headers=headers_a, json={"policy_id": policy.id})
    legacy = client.post(f"{API}/collections", headers=headers_a, json={"policy_id": policy.id})
    assert res.status_code == legacy.status_code == 201, (res.text, legacy.text)
    assert res.json()["policy_id"] == legacy.json()["policy_id"] == policy.id
    assert client.post(
        f"{API}/collections/plans", headers=headers_a, json={"policy_id": 999999}
    ).status_code == 404
