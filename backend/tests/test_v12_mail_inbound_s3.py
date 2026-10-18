"""v12 mail transition — Bcc copy, reply address, SES inbound via S3, forward, dev allowlist (tech-ref §19.7).

Hermetic: SES is the ``FakeSes`` client of ``test_v12_mail_ses`` (behind the
real factory), S3 is :class:`FakeS3` injected through
``s3_inbound._OVERRIDE``; the autouse conftest guard refuses to build a real
``sesv2`` or ``s3`` client, and AI is a fake ``tool_call``.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.jobs import run_job
from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    EvidenceStatus,
    InquiryStatus,
    JobStatus,
    MailAccountStatus,
    MailDirection,
    MailMessageStatus,
    MailSender,
)
from app.models.mail import MailMessage, PaymentEvidence
from app.services.mail import inbound as inbound_module
from app.services.mail import s3_inbound
from app.services.mail.inbound import ensure_system_account
from app.services.mail.sender import REASON_NOT_ALLOWLISTED, send_mail
from scripts import mail_preflight
from tests.conftest import API
from tests.fakes_v12 import build_message, make_insurer_profile
from tests.test_v12_mail import NOW, _inquiry, fake_ai, mail_on, make_mail_account, policy_with_payer
from tests.test_v12_mail_ses import SES_HEADER, FakeSes, FakeSmtp, fake_smtp, ses  # noqa: F401 (fixtures)

COPY = "contacto@radalseguros.cl"
REPLY = "respuestas@correo.radalseguros.cl"
BUCKET = settings.S3_BUCKET
PREFIX = "inbound-mail/"


# =============================================================================
# fakes
# =============================================================================

class FakeS3:
    """The slice of a boto3 ``s3`` client the inbound poller uses."""

    def __init__(self, page_size: int = 1000) -> None:
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.page_size = page_size
        self.calls: list[tuple[str, dict]] = []
        self.fail_list: Exception | None = None
        self.fail_copy: Exception | None = None
        self.fail_get: set[str] = set()
        self._tick = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)

    def put(self, key: str, data: bytes | str) -> str:
        self._tick += timedelta(seconds=1)
        self.objects[key] = (data.encode() if isinstance(data, str) else data, self._tick)
        return key

    def list_objects_v2(self, **kw):  # noqa: ANN003, ANN201
        self.calls.append(("list", kw))
        if self.fail_list is not None:
            raise self.fail_list
        prefix, delimiter = kw.get("Prefix", ""), kw.get("Delimiter")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        if delimiter:
            keys = [k for k in keys if delimiter not in k[len(prefix):]]
        size = min(self.page_size, int(kw.get("MaxKeys") or 1000))
        start = int(kw.get("ContinuationToken") or 0)
        page = keys[start:start + size]
        truncated = start + size < len(keys)
        response = {
            "KeyCount": len(page),
            "IsTruncated": truncated,
            "Contents": [{"Key": k, "LastModified": self.objects[k][1], "Size": len(self.objects[k][0])}
                         for k in page],
        }
        if truncated:
            response["NextContinuationToken"] = str(start + size)
        return response

    def get_object(self, Bucket, Key):  # noqa: ANN001, ANN201, N803
        self.calls.append(("get", {"Bucket": Bucket, "Key": Key}))
        assert Bucket == BUCKET
        if Key in self.fail_get:
            raise ConnectionError("s3 read failed")
        return {"Body": io.BytesIO(self.objects[Key][0])}

    def copy_object(self, Bucket, Key, CopySource):  # noqa: ANN001, ANN201, N803
        self.calls.append(("copy", {"Bucket": Bucket, "Key": Key, "CopySource": CopySource}))
        if self.fail_copy is not None:
            exc, self.fail_copy = self.fail_copy, None
            raise exc
        self.objects[Key] = self.objects[CopySource["Key"]]
        return {}

    def delete_object(self, Bucket, Key):  # noqa: ANN001, ANN201, N803
        self.calls.append(("delete", {"Bucket": Bucket, "Key": Key}))
        self.objects.pop(Key, None)
        return {}

    def pending(self) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(PREFIX) and "/" not in k[len(PREFIX):])

    def processed(self) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(PREFIX + "processed/"))


@pytest.fixture()
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(s3_inbound, "_OVERRIDE", fake)
    return fake


@pytest.fixture()
def inbound_s3(monkeypatch, ses):  # noqa: F811 - the ses fixture is imported
    """Mail on, system box read from S3, replies to respuestas@, SES faked."""
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_INBOUND_SOURCE", "s3")
    monkeypatch.setattr(settings, "RADAL_MAIL_REPLY_TO", REPLY)
    return ses


def _ses_raw(*, sender: str, subject: str, body: str, in_reply_to: str | None = None,
             extra: dict[str, str] | None = None) -> bytes:
    """A message as SES inbound writes it: SES headers on top, CRLF."""
    msg = build_message(from_address=sender, to=REPLY, subject=subject, body=body, in_reply_to=in_reply_to)
    msg["Date"] = "Tue, 10 Mar 2026 14:55:00 +0000"
    for name, value in (extra or {}).items():
        msg[name] = value
    head = (
        "Return-Path: <bounce@hdi.cl>\r\n"
        "Received: from mail.hdi.cl by inbound-smtp.us-east-1.amazonaws.com with SMTP id abc123\r\n"
        " for respuestas@correo.radalseguros.cl; Tue, 10 Mar 2026 14:55:01 +0000 (UTC)\r\n"
        "X-SES-Spam-Verdict: PASS\r\n"
        "X-SES-Virus-Verdict: PASS\r\n"
    ).encode()
    return head + msg.as_bytes(policy=policy.default.clone(linesep="\r\n"))


def _forwards(db) -> list[MailMessage]:
    return list(db.scalars(
        select(MailMessage).where(MailMessage.direction == MailDirection.OUTBOUND,
                                  MailMessage.provider_uid.like("fwd:%")).order_by(MailMessage.id)
    ))


def _inbound(db) -> list[MailMessage]:
    return list(db.scalars(
        select(MailMessage).where(MailMessage.direction == MailDirection.INBOUND).order_by(MailMessage.id)
    ))


def _attached_original(raw: bytes) -> bytes:
    sent = message_from_bytes(raw, policy=policy.default)
    (part,) = [p for p in sent.walk() if p.get_content_type() == "message/rfc822"]
    assert part.get_filename() == "mensaje-original.eml"
    return part.get_payload(0).as_bytes()


# =============================================================================
# 1. the visual copy by Bcc
# =============================================================================

def test_system_mail_bccs_the_copy_address_only_in_the_envelope(db, world, ses, monkeypatch):  # noqa: F811
    mail_on(monkeypatch)
    row = send_mail(db, broker_id=world.a.broker_id, kind="payer_reminder", to="pagador@cliente.cl",
                    cc=["otro@cliente.cl"], subject="Recordatorio", text="cuerpo")
    assert row.status == MailMessageStatus.SENT
    (request,) = ses.sent
    assert request["Destination"] == {"ToAddresses": ["pagador@cliente.cl"], "CcAddresses": ["otro@cliente.cl"],
                                      "BccAddresses": [COPY]}
    raw = request["Content"]["Raw"]["Data"]
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert b"\r\nBcc:" not in head and not head.startswith(b"Bcc:")
    sent = message_from_bytes(raw, policy=policy.default)
    assert "Bcc" not in sent and COPY not in str(sent["To"]) and COPY not in str(sent["Cc"])
    # The row records the visible recipients only.
    assert row.to_addresses == ["pagador@cliente.cl"] and row.cc_addresses == ["otro@cliente.cl"]


def test_no_bcc_when_already_a_recipient_or_mode_is_not_bcc(db, world, ses, monkeypatch):  # noqa: F811
    mail_on(monkeypatch)
    send_mail(db, broker_id=None, kind="ops", to=["Contacto@RadalSeguros.cl"], subject="s", text="t")
    assert "BccAddresses" not in ses.sent[-1]["Destination"]
    for mode in ("none", "imap_append"):
        monkeypatch.setattr(settings, "MAIL_SENT_COPY", mode)
        send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
        assert "BccAddresses" not in ses.sent[-1]["Destination"]
    monkeypatch.setattr(settings, "MAIL_SENT_COPY", "bcc")
    monkeypatch.setattr(settings, "MAIL_COPY_ADDRESS", "")
    send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t")
    assert "BccAddresses" not in ses.sent[-1]["Destination"]


def test_a_broker_mailbox_never_gets_the_bcc(db, world, ses, fake_smtp, monkeypatch):  # noqa: F811
    mail_on(monkeypatch)
    monkeypatch.setattr("app.services.mail.transport.check_mail_host", lambda *a, **k: None)
    account = make_mail_account(db, world.a)
    db.add(CollectionSettings(broker_id=world.a.broker_id, default_sender=MailSender.BROKER_ACCOUNT,
                              sender_mail_account_id=account.id))
    db.commit()
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@y.cl"], subject="s", text="t",
                    account="auto")
    assert row.status == MailMessageStatus.SENT and row.mail_account_id == account.id
    (conn,) = fake_smtp.instances
    msg, _from = conn.sent[0]
    assert "Bcc" not in msg and ses.sent == []


# =============================================================================
# 2. the reply address
# =============================================================================

def test_inquiries_reply_to_the_inbound_address_payer_mail_to_the_broker(
    client, db, headers_a, world, ses, monkeypatch  # noqa: F811
):
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "RADAL_MAIL_REPLY_TO", REPLY)
    world.a.broker.email = "contacto@alfa.cl"
    policy_row, plan = policy_with_payer(db, world)
    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.cl")
    db.commit()
    resp = client.post(f"{API}/inquiries", json={"policy_id": policy_row.id,
                                                 "installment_ids": [plan.installments[0].id]}, headers=headers_a)
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    sent = message_from_bytes(ses.raw, policy=policy.default)
    assert sent["Reply-To"] == REPLY and f"[RADAL-{token}]" in sent["Subject"]
    assert ses.sent[-1]["Destination"]["BccAddresses"] == [COPY]

    again = client.post(f"{API}/inquiries/{resp.json()['id']}/resend", json={}, headers=headers_a)
    assert again.status_code == 200, again.text
    resent = message_from_bytes(ses.raw, policy=policy.default)
    assert resent["Reply-To"] == REPLY and f"[RADAL-{token}]" in resent["Subject"]

    send_mail(db, broker_id=world.a.broker_id, kind="payer_reminder", to=["pagador@cliente.cl"],
              subject="Recordatorio", text="t")
    assert message_from_bytes(ses.raw, policy=policy.default)["Reply-To"] == "contacto@alfa.cl"


def test_blank_reply_address_keeps_the_radal_address(db, world, ses, monkeypatch):  # noqa: F811
    from app.services.mail.sender import radal_reply_address
    from app.services.mail.transport import system_transport_label

    assert settings.RADAL_MAIL_REPLY_TO == "" and radal_reply_address() == settings.RADAL_MAIL_ADDRESS
    assert system_transport_label() == "Amazon SES · respuestas a contacto@radalseguros.cl (Zoho)"
    monkeypatch.setattr(settings, "RADAL_MAIL_REPLY_TO", REPLY)
    assert radal_reply_address() == REPLY and REPLY in system_transport_label()


# =============================================================================
# 3 + 4. inbound from S3, forward to the human mailbox
# =============================================================================

def test_s3_token_reply_goes_through_the_pipeline_moves_and_forwards_once(db, world, s3, inbound_s3, monkeypatch):
    policy_row, plan = policy_with_payer(db, world)
    inquiry = _inquiry(db, world, plan=plan, policy=policy_row)
    fake_ai(monkeypatch, {
        "classification": "insurer_status_reply", "confidence": 90, "reported_status": "paid",
        "installment_numbers": [1], "amount": float(plan.installments[0].gross_amount_uf), "currency": "UF",
    })
    raw = _ses_raw(sender="Cobranza HDI <cobranza@hdi.cl>",
                   subject="RE: [RADAL-INQ-ABCDEF] Consulta de estado de pago",
                   body="Estimados, la cuota 1 se encuentra pagada.")
    key = s3.put(PREFIX + "0n9pv0gq0hv9t6l4ef45r7cgq9qdqvb8vdhrh0g1", raw)

    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["system_source"] == "s3" and run.stats["kept"] == 1 and run.stats["moved"] == 1
    assert run.stats["forwarded"] == 1 and run.stats["errors"] == 0

    (message,) = _inbound(db)
    system = ensure_system_account(db)
    assert message.mail_account_id == system.id and message.provider_uid == key
    assert message.status == MailMessageStatus.PROCESSED and message.thread_token == "INQ-ABCDEF"
    assert message.document_id is not None and message.extraction_id is not None
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.status_inquiry_id == inquiry.id and ev.status == EvidenceStatus.SUGGESTED
    db.refresh(inquiry)
    assert inquiry.status == InquiryStatus.REPLIED

    # Moved: copy to processed/<tail>, then delete.
    assert s3.pending() == [] and s3.processed() == [PREFIX + "processed/0n9pv0gq0hv9t6l4ef45r7cgq9qdqvb8vdhrh0g1"]
    assert [c[0] for c in s3.calls if c[0] in ("copy", "delete")] == ["copy", "delete"]

    # Forwarded once, through SES, as Radal, to the human mailbox, original attached.
    (forward,) = _forwards(db)
    assert forward.status == MailMessageStatus.SENT and forward.broker_id is None
    assert forward.to_addresses == [COPY] and forward.provider_uid == "fwd:" + key
    assert forward.subject == "Fwd: RE: [RADAL-INQ-ABCDEF] Consulta de estado de pago"
    (request,) = inbound_s3.sent
    assert request["FromEmailAddress"] == COPY and request["Destination"] == {"ToAddresses": [COPY]}
    sent = message_from_bytes(request["Content"]["Raw"]["Data"], policy=policy.default)
    assert sent["From"].addresses[0].display_name == "Radal" and sent["X-Radal-Kind"] == "inbound_forward"
    assert sent["Reply-To"] == "cobranza@hdi.cl"
    text = sent.get_body(preferencelist=("plain",)).get_content()
    assert f"Recibido en {REPLY} y procesado por Radal" in text
    assert "De: Cobranza HDI <cobranza@hdi.cl>" in text and "Fecha: Tue, 10 Mar 2026" in text
    assert "RADAL-INQ-ABCDEF" in text
    attached = _attached_original(request["Content"]["Raw"]["Data"])
    assert b"la cuota 1 se encuentra pagada" in attached and b"X-SES-Virus-Verdict: PASS" in attached
    assert raw.split(b"\r\n\r\n", 1)[1].strip() in request["Content"]["Raw"]["Data"]  # byte-for-byte body

    # A rerun (even if the object came back) never reprocesses nor re-forwards.
    s3.put(key, raw)
    again = run_job(db, "mail.poll", NOW + timedelta(minutes=1))
    assert again.stats["kept"] == 0 and again.stats["forwarded"] == 0 and again.stats["moved"] == 1
    assert len(_inbound(db)) == 1 and len(_forwards(db)) == 1 and len(inbound_s3.sent) == 1
    assert len(db.scalars(select(PaymentEvidence)).all()) == 1


def test_s3_move_failure_is_retried_without_reprocessing(db, world, s3, inbound_s3, monkeypatch):
    policy_row, plan = policy_with_payer(db, world)
    _inquiry(db, world, plan=plan, policy=policy_row)
    fake_ai(monkeypatch, {"classification": "insurer_status_reply", "reported_status": "paid", "confidence": 80})
    key = s3.put(PREFIX + "msg-1", _ses_raw(sender="cobranza@hdi.cl", subject="[RADAL-INQ-ABCDEF] ok", body="pagada"))
    s3.fail_copy = ConnectionError("copy refused")
    first = run_job(db, "mail.poll", NOW)
    assert first.status == JobStatus.SUCCEEDED and first.stats["errors"] == 1 and first.stats["moved"] == 0
    assert s3.pending() == [key] and len(_inbound(db)) == 1 and len(_forwards(db)) == 1
    second = run_job(db, "mail.poll", NOW + timedelta(minutes=1))
    assert second.stats["moved"] == 1 and second.stats["errors"] == 0
    assert s3.pending() == [] and len(_inbound(db)) == 1 and len(_forwards(db)) == 1
    assert len(inbound_s3.sent) == 1


def test_s3_processing_failure_leaves_the_object_and_never_raises(db, world, s3, inbound_s3, monkeypatch):
    policy_row, plan = policy_with_payer(db, world)
    _inquiry(db, world, plan=plan, policy=policy_row)
    fake_ai(monkeypatch, {"classification": "insurer_status_reply", "reported_status": "paid", "confidence": 80})
    bad = s3.put(PREFIX + "msg-bad", _ses_raw(sender="cobranza@hdi.cl", subject="[RADAL-INQ-ABCDEF] x", body="y"))
    unreadable = s3.put(PREFIX + "msg-unreadable", _ses_raw(sender="a@b.cl", subject="hola", body="z"))
    s3.fail_get.add(unreadable)
    real = inbound_module.process_message

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(inbound_module, "process_message", _boom)
    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SUCCEEDED and run.stats["errors"] == 2 and run.stats["moved"] == 0
    assert s3.pending() == [bad, unreadable]
    assert _inbound(db) == [] and _forwards(db) == [] and inbound_s3.sent == []
    system = ensure_system_account(db)
    assert system.status == MailAccountStatus.CONNECTED and system.last_error

    monkeypatch.setattr(inbound_module, "process_message", real)
    s3.fail_get.clear()
    retry = run_job(db, "mail.poll", NOW + timedelta(minutes=1))
    assert retry.stats["errors"] == 0 and retry.stats["moved"] == 2
    assert s3.pending() == [] and len(_inbound(db)) == 2 and len(_forwards(db)) == 2


def test_s3_setup_notification_and_non_mail_are_archived_never_forwarded(db, world, s3, inbound_s3):
    # Mail-shaped on purpose: the KEY is what marks it (SES writes it when the rule is created).
    setup = s3.put(PREFIX + "AMAZON_SES_SETUP_NOTIFICATION",
                   "From: no-reply@amazonses.com\r\nDate: Tue, 10 Mar 2026 14:00:00 +0000\r\n"
                   "Message-ID: <setup@amazonses.com>\r\nSubject: Amazon SES Setup Notification\r\n\r\n"
                   "This is an email from Amazon SES confirming the bucket.")
    junk = s3.put(PREFIX + "garbage", b"\x00\x01not a mail at all")
    empty = s3.put(PREFIX + "empty", b"")
    virus = s3.put(PREFIX + "virus", _ses_raw(sender="x@evil.test", subject="factura", body="abre").replace(
        b"X-SES-Virus-Verdict: PASS", b"X-SES-Virus-Verdict: FAIL"))
    no_date = s3.put(PREFIX + "no-date", b"From: a@b.cl\r\nSubject: x\r\n\r\nsin fecha ni id")
    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["not_mail"] == 5 and run.stats["errors"] == 0 and run.stats["moved"] == 5
    assert s3.pending() == [] and len(s3.processed()) == 5
    assert PREFIX + "processed/AMAZON_SES_SETUP_NOTIFICATION" in s3.objects
    rows = _inbound(db)
    assert {r.provider_uid for r in rows} == {setup, junk, empty, virus, no_date}
    assert {r.error for r in rows} == {s3_inbound.ERROR_NOT_MAIL, s3_inbound.ERROR_VIRUS}
    assert all(r.status == MailMessageStatus.IGNORED and r.subject is None and r.from_address is None
               and r.body_text is None and r.document_id is None for r in rows)
    assert _forwards(db) == [] and inbound_s3.sent == []


def test_s3_unrelated_mail_is_ignored_but_forwarded_with_a_redacted_row(db, world, s3, inbound_s3):
    raw = _ses_raw(sender="Amigo <amigo@gmail.com>", subject="Fotos del asado", body="Mira las fotos")
    key = s3.put(PREFIX + "unrelated", raw)
    run = run_job(db, "mail.poll", NOW)
    assert run.stats["ignored"] == 1 and run.stats["forwarded"] == 1
    (row,) = _inbound(db)
    assert row.status == MailMessageStatus.IGNORED and row.subject is None and row.body_text is None
    (forward,) = _forwards(db)
    assert forward.status == MailMessageStatus.SENT
    assert "asado" not in (forward.subject or "") and not forward.body_text
    sent = message_from_bytes(inbound_s3.raw, policy=policy.default)
    assert sent["Subject"] == "Fwd: Fotos del asado"
    assert "no se guardó en Radal" in sent.get_body(preferencelist=("plain",)).get_content()
    assert b"Mira las fotos" in _attached_original(inbound_s3.raw)
    assert s3.processed() == [PREFIX + "processed/unrelated"] and key not in s3.objects


def test_s3_radal_own_mail_is_not_forwarded(db, world, s3, inbound_s3):
    s3.put(PREFIX + "loop", _ses_raw(sender=COPY, subject="Fwd: algo", body="x",
                                     extra={"X-Radal-Kind": "inbound_forward"}))
    run = run_job(db, "mail.poll", NOW)
    assert run.stats["moved"] == 1 and run.stats["forwarded"] == 0
    assert _forwards(db) == [] and inbound_s3.sent == []


def test_s3_failed_forward_keeps_the_object_and_is_retried(db, world, s3, inbound_s3):
    key = s3.put(PREFIX + "fwd-retry", _ses_raw(sender="amigo@gmail.com", subject="hola", body="x"))
    inbound_s3.fail = ConnectionError("ses down")
    first = run_job(db, "mail.poll", NOW)
    assert first.stats["errors"] == 1 and first.stats["moved"] == 0 and s3.pending() == [key]
    assert [f.status for f in _forwards(db)] == [MailMessageStatus.FAILED]
    inbound_s3.fail = None
    second = run_job(db, "mail.poll", NOW + timedelta(minutes=1))
    assert second.stats["forwarded"] == 1 and second.stats["moved"] == 1 and s3.pending() == []
    assert [f.status for f in _forwards(db)] == [MailMessageStatus.FAILED, MailMessageStatus.SENT]
    assert len(_inbound(db)) == 1


def test_s3_listing_paginates_ignores_processed_and_caps_per_run(db, world, s3, inbound_s3, monkeypatch):
    s3.page_size = 2
    monkeypatch.setattr(settings, "MAIL_INBOUND_S3_MAX_PER_RUN", 3)
    s3.put(PREFIX + "processed/old", _ses_raw(sender="a@b.cl", subject="viejo", body="x"))
    s3.put("other-prefix/x", b"From: a@b.cl\r\n\r\nx")
    keys = [s3.put(PREFIX + f"m{i}", _ses_raw(sender="a@b.cl", subject=f"s{i}", body="x")) for i in range(5)]
    listed = s3_inbound.list_pending(s3, BUCKET, PREFIX, limit=50)
    assert [o["Key"] for o in listed] == keys  # oldest first, across pages, no processed/
    assert sum(1 for c in s3.calls if c[0] == "list") == 3
    run = run_job(db, "mail.poll", NOW)
    assert run.stats["fetched"] == 3 and s3.pending() == keys[3:]
    assert PREFIX + "processed/old" in s3.objects and "other-prefix/x" in s3.objects


def test_s3_unlistable_bucket_marks_the_system_box_and_alerts_once(db, world, s3, inbound_s3, monkeypatch):
    monkeypatch.setattr(settings, "RADAL_OPS_EMAIL", "ops@radalseguros.cl")
    s3.fail_list = ConnectionError("AccessDenied")
    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SUCCEEDED and run.stats["errors"] == 1
    system = ensure_system_account(db)
    assert system.status == MailAccountStatus.ERROR and system.last_error == s3_inbound.REASON_S3_UNREADABLE
    run_job(db, "mail.poll", NOW + timedelta(minutes=1))
    ops = [m for m in db.scalars(select(MailMessage).where(MailMessage.direction == MailDirection.OUTBOUND))
           if (m.subject or "").startswith("[Radal ops]")]
    assert len(ops) == 1
    s3.fail_list = None
    run_job(db, "mail.poll", NOW + timedelta(minutes=2))
    db.refresh(system)
    assert system.status == MailAccountStatus.CONNECTED and system.last_error is None


def test_inbound_source_none_reads_no_system_box(db, world, s3, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_INBOUND_SOURCE", "auto")
    monkeypatch.setattr(settings, "RADAL_MAIL_PASSWORD", "")
    s3.put(PREFIX + "waiting", _ses_raw(sender="a@b.cl", subject="x", body="y"))
    run = run_job(db, "mail.poll", NOW)
    assert run.stats["system_source"] == "none" and run.stats["accounts"] == 0
    assert fake_transport.fetches == [] and s3.calls == [] and s3.pending() == [PREFIX + "waiting"]


# =============================================================================
# 5. the dev allowlist
# =============================================================================

def test_allowlist_drops_outsiders_and_skips_when_nobody_is_left(db, world, ses, monkeypatch):  # noqa: F811
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_RECIPIENT_ALLOWLIST", "radalseguros.cl, @nirvana.test ,ben@gmail.com")
    row = send_mail(db, broker_id=world.a.broker_id, kind="payer_reminder",
                    to=["pagador@cliente.cl", "equipo@radalseguros.cl"], cc=["ben@gmail.com", "otro@gmail.com"],
                    subject="s", text="t")
    assert row.status == MailMessageStatus.SENT and row.error is None
    assert row.to_addresses == ["equipo@radalseguros.cl"] and row.cc_addresses == ["ben@gmail.com"]
    assert ses.sent[-1]["Destination"] == {"ToAddresses": ["equipo@radalseguros.cl"],
                                           "CcAddresses": ["ben@gmail.com"], "BccAddresses": [COPY]}

    skipped = send_mail(db, broker_id=world.a.broker_id, kind="payer_reminder", to=["pagador@cliente.cl"],
                        subject="s", text="t")
    assert skipped.status == MailMessageStatus.SKIPPED and skipped.error == REASON_NOT_ALLOWLISTED
    assert skipped.error == "Destinatario fuera de la lista permitida de este ambiente"
    assert skipped.to_addresses == ["pagador@cliente.cl"] and len(ses.sent) == 1  # nothing (not even the Bcc) left

    # The copy address always passes (Bcc copy + forward), even outside the list.
    monkeypatch.setattr(settings, "MAIL_RECIPIENT_ALLOWLIST", "nirvana.test")
    ok = send_mail(db, broker_id=None, kind="ops", to=[COPY], subject="s", text="t")
    assert ok.status == MailMessageStatus.SENT


def test_allowlist_applies_to_broker_mailboxes_too(db, world, ses, fake_smtp, monkeypatch):  # noqa: F811
    mail_on(monkeypatch)
    monkeypatch.setattr(settings, "MAIL_RECIPIENT_ALLOWLIST", "radalseguros.cl")
    monkeypatch.setattr("app.services.mail.transport.check_mail_host", lambda *a, **k: None)
    account = make_mail_account(db, world.a)
    db.commit()
    row = send_mail(db, broker_id=world.a.broker_id, kind="test", to=["x@cliente.cl"], subject="s", text="t",
                    account=account)
    assert row.status == MailMessageStatus.SKIPPED and row.error == REASON_NOT_ALLOWLISTED
    assert fake_smtp.instances == []


def test_allowlist_never_blocks_the_inbound_forward(db, world, s3, inbound_s3, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_RECIPIENT_ALLOWLIST", "nirvana.test")
    s3.put(PREFIX + "a", _ses_raw(sender="amigo@gmail.com", subject="hola", body="x"))
    assert run_job(db, "mail.poll", NOW).stats["forwarded"] == 1


# =============================================================================
# 6. preflight
# =============================================================================

def test_preflight_reports_copy_reply_inbound_and_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_INBOUND_SOURCE", "s3")
    monkeypatch.setattr(settings, "RADAL_MAIL_REPLY_TO", REPLY)
    monkeypatch.setattr(settings, "MAIL_RECIPIENT_ALLOWLIST", "radalseguros.cl")
    fake = FakeS3()
    fake.put(PREFIX + "AMAZON_SES_SETUP_NOTIFICATION", b"x")
    fake.put(PREFIX + "abc", b"From: a@b.cl\r\n\r\nx")
    fake.put(PREFIX + "processed/old", b"x")
    lines: list[str] = []
    code, report = mail_preflight.run([], transport=_ses_transport(), s3_client=fake, out=lines.append)
    text = "\n".join(lines)
    assert code == mail_preflight.EXIT_OK, text
    cfg = report["config"]
    assert cfg["sent_copy"] == "bcc" and cfg["copy_address"] == COPY
    assert cfg["reply_to"] == REPLY and cfg["effective_reply_to"] == REPLY
    assert cfg["inbound_source"] == "s3" and cfg["inbound_s3"] == f"s3://{BUCKET}/{PREFIX}"
    assert cfg["allowlist"] == ["radalseguros.cl"]
    assert report["inbound"] == {"source": "s3", "bucket": BUCKET, "prefix": PREFIX, "ok": True, "pending": 2}
    assert ("list", {"Bucket": BUCKET, "Prefix": PREFIX, "Delimiter": "/", "MaxKeys": 1000}) in fake.calls
    assert report["imap"]["needed"] is False and "IMAP: no se usa" in text
    assert "MAIL_COPY_ADDRESS=contacto@radalseguros.cl" in text and f"RADAL_MAIL_REPLY_TO={REPLY}" in text
    assert "MAIL_RECIPIENT_ALLOWLIST=radalseguros.cl" in text and "2 objeto(s) por procesar" in text


def test_preflight_s3_unlistable_is_exit_5_and_send_test_bccs(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_INBOUND_SOURCE", "s3")
    fake = FakeS3()
    fake.fail_list = ConnectionError("denied")
    code, report = mail_preflight.run([], transport=_ses_transport(), s3_client=fake, out=lambda _l: None)
    assert code == mail_preflight.EXIT_INBOUND_FAILED and report["inbound"]["ok"] is False

    monkeypatch.setattr(settings, "MAIL_INBOUND_SOURCE", "none")
    monkeypatch.setattr(settings, "RADAL_MAIL_REPLY_TO", REPLY)
    ses_client = FakeSes()
    code, report = mail_preflight.run(["--send-test", "yo@radalseguros.cl"], transport=_ses_transport(ses_client),
                                      out=lambda _l: None)
    assert code == mail_preflight.EXIT_OK and report["send_test"]["bcc"] == [COPY]
    assert ses_client.sent[-1]["Destination"] == {"ToAddresses": ["yo@radalseguros.cl"], "BccAddresses": [COPY]}
    sent = message_from_bytes(ses_client.raw, policy=policy.default)
    assert "Bcc" not in sent and sent["Reply-To"] == REPLY and REPLY in sent.get_body(("plain",)).get_content()


def _ses_transport(client: FakeSes | None = None):
    from app.services.mail.transport import SesTransport

    return SesTransport(client=client or FakeSes(), configuration_set="", sent_copy=None)
