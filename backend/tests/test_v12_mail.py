"""v12 lane B4 — mail: transport, sender, mailboxes, inbound prefilter + classification.

Hermetic: the real ``SmtpImapTransport`` is exercised against in-process fakes
of ``smtplib`` / ``imaplib``; everything else runs on ``FakeTransport`` and a
fake ``ai.tool_call``. No socket is ever opened.
"""
from __future__ import annotations

import email.message
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.jobs import run_job
from app.models.ai import Extraction, ExtractionStatus
from app.models.collection_settings import CollectionNotice, CollectionSettings
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    EvidenceStatus,
    InboundClass,
    InquiryStatus,
    JobStatus,
    MailAccountStatus,
    MailDirection,
    MailMessageStatus,
    MailProvider,
    MailSecurity,
    MailSender,
    NoticeChannel,
    NoticeKind,
    NoticeStatus,
    NotificationKind,
    ReportedStatus,
)
from app.models.mail import MailAccount, MailMessage, PaymentEvidence, StatusInquiry
from app.models.notification import Notification
from app.services import ai as ai_service
from app.services.mail import transport as transport_module
from app.services.mail.inbound import ensure_system_account, poll_account
from app.services.mail.sender import send_mail
from app.services.mail.transport import (
    CheckResult,
    ImapConfig,
    MailTransportError,
    SmtpConfig,
    SmtpImapTransport,
)
from app.core.crypto import encrypt_secret
from tests.conftest import API, auth_headers, ensure_role_user
from tests.fakes_v12 import (
    build_message,
    make_insurer_profile,
    make_party,
    make_policy_with_plan,
)

NOW = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)


# =============================================================================
# shared helpers (imported by test_v12_inquiries / test_v12_notices)
# =============================================================================

def mail_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAIL_ENABLED", True)


def make_mail_account(
    db,
    tenant,
    *,
    address: str = "cobranza@alfa.cl",
    owner_user_id: int | None = None,
    status: MailAccountStatus = MailAccountStatus.CONNECTED,
    password: str = "s3cr3t-pass",
    **fields,
) -> MailAccount:
    account = MailAccount(
        broker_id=tenant.broker_id,
        owner_user_id=owner_user_id,
        provider=MailProvider.CUSTOM,
        address=address,
        display_name=f"Cobranza {tenant.broker.trade_name}",
        smtp_host="smtp.alfa.test",
        smtp_port=465,
        smtp_security=MailSecurity.SSL,
        imap_host="imap.alfa.test",
        imap_port=993,
        imap_security=MailSecurity.SSL,
        username=address,
        secret_ciphertext=encrypt_secret(password),
        send_enabled=True,
        receive_enabled=True,
        status=status,
        **fields,
    )
    db.add(account)
    db.flush()
    return account


def fake_ai(monkeypatch, parsed: dict) -> list[dict]:
    """Install a hermetic ``ai.tool_call`` returning ``parsed``; returns the call log."""
    calls: list[dict] = []

    def _tool_call(**kwargs):
        calls.append(kwargs)
        return ai_service.ToolCallResult(
            parsed=dict(parsed),
            payload=None,
            raw={"parsed": dict(parsed)},
            usage={"prompt_tokens": 12, "completion_tokens": 8},
            model="fake/classifier-1",
        )

    monkeypatch.setattr(ai_service, "tool_call", _tool_call)
    return calls


def system_mailbox() -> tuple[str, str]:
    cfg = transport_module.radal_imap_config()
    return (cfg.host, cfg.username)


def policy_with_payer(db, world, **kw):
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, **kw)
    make_party(db, policy, email="pagador@alfa-cliente.cl")
    db.commit()
    db.refresh(policy)
    db.refresh(plan)
    return policy, plan


# =============================================================================
# real transport against fake smtplib / imaplib
# =============================================================================

class _FakeSMTP:
    instances: list["_FakeSMTP"] = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):  # noqa: ANN001
        self.host, self.port, self.context = host, port, context
        self.calls: list[str] = []
        self.sent: list = []
        _FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):  # noqa: ANN001
        self.calls.append("starttls")

    def login(self, user, password):  # noqa: ANN001
        if _FakeSMTP.fail_login:
            raise OSError(f"535 bad credentials for {user} / {password}")
        self.calls.append("login")

    def send_message(self, msg, from_addr=None):  # noqa: ANN001
        self.sent.append((msg, from_addr))

    def noop(self):
        self.calls.append("noop")

    def quit(self):
        self.calls.append("quit")


class _FakeIMAP:
    mailbox: dict[int, bytes] = {}
    uidvalidity = "77"
    uidnext = "1"
    searches: list[str] = []

    def __init__(self, host, port=None, ssl_context=None, timeout=None):  # noqa: ANN001
        self.selected = None
        self.readonly = None

    def starttls(self, ssl_context=None):  # noqa: ANN001
        pass

    def login(self, user, password):  # noqa: ANN001
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):  # noqa: ANN001
        self.selected, self.readonly = mailbox, readonly
        return "OK", [b"1"]

    def response(self, name):  # noqa: ANN001
        if name == "UIDVALIDITY":
            return name, [self.uidvalidity.encode()]
        if name == "UIDNEXT":
            return name, [self.uidnext.encode()]
        return name, [None]

    def uid(self, command, *args):  # noqa: ANN001
        if command == "SEARCH":
            criteria = args[-1]
            _FakeIMAP.searches.append(criteria)
            uids = sorted(self.mailbox)
            if criteria.startswith("UID "):
                low = int(criteria.split()[1].split(":")[0])
                found = [u for u in uids if u >= low] or uids[-1:]  # IMAP answers the newest
            else:
                found = uids
            return "OK", [" ".join(str(u) for u in found).encode()]
        if command == "FETCH":
            uid = int(args[0])
            assert args[1] == "(BODY.PEEK[])"  # never flags \\Seen
            return "OK", [(f"{uid} (UID {uid} BODY[] {{10}}".encode(), self.mailbox[uid]), b")"]
        raise AssertionError(command)

    def logout(self):
        return "BYE", []


@pytest.fixture()
def fake_libs(monkeypatch):
    import imaplib
    import smtplib

    _FakeSMTP.instances = []
    _FakeSMTP.fail_login = False
    _FakeIMAP.mailbox = {
        5: b"Subject: uno\r\nMessage-ID: <1@x>\r\n\r\nhola",
        6: b"Subject: dos\r\nMessage-ID: <2@x>\r\n\r\nchao",
    }
    _FakeIMAP.searches = []
    _FakeIMAP.uidvalidity = "77"
    _FakeIMAP.uidnext = "7"
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeIMAP)
    monkeypatch.setattr(imaplib, "IMAP4", _FakeIMAP)


def _smtp(security="ssl", password="pw"):  # noqa: ANN001
    return SmtpConfig(host="smtp.t", port=465, security=security, username="u@t.cl",
                      password=password, from_address="u@t.cl")


def _imap():
    return ImapConfig(host="imap.t", port=993, security="ssl", username="u@t.cl", password="pw")


def test_real_transport_sends_over_ssl_and_starttls(fake_libs):
    transport = SmtpImapTransport()
    msg = build_message(from_address="u@t.cl", to="x@y.cl", subject="Hola", body="b")
    mid = transport.send(_smtp("ssl"), msg)
    assert mid == msg["Message-ID"]
    ssl_conn = _FakeSMTP.instances[-1]
    assert ssl_conn.sent[0][1] == "u@t.cl" and "login" in ssl_conn.calls
    assert "starttls" not in ssl_conn.calls

    transport.send(_smtp("starttls"), build_message(from_address="u@t.cl", to="x@y.cl", subject="s", body="b"))
    assert _FakeSMTP.instances[-1].calls[:3] == ["ehlo", "starttls", "ehlo"]


def test_real_transport_send_failure_is_a_transport_error_without_the_password(fake_libs):
    _FakeSMTP.fail_login = True
    with pytest.raises(MailTransportError) as info:
        SmtpImapTransport().send(_smtp(password="hunter2"), email.message.EmailMessage())
    assert "hunter2" not in str(info.value)


def test_real_transport_fetch_uses_the_uid_cursor_and_resets_on_uidvalidity(fake_libs):
    transport = SmtpImapTransport()
    # No cursor: reset window (SINCE), everything in it.
    batch, cursor = transport.fetch_since(_imap(), None)
    assert [m.uid for m in batch] == ["5", "6"]
    assert _FakeIMAP.searches[-1].startswith("SINCE ")
    assert cursor == {"uidvalidity": "77", "last_uid": 6}
    assert batch[0].subject == "uno"

    # Same epoch: UID search from last+1; IMAP's "newest" answer is filtered out.
    again, cursor2 = transport.fetch_since(_imap(), cursor)
    assert again == [] and cursor2 == cursor
    assert _FakeIMAP.searches[-1] == "UID 7:*"

    _FakeIMAP.mailbox[7] = b"Subject: tres\r\n\r\nx"
    fresh, cursor3 = transport.fetch_since(_imap(), cursor2)
    assert [m.uid for m in fresh] == ["7"] and cursor3["last_uid"] == 7

    # UIDVALIDITY changed: every UID is void -> back to the SINCE window.
    _FakeIMAP.uidvalidity = "78"
    reset, cursor4 = transport.fetch_since(_imap(), cursor3)
    assert [m.uid for m in reset] == ["5", "6", "7"]
    assert _FakeIMAP.searches[-1].startswith("SINCE ")
    assert cursor4["uidvalidity"] == "78"


def test_real_transport_limit_and_empty_reset(fake_libs):
    batch, cursor = SmtpImapTransport().fetch_since(_imap(), None, limit=1)
    assert [m.uid for m in batch] == ["5"] and cursor["last_uid"] == 5
    _FakeIMAP.mailbox = {}
    _FakeIMAP.uidnext = "40"
    empty, cursor = SmtpImapTransport().fetch_since(_imap(), None)
    assert empty == [] and cursor["last_uid"] == 39


def test_real_transport_check(fake_libs):
    ok = SmtpImapTransport().check(_smtp(), _imap())
    assert ok.ok and ok.smtp_ok and ok.imap_ok and ok.checked_at is not None
    _FakeSMTP.fail_login = True
    bad = SmtpImapTransport().check(_smtp(password="zz-secret"), None)
    assert bad.smtp_ok is False and bad.imap_ok is None and not bad.ok
    assert "zz-secret" not in (bad.smtp_error or "")


# =============================================================================
# sender
# =============================================================================

def test_send_mail_is_skipped_when_mail_is_off(db, world, fake_transport):
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"],
                    subject="Hola", text="cuerpo")
    db.commit()
    assert row.status == MailMessageStatus.SKIPPED
    assert row.message_id_header and row.message_id_header.startswith("<")
    assert row.direction == MailDirection.OUTBOUND and row.broker_id == world.a.broker_id
    assert fake_transport.sent == []


def test_send_mail_through_radal_names_the_broker(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    world.a.broker.email = "contacto@alfa.cl"
    db.commit()
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to="x@y.cl",
                    subject="Hola", text="Línea 1\n\n<b>no html</b>")
    db.commit()
    assert row.status == MailMessageStatus.SENT
    (cfg, msg), = fake_transport.sent
    assert cfg.host == settings.RADAL_MAIL_SMTP_HOST
    assert "Alfa vía Radal" in msg["From"] and settings.RADAL_MAIL_ADDRESS in msg["From"]
    assert msg["Reply-To"] == "contacto@alfa.cl"
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert "&lt;b&gt;no html&lt;/b&gt;" in html
    assert msg["Message-ID"] == row.message_id_header


def test_send_mail_auto_uses_the_connected_broker_account(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    account = make_mail_account(db, world.a)
    db.add(CollectionSettings(broker_id=world.a.broker_id, default_sender=MailSender.BROKER_ACCOUNT,
                              sender_mail_account_id=account.id))
    db.commit()
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"],
                    subject="s", text="t", account="auto")
    assert row.status == MailMessageStatus.SENT and row.mail_account_id == account.id
    (cfg, msg), = fake_transport.sent
    assert cfg.host == "smtp.alfa.test" and cfg.password == "s3cr3t-pass"
    assert "cobranza@alfa.cl" in msg["From"]

    # A paused account falls back to Radal on "auto" …
    account.status = MailAccountStatus.PAUSED
    db.commit()
    row2 = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"],
                     subject="s", text="t", account="auto")
    assert row2.mail_account_id is None and fake_transport.sent[-1][0].host == settings.RADAL_MAIL_SMTP_HOST
    # … but an explicitly chosen unusable account is a failed row, never re-routed.
    row3 = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"],
                     subject="s", text="t", account=account)
    assert row3.status == MailMessageStatus.FAILED and "no está conectada" in row3.error


def test_send_mail_never_raises(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    fake_transport.fail_send = ConnectionRefusedError("refused")
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert row.status == MailMessageStatus.FAILED and row.error == "ConnectionRefusedError: refused"
    other_broker_account = make_mail_account(db, world.b, address="b@beta.cl")
    row2 = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s",
                     text="t", account=other_broker_account)
    assert row2.status == MailMessageStatus.FAILED and "no pertenece" in row2.error


# =============================================================================
# /mail/accounts
# =============================================================================

_BODY = {
    "address": "Ventas@Alfa.cl",
    "display_name": "Ventas Alfa",
    "smtp_host": "smtp.godaddy.test",
    "smtp_port": 465,
    "smtp_security": "ssl",
    "imap_host": "imap.godaddy.test",
    "imap_port": 993,
    "imap_security": "ssl",
    "username": "ventas@alfa.cl",
    "password": "super-secret-pw",
    "owner": "me",
}


def test_create_custom_account_encrypts_and_never_echoes_the_password(client, db, headers_a, world):
    resp = client.post(f"{API}/mail/accounts", json=_BODY, headers=headers_a)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["address"] == "ventas@alfa.cl" and data["has_secret"] is True
    assert data["status"] == "draft" and data["owner"] == "me" and data["can_manage"]
    assert "super-secret-pw" not in resp.text and "password" not in data
    row = db.get(MailAccount, data["id"])
    assert row.secret_ciphertext and "super-secret-pw" not in row.secret_ciphertext
    assert decrypt_secret(row.secret_ciphertext) == "super-secret-pw"

    listing = client.get(f"{API}/mail/accounts", headers=headers_a).json()
    assert listing["items"][0]["kind"] == "radal_system"
    assert listing["items"][0]["read_only"] is True and listing["items"][0]["id"] is None
    assert listing["items"][0]["display_name"] == "Alfa vía Radal"
    assert [i["id"] for i in listing["items"][1:]] == [data["id"]]
    providers = {p["provider"]: p for p in listing["providers"]}
    assert providers["custom"]["available"] is True
    assert providers["google"]["available"] is False and "Pronto" in providers["google"]["reason"]

    dup = client.post(f"{API}/mail/accounts", json=_BODY, headers=headers_a)
    assert dup.status_code == 409


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_google_and_microsoft_are_pronto(client, headers_a, world, provider):
    resp = client.post(f"{API}/mail/accounts", json={**_BODY, "provider": provider}, headers=headers_a)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Pronto: conexión con Google / Microsoft 365 mediante OAuth"


def test_radal_system_and_missing_servers_are_refused(client, headers_a, world):
    resp = client.post(f"{API}/mail/accounts", json={**_BODY, "provider": "radal_system"}, headers=headers_a)
    assert resp.status_code == 422
    resp = client.post(f"{API}/mail/accounts", json={**_BODY, "smtp_host": None}, headers=headers_a)
    assert resp.status_code == 422 and "SMTP" in resp.json()["detail"]


def test_shared_vs_own_mailbox_rules(client, db, headers_a, headers_a_exec, world):
    # The executive may connect their own mailbox, not a shared one.
    shared = client.post(f"{API}/mail/accounts", json={**_BODY, "owner": "shared"}, headers=headers_a_exec)
    assert shared.status_code == 403
    own = client.post(f"{API}/mail/accounts", json={**_BODY, "address": "exec@alfa.cl"}, headers=headers_a_exec)
    assert own.status_code == 201
    own_id = own.json()["id"]

    admin_shared = client.post(f"{API}/mail/accounts", json={**_BODY, "owner": "shared"}, headers=headers_a)
    admin_personal = client.post(
        f"{API}/mail/accounts", json={**_BODY, "address": "jefa@alfa.cl"}, headers=headers_a
    )
    assert admin_shared.status_code == 201 and admin_personal.status_code == 201

    exec_view = client.get(f"{API}/mail/accounts", headers=headers_a_exec).json()["items"]
    ids = {i["id"] for i in exec_view}
    assert own_id in ids and admin_shared.json()["id"] in ids
    assert admin_personal.json()["id"] not in ids  # a colleague's personal box is invisible
    shared_row = next(i for i in exec_view if i["id"] == admin_shared.json()["id"])
    assert shared_row["can_manage"] is False

    # Executive: shared -> 403, colleague's personal -> 404, own -> 200.
    assert client.patch(f"{API}/mail/accounts/{admin_shared.json()['id']}", json={"display_name": "x"},
                        headers=headers_a_exec).status_code == 403
    assert client.patch(f"{API}/mail/accounts/{admin_personal.json()['id']}", json={"display_name": "x"},
                        headers=headers_a_exec).status_code == 404
    patched = client.patch(f"{API}/mail/accounts/{own_id}", json={"password": "new-pw", "paused": False},
                           headers=headers_a_exec)
    assert patched.status_code == 200 and patched.json()["status"] == "draft"
    assert decrypt_secret(db.get(MailAccount, own_id).secret_ciphertext) == "new-pw"
    # The admin manages everything in the tenant.
    assert client.delete(f"{API}/mail/accounts/{own_id}", headers=headers_a).status_code == 204


def test_mail_accounts_tenant_isolation_and_gates(client, db, headers_a, headers_b, world):
    created = client.post(f"{API}/mail/accounts", json=_BODY, headers=headers_a).json()
    account_id = created["id"]
    assert [i["id"] for i in client.get(f"{API}/mail/accounts", headers=headers_b).json()["items"]] == [None]
    assert client.patch(f"{API}/mail/accounts/{account_id}", json={"display_name": "x"},
                        headers=headers_b).status_code == 404
    assert client.post(f"{API}/mail/accounts/{account_id}/test", headers=headers_b).status_code == 404
    assert client.delete(f"{API}/mail/accounts/{account_id}", headers=headers_b).status_code == 404
    tech = ensure_role_user(db, world.a, "broker_technician", "technician")
    tech_headers = auth_headers(client, tech.email)
    assert client.get(f"{API}/mail/accounts", headers=tech_headers).status_code == 403
    assert client.post(f"{API}/mail/accounts", json=_BODY, headers=tech_headers).status_code == 403


def test_account_test_endpoint_sets_status(client, db, headers_a, world, fake_transport):
    created = client.post(f"{API}/mail/accounts", json=_BODY, headers=headers_a).json()
    resp = client.post(f"{API}/mail/accounts/{created['id']}/test", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["check"]["ok"] is True and resp.json()["account"]["status"] == "connected"
    smtp_cfg, imap_cfg = fake_transport.checks[-1]
    assert smtp_cfg.password == "super-secret-pw" and imap_cfg.host == "imap.godaddy.test"

    fake_transport.check_result = CheckResult(smtp_ok=True, imap_ok=False, imap_error="LOGIN failed")
    resp = client.post(f"{API}/mail/accounts/{created['id']}/test", headers=headers_a)
    body = resp.json()
    assert body["check"]["ok"] is False and body["account"]["status"] == "error"
    assert "IMAP: LOGIN failed" in body["account"]["last_error"]


def test_mail_messages_log_is_tenant_scoped(client, db, headers_a, headers_b, world, fake_transport):
    send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="De Alfa", text="t")
    send_mail(db, broker_id=world.b.broker_id, kind="test", to=["x@y.cl"], subject="De Beta", text="t")
    db.commit()
    a = client.get(f"{API}/mail/messages", headers=headers_a).json()
    assert [m["subject"] for m in a["items"]] == ["De Alfa"] and a["total"] == 1
    b = client.get(f"{API}/mail/messages?direction=outbound", headers=headers_b).json()
    assert [m["subject"] for m in b["items"]] == ["De Beta"]


# =============================================================================
# inbound: prefilter, classification, evidence, bounces
# =============================================================================

def _inquiry(db, world, *, plan, policy, token="INQ-ABCDEF") -> StatusInquiry:
    inquiry = StatusInquiry(
        broker_id=world.a.broker_id,
        insurer_id=world.insurer.id,
        policy_id=policy.id,
        token=token,
        installment_ids=[i.id for i in plan.installments[:2]],
        to_address="cobranza@hdi.cl",
        status=InquiryStatus.SENT,
        created_by_user_id=world.a.executive.id,
    )
    db.add(inquiry)
    db.commit()
    return inquiry


def test_mail_poll_is_skipped_while_mail_is_off(db, fake_transport):
    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SKIPPED
    assert fake_transport.fetches == []


def test_prefilter_privacy_unrelated_mail_stores_nothing_but_the_uid(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    fake_transport.queue_inbound(
        build_message(from_address="amigo@gmail.com", to=settings.RADAL_MAIL_ADDRESS,
                      subject="Fotos del asado", body="Mira las fotos del sábado"),
        mailbox=system_mailbox(),
    )
    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SUCCEEDED, run.error
    (row,) = db.scalars(select(MailMessage)).all()
    assert row.status == MailMessageStatus.IGNORED and row.direction == MailDirection.INBOUND
    assert row.provider_uid == "1:1" and row.occurred_at is not None  # {uidvalidity}:{uid}
    assert row.from_address is None and row.subject is None and row.body_text is None
    assert row.to_addresses is None and row.message_id_header is None and row.document_id is None
    assert db.scalars(select(Document)).all() == [world.a.document, world.b.document]
    system = ensure_system_account(db)
    assert system.broker_id is None and system.sync_cursor == {"uidvalidity": "1", "last_uid": 1}


def test_token_reply_becomes_suggested_evidence_with_ai(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world)
    inquiry = _inquiry(db, world, plan=plan, policy=policy)
    calls = fake_ai(monkeypatch, {
        "classification": "insurer_status_reply", "confidence": 90, "reported_status": "paid",
        "installment_numbers": [1], "amount": float(plan.installments[0].gross_amount_uf),
        "currency": "UF", "paid_on": "2026-01-04", "comment": "Cuota 1 pagada",
    })
    fake_transport.queue_inbound(
        build_message(from_address="Cobranza HDI <cobranza@hdi.cl>", to=settings.RADAL_MAIL_ADDRESS,
                      subject="RE: [RADAL-INQ-ABCDEF] Consulta de estado de pago",
                      body="Estimados, la cuota 1 se encuentra pagada."),
        mailbox=system_mailbox(),
    )
    assert run_job(db, "mail.poll", NOW).status == JobStatus.SUCCEEDED
    message = db.scalar(select(MailMessage).where(MailMessage.direction == MailDirection.INBOUND))
    assert message.status == MailMessageStatus.PROCESSED
    assert message.broker_id == world.a.broker_id and message.thread_token == "INQ-ABCDEF"
    assert message.classification == InboundClass.INSURER_STATUS_REPLY
    assert message.subject.startswith("RE: [RADAL-INQ-ABCDEF]") and "pagada" in message.body_text
    doc = db.get(Document, message.document_id)
    assert doc.category == DocumentCategory.EMAIL_MESSAGE and doc.broker_id == world.a.broker_id
    assert doc.entity_type.value == "status_inquiry" and doc.entity_id == inquiry.id
    assert doc.s3_key.endswith(".eml") and doc.mime_type == "message/rfc822"
    extraction = db.get(Extraction, message.extraction_id)
    assert extraction.status == ExtractionStatus.SUCCEEDED and extraction.document_id == doc.id
    assert "Contexto: Respuesta a la consulta INQ-ABCDEF" in calls[0]["messages"][1]["content"]
    assert calls[0]["tool_schema"]["name"] == "classify_inbound_mail"

    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.installment_id == plan.installments[0].id and ev.status_inquiry_id == inquiry.id
    assert ev.reported_status == ReportedStatus.PAID and ev.status == EvidenceStatus.SUGGESTED
    assert ev.score >= Decimal("70") and "token_match" in ev.reasons
    assert ev.reported_paid_on == date(2026, 1, 4)
    db.refresh(inquiry)
    assert inquiry.status == InquiryStatus.REPLIED and inquiry.replied_at is not None
    (note,) = db.scalars(select(Notification)).all()
    assert note.kind == NotificationKind.INQUIRY_REPLY and note.user_id == world.a.executive.id
    assert note.origin_type == "status_inquiry" and note.origin_id == inquiry.id

    # Re-polling never duplicates (cursor + unique uid).
    run_job(db, "mail.poll", datetime(2026, 3, 10, 15, 5, tzinfo=timezone.utc))
    assert len(db.scalars(select(PaymentEvidence)).all()) == 1


def test_ai_unavailable_falls_back_to_heuristic_and_still_writes_an_extraction(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world)
    _inquiry(db, world, plan=plan, policy=policy)
    fake_transport.queue_inbound(
        build_message(from_address="cobranza@hdi.cl", to=settings.RADAL_MAIL_ADDRESS,
                      subject="[RADAL-INQ-ABCDEF] respuesta", body="La cuota 2 no registra pago a la fecha."),
        mailbox=system_mailbox(),
    )
    run_job(db, "mail.poll", NOW)
    extraction = db.scalar(select(Extraction))
    assert extraction.status == ExtractionStatus.FAILED and extraction.error.startswith("AINotConfigured")
    assert extraction.parsed["heuristic"] is True
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.reported_status == ReportedStatus.UNPAID and ev.installment_id == plan.installments[1].id
    assert ev.status == EvidenceStatus.NEEDS_REVIEW and "heuristic_read" in ev.reasons


def test_in_reply_to_matches_our_outbound_message(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world)
    inquiry = _inquiry(db, world, plan=plan, policy=policy)
    out = send_mail(db, broker_id=world.a.broker_id, kind="insurer_status_inquiry", to="c@hdi.cl",
                    subject="consulta", text="t", thread_token=inquiry.token)
    inquiry.outbound_message_id = out.id
    db.commit()
    fake_ai(monkeypatch, {"classification": "insurer_status_reply", "reported_status": "paid",
                          "confidence": 80, "installment_numbers": [2]})
    fake_transport.queue_inbound(
        build_message(from_address="c@hdi.cl", to=settings.RADAL_MAIL_ADDRESS, subject="RE: consulta",
                      body="pagada", in_reply_to=out.message_id_header),
        mailbox=system_mailbox(),
    )
    run_job(db, "mail.poll", NOW)
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.status_inquiry_id == inquiry.id and "in_reply_to_match" in ev.reasons
    assert ev.installment_id == plan.installments[1].id


def test_insurer_domain_is_kept_only_on_a_broker_mailbox(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world, policy_number="HDI-778899")
    make_insurer_profile(db, world.insurer, sender_domains=["hdi.cl"])
    account = make_mail_account(db, world.a)
    db.commit()
    fake_ai(monkeypatch, {"classification": "insurer_statement", "reported_status": "paid",
                          "confidence": 60, "installment_numbers": [3]})
    statement = build_message(from_address="avisos@cobranza.hdi.cl", to="cobranza@alfa.cl",
                              subject="Estado de cuenta póliza HDI-778899", body="Cuota 3 pagada")
    fake_transport.queue_inbound(statement, mailbox=("imap.alfa.test", "cobranza@alfa.cl"))
    # The same mail at the shared Radal box is NOT kept (no token, not a broker box).
    fake_transport.queue_inbound(statement, mailbox=system_mailbox())
    run_job(db, "mail.poll", NOW)

    kept = db.scalar(select(MailMessage).where(MailMessage.mail_account_id == account.id))
    assert kept.status == MailMessageStatus.PROCESSED and kept.classification == InboundClass.INSURER_STATEMENT
    doc = db.get(Document, kept.document_id)
    assert doc.entity_type.value == "policy" and doc.entity_id == policy.id
    ignored = db.scalar(
        select(MailMessage).where(MailMessage.mail_account_id == ensure_system_account(db).id)
    )
    assert ignored.status == MailMessageStatus.IGNORED and ignored.subject is None
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.installment_id == plan.installments[2].id and ev.status_inquiry_id is None
    assert ev.source.value == "insurer_statement" and ev.status == EvidenceStatus.NEEDS_REVIEW
    (note,) = db.scalars(select(Notification)).all()
    assert note.kind == NotificationKind.EVIDENCE_PENDING and note.origin_type == "mail_message"


def test_another_brokers_token_at_a_broker_mailbox_is_ignored(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world)
    _inquiry(db, world, plan=plan, policy=policy, token="INQ-ZZZZZZ")  # broker A's inquiry
    account_b = make_mail_account(db, world.b, address="cobranza@beta.cl")
    db.commit()
    fake_transport.queue_inbound(
        build_message(from_address="x@hdi.cl", to="cobranza@beta.cl", subject="[RADAL-INQ-ZZZZZZ] RE",
                      body="pagada"),
        mailbox=("imap.alfa.test", "cobranza@beta.cl"),
    )
    run_job(db, "mail.poll", NOW)
    row = db.scalar(select(MailMessage).where(MailMessage.mail_account_id == account_b.id))
    assert row.status == MailMessageStatus.IGNORED and row.broker_id == world.b.broker_id
    assert row.subject is None and db.scalars(select(PaymentEvidence)).all() == []


def test_bounce_marks_the_notice_bounced(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world)
    out = send_mail(db, broker_id=world.a.broker_id, kind="payer_reminder", to="pagador@alfa-cliente.cl",
                    subject="Recordatorio", text="t")
    notice = CollectionNotice(
        broker_id=world.a.broker_id, installment_id=plan.installments[0].id, kind=NoticeKind.PAYER_REMINDER,
        cadence_level=-5, channel=NoticeChannel.EMAIL, to_address="pagador@alfa-cliente.cl",
        mail_message_id=out.id, status=NoticeStatus.SENT,
    )
    db.add(notice)
    db.commit()
    dsn = email.message.EmailMessage()
    dsn["From"] = "Mail Delivery Subsystem <MAILER-DAEMON@zoho.com>"
    dsn["To"] = settings.RADAL_MAIL_ADDRESS
    dsn["Subject"] = "Undelivered Mail Returned to Sender"
    dsn.set_content(f"Delivery failed.\n\nMessage-ID: {out.message_id_header}\nSubject: Recordatorio\n")
    fake_transport.queue_inbound(dsn, mailbox=system_mailbox())
    run_job(db, "mail.poll", NOW)
    db.refresh(notice)
    assert notice.status == NoticeStatus.BOUNCED
    bounce = db.scalar(select(MailMessage).where(MailMessage.direction == MailDirection.INBOUND))
    assert bounce.classification == InboundClass.BOUNCE and bounce.status == MailMessageStatus.PROCESSED

    # A bounce of something that is not ours is ignored like any other mail.
    stranger = email.message.EmailMessage()
    stranger["From"] = "postmaster@otro.cl"
    stranger["Subject"] = "Undeliverable"
    stranger.set_content("Message-ID: <nope@otro.cl>")
    fake_transport.queue_inbound(stranger, mailbox=system_mailbox())
    run_job(db, "mail.poll", datetime(2026, 3, 10, 15, 9, tzinfo=timezone.utc))
    last = db.scalars(select(MailMessage).order_by(MailMessage.id.desc())).first()
    assert last.status == MailMessageStatus.IGNORED and last.subject is None


def test_unreadable_broker_mailbox_goes_error_and_rings_the_owner(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    account = make_mail_account(db, world.a, owner_user_id=world.a.executive.id)
    db.commit()

    def _boom(cfg, cursor, *, limit=200):  # noqa: ANN001
        raise MailTransportError("IMAP LOGIN failed")

    monkeypatch.setattr(fake_transport, "fetch_since", _boom)
    stats = poll_account(db, account, now=NOW)
    db.commit()
    assert stats["errors"] == 1
    db.refresh(account)
    assert account.status == MailAccountStatus.ERROR and "LOGIN failed" in account.last_error
    (note,) = db.scalars(select(Notification)).all()
    assert note.kind == NotificationKind.MAIL_ACCOUNT_ERROR and note.user_id == world.a.executive.id
    # An errored mailbox is not polled again until it is re-tested.
    run = run_job(db, "mail.poll", NOW)
    assert run.stats["accounts"] == 1  # only the Radal system box


# =============================================================================
# fixer regressions
# =============================================================================

def test_uidvalidity_reset_new_mail_with_an_old_uid_is_still_read(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world)
    inquiry = _inquiry(db, world, plan=plan, policy=policy)
    fake_ai(monkeypatch, {"classification": "insurer_status_reply", "reported_status": "paid",
                          "confidence": 90, "installment_numbers": [1]})
    fake_transport.uidvalidity = "100"
    reply = build_message(from_address="cobranza@hdi.cl", to=settings.RADAL_MAIL_ADDRESS,
                          subject=f"RE: [RADAL-{inquiry.token}] Consulta", body="Cuota 1 pagada")
    fake_transport.queue_inbound(build_message(from_address="amigo@gmail.com", to=settings.RADAL_MAIL_ADDRESS,
                                               subject="hola", body="nada"), mailbox=system_mailbox(), uid=1)
    fake_transport.queue_inbound(reply, mailbox=system_mailbox(), uid=2)
    system = ensure_system_account(db)
    first = poll_account(db, system, now=NOW)
    db.commit()
    assert first["kept"] == 1 and first["ignored"] == 1
    assert len(db.scalars(select(PaymentEvidence)).all()) == 1

    # The server renumbers: a NEW mail arrives as uid 1, and the 2-day window
    # re-reads the reply already kept (now uid 2 of the new epoch).
    fake_transport.uidvalidity = "200"
    fake_transport.inbox.clear()
    fresh = build_message(from_address="cobranza@hdi.cl", to=settings.RADAL_MAIL_ADDRESS,
                          subject=f"RE: [RADAL-{inquiry.token}] Cuota 2", body="Cuota 2 pagada")
    fake_transport.queue_inbound(fresh, mailbox=system_mailbox(), uid=1)
    fake_transport.queue_inbound(reply, mailbox=system_mailbox(), uid=2)
    fake_ai(monkeypatch, {"classification": "insurer_status_reply", "reported_status": "paid",
                          "confidence": 90, "installment_numbers": [2]})
    second = poll_account(db, system, now=NOW)
    db.commit()
    assert second["fetched"] == 2 and second["kept"] == 1 and second["ignored"] == 1
    uids = sorted(m.provider_uid for m in db.scalars(select(MailMessage).where(
        MailMessage.direction == MailDirection.INBOUND)))
    assert uids == ["100:1", "100:2", "200:1", "200:2"]
    evidence = db.scalars(select(PaymentEvidence).order_by(PaymentEvidence.id)).all()
    assert [e.installment_id for e in evidence] == [plan.installments[0].id, plan.installments[1].id]
    dup = db.scalar(select(MailMessage).where(MailMessage.provider_uid == "200:2"))
    assert dup.status == MailMessageStatus.IGNORED and dup.subject is None and dup.error == "duplicate"


@pytest.mark.parametrize("field,value", [
    ("smtp_host", "127.0.0.1"),
    ("smtp_host", "localhost"),
    ("imap_host", "169.254.169.254"),
    ("imap_host", "10.0.0.5"),
    ("smtp_host", "rds"),
    ("smtp_host", "db.internal"),
    ("smtp_host", "[::1]"),
    ("smtp_port", 22),
    ("imap_port", 6379),
])
def test_mailbox_servers_must_be_public_mail_hosts(client, headers_a, world, field, value):
    resp = client.post(f"{API}/mail/accounts", json={**_BODY, field: value}, headers=headers_a)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "interna" in detail or "Puerto" in detail


def test_patch_cannot_point_a_mailbox_at_an_internal_host(client, db, headers_a, world):
    created = client.post(f"{API}/mail/accounts", json=_BODY, headers=headers_a).json()
    resp = client.patch(f"{API}/mail/accounts/{created['id']}", json={"imap_host": "192.168.1.10"},
                        headers=headers_a)
    assert resp.status_code == 422 and "interna" in resp.json()["detail"]
    assert db.get(MailAccount, created["id"]).imap_host == "imap.godaddy.test"


def test_transport_refuses_a_name_that_resolves_inside_the_vpc(fake_libs, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 465))])
    before = len(_FakeSMTP.instances)
    cfg = SmtpConfig(host="smtp.innocent.example", port=465, security="ssl", username="u@t.cl",
                     password="pw", restrict_network=True)
    with pytest.raises(MailTransportError) as info:
        SmtpImapTransport().send(cfg, build_message(from_address="u@t.cl", to="x@y.cl", subject="s", body="b"))
    assert "interna" in str(info.value) and len(_FakeSMTP.instances) == before  # never connected
    check = SmtpImapTransport().check(cfg, None)
    assert check.smtp_ok is False and "interna" in check.smtp_error


def test_transport_errors_never_echo_the_server_banner(fake_libs, monkeypatch):
    import smtplib

    def _banner(*a, **k):  # noqa: ANN002, ANN003
        raise smtplib.SMTPConnectError(421, b"redis-internal 6.2.1 ready")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _banner)
    result = SmtpImapTransport().check(_smtp(), None)
    assert result.smtp_ok is False and "redis" not in result.smtp_error
    assert result.smtp_error == "El servidor SMTP rechazó la operación"

    def _refused(*a, **k):  # noqa: ANN002, ANN003
        raise ConnectionRefusedError(61, "Connection refused by 10.0.0.9")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _refused)
    result = SmtpImapTransport().check(_smtp(), None)
    assert "10.0.0.9" not in result.smtp_error and "rechazó la conexión" in result.smtp_error

    class _AuthSMTP(_FakeSMTP):
        def login(self, user, password):  # noqa: ANN001
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 bad creds for " + password.encode())

    monkeypatch.setattr(smtplib, "SMTP_SSL", _AuthSMTP)
    result = SmtpImapTransport().check(_smtp(password="hunter2"), None)
    assert result.smtp_error.startswith("Autenticación rechazada") and "hunter2" not in result.smtp_error


def test_broker_mailbox_configs_carry_the_network_guard(db, world):
    from app.services.mail.sender import account_imap_config, account_smtp_config

    account = make_mail_account(db, world.a)
    assert account_smtp_config(account).restrict_network is True
    assert account_imap_config(account).restrict_network is True
    assert transport_module.radal_smtp_config().restrict_network is False
