"""v12 final-fixer regressions — one block per confirmed review finding.

1. portal_url / payment_url: http(s) only, on write (422) and on every read
   (the public share page never serves a ``javascript:`` href).
2. activation webhooks act only on SIGNED fields; inbox keys are signed-only.
3. a colleague's personal mailbox cannot be borrowed by POST /inquiries (404).
4. policies never attach another broker's private external insurer (404).
5. USD cuotas are never charged natively as UF.
6. a partial payment outside the provider supersedes the open link.
7. the extraction Σ check counts only ISSUED endorsement deltas.
8. chargeback / debit-rejected to-dos survive the collection clock.

Hermetic: ``FakePaymentProvider`` / ``FakeTransport``; no network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.core.urls import safe_http_url
from app.models.base_class import utcnow
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.endorsement import Endorsement
from app.models.enums import (
    EndorsementKind,
    EndorsementStatus,
    EventProcessing,
    IntentStatus,
    MandateStatus,
    NotificationKind,
    NotificationStatus,
    PaymentCurrency,
    PaymentMode,
    PolicyCurrency,
    ShareAudience,
    ShareEntity,
)
from app.models.insurer import Insurer
from app.models.notification import Notification
from app.models.payment import Payment, PaymentIntent, PaymentMandate
from app.models.provider import ProviderCharge, ProviderEvent
from app.services import fx
from app.services.collection import ledger
from app.services.collection.clock import run_broker_clock
from app.services.collection.resolver import resolve_collection_mode
from app.services.payments.checkout import CheckoutError
from app.services.payments.insurers import clean_instructions, merged_instructions
from app.services.payments.providers.base import account_secret, sign_payload
from app.services.payments.providers.fake import FakePaymentProvider
from app.services.share_links import create_share_link
from tests.conftest import API, auth_headers, make_policy
from tests.fakes_v12 import (
    TEST_HMAC_KEY,
    make_broker_override,
    make_insurer_profile,
    make_policy_with_plan,
)
from tests.test_v12_payments import NOW, create_intent, installments, native_policy
from tests.test_v12_webhooks import _pac_charge, paid_payload, post_hook, setup_intent

build = FakePaymentProvider.build_webhook_payload
EVIL = "javascript:alert(document.domain)"


# =============================================================================
# 1. portal_url scheme allowlist
# =============================================================================

@pytest.mark.parametrize(
    "value",
    [
        EVIL,
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example/x",
        "https:evil.example",
        "ftp://files.example/x",
        "nodomain",
    ],
)
def test_safe_http_url_rejects_non_http(value):
    assert safe_http_url(value) is None


def test_safe_http_url_keeps_http_and_upgrades_bare_domains():
    assert safe_http_url("https://pagos.hdi.cl/x?y=1") == "https://pagos.hdi.cl/x?y=1"
    assert safe_http_url(" http://pagos.hdi.cl ") == "http://pagos.hdi.cl"
    assert safe_http_url("www.hdi.cl/pagos") == "https://www.hdi.cl/pagos"
    assert safe_http_url(None) is None and safe_http_url("") is None


def test_override_with_javascript_portal_url_is_422(client, db, world, headers_a):
    url = f"{API}/payments/insurers/{world.insurer.id}/override"
    res = client.put(url, json={"payment_instructions": {"portal_url": EVIL}}, headers=headers_a)
    assert res.status_code == 422
    assert "https://" in json.dumps(res.json(), ensure_ascii=False)
    ok = client.put(url, json={"payment_instructions": {"portal_url": "https://pagos.hdi.cl"}}, headers=headers_a)
    assert ok.status_code == 200, ok.text


def test_insurer_payment_url_is_validated_on_create_and_update(client, db, world, headers_a):
    body = {"rut": "76086428-5", "cmf_code": "CMF-EXT-77", "legal_name": "Externa Alfa SpA", "payment_url": EVIL}
    assert client.post(f"{API}/insurers", json=body, headers=headers_a).status_code == 422
    created = client.post(f"{API}/insurers", json={**body, "payment_url": "https://pagos.externa.cl"}, headers=headers_a)
    assert created.status_code in (200, 201), created.text
    insurer_id = created.json()["id"]
    res = client.patch(f"{API}/insurers/{insurer_id}", json={"payment_url": "data:text/html,x"}, headers=headers_a)
    assert res.status_code == 422


def test_legacy_stored_javascript_urls_never_reach_the_public_share_page(client, db, world):
    """Rows written before the validator existed are dropped at the read choke point."""
    policy, _plan = make_policy_with_plan(db, world.a, world.insurer, n_installments=2, policy_number="POL-XSS-1")
    # Bypass the API (an old row): profile, override and the legacy column all hostile.
    make_insurer_profile(db, world.insurer, payment_instructions={"portal_url": EVIL, "bank_name": "Banco"})
    make_broker_override(db, world.a.broker_id, world.insurer, payment_instructions={"portal_url": "vbscript:x"})
    world.insurer.payment_url = "javascript:alert(2)"
    db.commit()
    assert "portal_url" not in merged_instructions(db, broker_id=world.a.broker_id, insurer_id=world.insurer.id)
    assert clean_instructions({"portal_url": EVIL}) == {}

    link = create_share_link(
        db, broker_id=world.a.broker_id, user=world.a.admin, entity_type=ShareEntity.POLICY,
        entity_id=policy.id, audience=ShareAudience.INSURED,
    )
    db.commit()
    res = client.get(f"{API}/public/share/{link.token}")
    assert res.status_code == 200, res.text
    assert "javascript:" not in res.text and "vbscript:" not in res.text
    instructions = res.json()["policy"]["payment_instructions"]
    assert instructions["portal_url"] is None and instructions["bank_name"] == "Banco"

    # A safe legacy URL is still the fallback.
    world.insurer.payment_url = "https://legacy.hdi.cl/pagar"
    db.commit()
    res = client.get(f"{API}/public/share/{link.token}")
    assert res.json()["policy"]["payment_instructions"]["portal_url"] == "https://legacy.hdi.cl/pagar"


def test_insurer_read_never_returns_a_non_http_payment_url(client, db, world, headers_a):
    world.insurer.payment_url = EVIL
    db.commit()
    res = client.get(f"{API}/insurers/{world.insurer.id}", headers=headers_a)
    assert res.status_code == 200 and res.json()["payment_url"] is None


# =============================================================================
# 2. activation / dedup act only on signed fields
# =============================================================================

def _mandate(db, world, *, mandate_id="DYN-9", status=MandateStatus.ACTIVE):
    _, plan, acct = native_policy(db, world.a, world.insurer)
    mandate = PaymentMandate(
        broker_id=world.a.broker_id, plan_id=plan.id, provider_account_id=acct.id,
        provider_mandate_id=mandate_id, status=status,
    )
    db.add(mandate)
    db.commit()
    return acct, mandate


def _activation(product: dict, *, fields=None) -> dict:
    return sign_payload(
        TEST_HMAC_KEY,
        {"businessPartner": {"businessPartnerId": "radal"}, "customer": {"userId": "c"}, "product": product},
        event_type="activation",
        fields=fields,
    )


def test_mutated_unsigned_activation_state_never_revokes_a_mandate(client, db, world, fake_provider):
    acct, mandate = _mandate(db, world)
    signed = _activation({"id": "DYN-9", "internalId": "DYN-9", "state": {"id": "ACTIVO"}})
    mutated = {**signed, "product": {**signed["product"], "state": {"id": 6}}}  # not re-signed
    res = post_hook(client, acct, mutated, kind="activation")
    assert res.status_code == 200 and res.json()["processing_status"] == "ignored"
    db.expire_all()
    assert db.get(PaymentMandate, mandate.id).status == MandateStatus.ACTIVE
    (event,) = db.scalars(select(ProviderEvent)).all()
    assert "no viene firmado" in event.error
    # The genuine body (state also unsigned by default) is a hint, never a write.
    res = post_hook(client, acct, signed, kind="activation")
    assert res.status_code == 200
    db.expire_all()
    assert db.get(PaymentMandate, mandate.id).status == MandateStatus.ACTIVE
    assert db.get(PaymentMandate, mandate.id).revoked_at is None


def test_activation_cannot_be_retargeted_through_the_unsigned_internal_id(client, db, world, fake_provider):
    acct, victim = _mandate(db, world, mandate_id="DYN-VICTIM")
    acct.secret_ciphertext = encrypt_secret(
        {**account_secret(acct), "signature_fields": {"activation": [
            "businessPartner.businessPartnerId", "customer.userId", "product.id", "product.state.id",
        ]}}
    )
    db.commit()
    fields = ["businessPartner.businessPartnerId", "customer.userId", "product.id", "product.state.id"]
    signed = _activation({"id": "DYN-OTHER", "internalId": "DYN-OTHER", "state": {"id": 6}}, fields=fields)
    retarget = {**signed, "product": {**signed["product"], "internalId": "DYN-VICTIM"}}
    res = post_hook(client, acct, retarget, kind="activation")
    assert res.status_code == 200 and res.json()["processing_status"] == "ignored"
    db.expire_all()
    assert db.get(PaymentMandate, victim.id).status == MandateStatus.ACTIVE


def test_activation_dedup_key_is_built_from_signed_fields(client, db, world, fake_provider):
    acct, _mandate_row = _mandate(db, world)
    signed = _activation({"id": "DYN-9", "internalId": "DYN-9", "state": {"id": "ACTIVO"}})
    post_hook(client, acct, signed, kind="activation")
    noise = {**signed, "product": {**signed["product"], "note": "x"}}
    post_hook(client, acct, noise, kind="activation")
    again = post_hook(client, acct, noise, kind="activation")
    assert again.json() == {"status": "duplicate"}
    (event,) = db.scalars(select(ProviderEvent)).all()  # one inbox row, whatever unsigned noise
    assert event.dedup_key.startswith("activation:") and len(event.dedup_key) > len("activation:") + 32


def test_payment_replay_with_mutated_unsigned_currency_is_a_duplicate(client, db, world, fake_provider):
    intent, acct, _ = setup_intent(db, world)
    item = intent.items[0]
    genuine = paid_payload(item)
    assert post_hook(client, acct, genuine).json()["processing_status"] == "processed"
    replay = {**genuine, "installment": {**genuine["installment"], "currency": "CLP", "internalId": "FP-X"}}
    assert post_hook(client, acct, replay).json() == {"status": "duplicate"}
    assert len(db.scalars(select(ProviderEvent)).all()) == 1
    assert len(db.scalars(select(Payment)).all()) == 1


# =============================================================================
# 3. a colleague's personal mailbox
# =============================================================================

def test_inquiry_cannot_send_through_a_colleagues_personal_mailbox(
    client, db, world, headers_a, headers_a_exec, fake_transport, monkeypatch
):
    from tests.test_v12_inquiries import _create, _setup
    from tests.test_v12_mail import mail_on, make_mail_account

    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    boss_box = make_mail_account(db, world.a, address="jefa@alfa.cl", owner_user_id=world.a.admin.id)
    db.commit()
    # The executive cannot see the box ...
    assert client.patch(
        f"{API}/mail-accounts/{boss_box.id}", json={"display_name": "x"}, headers=headers_a_exec
    ).status_code == 404
    # ... nor send through it (same 404), and nothing leaves.
    res = _create(client, headers_a_exec, policy, plan, send_via="account", mail_account_id=boss_box.id)
    assert res.status_code == 404, res.text
    assert fake_transport.sent == []

    # Its owner (an admin) still can; resend by the executive through it is 404 too.
    ok = _create(client, headers_a, policy, plan, send_via="account", mail_account_id=boss_box.id)
    assert ok.status_code == 201, ok.text
    sent = len(fake_transport.sent)
    resend = client.post(
        f"{API}/inquiries/{ok.json()['id']}/resend",
        json={"send_via": "account", "mail_account_id": boss_box.id},
        headers=headers_a_exec,
    )
    assert resend.status_code == 404
    assert len(fake_transport.sent) == sent

    # A shared box (no owner) stays usable by the executive.
    shared = make_mail_account(db, world.a, address="cobranza2@alfa.cl")
    db.commit()
    # (Another cuota: one OPEN inquiry per cuota since the review-v12 fixes.)
    assert _create(
        client, headers_a_exec, policy, plan, send_via="account", mail_account_id=shared.id,
        installment_ids=[plan.installments[1].id],
    ).status_code == 201


# =============================================================================
# 4. another broker's private external insurer
# =============================================================================

def _private_insurer_of_a(db, world) -> Insurer:
    row = Insurer(
        legal_name="Externa Privada de Alfa SpA", rut="76543210-3", cmf_code="CMF-PRIV-A",
        is_native=False, created_by_broker_id=world.a.broker_id,
    )
    db.add(row)
    db.commit()
    return row


def test_policies_never_attach_another_brokers_private_insurer(client, db, world, catalog, headers_a, headers_b):
    from tests.test_v12_policies import _manual_body, _sub

    private = _private_insurer_of_a(db, world)
    assert client.get(f"{API}/insurers/{private.id}", headers=headers_b).status_code == 404
    sub = _sub(db, "PRO-02")
    body_b = {**_manual_body(world, sub), "client_id": world.b.client.id, "insurer_id": private.id}
    res = client.post(f"{API}/policies", json=body_b, headers=headers_b)
    assert res.status_code == 404, res.text
    assert "Externa Privada" not in res.text

    # PATCH on B's own policy cannot point at it either.
    own = client.post(
        f"{API}/policies", json={**body_b, "insurer_id": world.insurer.id, "policy_number": "MAN-B-1"},
        headers=headers_b,
    )
    assert own.status_code == 201, own.text
    patched = client.patch(
        f"{API}/policies/{own.json()['id']}",
        json={"insurer_id": private.id, "correction_reason": "otra compañía"},
        headers=headers_b,
    )
    assert patched.status_code == 404
    assert client.get(f"{API}/policies/{own.json()['id']}", headers=headers_b).json()["insurer_name"] != (
        "Externa Privada de Alfa SpA"
    )

    # Its owner may use it.
    body_a = {**_manual_body(world, sub), "insurer_id": private.id, "policy_number": "MAN-A-PRIV"}
    assert client.post(f"{API}/policies", json=body_a, headers=headers_a).status_code == 201


def test_agent_manual_policy_validation_hides_another_brokers_private_insurer(db, world, catalog):
    from fastapi import HTTPException

    from app.services.agent_tools import CreatePolicyManualArgs, _validate_create_policy_manual
    from tests.test_v12_policies import _sub

    private = _private_insurer_of_a(db, world)
    sub = _sub(db, "PRO-02")
    args = CreatePolicyManualArgs(
        client_id=world.b.client.id, insurer_id=private.id, insurance_subtype_id=sub.id,
        start_date=date(2026, 3, 1), end_date=date(2027, 3, 1), policy_number="AG-B-1",
        taxable_premium_uf=Decimal("100"), exempt_premium_uf=Decimal("20"),
    )
    with pytest.raises(HTTPException) as exc:
        _validate_create_policy_manual(db, broker_id=world.b.broker_id, user=world.b.admin, args=args)
    assert exc.value.status_code == 404


# =============================================================================
# 5. USD cuotas are never charged natively
# =============================================================================

def _usd(plan):
    for inst in plan.installments:
        inst.currency = PolicyCurrency.USD


def test_usd_cuota_resolves_assisted_and_checkout_refuses(client, db, world, headers_a, fake_provider):
    policy, plan, _acct = native_policy(db, world.a, world.insurer)
    _usd(plan)
    db.commit()
    cuota = installments(plan)[0]
    mode = resolve_collection_mode(db, cuota, broker_id=world.a.broker_id)
    assert mode.level == "assisted" and "USD" in mode.reason
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [cuota.id]}, headers=headers_a)
    assert res.status_code == 409
    assert "dólares" in res.json()["detail"]
    assert fake_provider.calls_to("create_spot_checkout") == []
    with pytest.raises(CheckoutError) as exc:
        create_intent(db, world.a, [cuota.id])
    assert exc.value.status_code == 409


def test_provider_payment_on_a_usd_cuota_is_converted_not_booked_as_uf(client, db, world, fake_provider):
    intent, acct, cuotas = setup_intent(db, world)  # minted while the cuota was UF
    item = intent.items[0]
    cuotas[0].currency = PolicyCurrency.USD
    db.commit()

    # No dollar rate for the payment day: nothing is booked, the event is retryable, a to-do is raised.
    res = post_hook(client, acct, paid_payload(item))
    assert res.status_code == 200 and res.json()["processing_status"] == "failed"
    db.expire_all()
    assert db.scalars(select(Payment)).all() == []
    (charge,) = db.scalars(select(ProviderCharge)).all()
    todo = db.scalar(select(Notification).where(
        Notification.origin_type == "provider_charge", Notification.origin_id == charge.id
    ))
    assert todo.kind == NotificationKind.SENSITIVE_ACTION and todo.params["event"] == "provider_payment_unconverted"

    # With the rate loaded, the redelivery books the CLP paid, normalised to USD.
    fx.upsert_rates(db, [(date(2026, 10, 16), Decimal("38000"), Decimal("950"), "test")])
    db.commit()
    res = post_hook(client, acct, paid_payload(item))
    assert res.json()["processing_status"] == "processed"
    db.expire_all()
    (payment,) = db.scalars(select(Payment)).all()
    assert payment.currency == PaymentCurrency.CLP and payment.amount == Decimal("60000")
    assert payment.amount_uf == (Decimal("60000") / Decimal("950")).quantize(Decimal("0.0001"))


# =============================================================================
# 6. a partial payment outside the provider supersedes the open link
# =============================================================================

def test_partial_manual_payment_supersedes_the_open_link(client, db, world, headers_a, fake_provider):
    intent, _acct, cuotas = setup_intent(db, world)
    cuota = cuotas[0]
    item = intent.items[0]
    full = Decimal(str(item.amount_uf))
    half = (full / 2).quantize(Decimal("0.0001"))
    ledger.register_payment(
        db, cuota, amount=half, currency=PaymentCurrency.UF, paid_on=NOW.date(), today=NOW.date(), now=NOW
    )
    db.commit()
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.CANCELLED
    annul = fake_provider.calls_to("set_installment_state")
    assert annul and annul[-1]["state"] == "annulled" and item.external_installment_id in annul[-1]["external_ids"]

    # The next link charges the CURRENT balance, never the frozen full amount.
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [cuota.id]}, headers=headers_a)
    assert res.status_code == 201, res.text
    fresh = db.get(PaymentIntent, res.json()["intent"]["id"])
    assert fresh.id != intent.id
    assert Decimal(str(fresh.items[0].amount_uf)) == full - half


def test_link_at_the_current_balance_is_left_alone(db, world, fake_provider):
    """A write that does not change the balance (a promise) never supersedes the link."""
    intent, _acct, cuotas = setup_intent(db, world)
    calls = len(fake_provider.calls_to("set_installment_state"))
    ledger.after_installment_change(db, cuotas[0], today=NOW.date(), now=NOW)
    db.commit()
    db.expire_all()
    assert db.get(PaymentIntent, intent.id).status == IntentStatus.PENDING
    assert len(fake_provider.calls_to("set_installment_state")) == calls


# =============================================================================
# 7. the extraction Σ check counts only ISSUED endorsement deltas
# =============================================================================

def _ctx(db, world, amounts):
    rows = [
        SimpleNamespace(
            number=i + 1, coupon_number=None, due_date=date(2026, i + 1, 5),
            gross_amount_uf=Decimal(a), net_premium_uf=None, commission_uf=None,
        )
        for i, a in enumerate(amounts)
    ]
    return SimpleNamespace(
        db=db, broker_id=world.a.broker_id, payload=SimpleNamespace(installments=rows, payment_mode="PAC"),
        warnings=[], document=world.a.document,
    )


@pytest.mark.parametrize(
    "status",
    [EndorsementStatus.DRAFT, EndorsementStatus.PROPOSED, EndorsementStatus.APPLIED,
     EndorsementStatus.REJECTED, EndorsementStatus.CANCELLED],
)
def test_extraction_sum_ignores_non_issued_endorsements(db, world, status):
    from app.models.case_file import CaseFile
    from app.services.extraction_commit import _commit_policy_installments
    from tests.conftest import make_case_file

    policy = make_policy(db, world.a, world.insurer, policy_number=f"UP-E-{status.value}")
    case = make_case_file(db, world.a)
    db.add(Endorsement(
        broker_id=world.a.broker_id, policy_id=policy.id, sequence_no=1,
        kind=EndorsementKind.ADDITIONAL_INSURED, status=status, total_premium_delta_uf=Decimal("10"),
    ))
    db.flush()
    ctx = _ctx(db, world, ["69.5", "69.5"])
    assert _commit_policy_installments(ctx, policy, db.get(CaseFile, case.id)) is True, ctx.warnings
    assert db.scalars(select(CollectionPlan).where(CollectionPlan.policy_id == policy.id)).one()


def test_extraction_sum_counts_an_issued_endorsement(db, world):
    from app.services.extraction_commit import _commit_policy_installments
    from tests.conftest import make_case_file

    policy = make_policy(db, world.a, world.insurer, policy_number="UP-E-ISSUED")
    case = make_case_file(db, world.a)
    db.add(Endorsement(
        broker_id=world.a.broker_id, policy_id=policy.id, sequence_no=1,
        kind=EndorsementKind.ADDITIONAL_INSURED, status=EndorsementStatus.ISSUED,
        total_premium_delta_uf=Decimal("10"),
    ))
    db.flush()
    unbalanced = _ctx(db, world, ["69.5", "69.5"])
    assert _commit_policy_installments(unbalanced, policy, case) is False
    assert any("esperado 149.0000 UF" in w for w in unbalanced.warnings)
    balanced = _ctx(db, world, ["74.5", "74.5"])
    assert _commit_policy_installments(balanced, policy, case) is True


# =============================================================================
# 8. provider to-dos survive the collection clock
# =============================================================================

def test_chargeback_todo_survives_the_clock(client, db, world, fake_provider):
    intent, acct, cuotas = setup_intent(db, world)
    item = intent.items[0]
    post_hook(client, acct, paid_payload(item))
    post_hook(client, acct, paid_payload(item, state=12))
    db.expire_all()
    cuota = db.get(CollectionInstallment, cuotas[0].id)
    (charge,) = db.scalars(select(ProviderCharge)).all()

    later = datetime.combine(cuota.due_date + timedelta(days=4), datetime.min.time(), tzinfo=timezone.utc)
    later = later.replace(hour=15)
    run_broker_clock(db, world.a.broker_id, now=later)
    db.commit()
    db.expire_all()

    chargeback = db.scalars(select(Notification).where(
        Notification.origin_type == "provider_charge", Notification.origin_id == charge.id,
        Notification.status != NotificationStatus.CLOSED,
    )).all()
    assert chargeback and all(n.kind == NotificationKind.SENSITIVE_ACTION for n in chargeback)
    assert all(n.params["event"] == "chargeback" for n in chargeback)
    overdue = db.scalars(select(Notification).where(
        Notification.origin_type == "installment", Notification.origin_id == cuota.id,
        Notification.status != NotificationStatus.CLOSED,
    )).all()
    assert overdue and all(n.kind == NotificationKind.INSTALLMENT_OVERDUE for n in overdue)


def test_debit_rejection_todo_is_not_relit_by_the_clock(client, db, world, fake_provider):
    acct, cuota = _pac_charge(db, world)
    res = post_hook(client, acct, build(TEST_HMAC_KEY, internal_id="PAC-2", external_installment_id="rdl-pac-2",
                                        state=-99))
    assert res.json()["processing_status"] == "processed"
    db.expire_all()
    rows = db.scalars(select(Notification).where(
        Notification.kind == NotificationKind.DIRECT_DEBIT_REJECTED,
        Notification.status != NotificationStatus.CLOSED,
    )).all()
    assert rows
    rejected_on = db.get(CollectionInstallment, cuota.id).direct_debit_rejected_at
    assert all(n.level == f"d{rejected_on.isoformat()}" for n in rows)
    for n in rows:
        n.status = NotificationStatus.SEEN  # the user already saw the bell
    db.commit()

    run_broker_clock(db, world.a.broker_id, now=utcnow())
    db.commit()
    db.expire_all()
    rows = db.scalars(select(Notification).where(
        Notification.kind == NotificationKind.DIRECT_DEBIT_REJECTED,
        Notification.status != NotificationStatus.CLOSED,
    )).all()
    assert rows and all(n.status == NotificationStatus.SEEN for n in rows)
