"""v12 mail via Amazon SES — the Radal system sender (tech-ref §19.7).

Hermetic: SES is a fake ``sesv2`` client (injected, or by patching
``transport._ses_client``), IMAP / SMTP are in-process fakes of ``imaplib`` /
``smtplib``. The autouse ``_no_real_ses`` fixture (conftest) makes any real
client construction fail, so nothing here can reach AWS, Zoho or Google.
"""
from __future__ import annotations

import time
from email import message_from_bytes, policy

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

from app.core.config import Settings, settings
from app.jobs import run_job
from app.models.collection_settings import CollectionSettings
from app.models.enums import MailMessageStatus, MailSender
from app.models.mail import PaymentEvidence
from app.services.mail import transport as transport_module
from app.services.mail.fake import FakeTransport
from app.services.mail.sender import build_email, send_mail
from app.services.mail.transport import (
    ERR_SES_AUTH,
    ERR_SES_IDENTITY,
    ERR_SES_SANDBOX,
    ERR_SES_SENDING_DISABLED,
    ERR_SES_UNREACHABLE,
    ImapConfig,
    SesTransport,
    SmtpConfig,
    SmtpImapTransport,
    append_sent_copy,
    get_system_transport,
    radal_smtp_config,
    raw_message_bytes,
    ses_message_id,
)
from scripts import mail_preflight
from tests.conftest import API
from tests.fakes_v12 import build_message
from tests.test_v12_mail import (
    NOW,
    _inquiry,
    fake_ai,
    mail_on,
    make_mail_account,
    policy_with_payer,
    system_mailbox,
)

# A realistic SES MessageId (60 chars: the header is longer than 78 columns).
SES_ID = "010001926c5d3f1a-5a9e1c7b-3d2f-4b8e-9a1c-2e3f4a5b6c7d-000000"
SES_HEADER = f"<{SES_ID}@email.amazonses.com>"


# =============================================================================
# fakes
# =============================================================================

def _client_error(code: str, message: str = "", op: str = "SendEmail") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, op)


class FakeSes:
    """The slice of a boto3 ``sesv2`` client the transport uses."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail: Exception | None = None
        self.account_error: Exception | None = None
        self.account = {
            "SendingEnabled": True,
            "ProductionAccessEnabled": False,
            "EnforcementStatus": "HEALTHY",
            "SendQuota": {"Max24HourSend": 200.0, "MaxSendRate": 1.0, "SentLast24Hours": 3.0},
        }
        self.identities = {
            "radalseguros.cl": {
                "IdentityType": "DOMAIN",
                "VerifiedForSendingStatus": True,
                "VerificationStatus": "SUCCESS",
                "DkimAttributes": {"SigningEnabled": True, "Status": "SUCCESS"},
                "MailFromAttributes": {
                    "MailFromDomain": "mail.radalseguros.cl",
                    "MailFromDomainStatus": "SUCCESS",
                    "BehaviorOnMxFailure": "USE_DEFAULT_VALUE",
                },
            }
        }

    def send_email(self, **request):  # noqa: ANN003, ANN201
        self.sent.append(request)
        if self.fail is not None:
            raise self.fail
        return {"MessageId": SES_ID}

    def get_account(self):  # noqa: ANN201
        if self.account_error is not None:
            raise self.account_error
        return self.account

    def get_email_identity(self, EmailIdentity):  # noqa: ANN001, ANN201, N803
        if EmailIdentity not in self.identities:
            raise _client_error("NotFoundException", f"{EmailIdentity} not found", "GetEmailIdentity")
        return self.identities[EmailIdentity]

    @property
    def raw(self) -> bytes:
        return self.sent[-1]["Content"]["Raw"]["Data"]


_IMAP_ERROR = __import__("imaplib").IMAP4.error  # captured before any test swaps imaplib.IMAP4


class FakeImap:
    """``imaplib.IMAP4_SSL`` stand-in recording logins and APPENDs."""

    appends: list[tuple] = []
    logins: list[tuple] = []
    fail_login = False
    append_delay = 0.0

    def __init__(self, host, port=None, ssl_context=None, timeout=None):  # noqa: ANN001
        self.host, self.port, self.timeout = host, port, timeout

    def starttls(self, ssl_context=None):  # noqa: ANN001
        pass

    def login(self, user, password):  # noqa: ANN001
        if FakeImap.fail_login:
            raise _IMAP_ERROR(f"AUTHENTICATIONFAILED {user} {password}")
        FakeImap.logins.append((self.host, user))
        return "OK", [b"logged in"]

    def append(self, mailbox, flags, date_time, message):  # noqa: ANN001
        if FakeImap.append_delay:
            time.sleep(FakeImap.append_delay)
        FakeImap.appends.append((mailbox, flags, date_time, message))
        return "OK", [b"APPEND completed"]

    def select(self, mailbox, readonly=False):  # noqa: ANN001
        return "OK", [b"1"]

    def logout(self):
        return "BYE", []

    def shutdown(self):
        pass


class FakeSmtp:
    instances: list["FakeSmtp"] = []

    def __init__(self, host, port, timeout=None, context=None):  # noqa: ANN001
        self.host, self.port = host, port
        self.sent: list = []
        FakeSmtp.instances.append(self)

    def ehlo(self):
        pass

    def starttls(self, context=None):  # noqa: ANN001
        pass

    def login(self, user, password):  # noqa: ANN001
        pass

    def send_message(self, msg, from_addr=None):  # noqa: ANN001
        self.sent.append((msg, from_addr))

    def noop(self):
        pass

    def quit(self):
        pass


@pytest.fixture()
def fake_imap(monkeypatch):
    import imaplib

    FakeImap.appends, FakeImap.logins = [], []
    FakeImap.fail_login, FakeImap.append_delay = False, 0.0
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(imaplib, "IMAP4", FakeImap)
    return FakeImap


@pytest.fixture()
def fake_smtp(monkeypatch):
    import smtplib

    FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


@pytest.fixture()
def ses(monkeypatch):
    """A fake SES behind the real factory; records how many clients were built."""
    fake = FakeSes()
    built: list[tuple] = []

    def _factory(region, timeout):  # noqa: ANN001, ANN202
        built.append((region, timeout))
        return fake

    monkeypatch.setattr(transport_module, "_ses_client", _factory)
    fake.built = built  # type: ignore[attr-defined]
    return fake


@pytest.fixture()
def zoho_imap_creds(monkeypatch):
    monkeypatch.setattr(settings, "RADAL_MAIL_USERNAME", "contacto@radalseguros.cl")
    monkeypatch.setattr(settings, "RADAL_MAIL_PASSWORD", "zoho-app-pw-123")
    monkeypatch.setattr(settings, "RADAL_MAIL_IMAP_HOST", "imap.zoho.com")
    monkeypatch.setattr(settings, "RADAL_MAIL_IMAP_PORT", 993)


def _msg(**over):  # noqa: ANN003
    fields = dict(
        from_name="Alfa vía Radal",
        from_address=settings.RADAL_MAIL_ADDRESS,
        to=["pagador@cliente.cl"],
        cc=["copia@cliente.cl"],
        subject="Recordatorio de pago — cuota 2",
        text="Hola,\n\nsu cuota vence pronto.",
        html=None,
        reply_to="contacto@alfa.cl",
        message_id="<original.123@radalseguros.cl>",
        kind="payer_reminder",
        thread_token="RADAL-INQ-ABC123",
    )
    fields.update(over)
    return build_email(**fields)


# =============================================================================
# settings
# =============================================================================

def test_settings_defaults_and_validation():
    defaults = {name: f.default for name, f in Settings.model_fields.items()}
    assert defaults["MAIL_TRANSPORT"] == "ses" and defaults["MAIL_SENT_COPY"] == "bcc"
    assert defaults["MAIL_COPY_ADDRESS"] == "contacto@radalseguros.cl"
    assert defaults["RADAL_MAIL_REPLY_TO"] == "" and defaults["MAIL_RECIPIENT_ALLOWLIST"] == ""
    assert defaults["MAIL_INBOUND_SOURCE"] == "auto" and defaults["MAIL_INBOUND_S3_PREFIX"] == "inbound-mail/"
    assert defaults["SES_REGION"] == "us-east-1" and defaults["MAIL_SENT_FOLDER"] == "Sent"
    assert defaults["SES_CONFIGURATION_SET"] == ""
    assert defaults["RADAL_MAIL_ADDRESS"] == "contacto@radalseguros.cl"
    assert defaults["RADAL_MAIL_DISPLAY_NAME"] == "Radal"
    assert (defaults["RADAL_MAIL_IMAP_HOST"], defaults["RADAL_MAIL_IMAP_PORT"],
            defaults["RADAL_MAIL_IMAP_SECURITY"]) == ("imap.zoho.com", 993, "ssl")
    fresh = Settings(_env_file=None, MAIL_TRANSPORT="", MAIL_SENT_COPY="", SES_REGION="", MAIL_SENT_FOLDER="")
    assert fresh.MAIL_TRANSPORT == "ses" and fresh.MAIL_SENT_COPY == "bcc"
    assert fresh.SES_REGION == "us-east-1" and fresh.MAIL_SENT_FOLDER == "Sent"
    assert Settings(_env_file=None, MAIL_TRANSPORT=" SMTP ").MAIL_TRANSPORT == "smtp"
    with pytest.raises(ValueError):
        Settings(_env_file=None, MAIL_TRANSPORT="sendgrid")
    for mode in ("bcc", "imap_append", "none"):
        assert Settings(_env_file=None, MAIL_SENT_COPY=mode.upper()).MAIL_SENT_COPY == mode
    with pytest.raises(ValueError):
        Settings(_env_file=None, MAIL_SENT_COPY="cc")
    # Inbound source: blank = auto; auto = imap only with a Zoho password.
    auto = Settings(_env_file=None, MAIL_INBOUND_SOURCE="", RADAL_MAIL_PASSWORD="")
    assert auto.MAIL_INBOUND_SOURCE == "auto" and auto.mail_inbound_source == "none"
    assert Settings(_env_file=None, MAIL_INBOUND_SOURCE="auto",
                    RADAL_MAIL_PASSWORD="pw").mail_inbound_source == "imap"
    s3 = Settings(_env_file=None, MAIL_INBOUND_SOURCE=" S3 ", MAIL_INBOUND_S3_PREFIX="/replies")
    assert s3.mail_inbound_source == "s3" and s3.MAIL_INBOUND_S3_PREFIX == "replies/"
    assert s3.mail_inbound_bucket == s3.S3_BUCKET
    assert Settings(_env_file=None, MAIL_INBOUND_S3_BUCKET="b2").mail_inbound_bucket == "b2"
    with pytest.raises(ValueError):
        Settings(_env_file=None, MAIL_INBOUND_SOURCE="pop3")


# =============================================================================
# SesTransport.send
# =============================================================================

def test_ses_send_posts_the_raw_mime_and_returns_the_ses_message_id():
    fake = FakeSes()
    transport = SesTransport(client=fake, configuration_set="", sent_copy=None)
    msg = _msg()
    returned = transport.send(radal_smtp_config(), msg)

    assert returned == SES_HEADER
    assert msg["Message-ID"] == SES_HEADER  # written back for the sent copy
    (request,) = fake.sent
    assert set(request) == {"FromEmailAddress", "Destination", "Content"}  # no ConfigurationSetName
    assert request["FromEmailAddress"] == settings.RADAL_MAIL_ADDRESS
    assert request["Destination"] == {"ToAddresses": ["pagador@cliente.cl"], "CcAddresses": ["copia@cliente.cl"]}
    raw = request["Content"]["Raw"]["Data"]
    assert isinstance(raw, bytes) and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    sent = message_from_bytes(raw, policy=policy.default)
    assert sent["From"].addresses[0].display_name == "Alfa vía Radal"
    assert sent["From"].addresses[0].addr_spec == settings.RADAL_MAIL_ADDRESS
    assert sent["Reply-To"] == "contacto@alfa.cl"
    assert sent["X-Radal-Token"] == "RADAL-INQ-ABC123" and sent["X-Radal-Kind"] == "payer_reminder"
    assert sent["Subject"] == "Recordatorio de pago — cuota 2"
    assert sent["Date"]
    assert sent.get_content_type() == "multipart/alternative"
    assert "su cuota vence pronto" in sent.get_body(preferencelist=("plain",)).get_content()
    assert transport.last_sent_copy_ok is None


def test_ses_configuration_set_only_when_set_and_region_message_id():
    fake = FakeSes()
    SesTransport(client=fake, configuration_set="radal-dev-events").send(radal_smtp_config(), _msg())
    assert fake.sent[-1]["ConfigurationSetName"] == "radal-dev-events"
    eu = SesTransport(client=fake, region="eu-west-1", configuration_set="")
    assert eu.send(radal_smtp_config(), _msg()) == f"<{SES_ID}@eu-west-1.amazonses.com>"
    assert "ConfigurationSetName" not in fake.sent[-1]
    assert ses_message_id("abc", "us-east-1") == "<abc@email.amazonses.com>"


def test_ses_bcc_goes_to_the_envelope_never_into_the_raw():
    fake = FakeSes()
    msg = _msg(cc=None)
    msg["Bcc"] = "oculto@radalseguros.cl"
    SesTransport(client=fake, configuration_set="").send(radal_smtp_config(), msg)
    assert fake.sent[-1]["Destination"] == {
        "ToAddresses": ["pagador@cliente.cl"], "BccAddresses": ["oculto@radalseguros.cl"]}
    assert b"oculto@radalseguros.cl" not in fake.raw


def test_ses_client_error_is_a_spanish_category():
    fake = FakeSes()
    fake.fail = _client_error(
        "MessageRejected",
        "Email address is not verified. The following identities failed the check in region "
        "US-EAST-1: pagador@cliente.cl",
    )
    with pytest.raises(transport_module.MailTransportError) as info:
        SesTransport(client=fake, configuration_set="").send(radal_smtp_config(), _msg())
    assert str(info.value) == ERR_SES_SANDBOX
    fake.fail = _client_error("AccessDeniedException", "not authorized arn:aws:iam::123:role/x")
    with pytest.raises(transport_module.MailTransportError) as info:
        SesTransport(client=fake, configuration_set="").send(radal_smtp_config(), _msg())
    assert str(info.value) == ERR_SES_AUTH and "arn:aws" not in str(info.value)


# =============================================================================
# routing through send_mail
# =============================================================================

def test_system_send_goes_through_ses_and_stores_the_ses_message_id(db, world, ses, monkeypatch):
    mail_on(monkeypatch)
    world.a.broker.email = "contacto@alfa.cl"
    db.commit()
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to="pagador@cliente.cl",
                    subject="Hola", text="cuerpo")
    db.commit()
    assert row.status == MailMessageStatus.SENT
    assert row.message_id_header == SES_HEADER
    assert row.from_address == settings.RADAL_MAIL_ADDRESS and row.mail_account_id is None
    (request,) = ses.sent
    assert request["FromEmailAddress"] == settings.RADAL_MAIL_ADDRESS
    sent = message_from_bytes(ses.raw, policy=policy.default)
    assert sent["From"].addresses[0].display_name == "Alfa vía Radal"
    assert sent["Reply-To"] == "contacto@alfa.cl"
    assert ses.built == [("us-east-1", int(settings.MAIL_TIMEOUT_SECONDS))]


def test_a_reply_to_the_ses_message_id_is_matched(db, world, ses, monkeypatch):
    mail_on(monkeypatch)
    policy_row, plan = policy_with_payer(db, world)
    inquiry = _inquiry(db, world, plan=plan, policy=policy_row)
    out = send_mail(db, broker_id=world.a.broker_id, kind="insurer_status_inquiry", to="c@hdi.cl",
                    subject="consulta", text="t", thread_token=inquiry.token,
                    reply_to=settings.RADAL_MAIL_ADDRESS)
    inquiry.outbound_message_id = out.id
    db.commit()
    assert out.message_id_header == SES_HEADER

    inbound = FakeTransport()  # inbound stays IMAP: script the Zoho reply
    monkeypatch.setattr(transport_module, "_OVERRIDE", inbound)
    fake_ai(monkeypatch, {"classification": "insurer_status_reply", "reported_status": "paid",
                          "confidence": 80, "installment_numbers": [2]})
    inbound.queue_inbound(
        build_message(from_address="c@hdi.cl", to=settings.RADAL_MAIL_ADDRESS, subject="RE: consulta",
                      body="pagada", in_reply_to=SES_HEADER),
        mailbox=system_mailbox(),
    )
    run_job(db, "mail.poll", NOW)
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.status_inquiry_id == inquiry.id and "in_reply_to_match" in ev.reasons


def test_mail_disabled_is_skipped_and_builds_no_ses_client(db, world, ses, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_ENABLED", False)
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert row.status == MailMessageStatus.SKIPPED
    assert ses.built == [] and ses.sent == []
    # Choosing the system transport opens nothing either: the client is lazy.
    monkeypatch.setattr(settings, "MAIL_ENABLED", True)
    assert isinstance(get_system_transport(), SesTransport) and ses.built == []


def test_ses_client_error_is_a_failed_row_and_never_raises(db, world, ses, monkeypatch):
    mail_on(monkeypatch)
    ses.fail = _client_error("TooManyRequestsException", "Maximum sending rate exceeded.")
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert row.status == MailMessageStatus.FAILED
    assert row.error == transport_module.ERR_SES_THROTTLED
    assert row.message_id_header.startswith("<") and "amazonses" not in row.message_id_header


def test_unreachable_ses_in_the_suite_is_a_failed_row(db, world, monkeypatch):
    # No fake injected: the conftest guard refuses to build a real client.
    mail_on(monkeypatch)
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert row.status == MailMessageStatus.FAILED and row.error == ERR_SES_UNREACHABLE


def test_mail_transport_smtp_uses_zoho_smtp_and_no_ses(db, world, ses, fake_smtp, monkeypatch):
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_TRANSPORT", "smtp")
    assert isinstance(get_system_transport(), SmtpImapTransport)
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert row.status == MailMessageStatus.SENT
    (conn,) = fake_smtp.instances
    assert conn.host == settings.RADAL_MAIL_SMTP_HOST and conn.sent[0][1] == settings.RADAL_MAIL_ADDRESS
    assert row.message_id_header == conn.sent[0][0]["Message-ID"] and "amazonses" not in row.message_id_header
    assert ses.built == [] and ses.sent == []


def test_a_broker_custom_account_stays_on_its_own_smtp(db, world, ses, fake_smtp, monkeypatch):
    mail_on(monkeypatch)
    guarded: list[tuple] = []  # the SSRF guard still runs (no DNS in the suite)
    monkeypatch.setattr(transport_module, "check_mail_host",
                        lambda host, port, *, kind, resolve: guarded.append((host, port, kind, resolve)))
    assert settings.MAIL_TRANSPORT == "ses"
    account = make_mail_account(db, world.a)
    db.add(CollectionSettings(broker_id=world.a.broker_id, default_sender=MailSender.BROKER_ACCOUNT,
                              sender_mail_account_id=account.id))
    db.commit()
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t",
                    account="auto")
    assert row.status == MailMessageStatus.SENT and row.mail_account_id == account.id
    (conn,) = fake_smtp.instances
    assert conn.host == "smtp.alfa.test" and conn.sent[0][1] == "cobranza@alfa.cl"
    assert guarded == [("smtp.alfa.test", 465, "smtp", True)]
    assert ses.built == [] and ses.sent == []


# =============================================================================
# the sent copy (Zoho "Enviados")
# =============================================================================

def test_sent_copy_appends_the_exact_raw_message_seen(db, world, ses, fake_imap, zoho_imap_creds, monkeypatch):
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_SENT_COPY", "imap_append")
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="Copia", text="t")
    assert row.status == MailMessageStatus.SENT and row.message_id_header == SES_HEADER
    (mailbox, flags, date_time, appended), = fake_imap.appends
    assert mailbox == '"Sent"' and flags == r"(\Seen)" and date_time.startswith('"')
    assert fake_imap.logins == [("imap.zoho.com", "contacto@radalseguros.cl")]
    copy = message_from_bytes(appended, policy=policy.default)
    assert copy["Message-ID"] == SES_HEADER
    # Byte-identical to what SES received, except the Message-ID SES rewrote.
    original_id = message_from_bytes(ses.raw, policy=policy.default)["Message-ID"]
    assert appended == ses.raw.replace(original_id.encode(), SES_HEADER.encode())


def test_sent_copy_failure_never_fails_the_send(db, world, ses, fake_imap, zoho_imap_creds, monkeypatch, caplog):
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_SENT_COPY", "imap_append")
    fake_imap.fail_login = True
    with caplog.at_level("WARNING"):
        row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert row.status == MailMessageStatus.SENT and row.message_id_header == SES_HEADER
    assert fake_imap.appends == []
    assert "sent copy" in caplog.text and "zoho-app-pw-123" not in caplog.text


def test_sent_copy_is_bounded_in_time(fake_imap):
    fake_imap.append_delay = 1.5
    cfg = ImapConfig(host="imap.zoho.com", port=993, security="ssl", username="u", password="p", timeout=1)
    started = time.monotonic()
    assert append_sent_copy(cfg, b"Subject: x\r\n\r\nb", "Sent", timeout=0.2) is False
    assert time.monotonic() - started < 1.0


def test_sent_copy_off_or_without_credentials_does_nothing(db, world, ses, fake_imap, zoho_imap_creds, monkeypatch):
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_SENT_COPY", "none")
    send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert fake_imap.appends == [] and fake_imap.logins == []
    monkeypatch.setattr(settings, "MAIL_SENT_COPY", "imap_append")
    monkeypatch.setattr(settings, "RADAL_MAIL_PASSWORD", "")
    assert get_system_transport().sent_copy is None  # type: ignore[union-attr]


def test_raw_message_bytes_quotes_and_crlf():
    raw = raw_message_bytes(_msg())
    assert raw.count(b"\r\n") > 5 and b"\r\r" not in raw
    assert transport_module._quote_mailbox('Elementos "enviados"') == '"Elementos \\"enviados\\""'


# =============================================================================
# check() and the preflight never raise
# =============================================================================

def test_ses_check_reports_sandbox_identity_and_never_raises(fake_imap):
    fake = FakeSes()
    result = SesTransport(client=fake).check(radal_smtp_config(), None)
    assert result.transport == "ses" and result.smtp_ok is True and result.imap_ok is None and result.ok
    assert result.details["account"]["sandbox"] is True
    assert result.details["identity_status"]["dkim_status"] == "SUCCESS"
    assert result.details["identity_status"]["mail_from_domain"] == "mail.radalseguros.cl"
    assert any("sandbox" in w for w in result.details["warnings"])

    fake.account["SendingEnabled"] = False
    assert SesTransport(client=fake).check(radal_smtp_config(), None).smtp_error == ERR_SES_SENDING_DISABLED
    fake.account["SendingEnabled"] = True
    fake.identities["radalseguros.cl"]["VerifiedForSendingStatus"] = False
    assert SesTransport(client=fake).check(radal_smtp_config(), None).smtp_error == ERR_SES_IDENTITY
    fake.identities.clear()
    assert SesTransport(client=fake).check(radal_smtp_config(), None).smtp_error == ERR_SES_IDENTITY

    fake.account_error = _client_error("AccessDeniedException", "secret-ish arn", "GetAccount")
    denied = SesTransport(client=fake).check(radal_smtp_config(), None)
    assert denied.smtp_ok is False and denied.smtp_error == ERR_SES_AUTH and not denied.ok

    fake.account_error = RuntimeError("boom")
    assert SesTransport(client=fake).check(radal_smtp_config(), None).smtp_ok is False

    # IMAP part: a refused login is a category without the password.
    fake_imap.fail_login = True
    imap = ImapConfig(host="imap.zoho.com", port=993, security="ssl", username="contacto@radalseguros.cl",
                      password="zoho-app-pw-123")
    both = SesTransport(client=FakeSes()).check(radal_smtp_config(), imap)
    assert both.smtp_ok is True and both.imap_ok is False and "zoho-app-pw-123" not in both.imap_error


def test_ses_check_with_the_refusing_factory_never_raises():
    result = SesTransport().check(SmtpConfig(host="", port=0, security="ssl", username="", password="",
                                             from_address="contacto@radalseguros.cl"), None)
    assert result.smtp_ok is False and result.smtp_error == ERR_SES_UNREACHABLE


def test_preflight_ready_sandbox_without_imap_is_exit_0():
    lines: list[str] = []
    code, report = mail_preflight.run([], transport=SesTransport(client=FakeSes()), out=lines.append)
    text = "\n".join(lines)
    assert code == mail_preflight.EXIT_OK and report["send"]["ok"] is True
    assert "SANDBOX" in text and "mail.radalseguros.cl" in text and "IMAP no configurado" in text
    assert report["imap"]["configured"] is False


def test_preflight_ses_not_ready_is_exit_3_and_never_raises():
    fake = FakeSes()
    fake.account["SendingEnabled"] = False
    code, _ = mail_preflight.run([], transport=SesTransport(client=fake), out=lambda _l: None)
    assert code == mail_preflight.EXIT_SEND_NOT_READY
    fake = FakeSes()
    fake.account_error = RuntimeError("network down")
    code, report = mail_preflight.run(["--send-test", "yo@radalseguros.cl"],
                                      transport=SesTransport(client=fake), out=lambda _l: None)
    assert code == mail_preflight.EXIT_SEND_NOT_READY and fake.sent == []
    assert report["send_test"]["sent"] is False


def test_preflight_imap_failure_is_exit_4_and_hides_the_password(fake_imap, zoho_imap_creds):
    fake_imap.fail_login = True
    lines: list[str] = []
    code, report = mail_preflight.run(["--json"], transport=SesTransport(client=FakeSes()), out=lines.append)
    assert code == mail_preflight.EXIT_IMAP_FAILED
    assert report["imap"] == {"configured": True, "host": "imap.zoho.com", "user": "contacto@radalseguros.cl",
                              "ok": False, "error": transport_module.ERR_AUTH}
    assert "zoho-app-pw-123" not in "\n".join(lines)


def test_preflight_send_test_goes_through_ses_and_the_sent_copy(fake_imap, zoho_imap_creds, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_SENT_COPY", "imap_append")
    fake = FakeSes()
    transport = SesTransport(client=fake, configuration_set="",
                             sent_copy=transport_module.radal_imap_config())
    code, report = mail_preflight.run(["--send-test", "yo@radalseguros.cl"], transport=transport,
                                      out=lambda _l: None)
    assert code == mail_preflight.EXIT_OK
    assert report["send_test"] == {"sent": True, "message_id": SES_HEADER, "sent_copy_ok": True}
    sent = message_from_bytes(fake.raw, policy=policy.default)
    assert sent["Subject"] == "[Radal] prueba de correo" and sent["To"] == "yo@radalseguros.cl"
    assert len(fake_imap.appends) == 1


def test_preflight_send_test_failure_is_exit_3():
    fake = FakeSes()
    fake.fail = _client_error("MessageRejected", "Email address is not verified. The following identities "
                                                 "failed the check in region US-EAST-1: yo@gmail.com")
    code, report = mail_preflight.run(["--send-test", "yo@gmail.com"], transport=SesTransport(client=fake),
                                      out=lambda _l: None)
    assert code == mail_preflight.EXIT_SEND_NOT_READY and report["send_test"]["error"] == ERR_SES_SANDBOX


# =============================================================================
# GET /mail/accounts — the Radal system entry says how it sends
# =============================================================================

def test_radal_entry_carries_the_transport(client, headers_a, monkeypatch):
    body = client.get(f"{API}/mail/accounts", headers=headers_a).json()
    radal = body["items"][0]
    assert radal["kind"] == "radal_system" and radal["transport"] == "ses"
    assert radal["transport_label"] == "Amazon SES · respuestas a contacto@radalseguros.cl (Zoho)"
    monkeypatch.setattr(settings, "MAIL_TRANSPORT", "smtp")
    radal = client.get(f"{API}/mail/accounts", headers=headers_a).json()["items"][0]
    assert radal["transport"] == "smtp" and "Zoho" in radal["transport_label"]
