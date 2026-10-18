"""Regressions for the 2026-10-17 Chrome review of v12 (docs/handoff-2026-10-17-v12-review-fixes.md).

One block per item. Hermetic like the rest of the suite (no AI, no mail, no
Forpay, RPA disabled).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.activity import Activity
from app.models.case_file import CaseFileStageEvent
from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    CaseStage,
    EntityType,
    EvidenceLevel,
    EvidenceSource,
    EvidenceStatus,
    InquiryStatus,
    NotificationKind,
    NotificationStatus,
    NotificationTray,
    PartyRole,
    PaymentMode,
    PaymentStatus,
    PortalRunStatus,
    ReportedStatus,
)
from app.models.mail import PaymentEvidence, StatusInquiry
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.rpa import PortalSearchRun
from app.services.collection.clock import business_today
from app.services.notifications import notify
from tests.conftest import API, make_case_file, make_policy
from tests.fakes_v12 import (
    make_insurer_profile,
    make_native_channel,
    make_party,
    make_payment,
    make_policy_with_plan,
    make_provider_account,
)

TODAY = business_today()


def _plan(db, world, **kw):
    """Policy (gross UF 139) + 3 cuotas due 2026-01/02/03 — all late by today."""
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, **kw)
    make_party(db, policy, role=PartyRole.PAYER, name="Rentas Los Andes SpA",
               email="pagos@rentaslosandes.cl")
    db.commit()
    db.refresh(policy)
    db.refresh(plan)
    return policy, plan


def _pay(client, headers, inst_id, amount, currency="UF", paid_on=None):
    return client.post(
        f"{API}/collections/installments/{inst_id}/payments",
        json={"amount": str(amount), "currency": currency, "paid_on": (paid_on or TODAY).isoformat()},
        headers=headers,
    )


# =============================================================================
# 1 / 5 — notifications: admin "Todas" + no duplicate history rows
# =============================================================================

def test_admin_scope_all_lists_team_rows_once_per_origin_with_the_recipient(
    client, db, world, headers_a, headers_a_exec
):
    broker_id = world.a.broker_id
    # The executive owns the cuota alert (todo); the admin got a fyi on the same origin.
    notify(db, broker_id=broker_id, user_ids=[world.a.executive.id],
           kind=NotificationKind.TERMINATION_RISK, tray=NotificationTray.TODO,
           origin_type="installment", origin_id=501, level="term")
    notify(db, broker_id=broker_id, user_ids=[world.a.admin.id],
           kind=NotificationKind.INSTALLMENT_DUE, tray=NotificationTray.FYI,
           origin_type="installment", origin_id=501, level="0")
    notify(db, broker_id=broker_id, user_ids=[world.a.executive.id],
           kind=NotificationKind.EVIDENCE_PENDING, tray=NotificationTray.TODO,
           origin_type="payment_evidence", origin_id=9)
    # Another broker's row never leaks.
    notify(db, broker_id=world.b.broker_id, user_ids=[world.b.admin.id],
           kind=NotificationKind.TERMINATION_RISK, tray=NotificationTray.TODO,
           origin_type="installment", origin_id=777, level="term")
    db.commit()

    mine = client.get(f"{API}/notifications", headers=headers_a).json()
    assert [r["origin_id"] for r in mine["items"]] == [501]  # only the admin's own fyi

    team = client.get(f"{API}/notifications", params={"scope": "all"}, headers=headers_a)
    assert team.status_code == 200, team.text
    items = team.json()["items"]
    assert sorted((r["origin_type"], r["origin_id"]) for r in items) == [
        ("installment", 501), ("payment_evidence", 9)
    ]
    cuota = next(r for r in items if r["origin_id"] == 501)
    assert cuota["kind"] == "termination_risk"  # the most severe row of the origin
    assert cuota["recipient_name"] == world.a.executive.full_name
    count = client.get(f"{API}/notifications/count", params={"scope": "all"}, headers=headers_a).json()
    assert count == {"todo_new": 2, "fyi_new": 0}

    denied = client.get(f"{API}/notifications", params={"scope": "all"}, headers=headers_a_exec)
    assert denied.status_code == 403 and "administrador" in denied.json()["detail"]
    assert client.get(
        f"{API}/notifications/count", params={"scope": "all"}, headers=headers_a_exec
    ).status_code == 403


def test_a_reversal_reopens_the_same_alert_so_history_has_no_twins(client, db, world, headers_a):
    _policy, plan = _plan(db, world)
    first = plan.installments[0]
    # A plan write syncs the alerts at once (review #5d): the cuota is late today.
    _pay(client, headers_a, first.id, "10")  # partial -> alert stays open
    open_rows = db.scalars(select(Notification).where(
        Notification.origin_type == "installment", Notification.origin_id == first.id)).all()
    assert len(open_rows) == 1 and open_rows[0].status != NotificationStatus.CLOSED
    alert_id = open_rows[0].id

    rest = _pay(client, headers_a, first.id, Decimal(str(first.gross_amount_uf)) - Decimal("10"))
    assert rest.status_code == 201, rest.text
    reversed_ = client.post(f"{API}/collections/payments/{rest.json()['payment']['id']}/reverse",
                            json={"reason": "error", "note": "mal digitado"}, headers=headers_a)
    assert reversed_.status_code == 200, reversed_.text
    _pay(client, headers_a, first.id, Decimal(str(first.gross_amount_uf)) - Decimal("10"))

    db.expire_all()
    rows = db.scalars(select(Notification).where(
        Notification.origin_type == "installment", Notification.origin_id == first.id)).all()
    assert [r.id for r in rows] == [alert_id]  # reopened and re-closed, never duplicated
    history = client.get(f"{API}/notifications", params={"status": "closed"}, headers=headers_a).json()
    assert sum(1 for r in history["items"] if r["origin_id"] == first.id) == 1


def test_account_pending_actions_carry_the_collection_risk(client, db, world, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.ACTIVE)
    _policy, plan = _plan(db, world)
    db.commit()
    res = client.get(f"{API}/case-files/{case.id}/pending-actions", headers=headers_a)
    assert res.status_code == 200, res.text
    actions = {a["code"]: a for a in res.json()["actions"]}
    risk = actions["collection_termination_risk"]
    assert risk["severity"] == "blocker" and risk["tab"] == "policies"
    assert risk["count"] == 3 and "Cuota 1/3" in risk["reason"] and "riesgo de término" in risk["reason"]


def test_creating_a_plan_with_past_dates_warns_raises_alerts_and_can_start_cobranza(
    client, db, world, headers_a
):
    policy = make_policy(db, world.a, world.insurer, policy_number="POL-PAST-1")
    db.commit()
    res = client.post(
        f"{API}/collections/plans",
        headers=headers_a,
        json={
            "policy_id": policy.id,
            "start_collection": True,
            "installments": [
                {"number": 1, "gross_amount_uf": "69.5", "due_date": (TODAY - timedelta(days=5)).isoformat()},
                {"number": 2, "gross_amount_uf": "69.5", "due_date": (TODAY + timedelta(days=40)).isoformat()},
            ],
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["notices_enabled"] is True
    assert len(body["warnings"]) == 1 and "nace vencida" in body["warnings"][0]
    first = next(i for i in body["installments"] if i["number"] == 1)
    assert first["status"] == "overdue"  # the cache is derived on write
    alert = db.scalar(select(Notification).where(
        Notification.origin_type == "installment", Notification.origin_id == first["id"]))
    assert alert is not None and alert.kind == NotificationKind.INSTALLMENT_OVERDUE
    assert db.scalar(select(Activity).where(Activity.action == "collection_plan.started")) is not None


# =============================================================================
# 1 — money: never over gross; declared payments confirmed; summaries capped
# =============================================================================

def test_ledger_refuses_a_payment_above_the_balance(client, db, world, headers_a):
    _policy, plan = _plan(db, world)
    first = plan.installments[0]
    assert _pay(client, headers_a, first.id, "40").status_code == 201
    over = _pay(client, headers_a, first.id, "10")  # balance is 6.3333
    assert over.status_code == 409
    assert over.json()["detail"].startswith("El monto supera el saldo de la cuota (saldo UF 6,3333")
    exact = _pay(client, headers_a, first.id, "6.3333")
    assert exact.status_code == 201 and exact.json()["state"]["state"] == "paid"


def _insurer_evidence(db, world, inst, *, amount, status=ReportedStatus.PAID, source=EvidenceSource.INSURER_SHARE_LINK):
    ev = PaymentEvidence(
        broker_id=world.a.broker_id, installment_id=inst.id, source=source,
        reported_status=status, reported_amount=Decimal(str(amount)), reported_currency="UF",
        reported_paid_on=TODAY, status=EvidenceStatus.SUGGESTED,
    )
    db.add(ev)
    db.commit()
    return ev


def test_confirming_an_insurer_paid_reply_books_only_the_balance_and_confirms_the_declared(
    client, db, world, headers_a
):
    _policy, plan = _plan(db, world)
    second = plan.installments[1]
    gross = Decimal(str(second.gross_amount_uf))  # 46.3333
    assert _pay(client, headers_a, second.id, "3.80").status_code == 201  # *Declarado*
    ev = _insurer_evidence(db, world, second, amount=gross)

    listed = client.get(f"{API}/collections/evidence", headers=headers_a).json()["items"]
    row = next(r for r in listed if r["id"] == ev.id)
    summary = row["installment_summary"]
    assert Decimal(summary["gross_uf"]) == gross and Decimal(summary["paid_uf"]) == Decimal("3.80")
    assert Decimal(summary["balance_uf"]) == gross - Decimal("3.80")
    assert [p["evidence_level"] for p in summary["payments"]] == ["declared"]
    assert Decimal(row["suggested_amount_uf"]) == gross - Decimal("3.80")  # the balance, not the gross

    res = client.post(f"{API}/collections/evidence/{ev.id}/confirm", json={}, headers=headers_a)
    assert res.status_code == 200, res.text
    db.expire_all()
    active = [p for p in db.scalars(select(Payment).where(Payment.installment_id == second.id))
              if p.status == PaymentStatus.ACTIVE]
    assert sum(p.amount_uf for p in active) == gross  # never UF 50,13 on a UF 46,33 cuota
    declared = next(p for p in active if p.amount_uf == Decimal("3.80"))
    assert declared.evidence_level == EvidenceLevel.INSURER_CONFIRMED
    assert db.scalar(select(Activity).where(
        Activity.action == "payment.confirmed", Activity.description.like("%confirmó el pago declarado%")
    )) is not None

    board = client.get(f"{API}/collections/installments", params={"plan_id": plan.id}, headers=headers_a).json()
    panel = next(r for r in board["items"] if r["id"] == second.id)
    assert Decimal(panel["paid_uf"]) == gross and panel["state"] == "paid"


def test_explicit_amount_over_the_balance_and_a_paid_target_are_409(client, db, world, headers_a):
    _policy, plan = _plan(db, world)
    first, second, third = plan.installments
    assert _pay(client, headers_a, first.id, first.gross_amount_uf).status_code == 201  # cuota 1 paid
    ev = _insurer_evidence(db, world, second, amount="1", status=ReportedStatus.PARTIAL)

    over = client.post(f"{API}/collections/evidence/{ev.id}/confirm",
                       json={"amount": "999", "currency": "UF"}, headers=headers_a)
    assert over.status_code == 409 and "supera el saldo" in over.json()["detail"]

    to_paid = client.post(f"{API}/collections/evidence/{ev.id}/confirm",
                          json={"installment_id": first.id}, headers=headers_a)
    assert to_paid.status_code == 409 and "ya figura pagada" in to_paid.json()["detail"]

    # "Otra cuota" with a "paid" report: that cuota's own balance, never more.
    paid_ev = _insurer_evidence(db, world, second, amount="999")
    ok = client.post(f"{API}/collections/evidence/{paid_ev.id}/confirm",
                     json={"installment_id": third.id}, headers=headers_a)
    assert ok.status_code == 200, ok.text
    payment = db.get(Payment, ok.json()["payment_id"])
    assert payment.installment_id == third.id and payment.amount_uf == Decimal(str(third.gross_amount_uf))


def test_kpis_and_plan_status_use_capped_amounts(client, db, world, headers_a):
    _policy, plan = _plan(db, world)
    first = plan.installments[0]
    gross = Decimal(str(first.gross_amount_uf))
    # Legacy over-payment written around the ledger (pre-fix data).
    make_payment(db, first, amount_uf="3.80", paid_on=TODAY)
    make_payment(db, first, amount_uf=gross, paid_on=TODAY)
    db.commit()
    summary = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert Decimal(summary["collected_month_uf"]) == gross
    status_body = client.get(f"{API}/collections/{plan.id}/status", headers=headers_a).json()
    assert Decimal(status_body["paid_uf"]) == gross
    rows = client.get(f"{API}/collections/installments", params={"plan_id": plan.id}, headers=headers_a).json()
    assert Decimal(next(r for r in rows["items"] if r["id"] == first.id)["paid_uf"]) == gross


# =============================================================================
# 2 — the reminder asks for the balance and greets the recipient
# =============================================================================

def test_notice_preview_uses_the_balance_and_greets_the_payer(client, db, world, headers_a):
    _policy, plan = _plan(db, world)
    second = plan.installments[1]
    assert _pay(client, headers_a, second.id, "3.80").status_code == 201
    res = client.post(f"{API}/collections/notices/preview",
                      json={"installment_id": second.id, "kind": "payer_overdue"}, headers=headers_a)
    assert res.status_code == 200, res.text
    body = res.json()
    balance = Decimal(str(second.gross_amount_uf)) - Decimal("3.80")
    assert Decimal(body["amount_uf"]) == balance and Decimal(body["paid_uf"]) == Decimal("3.80")
    assert body["recipient_name"] == "Rentas Los Andes SpA"
    assert body["to_address"] == "pagos@rentaslosandes.cl"
    assert "Hola Rentas Los Andes SpA" in body["text"]
    assert "UF 42,5333" in body["text"] and "UF 46,3333" in body["text"]  # balance + "de UF <gross>"
    variables = client.get(f"{API}/collections/templates", headers=headers_a).json()["variables"]
    assert {"recipient_name", "balance_uf", "balance_clp", "paid_uf", "amount_uf"} <= set(variables)


# =============================================================================
# 14 — template approval is per template
# =============================================================================

def test_editing_one_template_unapproves_only_that_one(client, db, world, headers_a):
    approved = client.post(f"{API}/collections/templates/approve", headers=headers_a)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["approved"] is True and all(i["is_approved"] for i in body["items"])
    assert all(i["approved_by_name"] == world.a.admin.full_name for i in body["items"])

    target = next(i for i in body["items"] if i["kind"] == "payer_overdue" and i["cadence_level"] == 3)
    edited = client.put(f"{API}/collections/templates", headers=headers_a, json={"templates": [{
        "kind": "payer_overdue", "cadence_level": 3, "channel": "email",
        "subject": target["subject"], "body": target["body"] + "\n\nGracias.",
    }]})
    assert edited.status_code == 200, edited.text
    items = {(i["kind"], i["cadence_level"]): i for i in edited.json()["items"]}
    assert items[("payer_overdue", 3)]["is_approved"] is False
    assert items[("payer_reminder", -5)]["is_approved"] is True  # the others keep sending
    assert edited.json()["pending_count"] == 1 and edited.json()["approved_at"] is not None

    again = client.post(f"{API}/collections/templates/approve", headers=headers_a,
                        json={"items": [{"kind": "payer_overdue", "cadence_level": 3}]})
    assert again.status_code == 200, again.text
    assert again.json()["approved"] is True and again.json()["pending_count"] == 0
    bad = client.post(f"{API}/collections/templates/approve", headers=headers_a,
                      json={"items": [{"kind": "payer_overdue", "cadence_level": 77}]})
    assert bad.status_code == 422


def test_the_job_skips_an_unapproved_slot_and_sends_the_approved_ones(db, world, monkeypatch, fake_transport):
    from app.core.config import settings
    from app.jobs import run_job
    from app.services.collection.templates import approve_templates, upsert_templates

    monkeypatch.setattr(settings, "MAIL_ENABLED", True)
    _policy, plan = _plan(db, world, start=TODAY - timedelta(days=3))  # cuota 1 is +3 today
    db.add(CollectionSettings(broker_id=world.a.broker_id, notices_enabled=True))
    plan.notices_enabled = True
    db.flush()
    approve_templates(db, world.a.broker_id, user_id=world.a.admin.id)
    upsert_templates(db, world.a.broker_id, [{
        "kind": "payer_overdue", "cadence_level": 3, "channel": "email",
        "subject": "Cuota pendiente", "body": "Hola {{recipient_name}}: saldo UF {{balance_uf}}.",
    }])
    db.commit()
    stats = run_job(db, "collection.notices", datetime.now(timezone.utc)).stats
    assert stats["unapproved_template"] == 1 and stats["sent"] == 0


# =============================================================================
# 3 — the bitácora speaks Spanish, collapses a policy entry and is UTC
# =============================================================================

def test_skip_to_and_policy_entry_write_spanish_collapsed_activity(client, db, world, headers_a):
    from app.services import case_files as machine

    case = make_case_file(db, world.a, stage=CaseStage.LEAD)
    # Creating a folder through the API writes its first stage event.
    db.add(CaseFileStageEvent(broker_id=case.broker_id, case_file_id=case.id,
                              from_stage=None, to_stage=CaseStage.LEAD))
    db.commit()
    res = client.post(f"{API}/case-files/{case.id}/skip-to",
                      json={"target_stage": "quotes_received"}, headers=headers_a)
    assert res.status_code == 200, res.text
    skipped = db.scalar(select(Activity).where(Activity.action == "case_file.skipped_to",
                                                Activity.entity_id == case.id))
    assert skipped.description == "Saltó de Interés a Cotizaciones recibidas; 4 etapas omitidas"
    stages = {s["stage"]: s["state"] for s in res.json()["stages"]}
    assert stages["intake"] == "skipped" and stages["lead"] == "done"

    other = make_case_file(db, world.a, stage=CaseStage.LEAD, n=2, placement_id=None)
    machine.advance_to_active(db, case=other, user=world.a.admin, fresh=True)
    db.commit()
    rows = db.scalars(select(Activity).where(Activity.entity_type == EntityType.CASE_FILE,
                                             Activity.entity_id == other.id)).all()
    assert [r.description for r in rows] == ["La cuenta entró con la póliza; 11 etapas omitidas"]
    events = db.scalars(select(CaseFileStageEvent).where(CaseFileStageEvent.case_file_id == other.id)).all()
    assert sum(1 for e in events if e.is_skipped) == 11  # the rail keeps every stage

    timeline = client.get(f"{API}/case-files/{case.id}/timeline", headers=headers_a).json()["entries"]
    for entry in timeline:
        assert entry["occurred_at"].endswith("Z") or entry["occurred_at"].endswith("+00:00")
        for text in (entry.get("detail") or "",):
            assert "_" not in text and "R42" not in text
    assert any(e["is_skipped"] for e in timeline if e["kind"] == "stage")
    feed = client.get(f"{API}/activities", params={"entity_type": "case_file", "entity_id": case.id},
                      headers=headers_a).json()["items"]
    assert feed and all(i["occurred_at"].endswith(("Z", "+00:00")) for i in feed)


def test_group_timeline_dates_are_noon_in_chile_and_flagged():
    from app.api.routers.account_groups import _date_only, _instant

    moment = _instant(date(2026, 3, 1))
    assert moment.hour in (15, 16)  # noon America/Santiago in UTC
    assert _date_only(None, date(2026, 3, 1)) is True
    assert _date_only(datetime(2026, 3, 1, 12), date(2026, 3, 1)) is False


# =============================================================================
# 9 / 8 — no walking back from Vigente; skipped stages read skipped
# =============================================================================

def test_a_vigente_account_never_walks_back(client, db, world, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.ACTIVE)
    db.commit()
    res = client.post(f"{API}/case-files/{case.id}/transition",
                      json={"to_stage": "mirror_validation"}, headers=headers_a)
    assert res.status_code == 409 and "Vigente" in res.json()["detail"]
    body = client.get(f"{API}/case-files/{case.id}/transitions", headers=headers_a).json()
    assert body["backward_locked"] is True and "endoso" in body["backward_locked_reason"]
    backward = [o for o in body["options"] if o["direction"] == "backward"]
    assert backward and not any(o["allowed"] for o in backward)
    assert next(o for o in body["options"] if o["to_stage"] == "closed")["direction"] == "close"


# =============================================================================
# 13 / 17 — Forpay: only an ACTIVE *verified* account is native
# =============================================================================

def test_a_failed_verify_drops_native_and_the_card_says_so(client, db, world, headers_a):
    from app.services.collection.resolver import resolve_collection_mode
    from tests.test_v12_payments import make_platform_user
    from tests.conftest import auth_headers

    _policy, plan = _plan(db, world)
    account = make_provider_account(db)
    make_native_channel(db, world.insurer, account)
    db.commit()
    inst = plan.installments[0]
    assert resolve_collection_mode(db, inst, broker_id=world.a.broker_id).level == "native"

    account.last_error = "La cuenta no tiene la llave pública (public_key_pem)"
    db.commit()
    assert resolve_collection_mode(db, inst, broker_id=world.a.broker_id).level == "assisted"
    res = client.post(f"{API}/payments/intents", json={"installment_ids": [inst.id]}, headers=headers_a)
    assert res.status_code == 409 and "sube el comprobante" in res.json()["detail"]

    platform = auth_headers(client, make_platform_user(db).email)
    card = client.get(f"{API}/platform/provider-accounts/{account.id}", headers=platform).json()
    assert card["verify_status"] == "failed" and card["usable"] is False
    assert card["last_error"].startswith("La cuenta no tiene")
    # Webhook URLs point at the BACKEND origin (the test server), never the SPA host.
    assert card["webhook_url"].startswith("http://testserver/api/v1/webhooks/forpay?")

    unverified = make_provider_account(db, verified=False, label="nunca verificada")
    db.commit()
    other = client.get(f"{API}/platform/provider-accounts/{unverified.id}", headers=platform).json()
    assert other["verify_status"] == "never" and other["usable"] is False


# =============================================================================
# 16 — portal search: a queued run that cannot run says so; steps in Spanish
# =============================================================================

def test_queued_run_with_rpa_disabled_will_not_run_and_steps_have_labels(
    client, db, world, headers_a, portal_search_on
):
    run = PortalSearchRun(
        broker_id=world.a.broker_id, portal="hdi", requested_by_user_id=world.a.admin.id,
        input={"plate": "ABCD12", "start_date": "2026-10-27", "insured_rut": "76345678-1"},
        status=PortalRunStatus.QUEUED,
        steps=[{"name": "open_login", "op": "goto", "ok": True}, {"name": "mystery", "ok": False}],
    )
    db.add(run)
    db.commit()
    body = client.get(f"{API}/portal-searches/{run.id}", headers=headers_a).json()
    assert body["will_not_run"] is True
    assert body["will_not_run_reason"].startswith("No se ejecutará: la búsqueda en portales no está habilitada")
    assert [s["label"] for s in body["steps"]] == ["Abrir el portal", "Paso 2"]
    assert body["input_display"]["start_date"] == "27-10-2026"
    assert body["input_display"]["insured_rut"] == "76.345.678-1"
    portals = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()
    hdi = next(p for p in portals if p["key"] == "hdi")
    assert hdi["status_label"] == "No disponible en este ambiente"


# =============================================================================
# P3 — inquiries: one open inquiry per cuota; paid cuotas are marked / cancelled
# =============================================================================

def test_duplicate_open_inquiry_is_409_and_paid_cuotas_are_marked(client, db, world, headers_a):
    policy, plan = _plan(db, world)
    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.cl")
    db.commit()
    first = plan.installments[0]
    created = client.post(f"{API}/inquiries", json={"policy_id": policy.id, "installment_ids": [first.id]},
                          headers=headers_a)
    assert created.status_code == 201, created.text
    dup = client.post(f"{API}/inquiries", json={"policy_id": policy.id, "installment_ids": [first.id]},
                      headers=headers_a)
    assert dup.status_code == 409
    assert dup.json()["detail"]["existing_inquiry_id"] == created.json()["id"]
    assert "Ya hay una consulta abierta" in dup.json()["detail"]["message"]

    assert _pay(client, headers_a, first.id, first.gross_amount_uf).status_code == 201
    detail = client.get(f"{API}/inquiries/{created.json()['id']}", headers=headers_a).json()
    assert detail["installments"][0]["is_paid"] is True and detail["all_paid"] is True
    resend = client.post(f"{API}/inquiries/{created.json()['id']}/resend", json={}, headers=headers_a)
    assert resend.status_code == 409 and "ya figuran pagadas" in resend.json()["detail"]


def test_the_inquiries_job_cancels_an_unsent_inquiry_whose_cuotas_are_paid(client, db, world, headers_a):
    from app.jobs import run_job

    policy, plan = _plan(db, world)
    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.cl")
    db.commit()
    first = plan.installments[0]
    created = client.post(f"{API}/inquiries", json={"policy_id": policy.id, "installment_ids": [first.id]},
                          headers=headers_a).json()
    assert created["status"] == "draft"  # MAIL_ENABLED off: "Sin enviar"
    assert _pay(client, headers_a, first.id, first.gross_amount_uf).status_code == 201
    stats = run_job(db, "collection.inquiries", datetime.now(timezone.utc)).stats
    assert stats["cancelled_paid"] == 1
    db.expire_all()
    assert db.get(StatusInquiry, created["id"]).status == InquiryStatus.CANCELLED
    assert db.scalar(select(Activity).where(
        Activity.action == "status_inquiry.cancelled", Activity.description.like("%ya figuran pagadas%")
    )) is not None


# =============================================================================
# P3 — "PAC rechazado" never on a PAT cuota
# =============================================================================

def test_board_rows_name_the_debit_kind(client, db, world, headers_a):
    _p1, pac = _plan(db, world, payment_mode=PaymentMode.DIRECT_DEBIT)
    _p2, pat = _plan(db, world, payment_mode=PaymentMode.CARD_DEBIT, policy_number="POL-PAT-1")
    rows = client.get(f"{API}/collections/installments", headers=headers_a).json()["items"]
    kinds = {r["plan_id"]: r["direct_debit_kind"] for r in rows}
    assert kinds[pac.id] == "pac" and kinds[pat.id] == "pat"


def test_display_rut_formats_and_never_raises():
    from app.services.identifiers import display_rut

    assert display_rut("76345678-1") == "76.345.678-1"
    assert display_rut("763456781") == "76.345.678-1"
    assert display_rut("no-es-rut") == "no-es-rut"
    assert display_rut(None) == ""
