"""Native checkout, intents, insurer capability and the payments jobs (v12, 02 §5, 03 §6).

Hermetic: every provider call goes to ``FakePaymentProvider`` (``fake_provider``).
The helpers at the top are reused by ``test_v12_webhooks.py`` / ``test_v12_platform.py``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.jobs import run_job
from app.models.activity import Activity
from app.models.collection import CollectionInstallment
from app.models.enums import (
    CollectionLevelPreference,
    EntityType,
    EvidenceLevel,
    IntentStatus,
    JobStatus,
    OnboardingStatus,
    PartyRole,
    ShareAudience,
    ShareEntity,
    UserType,
)
from app.models.payment import Payment, PaymentIntent, PaymentIntentItem
from app.models.provider import ProviderCharge, ProviderPayout
from app.models.user import User
from app.services.payments.checkout import (
    CheckoutError,
    create_checkout_for_share,
    create_intent_for_installments,
    mark_paid_outside,
)
from app.services.payments.providers.base import PaymentProviderError
from app.services.share_links import create_share_link
from tests.conftest import API, _password_hash, auth_headers, ensure_role_user
from tests.fakes_v12 import (
    make_broker_override,
    make_collection_settings,
    make_insurer_profile,
    make_native_channel,
    make_party,
    make_payment,
    make_policy_with_plan,
    make_provider_account,
)

PAYER_RUT = "11111111-1"
NOW = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)


# --- shared helpers (imported by the sibling suites) ---------------------------------

_counter = {"n": 0}


def native_policy(
    db,
    tenant,
    insurer,
    *,
    account=None,
    n=3,
    start=date(2026, 11, 5),
    channel=True,
    payer_email="pagador@example.test",
    payer_rut=PAYER_RUT,
    store="597000000001",
    **plan_kw,
):
    """A policy + plan whose lead insurer collects natively through ``account``."""
    _counter["n"] += 1
    policy, plan = make_policy_with_plan(
        db, tenant, insurer, n_installments=n, start=start,
        policy_number=f"POL-NAT-{tenant.broker_id}-{_counter['n']}", **plan_kw,
    )
    make_party(db, policy, role=PartyRole.PAYER, rut=payer_rut, email=payer_email)
    if account is None:
        account = make_provider_account(db)
    if channel:
        from app.models.insurer_collection import InsurerPaymentChannel

        exists = db.scalar(
            select(InsurerPaymentChannel).where(InsurerPaymentChannel.insurer_id == insurer.id)
        )
        if exists is None:
            make_native_channel(db, insurer, account, store_commerce_code=store)
    db.commit()
    db.refresh(plan)
    return policy, plan, account


def installments(plan) -> list[CollectionInstallment]:
    return sorted(plan.installments, key=lambda i: i.number)


def make_platform_user(db, email: str = "ops@radal.cl") -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            broker_id=None,
            email=email,
            hashed_password=_password_hash(),
            full_name="Radal Ops",
            user_type=UserType.PLATFORM,
            role="platform_admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
    return user


def create_intent(db, tenant, ids, *, user=None, share_link=None):
    result = create_intent_for_installments(
        db, broker_id=tenant.broker_id, installment_ids=ids, user=user, share_link=share_link, now=NOW
    )
    db.commit()
    return db.get(PaymentIntent, result.intent_id), result


# --- POST /payments/intents ---------------------------------------------------------------

def test_create_intent_happy_path(client, db, world, headers_a, fake_provider):
    policy, plan, acct = native_policy(db, world.a, world.insurer)
    first, second, _ = installments(plan)

    res = client.post(f"{API}/payments/intents", json={"installment_ids": [first.id, second.id]}, headers=headers_a)
    assert res.status_code == 201, res.text
    body = res.json()
    intent = body["intent"]
    assert body["checkout_url"].startswith("https://pay.fake-provider.test/checkout/rdl-intent-")
    assert intent["status"] == "pending"
    assert intent["provider_account_id"] == acct.id
    assert [i["installment_id"] for i in intent["items"]] == [first.id, second.id]
    assert all(i["external_installment_id"].startswith(f"rdl-cuota-{i['installment_id']}-") for i in intent["items"])
    assert all(i["store_commerce_code"] == "597000000001" for i in intent["items"])
    assert Decimal(intent["total_uf"]) == Decimal(str(first.gross_amount_uf)) + Decimal(str(second.gross_amount_uf))

    (customer,) = fake_provider.calls_to("ensure_customer")
    assert customer["payer"].rut == PAYER_RUT and customer["payer"].email == "pagador@example.test"
    (checkout,) = fake_provider.calls_to("create_spot_checkout")
    assert checkout["external_id"] == intent["external_id"]
    assert "/api/v1/webhooks/forpay?account=" in checkout["webhook_url"]
    assert "&sig=" in checkout["webhook_url"] and checkout["webhook_url"].endswith("kind=payment")
    assert [i.amount_uf for i in checkout["items"]] == [first.gross_amount_uf, second.gross_amount_uf]

    activity = db.scalar(select(Activity).where(Activity.entity_type == EntityType.PAYMENT_INTENT))
    assert activity is not None and activity.broker_id == world.a.broker_id


def test_same_set_reuses_the_open_link(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    ids = [installments(plan)[0].id]
    one = client.post(f"{API}/payments/intents", json={"installment_ids": ids}, headers=headers_a).json()
    two = client.post(f"{API}/payments/intents", json={"installment_ids": ids}, headers=headers_a).json()
    assert two["reused"] is True and two["intent"]["id"] == one["intent"]["id"]
    assert len(fake_provider.calls_to("create_spot_checkout")) == 1


def test_overlapping_link_is_superseded_and_annulled(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    a, b, _ = installments(plan)
    old = client.post(f"{API}/payments/intents", json={"installment_ids": [a.id, b.id]}, headers=headers_a).json()
    new = client.post(f"{API}/payments/intents", json={"installment_ids": [a.id]}, headers=headers_a).json()
    assert new["intent"]["id"] != old["intent"]["id"]
    (annul,) = fake_provider.calls_to("set_installment_state")
    assert annul["state"] == "annulled"
    assert sorted(annul["external_ids"]) == sorted(i["external_installment_id"] for i in old["intent"]["items"])
    db.expire_all()
    assert db.get(PaymentIntent, old["intent"]["id"]).status == IntentStatus.CANCELLED


def test_assisted_and_record_cuotas_are_refused_with_the_reason(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer, channel=False)
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers_a)
    assert res.status_code == 409
    # Review v12 #13: plain Spanish, pointing to what DOES work.
    assert "El pago en línea no está disponible" in res.json()["detail"]
    assert "sube el comprobante" in res.json()["detail"]

    _, plan2, _ = native_policy(
        db, world.a, world.insurer, level_preference=CollectionLevelPreference.RECORD
    )
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan2)[0].id]}, headers=headers_a)
    assert res.status_code == 409 and "registro" in res.json()["detail"]
    assert fake_provider.calls_to("create_spot_checkout") == []


def test_paid_cuota_is_refused(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    make_payment(db, first, paid_on=date(2026, 10, 1))
    db.commit()
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [first.id]}, headers=headers_a)
    assert res.status_code == 409 and res.json()["detail"] == "La cuota 1 ya está pagada"


def test_partial_payment_charges_only_the_balance(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    make_payment(db, first, amount_uf="10.0000", paid_on=date(2026, 10, 1))
    db.commit()
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [first.id]}, headers=headers_a)
    assert res.status_code == 201
    assert Decimal(res.json()["intent"]["items"][0]["amount_uf"]) == Decimal(str(first.gross_amount_uf)) - Decimal("10")


def test_tenant_isolation(client, db, world, headers_a, headers_b, fake_provider):
    _, plan_b, _ = native_policy(db, world.b, world.insurer)
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan_b)[0].id]}, headers=headers_a)
    assert res.status_code == 404

    _, plan_a, _ = native_policy(db, world.a, world.insurer)
    created = client.post(
        f"{API}/payments/intents", json={"installment_ids": [installments(plan_a)[0].id]}, headers=headers_a
    ).json()["intent"]
    assert client.get(f"{API}/payments/intents/{created['id']}", headers=headers_b).status_code == 404
    assert client.post(f"{API}/payments/intents/{created['id']}/cancel", headers=headers_b).status_code == 404
    assert client.get(f"{API}/payments/intents", headers=headers_b).json()["total"] == 0
    assert client.get(f"{API}/payments/intents", headers=headers_a).json()["total"] == 1

    # a share link of broker B is a 404 for broker A
    link_b = create_share_link(
        db, broker_id=world.b.broker_id, user=None, entity_type=ShareEntity.COLLECTION_PLAN,
        entity_id=plan_b.id, audience=ShareAudience.INSURED,
    )
    db.commit()
    res = client.post(
        f"{API}/payments/intents",
        json={"installment_ids": [installments(plan_a)[1].id], "share_link_id": link_b.id},
        headers=headers_a,
    )
    assert res.status_code == 404


def test_two_provider_accounts_must_pay_separately(client, db, world, headers_a, fake_provider):
    _, plan1, _ = native_policy(db, world.a, world.insurer)
    other_acct = make_provider_account(db, label="Cuenta Sin CMF")
    _, plan2, _ = native_policy(db, world.a, world.insurer_no_cmf, account=other_acct, store="597000000002")
    res = client.post(
        f"{API}/payments/intents",
        json={"installment_ids": [installments(plan1)[0].id, installments(plan2)[0].id]},
        headers=headers_a,
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "Paga por separado las cuotas de Sin CMF Seguros S.A."


def test_one_mall_account_can_mix_insurers(client, db, world, headers_a, fake_provider):
    _, plan1, acct = native_policy(db, world.a, world.insurer, store="597000000001")
    _, plan2, _ = native_policy(db, world.a, world.insurer_no_cmf, account=acct, store="597000000002")
    res = client.post(
        f"{API}/payments/intents",
        json={"installment_ids": [installments(plan1)[0].id, installments(plan2)[0].id]},
        headers=headers_a,
    )
    assert res.status_code == 201, res.text
    stores = [i["store_commerce_code"] for i in res.json()["intent"]["items"]]
    assert stores == ["597000000001", "597000000002"]


def test_payer_without_email_is_refused(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer, payer_email=None)
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers_a)
    assert res.status_code == 409 and "correo del pagador" in res.json()["detail"]


def test_different_payers_must_pay_separately(client, db, world, headers_a, fake_provider):
    _, plan1, acct = native_policy(db, world.a, world.insurer)
    _, plan2, _ = native_policy(db, world.a, world.insurer, account=acct, payer_rut="22222222-2")
    res = client.post(
        f"{API}/payments/intents",
        json={"installment_ids": [installments(plan1)[0].id, installments(plan2)[0].id]},
        headers=headers_a,
    )
    assert res.status_code == 409 and "distintos pagadores" in res.json()["detail"]


def test_provider_failure_is_502_and_the_intent_is_kept_failed(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    fake_provider.fail_next = PaymentProviderError("boom")
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers_a)
    assert res.status_code == 502
    assert "No pudimos generar el link de pago" in res.json()["detail"]
    db.expire_all()
    (intent,) = db.scalars(select(PaymentIntent)).all()
    assert intent.status == IntentStatus.FAILED


def test_permissions(client, db, world, headers_a_exec, headers_a_tech, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    ids = [installments(plan)[0].id]
    assert client.post(f"{API}/payments/intents", json={"installment_ids": ids}, headers=headers_a_tech).status_code == 403
    assert client.post(f"{API}/payments/intents", json={"installment_ids": ids}, headers=headers_a_exec).status_code == 201
    assert client.get(f"{API}/payments/intents", headers=headers_a_tech).status_code == 200
    assert client.post(f"{API}/payments/intents", json={"installment_ids": []}, headers=headers_a_exec).status_code == 422
    assert client.post(f"{API}/payments/intents", json={"installment_ids": ids}).status_code == 401

    platform = make_platform_user(db)
    headers_p = auth_headers(client, platform.email)
    assert client.post(f"{API}/payments/intents", json={"installment_ids": ids}, headers=headers_p).status_code == 403
    assert client.get(f"{API}/payments/insurers", headers=headers_p).status_code == 403


def test_cancel_intent(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    created = client.post(
        f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers_a
    ).json()["intent"]
    res = client.post(f"{API}/payments/intents/{created['id']}/cancel", headers=headers_a)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    (annul,) = fake_provider.calls_to("set_installment_state")
    assert annul["state"] == "annulled" and annul["external_ids"] == [created["items"][0]["external_installment_id"]]
    again = client.post(f"{API}/payments/intents/{created['id']}/cancel", headers=headers_a)
    assert again.status_code == 409 and again.json()["detail"] == "El link de pago ya está cerrado"


def test_cancel_failure_at_provider_keeps_the_link(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    created = client.post(
        f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers_a
    ).json()["intent"]
    fake_provider.fail_next = PaymentProviderError("down")
    res = client.post(f"{API}/payments/intents/{created['id']}/cancel", headers=headers_a)
    assert res.status_code == 502
    db.expire_all()
    assert db.get(PaymentIntent, created["id"]).status == IntentStatus.PENDING


def test_list_intents_filters(client, db, world, headers_a, fake_provider):
    policy, plan, acct = native_policy(db, world.a, world.insurer)
    a, b, _ = installments(plan)
    client.post(f"{API}/payments/intents", json={"installment_ids": [a.id]}, headers=headers_a)
    page = client.get(f"{API}/payments/intents", params={"installment_id": a.id}, headers=headers_a).json()
    assert page["total"] == 1
    assert client.get(f"{API}/payments/intents", params={"installment_id": b.id}, headers=headers_a).json()["total"] == 0
    assert client.get(f"{API}/payments/intents", params={"policy_id": policy.id}, headers=headers_a).json()["total"] == 1
    assert client.get(f"{API}/payments/intents", params={"status": "paid"}, headers=headers_a).json()["total"] == 0


# --- the share-link door ---------------------------------------------------------------------

def test_checkout_for_share_scopes_to_the_link(db, world, fake_provider):
    policy, plan, acct = native_policy(db, world.a, world.insurer)
    _, other_plan, _ = native_policy(db, world.a, world.insurer, account=acct)
    link = create_share_link(
        db, broker_id=world.a.broker_id, user=None, entity_type=ShareEntity.POLICY,
        entity_id=policy.id, audience=ShareAudience.INSURED,
    )
    db.commit()
    result = create_checkout_for_share(db, link, [installments(plan)[0].id], now=NOW)
    db.commit()
    intent = db.get(PaymentIntent, result.intent_id)
    assert intent.share_link_id == link.id
    (checkout,) = fake_provider.calls_to("create_spot_checkout")
    assert checkout["return_url"].endswith(f"/s/{link.token}?pago=retorno")

    with pytest.raises(CheckoutError) as info:
        create_checkout_for_share(db, link, [installments(other_plan)[0].id], now=NOW)
    assert info.value.status_code == 404

    insurer_link = create_share_link(
        db, broker_id=world.a.broker_id, user=None, entity_type=ShareEntity.POLICY,
        entity_id=policy.id, audience=ShareAudience.INSURER,
    )
    db.commit()
    with pytest.raises(CheckoutError) as info:
        create_checkout_for_share(db, insurer_link, [installments(plan)[1].id], now=NOW)
    assert info.value.status_code == 403

    no_pay = create_share_link(
        db, broker_id=world.a.broker_id, user=None, entity_type=ShareEntity.INSTALLMENT,
        entity_id=installments(plan)[2].id, audience=ShareAudience.INSURED, permissions={"can_pay": False},
    )
    db.commit()
    with pytest.raises(CheckoutError) as info:
        create_checkout_for_share(db, no_pay, [installments(plan)[2].id], now=NOW)
    assert info.value.status_code == 403


def test_checkout_for_share_installment_link(db, world, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    a, b, _ = installments(plan)
    link = create_share_link(
        db, broker_id=world.a.broker_id, user=None, entity_type=ShareEntity.INSTALLMENT,
        entity_id=a.id, audience=ShareAudience.INSURED,
    )
    db.commit()
    assert create_checkout_for_share(db, link, [a.id], now=NOW).checkout_url
    with pytest.raises(CheckoutError) as info:
        create_checkout_for_share(db, link, [b.id], now=NOW)
    assert info.value.status_code == 404


# --- mark_paid_outside ------------------------------------------------------------------------

def test_mark_paid_outside_tells_the_provider_and_closes_the_link(db, world, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    intent, _ = create_intent(db, world.a, [first.id])
    ext = intent.items[0].external_installment_id

    assert mark_paid_outside(db, first, now=NOW) == 0  # not paid yet: no-op
    assert fake_provider.calls_to("set_installment_state") == []

    make_payment(db, first, paid_on=date(2026, 10, 10))
    assert mark_paid_outside(db, first, now=NOW) == 1
    db.commit()
    (call,) = fake_provider.calls_to("set_installment_state")
    assert call == {"account_id": intent.provider_account_id, "external_ids": [ext], "state": "paid"}
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.CANCELLED


def test_mark_paid_outside_never_raises(db, world, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first, second, _ = installments(plan)
    intent, _ = create_intent(db, world.a, [first.id, second.id])
    make_payment(db, first, paid_on=date(2026, 10, 10))
    fake_provider.fail_next = PaymentProviderError("down")
    assert mark_paid_outside(db, first, now=NOW) == 0
    db.commit()
    db.expire_all()
    # the other cuota is still open: the link stays pending
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PENDING


# --- GET /payments/insurers + override -------------------------------------------------------

def test_insurer_capability_never_exposes_onboarding(client, db, world, headers_a, fake_provider):
    native_policy(db, world.a, world.insurer)
    make_insurer_profile(
        db, world.insurer, onboarding_status=OnboardingStatus.ACTIVE, expected_live_on=date(2026, 12, 1),
        status_note="interno: reunión con HDI", collections_email="cobranza@hdi.test",
        payment_instructions={"bank_name": "Banco HDI", "account_number": "000-1", "portal_url": "https://pagos.hdi.test"},
    )
    make_insurer_profile(
        db, world.insurer_no_cmf, onboarding_status=OnboardingStatus.ONBOARDING, expected_live_on=date(2026, 11, 30)
    )
    make_broker_override(
        db, world.a.broker_id, world.insurer_no_cmf, collections_email="otra@sincmf.test",
        payment_instructions={"account_number": "999"},
    )
    world.insurer_no_cmf.payment_url = "https://legacy.sincmf.test"
    db.commit()

    res = client.get(f"{API}/payments/insurers", headers=headers_a)
    assert res.status_code == 200
    text = res.text
    for forbidden in ("onboarding", "expected_live_on", "status_note", "interno", "2026-12-01", "2026-11-30"):
        assert forbidden not in text
    rows = {r["insurer"]["id"]: r for r in res.json()}
    hdi = rows[world.insurer.id]
    assert hdi["level_available"] == "native" and hdi["channels"] == ["webpay"]
    assert hdi["collections_email"] == "cobranza@hdi.test"
    assert hdi["instructions"]["bank_name"] == "Banco HDI"
    sin = rows[world.insurer_no_cmf.id]
    assert sin["level_available"] == "assisted" and sin["channels"] == []
    assert sin["collections_email"] == "otra@sincmf.test"
    assert sin["instructions"] == {"account_number": "999", "portal_url": "https://legacy.sincmf.test"}
    assert sin["broker_override"]["collections_email"] == "otra@sincmf.test"
    assert world.insurer_no_rut.id not in rows  # scope=mine: no policy, no override

    everything = client.get(f"{API}/payments/insurers", params={"scope": "all"}, headers=headers_a).json()
    assert world.insurer_no_rut.id in {r["insurer"]["id"] for r in everything}


def test_override_is_broker_scoped_and_gated(client, db, world, headers_a, headers_b, headers_a_exec, fake_provider):
    native_policy(db, world.a, world.insurer)
    native_policy(db, world.b, world.insurer)
    url = f"{API}/payments/insurers/{world.insurer.id}/override"
    body = {
        "collections_email": "pagos@corredora-a.cl",
        "contact_name": "María",
        "payment_instructions": {"account_number": "123", "bogus": "x"},
        "native_enabled": False,
    }
    assert client.put(url, json=body, headers=headers_a_exec).status_code == 403
    res = client.put(url, json=body, headers=headers_a)
    assert res.status_code == 200, res.text
    row = res.json()
    assert row["level_available"] == "assisted"  # opted out
    assert row["broker_override"]["payment_instructions"] == {"account_number": "123"}
    assert row["collections_email"] == "pagos@corredora-a.cl"

    other = {r["insurer"]["id"]: r for r in client.get(f"{API}/payments/insurers", headers=headers_b).json()}
    assert other[world.insurer.id]["broker_override"] is None
    assert other[world.insurer.id]["level_available"] == "native"

    # partial update keeps the other fields
    res = client.put(url, json={"native_enabled": True}, headers=headers_a)
    assert res.json()["level_available"] == "native"
    assert res.json()["broker_override"]["contact_name"] == "María"

    assert client.put(f"{API}/payments/insurers/999999/override", json={}, headers=headers_a).status_code == 404
    assert client.put(url, json={"collections_email": "no-es-correo"}, headers=headers_a).status_code == 422


def test_override_account_rut_is_mod11_validated_and_normalized(client, db, world, headers_a, fake_provider):
    """QA v12: a wrong check digit was saved verbatim and pasted into reminders."""
    native_policy(db, world.a, world.insurer)
    url = f"{API}/payments/insurers/{world.insurer.id}/override"
    bad = client.put(url, json={"payment_instructions": {"account_rut": "12.345.678-9"}}, headers=headers_a)
    assert bad.status_code == 422
    good = client.put(url, json={"payment_instructions": {"account_rut": "12.345.678-5"}}, headers=headers_a)
    assert good.status_code == 200, good.text
    assert good.json()["broker_override"]["payment_instructions"]["account_rut"] == "12345678-5"


def test_native_opt_out_blocks_checkout(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    make_collection_settings(db, world.a.broker_id, native_collection_enabled=False)
    db.commit()
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers_a)
    assert res.status_code == 409
    assert client.get(f"{API}/payments/insurers", headers=headers_a).json()[0]["level_available"] == "assisted"


# --- jobs --------------------------------------------------------------------------------------

def test_payments_sync_reconciles_idempotently(db, world, fake_provider):
    _, plan, acct = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    intent, _ = create_intent(db, world.a, [first.id])
    item = intent.items[0]
    fake_provider.add_receivable(
        provider_installment_id="FP-1",
        external_installment_id=item.external_installment_id,
        state=2,
        state_name="PAGADA",
        amount=item.amount_uf,
        currency="UF",
        payment_id="P-1",
        authorization_code="A1",
        payment_method="WEBPAY_PLUS",
        paid_at=datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc),
        convert_amount=Decimal("1800000"),
        convert_currency="PESOS",
        convert_date=date(2026, 10, 16),
    )

    run = run_job(db, "payments.sync", NOW)
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["payments_created"] == 1
    (call,) = fake_provider.calls_to("search_receivables")
    assert call["externalInstallmentId"] == [item.external_installment_id]
    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.evidence_level == EvidenceLevel.PROVIDER_CONFIRMED
    assert payment.currency.value == "CLP" and payment.amount == Decimal("1800000")
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PAID

    again = run_job(db, "payments.sync", NOW + timedelta(minutes=30))
    assert again.status == JobStatus.SUCCEEDED
    assert db.scalars(select(Payment)).all() == [payment]
    # same slot: not executed twice
    from app.jobs import trigger_job

    assert trigger_job(db, "payments.sync", NOW + timedelta(seconds=20)).executed is False


def test_payments_jobs_skip_without_accounts(db, world, fake_provider):
    assert run_job(db, "payments.sync", NOW).status == JobStatus.SKIPPED
    assert run_job(db, "payments.payouts", NOW).status == JobStatus.SKIPPED


def test_payments_sync_records_provider_errors_per_account(db, world, fake_provider):
    _, plan, acct = native_policy(db, world.a, world.insurer)
    create_intent(db, world.a, [installments(plan)[0].id])
    fake_provider.fail_next = PaymentProviderError("Forpay respondió 500 en /receivables")
    run = run_job(db, "payments.sync", NOW)
    assert run.status == JobStatus.SUCCEEDED
    assert run.stats["errors"][0]["account_id"] == acct.id
    db.expire_all()
    assert "500" in db.get(type(acct), acct.id).last_error


def test_payouts_settle_payments(db, world, fake_provider):
    _, plan, acct = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    intent, _ = create_intent(db, world.a, [first.id])
    item = intent.items[0]
    fake_provider.add_receivable(
        provider_installment_id="FP-9",
        external_installment_id=item.external_installment_id,
        state=2,
        amount=item.amount_uf,
        currency="UF",
        payment_id="P-9",
        paid_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc),
        payout={
            "dispersionId": "D-1",
            "creditedAmount": "1750000",
            "companyDepositDate": "2026-10-14",
            "destinationAccount": "000123456789",
            "bankTransactionNumber": "TX-1",
            "customerStatusName": "ABONADO AL COMERCIO",
        },
    )
    run = run_job(db, "payments.payouts", NOW)
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["payouts_created"] == 1 and run.stats["payments_settled"] == 1
    db.expire_all()
    (payout,) = db.scalars(select(ProviderPayout)).all()
    assert payout.dispersion_id == "D-1" and payout.insurer_id == world.insurer.id
    assert payout.destination_account_masked == "•••6789" and payout.deposit_date == date(2026, 10, 14)
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.payout_id == payout.id
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.evidence_level == EvidenceLevel.SETTLED
    (call,) = fake_provider.calls_to("search_receivables")
    assert call["hasPayout"] is True

    run_job(db, "payments.payouts", NOW + timedelta(days=1))
    assert len(db.scalars(select(ProviderPayout)).all()) == 1
    assert len(db.scalars(select(Payment)).all()) == 1


def test_intent_items_unique_external_ids(db, world, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    intent, _ = create_intent(db, world.a, [i.id for i in installments(plan)])
    exts = [i.external_installment_id for i in db.scalars(select(PaymentIntentItem)).all()]
    assert len(set(exts)) == 3 and all(len(e) <= 64 for e in exts)
    assert intent.external_id.startswith("rdl-intent-")


def test_role_executive_has_collections_edit(client, db, world, fake_provider):
    user = ensure_role_user(db, world.a, "broker_collections", "collector")
    headers = auth_headers(client, user.email)
    _, plan, _ = native_policy(db, world.a, world.insurer)
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [installments(plan)[0].id]}, headers=headers)
    assert res.status_code == 201


def test_open_link_is_not_reused_after_a_partial_payment(client, db, world, headers_a, fake_provider):
    _, plan, _ = native_policy(db, world.a, world.insurer)
    first = installments(plan)[0]
    full = Decimal(str(first.gross_amount_uf))
    one = client.post(f"{API}/payments/intents", json={"installment_ids": [first.id]}, headers=headers_a).json()
    assert Decimal(one["intent"]["items"][0]["amount_uf"]) == full
    make_payment(db, first, amount_uf="10.0000", paid_on=date(2026, 10, 1))
    db.commit()

    two = client.post(f"{API}/payments/intents", json={"installment_ids": [first.id]}, headers=headers_a).json()
    assert two["reused"] is False and two["intent"]["id"] != one["intent"]["id"]
    assert Decimal(two["intent"]["items"][0]["amount_uf"]) == full - Decimal("10")
    (annul,) = fake_provider.calls_to("set_installment_state")
    assert annul["state"] == "annulled"
    assert annul["external_ids"] == [one["intent"]["items"][0]["external_installment_id"]]
    db.expire_all()
    assert db.get(PaymentIntent, one["intent"]["id"]).status == IntentStatus.CANCELLED

    # unchanged balance: the new link IS reused
    three = client.post(f"{API}/payments/intents", json={"installment_ids": [first.id]}, headers=headers_a).json()
    assert three["reused"] is True and three["intent"]["id"] == two["intent"]["id"]
