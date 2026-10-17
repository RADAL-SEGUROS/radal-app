"""The generic provider webhook — the 09-testing matrix (v12, 03 §5).

unknown account 404 · bad sig 401 · bad JSON 400 · oversize 413 · bad provider
HMAC 401 (stored with ``signature_valid=false``) · duplicate 200 duplicate ·
state 2 creates exactly one payment · replay creates none · −99 / 12 handled ·
processing failures answer 200 and are retried · the real ``ForpayProvider``
verifies without any network.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.models.collection import CollectionInstallment
from app.models.enums import (
    EventProcessing,
    EvidenceLevel,
    InstallmentStatus,
    IntentStatus,
    MandateStatus,
    NotificationKind,
    PaymentChannel,
    PaymentCurrency,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    ProviderAccountStatus,
    ReversalReason,
)
from app.models.notification import Notification
from app.models.payment import Payment, PaymentIntent, PaymentIntentItem, PaymentMandate
from app.models.provider import ProviderCharge, ProviderEvent
from app.services.payments.accounts import rotate_webhook_secret
from app.services.payments.providers.base import account_secret
from app.services.payments.providers.fake import FakePaymentProvider
from tests.conftest import API
from tests.fakes_v12 import TEST_HMAC_KEY, TEST_WEBHOOK_SECRET, make_provider_account, webhook_sig
from tests.test_v12_payments import NOW, create_intent, installments, native_policy

build = FakePaymentProvider.build_webhook_payload


def post_hook(client, acct, payload, *, sig=None, key=None, kind="payment", provider="forpay", raw=None):
    params = {"account": key if key is not None else acct.webhook_key, "kind": kind}
    params["sig"] = sig if sig is not None else webhook_sig(TEST_WEBHOOK_SECRET, acct.webhook_key)
    content = raw if raw is not None else json.dumps(payload).encode()
    return client.post(
        f"{API}/webhooks/{provider}", params=params, content=content, headers={"Content-Type": "application/json"}
    )


def setup_intent(db, world, *, n_items=1):
    _, plan, acct = native_policy(db, world.a, world.insurer)
    cuotas = installments(plan)[:n_items]
    intent, _ = create_intent(db, world.a, [c.id for c in cuotas])
    return intent, acct, cuotas


def paid_payload(item, *, internal_id="FP-1", state=2, **kw):
    return build(
        TEST_HMAC_KEY,
        internal_id=internal_id,
        external_installment_id=item.external_installment_id,
        state=state,
        amount=str(item.amount_uf),
        **kw,
    )


# --- authentication & parsing -----------------------------------------------------------------

def test_unknown_account_wrong_provider_and_disabled_are_404(client, db, world, fake_provider):
    acct = make_provider_account(db)
    db.commit()
    assert post_hook(client, acct, {}, key="nope").status_code == 404
    assert post_hook(client, acct, {}, key="").status_code == 404
    assert post_hook(client, acct, {}, key="x" * 500).status_code == 404
    assert post_hook(client, acct, {}, provider="paypal").status_code == 404
    acct.status = ProviderAccountStatus.DISABLED
    db.commit()
    assert post_hook(client, acct, {}).status_code == 404
    assert db.scalars(select(ProviderEvent)).all() == []


def test_bad_sig_is_401_and_nothing_is_stored(client, db, world, fake_provider):
    acct = make_provider_account(db)
    db.commit()
    assert post_hook(client, acct, {}, sig="00" * 32).status_code == 401
    assert post_hook(client, acct, {}, sig="").status_code == 401
    assert post_hook(client, acct, {}, sig=webhook_sig("other-secret", acct.webhook_key)).status_code == 401
    assert db.scalars(select(ProviderEvent)).all() == []


def test_bad_json_is_400_and_oversize_is_413(client, db, world, fake_provider):
    acct = make_provider_account(db)
    db.commit()
    assert post_hook(client, acct, None, raw=b"{not json").status_code == 400
    assert post_hook(client, acct, None, raw=b"[1,2]").status_code == 400
    assert post_hook(client, acct, None, raw=b"").status_code == 400
    assert post_hook(client, acct, None, raw=b"\xff\xfe").status_code == 400
    big = json.dumps({"pad": "x" * (256 * 1024)}).encode()
    assert post_hook(client, acct, None, raw=big).status_code == 413
    assert db.scalars(select(ProviderEvent)).all() == []


def test_bad_provider_hmac_is_stored_then_401(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    item = intent.items[0]
    tampered = paid_payload(item, tamper=True)
    res = post_hook(client, acct, tampered)
    assert res.status_code == 401
    (event,) = db.scalars(select(ProviderEvent)).all()
    assert event.signature_valid is False
    assert event.processing_status == EventProcessing.IGNORED
    assert event.dedup_key.startswith("invalid:")
    assert db.scalars(select(ProviderCharge)).all() == []
    assert db.scalars(select(Payment)).all() == []
    # the same forged body again: still 401, still one row
    assert post_hook(client, acct, tampered).status_code == 401
    assert len(db.scalars(select(ProviderEvent)).all()) == 1

    # the forged event never steals the genuine event's dedup key
    res = post_hook(client, acct, paid_payload(item))
    assert res.status_code == 200 and res.json()["status"] == "ok"
    assert len(db.scalars(select(Payment)).all()) == 1


def test_account_without_hmac_key_fails_closed(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    bundle = account_secret(acct)
    bundle.pop("hmac_key")
    acct.secret_ciphertext = encrypt_secret(bundle)
    db.commit()
    assert post_hook(client, acct, paid_payload(intent.items[0])).status_code == 401


# --- processing -------------------------------------------------------------------------------

def test_paid_event_creates_exactly_one_provider_payment(client, db, world, fake_provider):
    intent, acct, cuotas = setup_intent(db, world, n_items=2)
    first_item = intent.items[0]
    payload = paid_payload(
        first_item,
        convert={"date": "2026-10-16", "amount": "1700000", "currency": "PESOS"},
        payment_id="PAY-1",
        authorization_code="AUTH-9",
    )
    res = post_hook(client, acct, payload)
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "processing_status": "processed"}

    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.source == PaymentSource.PROVIDER
    assert payment.evidence_level == EvidenceLevel.PROVIDER_CONFIRMED
    assert payment.currency == PaymentCurrency.CLP and payment.amount == Decimal("1700000")
    assert payment.amount_uf == first_item.amount_uf
    assert payment.uf_rate == (Decimal("1700000") / first_item.amount_uf).quantize(Decimal("0.0001"))
    assert payment.method == PaymentMode.CARD_DEBIT
    assert payment.paid_on.isoformat() == "2026-10-16"
    assert "AUTH-9" in payment.note
    inst = db.get(CollectionInstallment, first_item.installment_id)
    assert inst.status in (InstallmentStatus.PAID, InstallmentStatus.PAID_LATE)
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.state == 2 and charge.amount_clp == 1700000 and charge.intent_id == intent.id
    assert charge.broker_id == world.a.broker_id and charge.provider_installment_id == "FP-1"
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PARTIALLY_PAID
    (event,) = db.scalars(select(ProviderEvent)).all()
    assert event.signature_valid and event.processing_status == EventProcessing.PROCESSED
    assert event.broker_id == world.a.broker_id and event.attempts == 1
    # the inbox key is built from SIGNED fields only (installment.id, state)
    assert event.dedup_key == f"{first_item.external_installment_id}:2"
    note = db.scalar(select(Notification).where(Notification.kind == NotificationKind.PAYMENT_RECEIVED))
    assert note is not None and note.user_id == world.a.admin.id and note.origin_id == inst.id

    # replay: duplicate, no second payment
    again = post_hook(client, acct, payload)
    assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert len(db.scalars(select(Payment)).all()) == 1

    # the second cuota → the intent is paid
    post_hook(client, acct, paid_payload(intent.items[1], internal_id="FP-2"))
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PAID
    assert len(db.scalars(select(Payment)).all()) == 2


def test_uf_payment_without_convert(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    payload = paid_payload(intent.items[0])
    payload.pop("payment")
    payload["payment"] = {"id": "P", "authorizationCode": "A", "amount": payload["installment"]["amount"]}
    from app.services.payments.providers.base import sign_payload

    payload = sign_payload(TEST_HMAC_KEY, payload)
    assert post_hook(client, acct, payload).status_code == 200
    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.currency == PaymentCurrency.UF and payment.uf_rate is None
    assert payment.amount == payment.amount_uf == intent.items[0].amount_uf


def test_reversal_after_payment_is_a_chargeback(client, db, world, fake_provider):
    intent, acct, cuotas = setup_intent(db, world)
    item = intent.items[0]
    post_hook(client, acct, paid_payload(item))
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PAID

    res = post_hook(client, acct, paid_payload(item, state=12))
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.status == PaymentStatus.REVERSED and payment.reversal_reason == ReversalReason.CHARGEBACK
    inst = db.get(CollectionInstallment, item.installment_id)
    assert inst.status not in (InstallmentStatus.PAID, InstallmentStatus.PAID_LATE)
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PENDING
    (charge,) = db.scalars(select(ProviderCharge)).all()
    # the chargeback to-do lives on the CHARGE's origin, not on the clock-owned cuota origin
    note = db.scalar(
        select(Notification).where(
            Notification.origin_type == "provider_charge", Notification.origin_id == charge.id
        )
    )
    assert note.kind == NotificationKind.SENSITIVE_ACTION and note.params["event"] == "chargeback"


def test_late_pending_event_never_downgrades_a_paid_charge(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    item = intent.items[0]
    post_hook(client, acct, paid_payload(item))
    post_hook(client, acct, paid_payload(item, state=9))
    db.expire_all()
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.state == 2
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PAID


def test_in_process_moves_a_created_intent_to_pending(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    intent.status = IntentStatus.CREATED
    db.commit()
    post_hook(client, acct, paid_payload(intent.items[0], state=9))
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PENDING
    assert db.scalars(select(Payment)).all() == []


def test_rejected_spot_attempt_only_mirrors_the_charge(client, db, world, fake_provider):
    intent, acct, cuotas = setup_intent(db, world)
    res = post_hook(client, acct, paid_payload(intent.items[0], state=-99))
    assert res.status_code == 200
    db.expire_all()
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.state == -99
    assert db.get(CollectionInstallment, cuotas[0].id).direct_debit_rejected_at is None


def test_rejected_recurring_charge_flags_the_cuota(client, db, world, fake_provider):
    _, plan, acct = native_policy(db, world.a, world.insurer, payment_mode=PaymentMode.DIRECT_DEBIT)
    cuota = installments(plan)[0]
    mandate = PaymentMandate(
        broker_id=world.a.broker_id, plan_id=plan.id, provider_account_id=acct.id,
        channel=PaymentChannel.DIRECT_DEBIT, provider_mandate_id="DYN-1", status=MandateStatus.ACTIVE,
    )
    db.add(mandate)
    db.flush()
    db.add(
        ProviderCharge(
            broker_id=world.a.broker_id, provider_account_id=acct.id, mandate_id=mandate.id,
            installment_id=cuota.id, provider_installment_id="PAC-1", external_installment_id="rdl-pac-1",
        )
    )
    db.commit()
    payload = build(TEST_HMAC_KEY, internal_id="PAC-1", external_installment_id="rdl-pac-1", state=-99)
    res = post_hook(client, acct, payload)
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    inst = db.get(CollectionInstallment, cuota.id)
    assert inst.direct_debit_rejected_at is not None
    note = db.scalar(select(Notification).where(Notification.kind == NotificationKind.DIRECT_DEBIT_REJECTED))
    assert note is not None and note.origin_id == cuota.id and note.tray.value == "todo"


def test_unknown_installment_is_ignored_not_failed(client, db, world, fake_provider):
    acct = make_provider_account(db)
    db.commit()
    payload = build(TEST_HMAC_KEY, internal_id="X-1", external_installment_id="rdl-cuota-999-zz", state=2)
    res = post_hook(client, acct, payload)
    assert res.status_code == 200 and res.json()["processing_status"] == "ignored"
    (event,) = db.scalars(select(ProviderEvent)).all()
    assert event.processing_status == EventProcessing.IGNORED and event.broker_id is None
    assert db.scalars(select(ProviderCharge)).all() == []


def test_an_item_of_another_account_is_ignored(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    other = make_provider_account(db, label="otra", webhook_secret=TEST_WEBHOOK_SECRET)
    db.commit()
    res = post_hook(client, other, paid_payload(intent.items[0]))
    assert res.status_code == 200 and res.json()["processing_status"] == "ignored"
    assert db.scalars(select(Payment)).all() == []


def test_processing_failure_answers_200_and_is_retried(client, db, world, fake_provider, monkeypatch):
    from app.services.payments import webhooks as service

    intent, acct, _ = setup_intent(db, world)
    payload = paid_payload(intent.items[0])

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("bug")

    monkeypatch.setattr(service, "apply_charge_update", boom)
    res = post_hook(client, acct, payload)
    assert res.status_code == 200 and res.json()["processing_status"] == "failed"
    db.expire_all()
    (event,) = db.scalars(select(ProviderEvent)).all()
    assert event.processing_status == EventProcessing.FAILED and "RuntimeError" in event.error
    assert db.scalars(select(Payment)).all() == []

    monkeypatch.undo()
    res = post_hook(client, acct, payload)
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    event = db.get(ProviderEvent, event.id)
    assert event.processing_status == EventProcessing.PROCESSED and event.attempts == 2
    assert len(db.scalars(select(Payment)).all()) == 1


def test_previous_sig_is_accepted_for_30_days_after_rotation(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    old_sig = webhook_sig(TEST_WEBHOOK_SECRET, acct.webhook_key)
    rotate_webhook_secret(acct, now=datetime.now(timezone.utc))
    db.commit()
    assert post_hook(client, acct, paid_payload(intent.items[0]), sig=old_sig).status_code == 200

    bundle = account_secret(acct)
    bundle["previous_webhook_secret_until"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    acct.secret_ciphertext = encrypt_secret(bundle)
    db.commit()
    assert post_hook(client, acct, {}, sig=old_sig).status_code == 401


def test_paying_one_link_closes_the_other_open_link(client, db, world, fake_provider):
    intent, acct, cuotas = setup_intent(db, world)
    # A second open link on the same cuota (e.g. created before supersede existed).
    other = PaymentIntent(
        broker_id=world.a.broker_id, external_id="rdl-intent-other", provider_account_id=acct.id,
        provider_mandate_id="fake-mandate-other", checkout_url="https://x", status=IntentStatus.PENDING,
    )
    other.items.append(
        PaymentIntentItem(
            broker_id=world.a.broker_id, installment_id=cuotas[0].id, amount_uf=cuotas[0].gross_amount_uf,
            external_installment_id="rdl-cuota-other",
        )
    )
    db.add(other)
    db.commit()
    post_hook(client, acct, paid_payload(intent.items[0]))
    calls = fake_provider.calls_to("set_installment_state")
    assert calls == [{"account_id": acct.id, "external_ids": ["rdl-cuota-other"], "state": "paid"}]
    db.expire_all()
    assert db.get(PaymentIntent, other.id).status == IntentStatus.CANCELLED
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PAID


ACTIVATION_SIGNED_STATE = [
    "businessPartner.businessPartnerId", "customer.userId", "product.id", "product.state.id",
]


def test_activation_event_for_a_mandate(client, db, world, fake_provider):
    """With the state covered by the account's signature, an activation moves the mandate."""
    _, plan, acct = native_policy(db, world.a, world.insurer)
    acct.secret_ciphertext = encrypt_secret(
        {**account_secret(acct), "signature_fields": {"activation": ACTIVATION_SIGNED_STATE}}
    )
    mandate = PaymentMandate(
        broker_id=world.a.broker_id, plan_id=plan.id, provider_account_id=acct.id,
        provider_mandate_id="DYN-9", status=MandateStatus.PENDING,
    )
    db.add(mandate)
    db.commit()
    from app.services.payments.providers.base import sign_payload

    payload = sign_payload(
        TEST_HMAC_KEY,
        {
            "businessPartner": {"businessPartnerId": "radal"},
            "customer": {"userId": "c"},
            "product": {"type": "MANDATE", "internalId": "DYN-9", "id": "DYN-9", "state": {"id": 11}},
        },
        event_type="activation",
        fields=ACTIVATION_SIGNED_STATE,
    )
    res = post_hook(client, acct, payload, kind="activation")
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    assert db.get(PaymentMandate, mandate.id).status == MandateStatus.BANK_CONFIRMATION


def test_works_with_the_real_forpay_provider_and_no_network(client, db, world):
    """No ``fake_provider``: the registry builds ``ForpayProvider``; verify/parse are local."""
    from app.services.payments import providers as registry
    from app.services.payments.providers.forpay import ForpayProvider

    _, plan, acct = native_policy(db, world.a, world.insurer)
    cuota = installments(plan)[0]
    intent = PaymentIntent(
        broker_id=world.a.broker_id, external_id="rdl-intent-real", provider_account_id=acct.id,
        provider_mandate_id="M-real", checkout_url="https://x", status=IntentStatus.PENDING,
    )
    intent.items.append(
        PaymentIntentItem(
            broker_id=world.a.broker_id, installment_id=cuota.id, amount_uf=cuota.gross_amount_uf,
            external_installment_id="rdl-cuota-real",
        )
    )
    db.add(intent)
    db.commit()
    registry._INSTANCES.pop("forpay", None)
    assert isinstance(registry.get_provider("forpay"), ForpayProvider)

    payload = build(TEST_HMAC_KEY, internal_id="R-1", external_installment_id="rdl-cuota-real", state=2,
                    amount=str(cuota.gross_amount_uf))
    res = post_hook(client, acct, payload)
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    assert post_hook(client, acct, build(TEST_HMAC_KEY, internal_id="R-1", external_installment_id="rdl-cuota-real",
                                          state=2, tamper=True)).status_code == 401
    db.expire_all()
    assert len(db.scalars(select(Payment)).all()) == 1


def test_x_radal_token_is_not_needed_and_auth_headers_are_ignored(client, db, world, fake_provider, headers_b):
    intent, acct, _ = setup_intent(db, world)
    params = {"account": acct.webhook_key, "sig": webhook_sig(TEST_WEBHOOK_SECRET, acct.webhook_key), "kind": "payment"}
    res = client.post(f"{API}/webhooks/forpay", params=params, json=paid_payload(intent.items[0]), headers=headers_b)
    assert res.status_code == 200  # a broker JWT neither helps nor hurts: the URL is the credential


def test_now_is_naive_safe(db, world, fake_provider):
    from app.services.payments.intents import santiago_today

    assert santiago_today(datetime(2026, 10, 17, 2, 0)).isoformat() == "2026-10-16"
    assert santiago_today(NOW).isoformat() == "2026-10-16"


# --- one cuota = one charge = at most one provider payment (review fixes) ---------------------

def _active_payments(db):
    return db.scalars(select(Payment).where(Payment.status == PaymentStatus.ACTIVE)).all()


def test_receivable_without_internal_id_reuses_the_webhook_charge(client, db, world, fake_provider):
    from app.services.payments.sync import sync_account

    intent, acct, _ = setup_intent(db, world, n_items=2)  # 2 items: the intent stays open for the sync
    item = intent.items[0]
    assert post_hook(client, acct, paid_payload(item, internal_id="FP-1")).status_code == 200
    # Forpay's receivable for the same cuota, without internalId (parse_receivable falls back to our id)
    fake_provider.add_receivable(
        provider_installment_id=item.external_installment_id,
        external_installment_id=item.external_installment_id,
        state=2, amount=item.amount_uf, currency="UF", payment_id="pay-1", authorization_code="AUTH01",
    )
    stats = sync_account(db, db.get(type(acct), acct.id), now=NOW)
    assert stats["payments_created"] == 0
    db.expire_all()
    assert len(db.scalars(select(ProviderCharge)).all()) == 1
    assert len(_active_payments(db)) == 1


def test_receivable_first_then_webhook_adopts_forpays_internal_id(client, db, world, fake_provider):
    from app.services.payments.sync import sync_account

    intent, acct, _ = setup_intent(db, world)
    item = intent.items[0]
    fake_provider.add_receivable(
        provider_installment_id=item.external_installment_id,
        external_installment_id=item.external_installment_id,
        state=2, amount=item.amount_uf, currency="UF", payment_id="pay-1",
    )
    assert sync_account(db, acct, now=NOW)["payments_created"] == 1
    assert post_hook(client, acct, paid_payload(item, internal_id="FP-7")).json()["processing_status"] == "processed"
    db.expire_all()
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.provider_installment_id == "FP-7"
    assert len(_active_payments(db)) == 1


def test_replay_with_a_changed_internal_id_creates_no_second_payment(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    item = intent.items[0]
    genuine = paid_payload(item, internal_id="FP-1")
    assert post_hook(client, acct, genuine).status_code == 200
    replay = {**genuine, "installment": {**genuine["installment"], "internalId": "FP-EVIL"}}
    res = post_hook(client, acct, replay)  # internalId is unsigned: the HMAC still verifies
    # ... and the inbox key is signed-only, so the mutated replay IS the same event
    assert res.status_code == 200 and res.json() == {"status": "duplicate"}
    assert len(db.scalars(select(ProviderEvent)).all()) == 1
    db.expire_all()
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.provider_installment_id == "FP-1"
    assert len(_active_payments(db)) == 1


def test_an_internal_id_pointing_at_another_cuota_is_ignored(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world, n_items=2)
    one, two = intent.items
    post_hook(client, acct, paid_payload(one, internal_id="FP-1", state=9))
    # a genuine body for cuota two, its unsigned internalId swapped for cuota one's
    body = paid_payload(two, internal_id="FP-2")
    forged = {**body, "installment": {**body["installment"], "internalId": "FP-1"}}
    res = post_hook(client, acct, forged)
    assert res.status_code == 200 and res.json()["processing_status"] == "ignored"
    db.expire_all()
    assert db.scalars(select(Payment)).all() == []
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.state == 9
    # The inbox key is signed-only, so the forged variant holds the genuine
    # event's key; because it was IGNORED, the genuine body is still processed.
    res = post_hook(client, acct, body)
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    assert len(_active_payments(db)) == 1
    assert len(db.scalars(select(ProviderEvent)).all()) == 2


def _pac_charge(db, world):
    _, plan, acct = native_policy(db, world.a, world.insurer, payment_mode=PaymentMode.DIRECT_DEBIT)
    cuota = installments(plan)[0]
    mandate = PaymentMandate(
        broker_id=world.a.broker_id, plan_id=plan.id, provider_account_id=acct.id,
        channel=PaymentChannel.DIRECT_DEBIT, provider_mandate_id="DYN-2", status=MandateStatus.ACTIVE,
    )
    db.add(mandate)
    db.flush()
    db.add(
        ProviderCharge(
            broker_id=world.a.broker_id, provider_account_id=acct.id, mandate_id=mandate.id,
            installment_id=cuota.id, provider_installment_id="PAC-2", external_installment_id="rdl-pac-2",
        )
    )
    db.commit()
    return acct, cuota


def test_late_rejection_after_payment_is_stale(client, db, world, fake_provider):
    acct, cuota = _pac_charge(db, world)
    amount = str(cuota.gross_amount_uf)
    post_hook(client, acct, build(TEST_HMAC_KEY, internal_id="PAC-2", external_installment_id="rdl-pac-2",
                                  state=2, amount=amount))
    res = post_hook(client, acct, build(TEST_HMAC_KEY, internal_id="PAC-2", external_installment_id="rdl-pac-2",
                                        state=-99, amount=amount))
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.status == PaymentStatus.ACTIVE
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.state == 2
    inst = db.get(CollectionInstallment, cuota.id)
    assert inst.direct_debit_rejected_at is None
    assert inst.status in (InstallmentStatus.PAID, InstallmentStatus.PAID_LATE)
    assert db.scalar(select(Notification).where(Notification.kind == NotificationKind.DIRECT_DEBIT_REJECTED)) is None


def test_rejection_never_reverses_a_payment(client, db, world, fake_provider):
    """−99 flags + notifies only; reversal is state 12 (03 §5)."""
    acct, cuota = _pac_charge(db, world)
    res = post_hook(client, acct, build(TEST_HMAC_KEY, internal_id="PAC-2", external_installment_id="rdl-pac-2",
                                        state=-99))
    assert res.json()["processing_status"] == "processed"
    db.expire_all()
    assert db.get(CollectionInstallment, cuota.id).direct_debit_rejected_at is not None
    assert db.scalars(select(Payment).where(Payment.status == PaymentStatus.REVERSED)).all() == []


def test_provider_echo_of_paid_outside_is_not_a_second_payment(client, db, world, fake_provider):
    from app.services.payments.checkout import mark_paid_outside
    from app.services.payments.providers.base import sign_payload
    from tests.fakes_v12 import make_payment

    intent, acct, cuotas = setup_intent(db, world)
    item = intent.items[0]
    make_payment(db, cuotas[0], paid_on=NOW.date())
    assert mark_paid_outside(db, cuotas[0], now=NOW) == 1
    db.commit()
    db.expire_all()
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.state == 2 and charge.raw.get("radal_marked_paid")

    echo = paid_payload(item, internal_id="FP-ECHO")
    echo.pop("payment")
    echo = sign_payload(TEST_HMAC_KEY, echo)
    res = post_hook(client, acct, echo)
    assert res.status_code == 200 and res.json()["processing_status"] == "processed"
    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()  # only the manual one
    assert payment.source != PaymentSource.PROVIDER
    (charge,) = db.scalars(select(ProviderCharge)).all()
    assert charge.provider_installment_id == "FP-ECHO" and charge.raw.get("radal_marked_paid")
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.CANCELLED

    # A sync receivable of the same echo (no payment block) books nothing either.
    from app.services.payments.webhooks import apply_charge_update
    from app.services.payments.providers.base import ProviderEventIn

    apply_charge_update(
        db, acct,
        ProviderEventIn(event_type="payment", dedup_key="x", provider_installment_id="FP-ECHO",
                        external_installment_id=item.external_installment_id, state=2),
        now=NOW,
    )
    db.commit()
    assert len(db.scalars(select(Payment)).all()) == 1
