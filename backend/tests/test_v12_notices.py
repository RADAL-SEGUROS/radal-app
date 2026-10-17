"""v12 lane B4 — payer notices: rendering, preview/send, switches, dedupe, the daily job."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.jobs import run_job
from app.models.base_class import utcnow
from app.models.collection_settings import CollectionNotice, CollectionSettings, NoticeTemplate
from app.models.enums import (
    JobStatus,
    MailMessageStatus,
    NoticeKind,
    NoticeStatus,
    ShareAudience,
)
from app.models.mail import MailMessage
from app.models.share_link import ShareLink
from app.services import fx
from app.services.notices import (
    business_today,
    cadence_step,
    effective_template,
    format_clp,
    format_uf,
    render_html,
    render_text,
)
from tests.conftest import API
from tests.fakes_v12 import (
    make_insurer_profile,
    make_native_channel,
    make_payment,
    make_policy_with_plan,
    make_provider_account,
)
from tests.test_v12_mail import mail_on, policy_with_payer

# Cuotas of make_policy_with_plan fall due 2026-01-05, 2026-02-05, 2026-03-05.
FIVE_BEFORE = datetime(2025, 12, 31, 15, 0, tzinfo=timezone.utc)
ON_DUE = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
THREE_AFTER = datetime(2026, 1, 8, 15, 0, tzinfo=timezone.utc)


# --- rendering -----------------------------------------------------------------

def test_render_text_replaces_known_vars_and_blanks_unknown():
    assert render_text("Hola {{ name }}, {{nope}}!", {"name": "Ana"}) == "Hola Ana, !"
    assert render_text("{{a}}{{a}}", {"a": 1}) == "11"


def test_render_html_escapes_values_and_literals_and_links_urls():
    html = render_html("<b>Hola</b> {{name}}\n{{url}}\n\nFin", {"name": "<script>x</script>",
                                                             "url": "https://app.radal.test/s/abc"})
    assert "&lt;b&gt;Hola&lt;/b&gt;" in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html and "<script>" not in html
    assert '<a href="https://app.radal.test/s/abc">https://app.radal.test/s/abc</a>' in html
    assert "<br>" in html and html.count("<p>") == 2


def test_number_formats_and_cadence():
    assert format_uf(Decimal("1234.5")) == "1.234,50"
    assert format_uf(Decimal("46.3333")) == "46,3333"
    assert format_clp(1234567) == "1.234.567"
    assert cadence_step(-5, [-15, -5, 0, 3, 10]) == -5
    assert cadence_step(-4, [-15, -5, 0, 3, 10]) == -5
    assert cadence_step(-16, [-15, -5, 0, 3, 10]) is None
    assert cadence_step(40, [-15, -5, 0, 3, 10]) == 10


def test_broker_template_wins_over_the_default(db, world):
    seeded = effective_template(db, broker_id=world.a.broker_id, kind="payer_reminder", cadence_level=-5)
    assert seeded.source == "default" and "{{installment_number}}" in seeded.subject
    db.add(NoticeTemplate(broker_id=world.a.broker_id, kind=NoticeKind.PAYER_REMINDER, cadence_level=-5,
                          subject="Propio {{policy_number}}", body="Cuerpo propio"))
    db.commit()
    own = effective_template(db, broker_id=world.a.broker_id, kind="payer_reminder", cadence_level=-5)
    assert own.source == "broker" and own.subject == "Propio {{policy_number}}"
    # Strict lookup (the job): no template for -15 -> not mailed.
    assert effective_template(db, broker_id=world.a.broker_id, kind="payer_reminder",
                              cadence_level=-15, strict=True) is None
    # Another broker still gets the default.
    assert effective_template(db, broker_id=world.b.broker_id, kind="payer_reminder",
                              cadence_level=-5).source == "default"


# --- preview / send endpoints ---------------------------------------------------------

def test_preview_shows_clp_instructions_and_level(client, db, headers_a, world):
    policy, plan = policy_with_payer(db, world)
    make_insurer_profile(db, world.insurer, payment_instructions={
        "bank_name": "Banco Uno", "account_number": "000123", "transfer_email": "pagos@hdi.cl"})
    fx.upsert_rates(db, [(business_today(), Decimal("40000"), Decimal("950"), "test")])
    db.commit()
    first = plan.installments[0]
    resp = client.post(f"{API}/inquiries/notices/preview",
                       json={"installment_id": first.id, "kind": "payer_reminder"}, headers=headers_a)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["to_address"] == "pagador@alfa-cliente.cl"
    assert data["level"] == "assisted" and "Cobro asistido" in data["level_reason"]
    expected_clp = format_clp(fx.uf_to_clp(first.gross_amount_uf, Decimal("40000")))
    assert f"aprox. ${expected_clp}" in data["text"]
    assert "Banco: Banco Uno" in data["text"] and "pagos@hdi.cl" in data["text"]
    assert policy.policy_number in data["subject"]
    assert data["template_source"] == "default"
    assert db.scalars(select(CollectionNotice)).all() == []  # a preview writes nothing
    assert db.scalars(select(ShareLink)).all() == []


def test_whatsapp_is_pronto_and_isolation(client, db, headers_a, headers_b, world):
    _policy, plan = policy_with_payer(db, world)
    inst_id = plan.installments[0].id
    for path in ("preview", "send"):
        resp = client.post(f"{API}/inquiries/notices/{path}",
                           json={"installment_id": inst_id, "channel": "whatsapp"}, headers=headers_a)
        assert resp.status_code == 409 and resp.json()["detail"].startswith("Pronto")
        other = client.post(f"{API}/inquiries/notices/{path}", json={"installment_id": inst_id},
                            headers=headers_b)
        assert other.status_code == 404
    assert client.get(f"{API}/inquiries/notices?installment_id={inst_id}", headers=headers_b).json()["total"] == 0


def test_send_now_records_notice_mail_and_an_insured_link(client, db, headers_a, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    first = plan.installments[0]
    resp = client.post(f"{API}/inquiries/notices/send", json={"installment_id": first.id}, headers=headers_a)
    assert resp.status_code == 201, resp.text
    notice = resp.json()
    assert notice["status"] == "sent" and notice["cadence_level"] is None
    assert notice["to_address"] == "pagador@alfa-cliente.cl"
    link = db.get(ShareLink, notice["share_link_id"])
    assert link.audience == ShareAudience.INSURED and link.permissions["can_pay"] is False  # assisted
    (_cfg, msg), = fake_transport.sent
    assert link.token in msg.get_body(preferencelist=("plain",)).get_content()
    assert db.get(MailMessage, notice["mail_message_id"]).status == MailMessageStatus.SENT

    dup = client.post(f"{API}/inquiries/notices/send", json={"installment_id": first.id}, headers=headers_a)
    assert dup.status_code == 409  # one one-off notice per cuota/kind/day
    other_kind = client.post(f"{API}/inquiries/notices/send",
                             json={"installment_id": first.id, "kind": "payer_overdue"}, headers=headers_a)
    assert other_kind.status_code == 201
    listed = client.get(f"{API}/inquiries/notices?installment_id={first.id}", headers=headers_a).json()
    assert listed["total"] == 2


def test_send_refuses_paid_cuota_and_missing_payer(client, db, headers_a, world, fake_transport):
    _policy, plan = policy_with_payer(db, world)
    make_payment(db, plan.installments[0])
    db.commit()
    paid = client.post(f"{API}/inquiries/notices/send", json={"installment_id": plan.installments[0].id},
                       headers=headers_a)
    assert paid.status_code == 409
    bare, bare_plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-SIN-PAGADOR")
    db.commit()
    resp = client.post(f"{API}/inquiries/notices/send", json={"installment_id": bare_plan.installments[0].id},
                       headers=headers_a)
    assert resp.status_code == 422 and "pagador" in resp.json()["detail"]
    assert db.scalars(select(ShareLink)).all() == []  # a refused send leaves nothing behind


def test_native_level_link_can_pay(client, db, headers_a, world, fake_transport):
    _policy, plan = policy_with_payer(db, world)
    account = make_provider_account(db)
    make_native_channel(db, world.insurer, account)
    db.commit()
    resp = client.post(f"{API}/inquiries/notices/send",
                       json={"installment_id": plan.installments[0].id, "kind": "payment_link"}, headers=headers_a)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "skipped"  # MAIL_ENABLED off: recorded, not sent
    link = db.get(ShareLink, resp.json()["share_link_id"])
    assert link.permissions["can_pay"] is True


# --- the daily job ------------------------------------------------------------------------

def _enable(db, world, plan, **settings_fields):
    db.add(CollectionSettings(broker_id=world.a.broker_id, notices_enabled=True,
                              notices_approved_at=utcnow(), **settings_fields))
    plan.notices_enabled = True
    db.flush()
    # Approval is per template since review v12 #14: approve the effective set.
    from app.services.collection.templates import approve_templates

    approve_templates(db, world.a.broker_id, user_id=world.a.admin.id)
    db.commit()


def _notices(db):
    return list(db.scalars(select(CollectionNotice).order_by(CollectionNotice.id)))


def test_job_sends_one_step_per_cuota_and_dedupes(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    _enable(db, world, plan)
    run = run_job(db, "collection.notices", FIVE_BEFORE)
    assert run.status == JobStatus.SUCCEEDED, run.error
    (notice,) = _notices(db)
    assert notice.installment_id == plan.installments[0].id
    assert notice.kind == NoticeKind.PAYER_REMINDER and notice.cadence_level == -5
    assert notice.status == NoticeStatus.SENT and len(fake_transport.sent) == 1

    # Next day is still step -5: already sent, nothing new.
    run_job(db, "collection.notices", datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert len(_notices(db)) == 1
    # Due date -> step 0; three days late -> payer_overdue +3.
    run_job(db, "collection.notices", ON_DUE)
    run_job(db, "collection.notices", THREE_AFTER)
    steps = [(n.kind.value, n.cadence_level) for n in _notices(db)]
    assert steps == [("payer_reminder", -5), ("payer_reminder", 0), ("payer_overdue", 3)]
    assert len(fake_transport.sent) == 3


def test_job_retries_a_failed_step_in_place(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    _enable(db, world, plan)
    fake_transport.fail_send = ConnectionRefusedError("down")
    run_job(db, "collection.notices", FIVE_BEFORE)
    (notice,) = _notices(db)
    assert notice.status == NoticeStatus.FAILED
    run_job(db, "collection.notices", datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc))
    (notice,) = _notices(db)
    db.refresh(notice)
    assert notice.status == NoticeStatus.SENT


def test_job_honours_every_switch(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    # No settings row at all.
    assert run_job(db, "collection.notices", FIVE_BEFORE).stats["brokers"] == 0
    # Master switch on but templates not approved.
    row = CollectionSettings(broker_id=world.a.broker_id, notices_enabled=True)
    db.add(row)
    plan.notices_enabled = True
    db.commit()
    assert run_job(db, "collection.notices", datetime(2025, 12, 31, 16, 0, tzinfo=timezone.utc)
                   ).stats["brokers"] == 0
    # Approved, but the plan switched off.
    row.notices_approved_at = utcnow()
    plan.notices_enabled = False
    db.commit()
    assert run_job(db, "collection.notices", datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
                   ).stats["plans"] == 0
    # Plan on but paused.
    plan.notices_enabled = True
    plan.notices_paused_until = datetime(2026, 1, 10).date()
    db.commit()
    stats = run_job(db, "collection.notices", datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)).stats
    assert stats["paused"] == 1
    # Record level preference.
    plan.notices_paused_until = None
    plan.level_preference = "record"
    db.commit()
    stats = run_job(db, "collection.notices", datetime(2026, 1, 3, 15, 0, tzinfo=timezone.utc)).stats
    assert stats["record_level"] == 1
    assert _notices(db) == [] and fake_transport.sent == []


def test_job_skips_paid_cuotas_and_missing_payer(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    make_payment(db, plan.installments[0])
    bare, bare_plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-SIN-PAGADOR")
    bare_plan.notices_enabled = True
    _enable(db, world, plan)
    stats = run_job(db, "collection.notices", FIVE_BEFORE).stats
    assert stats["settled"] >= 1 and stats["no_payer_contact"] == 1 and stats["sent"] == 0
    assert _notices(db) == []


def test_job_uses_the_broker_template_and_another_broker_is_untouched(db, world, fake_transport, monkeypatch):
    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    other_policy, other_plan = make_policy_with_plan(db, world.b, world.insurer)
    other_plan.notices_enabled = True  # broker B has no settings: master switch off
    db.add(NoticeTemplate(broker_id=world.a.broker_id, kind=NoticeKind.PAYER_REMINDER, cadence_level=-5,
                          subject="Aviso propio {{policy_number}}", body="Hola {{insured_name}} <3"))
    _enable(db, world, plan)
    run_job(db, "collection.notices", FIVE_BEFORE)
    (notice,) = _notices(db)
    assert notice.broker_id == world.a.broker_id
    (_cfg, msg), = fake_transport.sent
    assert msg["Subject"] == f"Aviso propio {plan.policy.policy_number}"
    assert "&lt;3" in msg.get_body(preferencelist=("html",)).get_content()


# --- fixer regressions --------------------------------------------------------------------

def test_a_failing_cuota_never_rolls_back_a_notice_already_mailed(db, world, fake_transport, monkeypatch):
    """One cuota raising mid-run must not lose (and later re-send) another's mail."""
    import app.services.notices as notices_module

    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    _policy2, plan2 = policy_with_payer(db, world, policy_number="POL-SEGUNDA-0002")
    plan2.notices_enabled = True
    _enable(db, world, plan)
    bad_id = plan2.installments[0].id
    real = notices_module.resolve_collection_mode

    def _flaky(db_, inst, **kw):  # noqa: ANN001
        if inst.id == bad_id:
            raise RuntimeError("resolver down")
        return real(db_, inst, **kw)

    monkeypatch.setattr(notices_module, "resolve_collection_mode", _flaky)
    run = run_job(db, "collection.notices", FIVE_BEFORE)
    assert run.status == JobStatus.SUCCEEDED, run.error
    assert run.stats["sent"] == 1 and run.stats["errors"] == 1
    (notice,) = _notices(db)
    assert notice.installment_id == plan.installments[0].id and notice.status == NoticeStatus.SENT
    assert db.get(MailMessage, notice.mail_message_id).status == MailMessageStatus.SENT
    assert len(fake_transport.sent) == 1

    # A re-run (same day, resolver still down) mails nobody twice.
    notices_module.run_collection_notices(db, FIVE_BEFORE)
    assert len(fake_transport.sent) == 1 and len(_notices(db)) == 1

    # Resolver back: the next day sends ONLY the cuota that failed.
    monkeypatch.setattr(notices_module, "resolve_collection_mode", real)
    run_job(db, "collection.notices", datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc))
    assert sorted(n.installment_id for n in _notices(db)) == sorted([plan.installments[0].id, bad_id])
    assert len(fake_transport.sent) == 2


def test_a_slot_the_broker_switched_off_sends_nothing(client, db, headers_a, world, fake_transport, monkeypatch):
    from app.services.collection import templates as b2_templates

    mail_on(monkeypatch)
    _policy, plan = policy_with_payer(db, world)
    db.add(NoticeTemplate(broker_id=world.a.broker_id, kind=NoticeKind.PAYER_REMINDER, cadence_level=-5,
                          subject="Apagada", body="No enviar", is_active=False))
    _enable(db, world, plan)
    # B4 agrees with B2's single source of truth.
    assert b2_templates.effective_template(db, world.a.broker_id, "payer_reminder", -5) is None
    assert effective_template(db, broker_id=world.a.broker_id, kind="payer_reminder",
                              cadence_level=-5, strict=True) is None
    assert effective_template(db, broker_id=world.a.broker_id, kind="payer_reminder",
                              cadence_level=-5) is None  # not even a one-off borrows the seed
    stats = run_job(db, "collection.notices", FIVE_BEFORE).stats
    assert stats["no_template"] == 1 and stats["sent"] == 0
    assert _notices(db) == [] and fake_transport.sent == []
    # The step that is still ON (0 = due date) is mailed as usual.
    run_job(db, "collection.notices", ON_DUE)
    (notice,) = _notices(db)
    assert notice.cadence_level == 0 and notice.status == NoticeStatus.SENT
