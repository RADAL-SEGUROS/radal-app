"""v12 lane B4 — status inquiries + the evidence inbox (create, send, reply, confirm -> payment).

Hermetic: ``FakeTransport`` and a fake ``ai.tool_call``.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.config import settings
from app.jobs import run_job
from app.models.collection import CollectionInstallment
from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    EvidenceLevel,
    EvidenceSource,
    EvidenceStatus,
    InquiryStatus,
    InstallmentStatus,
    JobStatus,
    NotificationStatus,
    PaymentSource,
    ReportedStatus,
    ShareAudience,
    ShareEntity,
)
from app.models.mail import PaymentEvidence, StatusInquiry
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.share_link import ShareLink
from app.services import fx
from tests.conftest import API, auth_headers, ensure_role_user
from tests.fakes_v12 import build_message, make_insurer_profile, make_policy_with_plan
from tests.test_v12_mail import fake_ai, mail_on, make_mail_account, policy_with_payer, system_mailbox

NOW = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)
TOKEN_RE = re.compile(r"^INQ-[A-Z2-7]{6}$")


def _setup(db, world, **kw):
    policy, plan = policy_with_payer(db, world, **kw)
    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.cl")
    db.commit()
    return policy, plan


def _create(client, headers, policy, plan, **extra):
    body = {"policy_id": policy.id, "installment_ids": [plan.installments[0].id], **extra}
    return client.post(f"{API}/inquiries", json=body, headers=headers)


def test_create_inquiry_sends_the_token_and_mints_an_insurer_link(
    client, db, headers_a, world, fake_transport, monkeypatch
):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    resp = _create(client, headers_a, policy, plan, installment_ids=[plan.installments[1].id, plan.installments[0].id],
                   message="Gracias por su ayuda")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert TOKEN_RE.match(data["token"])
    assert data["status"] == "sent" and data["mail_status"] == "sent"
    assert data["to_address"] == "cobranza@hdi.cl" and data["insurer_name"] == "HDI Seguros S.A."
    assert [i["number"] for i in data["installments"]] == [1, 2]
    assert data["share_url"].endswith("/s/" + db.get(ShareLink, data["share_link_id"]).token)

    link = db.get(ShareLink, data["share_link_id"])
    assert link.audience == ShareAudience.INSURER and link.entity_type == ShareEntity.STATUS_INQUIRY
    assert link.entity_id == data["id"] and link.permissions["can_report_status"] is True
    assert link.permissions["can_pay"] is False

    (cfg, msg), = fake_transport.sent
    assert f"[RADAL-{data['token']}]" in msg["Subject"]
    assert msg["Reply-To"] == settings.RADAL_MAIL_ADDRESS  # replies come back to a polled box
    assert msg["To"] == "cobranza@hdi.cl"
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert policy.policy_number in text and "Cuota 1" in text and "Cuota 2" in text
    assert link.token in text and "Gracias por su ayuda" in text


def test_inquiry_is_draft_while_mail_is_off_and_needs_an_address(client, db, headers_a, world, fake_transport):
    policy, plan = policy_with_payer(db, world)
    no_address = _create(client, headers_a, policy, plan)
    assert no_address.status_code == 422
    assert "No hay un correo de cobranza registrado para HDI Seguros S.A." in no_address.json()["detail"]
    resp = _create(client, headers_a, policy, plan, to_address="manual@hdi.cl")
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft" and resp.json()["mail_status"] == "skipped"
    assert fake_transport.sent == []


def test_inquiry_validation_isolation_and_gates(client, db, headers_a, headers_b, world, fake_transport):
    policy, plan = _setup(db, world)
    other_policy, other_plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-OTHER")
    db.commit()
    foreign = _create(client, headers_a, policy, plan, installment_ids=[other_plan.installments[0].id])
    assert foreign.status_code == 422
    assert client.post(f"{API}/inquiries", json={"policy_id": policy.id, "installment_ids": [plan.installments[0].id]},
                       headers=headers_b).status_code == 404
    tech = ensure_role_user(db, world.a, "broker_technician", "technician")
    tech_h = auth_headers(client, tech.email)
    assert _create(client, tech_h, policy, plan).status_code == 403

    created = _create(client, headers_a, policy, plan).json()
    assert client.get(f"{API}/inquiries/{created['id']}", headers=headers_b).status_code == 404
    assert client.post(f"{API}/inquiries/{created['id']}/cancel", headers=headers_b).status_code == 404
    assert client.get(f"{API}/inquiries", headers=headers_b).json()["total"] == 0
    assert client.get(f"{API}/inquiries", headers=headers_a).json()["total"] == 1
    assert client.get(f"{API}/inquiries/{created['id']}", headers=tech_h).status_code == 200  # View is enough


def test_send_via_account_and_resend_and_cancel(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    account = make_mail_account(db, world.a)
    db.commit()
    resp = _create(client, headers_a, policy, plan, send_via="account", mail_account_id=account.id)
    assert resp.status_code == 201, resp.text
    cfg, msg = fake_transport.sent[-1]
    assert cfg.host == "smtp.alfa.test" and msg["Reply-To"] is None
    inquiry_id = resp.json()["id"]

    missing = _create(client, headers_a, policy, plan, send_via="account")
    assert missing.status_code == 422

    again = client.post(f"{API}/inquiries/{inquiry_id}/resend", json={"send_via": "radal"}, headers=headers_a)
    assert again.status_code == 200 and again.json()["status"] == "sent"
    assert fake_transport.sent[-1][1]["Subject"] == msg["Subject"]  # same token

    cancelled = client.post(f"{API}/inquiries/{inquiry_id}/cancel", headers=headers_a)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["share_url"] is None
    assert db.get(ShareLink, cancelled.json()["share_link_id"]).revoked_at is not None
    assert client.post(f"{API}/inquiries/{inquiry_id}/resend", headers=headers_a).status_code == 409
    assert client.post(f"{API}/inquiries/{inquiry_id}/cancel", headers=headers_a).status_code == 409


def _reply_and_poll(db, fake_transport, monkeypatch, *, token, parsed, body="La cuota fue pagada"):
    fake_ai(monkeypatch, parsed)
    fake_transport.queue_inbound(
        build_message(from_address="cobranza@hdi.cl", to=settings.RADAL_MAIL_ADDRESS,
                      subject=f"RE: [RADAL-{token}] Consulta", body=body),
        mailbox=system_mailbox(),
    )
    run = run_job(db, "mail.poll", NOW)
    assert run.status == JobStatus.SUCCEEDED, run.error


def test_reply_to_confirm_creates_the_payment(client, db, headers_a, headers_b, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    created = _create(client, headers_a, policy, plan).json()
    first = plan.installments[0]
    _reply_and_poll(db, fake_transport, monkeypatch, token=created["token"], parsed={
        "classification": "insurer_status_reply", "confidence": 95, "reported_status": "paid",
        "installment_numbers": [1], "amount": float(first.gross_amount_uf), "currency": "UF",
        "paid_on": "2026-01-04",
    })
    detail = client.get(f"{API}/inquiries/{created['id']}", headers=headers_a).json()
    assert detail["status"] == "replied" and detail["evidence_open"] == 1
    assert [m["status"] for m in detail["messages"]] == ["sent", "processed"]

    inbox = client.get(f"{API}/inquiries/evidence", headers=headers_a).json()
    assert inbox["total"] == 1
    item = inbox["items"][0]
    assert item["status"] == "suggested" and item["inquiry_token"] == created["token"]
    assert item["installment_number"] == 1 and item["policy_number"] == policy.policy_number
    assert item["mail_from"] == "cobranza@hdi.cl"
    assert client.get(f"{API}/inquiries/evidence", headers=headers_b).json()["total"] == 0
    assert client.post(f"{API}/inquiries/evidence/{item['id']}/confirm", json={},
                       headers=headers_b).status_code == 404

    bell = db.scalar(select(Notification).where(Notification.origin_type == "status_inquiry"))
    assert bell.status == NotificationStatus.NEW and bell.user_id == world.a.admin.id

    resp = client.post(f"{API}/inquiries/evidence/{item['id']}/confirm", json={}, headers=headers_a)
    assert resp.status_code == 200, resp.text
    decided = resp.json()
    assert decided["evidence"]["status"] == "confirmed" and decided["installment_status"] in ("paid", "paid_late")
    payment = db.get(Payment, decided["payment_id"])
    assert payment.source == PaymentSource.INSURER_REPLY
    assert payment.evidence_level == EvidenceLevel.REPORTED
    assert payment.amount_uf == first.gross_amount_uf and payment.paid_on == date(2026, 1, 4)
    assert payment.evidence_id == item["id"]
    db.expire_all()
    assert db.get(PaymentEvidence, item["id"]).payment_id == payment.id
    assert db.get(StatusInquiry, created["id"]).status == InquiryStatus.RESOLVED
    assert db.get(Notification, bell.id).status == NotificationStatus.CLOSED
    assert db.get(CollectionInstallment, first.id).status in (InstallmentStatus.PAID, InstallmentStatus.PAID_LATE)

    again = client.post(f"{API}/inquiries/evidence/{item['id']}/confirm", json={}, headers=headers_a)
    assert again.status_code == 409


def test_confirm_partial_needs_an_amount_and_clp_needs_a_rate(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    created = _create(client, headers_a, policy, plan).json()
    _reply_and_poll(db, fake_transport, monkeypatch, token=created["token"], parsed={
        "classification": "insurer_status_reply", "confidence": 90, "reported_status": "partial",
        "installment_numbers": [1],
    })
    ev_id = client.get(f"{API}/inquiries/evidence", headers=headers_a).json()["items"][0]["id"]
    url = f"{API}/inquiries/evidence/{ev_id}/confirm"
    assert client.post(url, json={}, headers=headers_a).status_code == 422
    no_rate = client.post(url, json={"amount": 500000, "currency": "CLP", "paid_on": "2026-03-01"}, headers=headers_a)
    assert no_rate.status_code == 422 and "UF" in no_rate.json()["detail"]
    fx.upsert_rates(db, [(date(2026, 3, 1), Decimal("39000"), Decimal("950"), "test")])
    db.commit()
    ok = client.post(url, json={"amount": 390000, "currency": "CLP", "paid_on": "2026-03-01"}, headers=headers_a)
    assert ok.status_code == 200, ok.text
    payment = db.get(Payment, ok.json()["payment_id"])
    assert payment.amount_uf == Decimal("10.0000") and payment.uf_rate == Decimal("39000.0000")


def test_unpaid_confirm_moves_no_money_and_reject_closes(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    created = _create(client, headers_a, policy, plan,
                      installment_ids=[plan.installments[0].id, plan.installments[1].id]).json()
    _reply_and_poll(db, fake_transport, monkeypatch, token=created["token"], parsed={
        "classification": "insurer_status_reply", "confidence": 90, "reported_status": "unpaid",
    })
    items = client.get(f"{API}/inquiries/evidence", headers=headers_a).json()["items"]
    assert len(items) == 2  # no numbers -> one per asked cuota
    first, second = sorted(items, key=lambda i: i["installment_number"])
    resp = client.post(f"{API}/inquiries/evidence/{first['id']}/confirm", json={}, headers=headers_a)
    assert resp.status_code == 200 and resp.json()["payment_id"] is None
    assert db.scalars(select(Payment)).all() == []
    rej = client.post(f"{API}/inquiries/evidence/{second['id']}/reject", json={"note": "no corresponde"},
                      headers=headers_a)
    assert rej.status_code == 200 and rej.json()["evidence"]["status"] == "rejected"
    assert client.get(f"{API}/inquiries/evidence", headers=headers_a).json()["total"] == 0
    history = client.get(f"{API}/inquiries/evidence?status=confirmed&status=rejected", headers=headers_a).json()
    assert history["total"] == 2
    db.expire_all()
    assert db.get(StatusInquiry, created["id"]).status == InquiryStatus.RESOLVED


def test_confirm_payer_upload_evidence_keeps_the_receipt(client, db, headers_a, world):
    policy, plan = policy_with_payer(db, world)
    first = plan.installments[0]
    ev = PaymentEvidence(
        broker_id=world.a.broker_id, installment_id=first.id, source=EvidenceSource.PAYER_UPLOAD,
        document_id=world.a.document.id, reported_status=ReportedStatus.PAID, status=EvidenceStatus.NEEDS_REVIEW,
    )
    db.add(ev)
    db.commit()
    resp = client.post(f"{API}/inquiries/evidence/{ev.id}/confirm", json={"paid_on": "2026-01-05"}, headers=headers_a)
    assert resp.status_code == 200, resp.text
    payment = db.get(Payment, resp.json()["payment_id"])
    assert payment.source == PaymentSource.PAYER_UPLOAD and payment.receipt_document_id == world.a.document.id
    assert payment.amount_uf == first.gross_amount_uf  # "paid" without a figure = the open balance


def test_auto_inquiries_job_one_open_inquiry_per_policy(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)  # cuotas due 2026-01-05, 02-05, 03-05
    # Switched off: nothing.
    assert run_job(db, "collection.inquiries", NOW).stats["brokers"] == 0
    db.add(CollectionSettings(broker_id=world.a.broker_id, auto_status_inquiries=True, inquiry_after_days=3))
    db.commit()
    run = run_job(db, "collection.inquiries", datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc))
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["created"] == 1
    (inquiry,) = db.scalars(select(StatusInquiry)).all()
    assert inquiry.created_by_job is True and inquiry.created_by_user_id is None
    assert inquiry.installment_ids == [i.id for i in plan.installments]  # 3 overdue cuotas, one inquiry
    assert inquiry.status == InquiryStatus.SENT
    # Next day: the open inquiry blocks a second one.
    run2 = run_job(db, "collection.inquiries", datetime(2026, 3, 12, 15, 0, tzinfo=timezone.utc))
    assert run2.stats["created"] == 0 and run2.stats["open_exists"] == 1
    # Another broker's policies are never touched.
    assert all(i.broker_id == world.a.broker_id for i in db.scalars(select(StatusInquiry)))


# --- fixer regressions --------------------------------------------------------------------

def test_auto_inquiries_job_keeps_an_inquiry_already_mailed_when_a_later_policy_fails(
    db, world, fake_transport, monkeypatch
):
    import app.services.mail.inquiries as inquiries_module
    from app.services.mail.inquiries import run_collection_inquiries

    mail_on(monkeypatch)
    _setup(db, world)
    _setup(db, world, policy_number="POL-SEGUNDA-0002")
    db.add(CollectionSettings(broker_id=world.a.broker_id, auto_status_inquiries=True, inquiry_after_days=3))
    db.commit()
    real = inquiries_module.create_share_link
    calls = {"n": 0}

    def _flaky(*args, **kwargs):  # noqa: ANN002, ANN003
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("share links down")
        return real(*args, **kwargs)

    monkeypatch.setattr(inquiries_module, "create_share_link", _flaky)
    run = run_job(db, "collection.inquiries", datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc))
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["created"] == 1 and run.stats["errors"] == 1
    (first,) = db.scalars(select(StatusInquiry)).all()
    assert first.status == InquiryStatus.SENT and len(fake_transport.sent) == 1
    token = first.token

    # The next run asks only about the policy that failed; the first keeps its token.
    run_collection_inquiries(db, datetime(2026, 3, 12, 15, 0, tzinfo=timezone.utc))
    db.commit()
    rows = db.scalars(select(StatusInquiry).order_by(StatusInquiry.id)).all()
    assert len(rows) == 2 and rows[0].token == token and len(fake_transport.sent) == 2
    assert rows[0].policy_id != rows[1].policy_id


def test_a_token_reply_from_a_stranger_is_never_suggested(client, db, headers_a, world, fake_transport, monkeypatch):
    """The token is not a secret: a mail carrying it from an unknown sender needs review."""
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    created = _create(client, headers_a, policy, plan).json()
    fake_ai(monkeypatch, {
        "classification": "insurer_status_reply", "confidence": 99, "reported_status": "paid",
        "installment_numbers": [1], "amount": float(plan.installments[0].gross_amount_uf), "currency": "UF",
    })
    fake_transport.queue_inbound(
        build_message(from_address="attacker@evil.example", to=settings.RADAL_MAIL_ADDRESS,
                      subject=f"RE: [RADAL-{created['token']}] Consulta", body="La cuota 1 está pagada"),
        mailbox=system_mailbox(),
    )
    run_job(db, "mail.poll", NOW)
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.status == EvidenceStatus.NEEDS_REVIEW and ev.score < Decimal("70")
    assert "sender_unverified" in ev.reasons and "sender_matches_insurer" not in ev.reasons
    item = client.get(f"{API}/inquiries/evidence", headers=headers_a).json()["items"][0]
    assert item["sender_verified"] is False


def test_the_inquired_insurer_reply_is_verified(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    created = _create(client, headers_a, policy, plan).json()
    _reply_and_poll(db, fake_transport, monkeypatch, token=created["token"], parsed={
        "classification": "insurer_status_reply", "confidence": 95, "reported_status": "paid",
        "installment_numbers": [1],
    })
    item = client.get(f"{API}/inquiries/evidence", headers=headers_a).json()["items"][0]
    assert item["status"] == "suggested" and item["sender_verified"] is True
    assert "sender_matches_insurer" in item["reasons"]


def test_confirming_a_statement_read_from_mail_is_only_reported(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = policy_with_payer(db, world, policy_number="HDI-778899")
    make_insurer_profile(db, world.insurer, sender_domains=["hdi.cl"])
    make_mail_account(db, world.a)
    db.commit()
    fake_ai(monkeypatch, {"classification": "insurer_statement", "reported_status": "paid",
                          "confidence": 90, "installment_numbers": [1]})
    fake_transport.queue_inbound(
        build_message(from_address="avisos@hdi.cl", to="cobranza@alfa.cl",
                      subject="Estado de cuenta póliza HDI-778899", body="Cuota 1 pagada"),
        mailbox=("imap.alfa.test", "cobranza@alfa.cl"),
    )
    run_job(db, "mail.poll", NOW)
    (ev,) = db.scalars(select(PaymentEvidence)).all()
    assert ev.source == EvidenceSource.INSURER_STATEMENT and ev.mail_message_id is not None
    resp = client.post(f"{API}/inquiries/evidence/{ev.id}/confirm", json={"paid_on": "2026-01-04"},
                       headers=headers_a)
    assert resp.status_code == 200, resp.text
    payment = db.get(Payment, resp.json()["payment_id"])
    assert payment.evidence_level == EvidenceLevel.REPORTED and payment.confirmed_by_insurer is False
    assert payment.source == PaymentSource.INSURER_STATEMENT


def test_confirm_snaps_a_clp_figure_within_tolerance_like_the_ledger(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    policy, plan = _setup(db, world)
    first = plan.installments[0]
    created = _create(client, headers_a, policy, plan).json()
    _reply_and_poll(db, fake_transport, monkeypatch, token=created["token"], parsed={
        "classification": "insurer_status_reply", "confidence": 90, "reported_status": "paid",
        "installment_numbers": [1],
    })
    rate = Decimal("39600")
    fx.upsert_rates(db, [(date(2026, 1, 4), rate, Decimal("950"), "test")])
    db.commit()
    # 0.25 % short of the cuota at that day's UF (the payer used another day's UF).
    clp = int((Decimal(str(first.gross_amount_uf)) * rate * Decimal("0.9975")).to_integral_value())
    ev_id = client.get(f"{API}/inquiries/evidence", headers=headers_a).json()["items"][0]["id"]
    resp = client.post(f"{API}/inquiries/evidence/{ev_id}/confirm",
                       json={"amount": clp, "currency": "CLP", "paid_on": "2026-01-04"}, headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["installment_status"] in ("paid", "paid_late")
    payment = db.get(Payment, resp.json()["payment_id"])
    assert payment.amount == Decimal(clp) and payment.amount_uf == first.gross_amount_uf  # snapped
