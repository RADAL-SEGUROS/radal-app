"""v12 phase-1 seams: payment-provider registry + signature, mail transport, notify_ops.

Hermetic: the real transport (lane B4) and ForpayProvider (lane B3) exist, but
the conftest points them at 127.0.0.1:9, so they fail closed; every test that
needs a working network-facing call goes through a fake.
"""
from __future__ import annotations

import email.message
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.models.enums import MailDirection, MailMessageStatus
from app.models.mail import MailMessage
from app.services.mail import transport as transport_module
from app.services.mail.fake import FakeTransport, build_message
from app.services.mail.ops import notify_ops
from app.services.mail.transport import (
    CheckResult,
    ImapConfig,
    MailTransport,
    SmtpConfig,
    SmtpImapTransport,
    get_transport,
    radal_imap_config,
    radal_smtp_config,
)
from app.services.payments import providers as providers_module
from app.services.payments.providers import ProviderUnavailable, get_provider, register_provider
from app.services.payments.providers.base import (
    STATE_PAID,
    STATE_REJECTED,
    CheckoutItem,
    PayerData,
    PaymentProvider,
    account_secret,
    compute_payload_signature,
    parse_installment_webhook,
    signature_fields_for,
    verify_payload_signature,
)
from app.services.payments.providers.fake import FakePaymentProvider
from tests.fakes_v12 import TEST_HMAC_KEY, make_provider_account, webhook_sig

# --- provider registry -------------------------------------------------------


def test_forpay_is_installed_and_unknown_names_are_unavailable(monkeypatch):
    # Lane B3 shipped ForpayProvider: the lazy factory now resolves it.
    from app.services.payments.providers.forpay import ForpayProvider

    monkeypatch.setattr(providers_module, "_INSTANCES", {})
    assert isinstance(get_provider("forpay"), ForpayProvider)
    with pytest.raises(ProviderUnavailable):
        get_provider("nope")


def test_registered_factory_is_used_and_cached(monkeypatch):
    monkeypatch.setattr(providers_module, "_FACTORIES", {})
    monkeypatch.setattr(providers_module, "_INSTANCES", {})
    register_provider("forpay", lambda: FakePaymentProvider("forpay"))
    first = get_provider("forpay")
    assert isinstance(first, FakePaymentProvider)
    assert get_provider("forpay") is first


def test_fake_provider_fixture_overrides_every_name(fake_provider):
    assert get_provider("forpay") is fake_provider
    assert get_provider("anything") is fake_provider
    assert isinstance(fake_provider, PaymentProvider)


def test_fake_provider_records_calls_and_is_deterministic(fake_provider):
    payer = PayerData(rut="77111111-4", name="Asegurado", last_name="Alfa", email="a@alfa.cl")
    items = [
        CheckoutItem(installment_id=1, external_installment_id="rdl-cuota-1-ab", amount_uf=Decimal("2.5"),
                     due_date=date(2026, 3, 10), store_commerce_code="597000000001")
    ]
    provider = get_provider("forpay")
    assert provider.ensure_customer(None, payer) == "fake-customer-77111111-4"
    session = provider.create_spot_checkout(
        None, payer, items, external_id="rdl-intent-ab", return_url="https://app/r", webhook_url="https://h/w"
    )
    assert session.checkout_url == "https://pay.fake-provider.test/checkout/rdl-intent-ab"
    assert session.provider_mandate_id == "fake-mandate-rdl-intent-ab"
    provider.set_installment_state(None, ["rdl-cuota-1-ab"], "paid")
    assert fake_provider.installment_states == {"rdl-cuota-1-ab": "paid"}
    assert [name for name, _ in fake_provider.calls] == [
        "ensure_customer", "create_spot_checkout", "set_installment_state",
    ]
    assert fake_provider.calls_to("create_spot_checkout")[0]["items"] == items


def test_fake_receivables_filters(fake_provider):
    fake_provider.add_receivable(provider_installment_id="f1", external_installment_id="e1", state=2,
                                 payout={"dispersionId": "D1"})
    fake_provider.add_receivable(provider_installment_id="f2", external_installment_id="e2", state=1)
    assert [r.provider_installment_id for r in fake_provider.search_receivables(None)] == ["f1", "f2"]
    assert [r.provider_installment_id
            for r in fake_provider.search_receivables(None, externalInstallmentId="e2")] == ["f2"]
    assert [r.provider_installment_id
            for r in fake_provider.search_receivables(None, hasPayout=True)] == ["f1"]


# --- payload signature -------------------------------------------------------


def _payload(**kw):
    return FakePaymentProvider.build_webhook_payload(
        TEST_HMAC_KEY, internal_id="fp-123", external_installment_id="rdl-cuota-9-aa", **kw
    )


def test_signed_payload_verifies_with_the_account_secret(db):
    account = make_provider_account(db)
    provider = FakePaymentProvider()
    assert provider.verify_webhook(account, _payload())
    assert not provider.verify_webhook(account, _payload(tamper=True))
    unsigned = _payload()
    unsigned.pop("signature")
    assert not provider.verify_webhook(account, unsigned)


def test_wrong_key_or_no_key_fails_closed(db):
    wrong = make_provider_account(db, hmac_key="another-key")
    assert not FakePaymentProvider().verify_webhook(wrong, _payload())
    assert not verify_payload_signature(None, _payload())
    assert not verify_payload_signature("", _payload())


def test_paid_state_signs_the_payment_fields_too():
    fields = signature_fields_for(_payload())
    assert fields[-3:] == ["payment.id", "payment.authorizationCode", "payment.amount"]
    tampered = _payload()
    tampered["payment"] = {**tampered["payment"], "amount": "1"}
    assert not verify_payload_signature(TEST_HMAC_KEY, tampered)
    rejected = _payload(state=STATE_REJECTED)
    assert "payment" not in rejected
    assert "payment.id" not in signature_fields_for(rejected)
    assert verify_payload_signature(TEST_HMAC_KEY, rejected)


def test_account_can_override_the_field_order(db):
    order = ["installment.id", "installment.state.id"]
    account = make_provider_account(db, secret_extra={"signature_fields": {"payment": order}})
    payload = _payload(fields=order)
    assert FakePaymentProvider().verify_webhook(account, payload)
    # The default order no longer matches that payload's signature.
    assert not verify_payload_signature(TEST_HMAC_KEY, payload)
    assert payload["signature"] == compute_payload_signature(TEST_HMAC_KEY, payload, order)


def test_parse_installment_webhook():
    event = parse_installment_webhook(_payload(amount="2.5000"))
    assert event.dedup_key == f"rdl-cuota-9-aa:{STATE_PAID}"  # signed installment.id, never internalId
    assert event.provider_installment_id == "fp-123"
    assert event.external_installment_id == "rdl-cuota-9-aa"
    assert event.state == STATE_PAID and event.state_name == "PAGADA"
    assert event.amount == Decimal("2.5000") and event.currency == "UF"
    assert event.payment_id == "pay-1" and event.authorization_code == "AUTH01"
    assert event.convert_amount == Decimal("60000") and event.convert_currency == "PESOS"
    assert event.convert_date == date(2026, 10, 16)
    assert event.paid_at is not None and event.paid_at.tzinfo is not None
    other = parse_installment_webhook(_payload(state=STATE_REJECTED))
    assert other.dedup_key == "rdl-cuota-9-aa:-99" and other.payment_id is None


def test_account_secret_is_decrypted_and_tolerant(db):
    account = make_provider_account(db)
    assert account_secret(account)["hmac_key"] == TEST_HMAC_KEY
    assert "test-forpay-hmac-key" not in account.secret_ciphertext
    account.secret_ciphertext = None
    assert account_secret(account) == {}
    account.secret_ciphertext = "not-a-fernet-token"
    assert account_secret(account) == {}
    account.secret_ciphertext = encrypt_secret("a plain string")
    assert account_secret(account) == {}


def test_webhook_sig_helper_is_stable():
    assert webhook_sig("s", "k") == webhook_sig("s", "k")
    assert webhook_sig("s", "k") != webhook_sig("s2", "k")
    assert len(webhook_sig("s", "k")) == 64


# --- mail transport ----------------------------------------------------------


def test_default_transport_is_smtp_imap_and_fails_closed_on_the_hermetic_host():
    # Lane B4 shipped the real transport; the hermetic 127.0.0.1:9 refuses it.
    from app.services.mail.transport import MailTransportError

    transport = get_transport()
    assert isinstance(transport, SmtpImapTransport)
    assert isinstance(transport, MailTransport)
    cfg = radal_smtp_config()
    with pytest.raises(MailTransportError):
        transport.send(cfg, email.message.EmailMessage())
    with pytest.raises(MailTransportError):
        transport.fetch_since(radal_imap_config(), None)
    result = transport.check(cfg, None)
    assert result.smtp_ok is False and result.smtp_error


def test_radal_configs_come_from_the_hermetic_settings():
    smtp = radal_smtp_config()
    imap = radal_imap_config()
    assert (smtp.host, smtp.port) == ("127.0.0.1", 9)
    assert (imap.host, imap.port) == ("127.0.0.1", 9)
    assert smtp.from_address == settings.RADAL_MAIL_ADDRESS
    assert smtp.password == ""


def test_configs_never_print_the_password():
    cfg = SmtpConfig(host="h", port=465, security="ssl", username="u", password="s3cr3t")
    icfg = ImapConfig(host="h", port=993, security="ssl", username="u", password="s3cr3t")
    assert "s3cr3t" not in repr(cfg) and "s3cr3t" not in repr(icfg)


def test_fake_transport_fixture_send_fetch_check(fake_transport):
    assert get_transport() is fake_transport
    cfg = radal_smtp_config()
    msg = build_message(from_address="a@x.cl", to="b@y.cl", subject="Hola", body="cuerpo")
    message_id = get_transport().send(cfg, msg)
    assert message_id == msg["Message-ID"]
    assert fake_transport.sent_messages == [msg]

    imap = ImapConfig(host="imap.test", port=993, security="ssl", username="u", password="p")
    fake_transport.queue_inbound(build_message(from_address="c@hdi.cl", to="u", subject="[RADAL-INQ-ABC123] Re",
                                               body="pagada"), mailbox=("imap.test", "u"))
    fake_transport.queue_inbound(b"Subject: any box\r\n\r\nx")
    fake_transport.queue_inbound(b"Subject: other box\r\n\r\nx", mailbox=("elsewhere", "z"))
    batch, cursor = fake_transport.fetch_since(imap, None)
    assert [m.subject for m in batch] == ["[RADAL-INQ-ABC123] Re", "any box"]
    assert cursor == {"uidvalidity": "1", "last_uid": 2}
    again, cursor2 = fake_transport.fetch_since(imap, cursor)
    assert again == [] and cursor2 == cursor
    # A UIDVALIDITY change resets the cursor.
    fake_transport.uidvalidity = "2"
    reset, _ = fake_transport.fetch_since(imap, cursor)
    assert len(reset) == 2

    fake_transport.check_result = CheckResult(smtp_ok=True, imap_ok=False, imap_error="login failed")
    result = get_transport().check(cfg, imap)
    assert not result.ok and result.imap_error == "login failed"
    assert CheckResult(smtp_ok=True).ok and not CheckResult().ok


def test_fake_transport_restored_after_the_fixture():
    assert transport_module._OVERRIDE is None
    assert providers_module._OVERRIDE is None


# --- notify_ops --------------------------------------------------------------


def _ops_rows(db):
    return list(db.scalars(select(MailMessage).order_by(MailMessage.id)))


def test_notify_ops_records_skipped_when_mail_is_off(db, fake_transport, monkeypatch):
    monkeypatch.setattr(settings, "RADAL_OPS_EMAIL", "ops@radal.test")
    row = notify_ops(db, "RPA hdi falló", "layout_changed en paso login")
    db.commit()
    assert row.status == MailMessageStatus.SKIPPED
    assert row.direction == MailDirection.OUTBOUND
    assert row.broker_id is None
    assert row.to_addresses == ["ops@radal.test"]
    assert row.subject == "[Radal ops] RPA hdi falló"
    assert row.body_text == "layout_changed en paso login"
    assert fake_transport.sent == []
    assert len(_ops_rows(db)) == 1


def test_notify_ops_sends_when_enabled(db, fake_transport, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_ENABLED", True)
    monkeypatch.setattr(settings, "RADAL_OPS_EMAIL", "ops@radal.test")
    row = notify_ops(db, "Job falló", "collection.clock: boom")
    db.commit()
    assert row.status == MailMessageStatus.SENT
    assert row.message_id_header and row.message_id_header.startswith("<")
    (cfg, msg), = fake_transport.sent
    assert cfg.host == "127.0.0.1"
    assert msg["To"] == "ops@radal.test"
    assert msg["Subject"] == "[Radal ops] Job falló"


def test_notify_ops_without_recipient_is_skipped(db, fake_transport, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_ENABLED", True)
    monkeypatch.setattr(settings, "RADAL_OPS_EMAIL", "")
    row = notify_ops(db, "x", "y")
    assert row.status == MailMessageStatus.SKIPPED
    assert row.error == "RADAL_OPS_EMAIL is not configured"
    assert row.to_addresses == []
    assert fake_transport.sent == []


def test_notify_ops_never_raises_on_a_send_failure(db, fake_transport, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_ENABLED", True)
    monkeypatch.setattr(settings, "RADAL_OPS_EMAIL", "ops@radal.test")
    fake_transport.fail_send = ConnectionRefusedError("refused")
    row = notify_ops(db, "x", "y")
    db.commit()
    assert row.status == MailMessageStatus.FAILED
    assert row.error == "ConnectionRefusedError: refused"


def test_notify_ops_with_the_real_transport_on_the_hermetic_host_records_failed(db, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_ENABLED", True)
    monkeypatch.setattr(settings, "RADAL_OPS_EMAIL", "ops@radal.test")
    row = notify_ops(db, "x", "y")
    assert row.status == MailMessageStatus.FAILED
    assert row.error and not row.error.startswith("NotImplementedError")


def test_fake_transport_is_a_mail_transport():
    assert isinstance(FakeTransport(), MailTransport)
