"""Review round 2 — backend fixes B1–B8 (docs/v12/12-review-r2-fixes.md §B).

Hermetic like the rest of the suite: the chat model is faked (conftest blanks
the AI key), no mail, no S3 (boto3 is faked where a presigned URL matters).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.activity import Activity
from app.models.ai import AgentActionStatus, Extraction
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    EntityType,
    EvidenceLevel,
    NotificationKind,
    NotificationStatus,
    PaymentMode,
    PolicyOrigin,
)
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.policy import Policy
from app.services import ai as ai_service
from app.services.collection import past_review
from app.services.collection.clock import business_today, run_broker_clock
from tests.conftest import API, make_case_file, make_policy
from tests.fakes_v12 import make_policy_with_plan
from tests.test_agent_tools import _seed_action
from tests.test_v8_broker_journey import _FakeChatModel

TODAY = business_today()


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


def _fake(monkeypatch, payload: dict):
    monkeypatch.setattr(
        ai_service, "_chat_model", lambda **kwargs: _FakeChatModel(content=json.dumps(payload))
    )


def _no_ai(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("the duplicate must be caught before any AI call")

    monkeypatch.setattr(ai_service, "_chat_model", _boom)


_INSURER = {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"}


def _payload(number: str, *, insurer=None, installments=None, start="2026-01-01", end="2027-01-01"):
    body = {
        "policy_number": number,
        "insurer": insurer if insurer is not None else _INSURER,
        "broker": {"name": "Corredora Alfa SpA"},
        "period_start_at": f"{start}T12:00:00Z",
        "period_end_at": f"{end}T12:00:00Z",
        "total_insured_amount_uf": 17920,
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 20},
        "payment_mode": "PAC",
    }
    if installments is not None:
        body["installments"] = installments
    return body


def _doc(db, tenant, tmp_path, *, case_id=None, name="poliza.txt", body=None, user=None):
    path = tmp_path / name
    text = body or ("Póliza emitida por la aseguradora. " * 20)
    path.write_text(text, encoding="utf-8")
    document = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CASE_FILE if case_id else EntityType.CLIENT,
        entity_id=case_id or tenant.client.id,
        case_file_id=case_id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        category=DocumentCategory.POLICY,
        uploaded_by_id=user.id if user is not None else None,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _count(db, model, *where) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _upload(client, headers, document, **extra):
    return client.post(
        f"{API}/policies/upload", json={"document_id": document.id, **extra}, headers=headers
    )


# =============================================================================
# B1 — duplicate upload: 409 policy_duplicate, no side effects
# =============================================================================


def test_reuploading_the_same_policy_is_a_409_with_no_side_effects(
    client, db, world, tmp_path, ai_key, monkeypatch, headers_a
):
    case = make_case_file(db, world.a)
    db.commit()
    first_doc = _doc(db, world.a, tmp_path, case_id=case.id, user=world.a.admin, body="primera " * 50)
    cuotas = [
        {"number": 1, "due_date": (TODAY + timedelta(days=10)).isoformat(), "gross_amount_uf": 69.5},
        {"number": 2, "due_date": (TODAY + timedelta(days=40)).isoformat(), "gross_amount_uf": 69.5},
    ]
    _fake(monkeypatch, _payload("DUP-1", installments=cuotas))
    first = _upload(client, headers_a, first_doc)
    assert first.status_code == 201, first.text
    policy_id = first.json()["policy"]["id"]

    before = {
        "notifications": _count(db, Notification),
        "plans": _count(db, CollectionPlan),
        "installments": _count(db, CollectionInstallment),
        "policies": _count(db, Policy),
        "activities": _count(db, Activity),
    }
    again = _doc(db, world.a, tmp_path, name="again.txt", user=world.a.admin, body="segunda " * 50)
    again_id = again.id
    _fake(monkeypatch, _payload("DUP-1", installments=cuotas))
    response = _upload(client, headers_a, again, case_file_id=case.id)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "policy_duplicate"
    assert detail["policy_id"] == policy_id and detail["policy_number"] == "DUP-1"
    assert detail["insurer_name"]
    # The company name's own trailing period ("… S.A.") is not doubled.
    assert detail["reason"] == f"Esta póliza ya existe: N° DUP-1 de {detail['insurer_name'].rstrip('.')}."
    # The orphan the same user just uploaded is gone (and its read with it).
    assert detail["document_removed"] is True
    db.expire_all()
    assert db.get(Document, again_id) is None
    assert _count(db, Extraction, Extraction.document_id == again_id) == 0
    assert not (tmp_path / "again.txt").exists()
    after = {
        "notifications": _count(db, Notification),
        "plans": _count(db, CollectionPlan),
        "installments": _count(db, CollectionInstallment),
        "policies": _count(db, Policy),
        "activities": _count(db, Activity),
    }
    assert after == before
    kept = db.get(Policy, policy_id)
    assert kept.source_document_id == first_doc.id


def test_the_same_file_is_refused_before_any_read(
    client, db, world, tmp_path, ai_key, monkeypatch, headers_a
):
    case = make_case_file(db, world.a)
    db.commit()
    first_doc = _doc(db, world.a, tmp_path, case_id=case.id, name="a.txt", body="mismo pdf " * 40)
    _fake(monkeypatch, _payload("DUP-SUM"))
    created = _upload(client, headers_a, first_doc)
    assert created.status_code == 201, created.text
    # The very same bytes, uploaded again by ANOTHER user's session: still a
    # duplicate, no AI call; the copy is not this user's -> it stays.
    copy = _doc(db, world.a, tmp_path, name="b.txt", body="mismo pdf " * 40, user=world.a.executive)
    _no_ai(monkeypatch)
    response = _upload(client, headers_a, copy, case_file_id=case.id)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "policy_duplicate" and detail["document_removed"] is False
    db.expire_all()
    remained = db.get(Document, copy.id)
    assert remained is not None and remained.case_file_id is None  # nothing re-filed


def test_name_only_insurer_duplicate_uses_the_name_suggestion(
    client, db, world, tmp_path, ai_key, monkeypatch, headers_a
):
    from app.services import policy_duplicates

    policy = make_policy(
        db, world.a, world.insurer, policy_number="NAME-1", source_document_id=None,
        has_source_document=True,
    )
    db.commit()
    insurer_name = world.insurer.legal_name
    distinctive = next(
        t for t in insurer_name.split() if t.lower().strip(".,") not in {"seguros", "s.a.", "compañía", "de"}
    )

    class P:
        policy_number = "NAME-1"

        class insurer:  # noqa: N801 - a read InsurerRef stand-in
            rut = None
            cmf_code = None
            legal_name = f"{distinctive} Seguros Generales"
            trade_name = None

    assert policy_duplicates.duplicate_by_number(db, broker_id=world.a.broker_id, payload=P).id == policy.id

    class Other(P):
        class insurer:  # noqa: N801
            rut = None
            cmf_code = None
            legal_name = "Zzzyx Aseguradora"
            trade_name = None

    assert policy_duplicates.duplicate_by_number(db, broker_id=world.a.broker_id, payload=Other) is None
    # The broker's explicit choice decides when given.
    assert (
        policy_duplicates.duplicate_by_number(
            db, broker_id=world.a.broker_id, payload=Other, chosen_insurer_id=world.insurer.id
        ).id
        == policy.id
    )
    # Another tenant never sees it.
    assert policy_duplicates.duplicate_by_number(db, broker_id=world.b.broker_id, payload=P) is None


def test_a_manual_policy_without_its_pdf_is_completed_not_refused(
    client, db, world, tmp_path, ai_key, monkeypatch, headers_a
):
    case = make_case_file(db, world.a)
    manual = make_policy(
        db, world.a, world.insurer, policy_number="MAN-PDF-1", case_file_id=case.id,
        origin=PolicyOrigin.MANUAL, has_source_document=False,
    )
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id, user=world.a.admin)
    _fake(monkeypatch, _payload("MAN-PDF-1", insurer={"legal_name": world.insurer.legal_name,
                                                       "rut": world.insurer.rut,
                                                       "cmf_code": world.insurer.cmf_code}))
    response = _upload(client, headers_a, document)
    assert response.status_code == 201, response.text
    assert response.json()["policy"]["id"] == manual.id
    assert response.json()["policy"]["has_source_document"] is True


# =============================================================================
# B2 — past cuotas of a policy loaded late are PENDING REVIEW
# =============================================================================


def test_upload_with_past_cuotas_asks_and_raises_no_alert(
    client, db, world, tmp_path, ai_key, monkeypatch, headers_a
):
    case = make_case_file(db, world.a)
    db.commit()
    cuotas = [
        {"number": 1, "due_date": (TODAY - timedelta(days=120)).isoformat(), "gross_amount_uf": 46.33},
        {"number": 2, "due_date": (TODAY - timedelta(days=90)).isoformat(), "gross_amount_uf": 46.33},
        {"number": 3, "due_date": (TODAY + timedelta(days=20)).isoformat(), "gross_amount_uf": 46.34},
    ]
    document = _doc(db, world.a, tmp_path, case_id=case.id, user=world.a.admin)
    _fake(monkeypatch, _payload("LATE-1", installments=cuotas,
                                start=(TODAY - timedelta(days=150)).isoformat(),
                                end=(TODAY + timedelta(days=215)).isoformat()))
    response = _upload(client, headers_a, document)
    assert response.status_code == 201, response.text
    body = response.json()
    past = body["past_installments"]
    assert past["count"] == 2
    assert Decimal(str(past["total_uf"])) == Decimal("92.66")
    assert past["first_due"] == cuotas[0]["due_date"] and past["last_due"] == cuotas[1]["due_date"]
    assert body["policy"]["past_installments_pending"]["plan_id"] == past["plan_id"]

    plan_id = past["plan_id"]
    insts = db.scalars(
        select(CollectionInstallment).where(CollectionInstallment.collection_plan_id == plan_id)
    ).all()
    late_ids = {i.id for i in insts if i.number in (1, 2)}
    open_alerts = db.scalars(select(Notification).where(
        Notification.origin_type == "installment",
        Notification.origin_id.in_(late_ids),
        Notification.status != NotificationStatus.CLOSED,
    )).all()
    assert open_alerts == []  # no "Riesgo de término" at upload
    assert all(i.status.value == "pending" for i in insts if i.id in late_ids)

    # Board + KPIs: not overdue, in the review bucket / card.
    summary = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert summary["overdue"]["count"] == 0
    assert summary["past_installments_pending"]["count"] == 2
    board = client.get(f"{API}/collections/installments", headers=headers_a).json()
    assert board["counts"]["overdue"] == 0 and board["counts"]["review"] == 2
    states = {row["number"]: row["state"] for row in board["items"]}
    assert states[1] == states[2] == "pending_review"
    cards = client.get(f"{API}/policies/summary", headers=headers_a).json()
    assert cards["overdue_installments"] == 0

    # The daily clock keeps them quiet too.
    run_broker_clock(db, world.a.broker_id, now=datetime.now(timezone.utc))
    db.commit()
    assert _count(
        db, Notification, Notification.origin_type == "installment",
        Notification.origin_id.in_(late_ids), Notification.status != NotificationStatus.CLOSED,
    ) == 0

    # The policy page and the plan read carry the banner data.
    detail = client.get(f"{API}/policies/{body['policy']['id']}", headers=headers_a).json()
    assert detail["past_installments_pending"]["count"] == 2
    plan_read = client.get(f"{API}/collections/{plan_id}", headers=headers_a).json()
    assert plan_read["past_installments_pending"]["count"] == 2


def _late_plan(db, world, *, policy_number="LATE-P", mode=PaymentMode.TRANSFER):
    policy, plan = make_policy_with_plan(
        db, world.a, world.insurer, policy_number=policy_number,
        start=TODAY - timedelta(days=75), payment_mode=mode, origin=PolicyOrigin.READER,
    )
    db.commit()
    past_review.mark_if_past(db, plan, today=TODAY, user=world.a.admin)
    db.commit()
    db.refresh(plan)
    return policy, plan


def test_declaring_the_past_cuotas_paid_books_them_through_the_ledger(
    client, db, world, headers_a
):
    policy, plan = _late_plan(db, world)
    pending = past_review.pending_summary(db, plan, today=TODAY)
    assert pending["count"] == 3  # due today-75, -45, -15

    url = f"{API}/collections/plans/{plan.id}/past-installments"
    response = client.post(url, json={"decision": "paid"}, headers=headers_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "plan_id": plan.id, "decision": "paid", "already_decided": False,
        "payments_registered": 3, "past_installments_pending": None,
        "counts": {"paid": 3},
    }
    payments = db.scalars(select(Payment).order_by(Payment.id)).all()
    by_inst = {p.installment_id: p for p in payments}
    for inst in plan.installments:
        payment = by_inst[inst.id]
        assert payment.paid_on == inst.due_date
        assert payment.amount_uf == Decimal(str(inst.gross_amount_uf))
        assert payment.method == PaymentMode.TRANSFER
        assert payment.evidence_level == EvidenceLevel.DECLARED
        assert payment.note == "Declarado pagado al cargar la póliza"
    decided = db.scalars(select(Activity).where(Activity.action == past_review.DECIDED_ACTION)).one()
    assert decided.entity_id == plan.id and decided.meta["decision"] == "paid"

    # Idempotent: the same call writes nothing more; the other decision is 409.
    again = client.post(url, json={"decision": "paid"}, headers=headers_a)
    assert again.status_code == 200 and again.json()["already_decided"] is True
    assert _count(db, Payment) == 3
    other = client.post(url, json={"decision": "unpaid"}, headers=headers_a)
    assert other.status_code == 409
    assert other.json()["detail"]["code"] == "past_installments_decided"
    assert client.get(f"{API}/policies/{policy.id}", headers=headers_a).json()[
        "past_installments_pending"
    ] is None


def test_declaring_them_unpaid_lets_the_alerts_resume(client, db, world, headers_a):
    _policy, plan = _late_plan(db, world, policy_number="LATE-U")
    first = plan.installments[0]
    assert _count(db, Notification, Notification.origin_id == first.id) == 0
    response = client.post(
        f"{API}/collections/plans/{plan.id}/past-installments",
        json={"decision": "unpaid"}, headers=headers_a,
    )
    assert response.status_code == 200, response.text
    assert response.json()["payments_registered"] == 0
    assert response.json()["counts"].get("termination_risk", 0) + response.json()["counts"].get(
        "overdue", 0
    ) == 3
    alert = db.scalars(select(Notification).where(
        Notification.origin_type == "installment", Notification.origin_id == first.id
    )).one()
    assert alert.kind in (NotificationKind.TERMINATION_RISK, NotificationKind.INSTALLMENT_OVERDUE)
    summary = client.get(f"{API}/collections/summary", headers=headers_a).json()
    assert summary["overdue"]["count"] == 3 and summary["past_installments_pending"]["count"] == 0


def test_past_installments_endpoint_guards(client, db, world, headers_a, headers_b, headers_a_tech):
    _policy, plan = _late_plan(db, world, policy_number="LATE-G")
    url = f"{API}/collections/plans/{plan.id}/past-installments"
    assert client.post(url, json={"decision": "paid"}, headers=headers_b).status_code == 404
    assert client.post(url, json={"decision": "paid"}, headers=headers_a_tech).status_code == 403
    assert client.post(url, json={"decision": "maybe"}, headers=headers_a).status_code == 422
    # A plan with nothing past -> 409 with a Spanish reason.
    _p2, fresh = make_policy_with_plan(db, world.a, world.insurer, policy_number="FRESH",
                                       start=TODAY + timedelta(days=5))
    db.commit()
    nothing = client.post(
        f"{API}/collections/plans/{fresh.id}/past-installments",
        json={"decision": "paid"}, headers=headers_a,
    )
    assert nothing.status_code == 409
    assert nothing.json()["detail"]["code"] == "no_past_installments"


def test_manual_policy_plan_with_past_dates_is_pending_review(client, db, world, headers_a):
    policy = make_policy(db, world.a, world.insurer, policy_number="MAN-LATE",
                         origin=PolicyOrigin.MANUAL)
    db.commit()
    res = client.post(
        f"{API}/collections/plans",
        headers=headers_a,
        json={
            "policy_id": policy.id,
            "installments": [
                {"number": 1, "gross_amount_uf": "69.5", "due_date": (TODAY - timedelta(days=60)).isoformat()},
                {"number": 2, "gross_amount_uf": "69.5", "due_date": (TODAY + timedelta(days=30)).isoformat()},
            ],
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["past_installments_pending"]["count"] == 1
    first = next(i for i in body["installments"] if i["number"] == 1)
    assert first["status"] == "pending"
    assert db.scalar(select(Notification).where(
        Notification.origin_type == "installment", Notification.origin_id == first["id"])) is None


def test_payer_notices_skip_pending_review_cuotas(db, world):
    from app.services import notices

    _policy, plan = _late_plan(db, world, policy_number="LATE-N")
    plan.notices_enabled = True
    db.commit()
    key = notices._notice_step(
        db, broker_id=world.a.broker_id, plan=plan, inst=plan.installments[0],
        cadence=[-15, -5, 0, 3, 10], today=TODAY, now=datetime.now(timezone.utc),
    )
    assert key == "pending_review"


# =============================================================================
# B3 — technical read: the whole coverage table, sublimits only when explicit
# =============================================================================


def test_technical_guidance_and_merge():
    from app.schemas.extraction.policy_tiers import TECHNICAL_GROUPS
    from app.services.policy_reading import technical_sections

    groups = {g.name: g for g in TECHNICAL_GROUPS}
    assert "TODAS las filas del CUADRO DE COBERTURAS" in groups["tech_risk"].guidance
    assert "vehículo de reemplazo" in groups["tech_risk"].guidance
    assert "devuelve sublimits: []" in groups["tech_sublimits"].guidance

    sections = technical_sections({
        "tech_risk": {"coverages": [
            {"name": "Daños propios", "limit_uf": 455},
            {"name": "Robo de accesorios", "sublimit_text": "Incluida"},
        ]},
        "tech_sublimits": {"sublimits": [
            {"name": "DAÑOS PROPIOS", "limit_uf": 455},  # an exact repeat -> dropped
            {"name": "Robo de accesorios", "sublimit_text": "Hasta 10%, tope UF 60"},  # kept
        ]},
    })
    names = [(row["name"], row.get("sublimit_text")) for row in sections["coverages"]]
    assert names == [
        ("Daños propios", None),
        ("Robo de accesorios", "Incluida"),
        ("Robo de accesorios", "Hasta 10%, tope UF 60"),
    ]


# =============================================================================
# B4 / B5 — expiring filter, "Mías" on the KPI cards
# =============================================================================


def test_policies_expiring_filter_and_owner_cards(client, db, world, headers_a, headers_a_exec):
    case = make_case_file(db, world.a)
    case.owner_user_id = world.a.executive.id
    soon = make_policy(db, world.a, world.insurer, policy_number="EXP-SOON",
                       start_date=TODAY - timedelta(days=300), end_date=TODAY + timedelta(days=10),
                       case_file_id=case.id)
    make_policy(db, world.a, world.insurer, policy_number="EXP-LATER",
                start_date=TODAY - timedelta(days=100), end_date=TODAY + timedelta(days=200))
    make_policy(db, world.b, world.insurer, policy_number="EXP-B",
                start_date=TODAY - timedelta(days=300), end_date=TODAY + timedelta(days=10))
    db.commit()

    page = client.get(f"{API}/policies", params={"expiring_within_days": 30}, headers=headers_a).json()
    assert [row["policy_number"] for row in page["items"]] == ["EXP-SOON"]
    assert client.get(
        f"{API}/policies", params={"expiring_within_days": -1}, headers=headers_a
    ).status_code == 422

    everyone = client.get(f"{API}/policies/summary", headers=headers_a_exec).json()
    mine = client.get(f"{API}/policies/summary", params={"mine": 1}, headers=headers_a_exec).json()
    mine_owner = client.get(f"{API}/policies/summary", params={"owner": "me"}, headers=headers_a_exec).json()
    assert everyone["owner_scope"] == "all" and everyone["active_count"] == 2
    assert mine["owner_scope"] == "mine" and mine["active_count"] == 1 == mine_owner["active_count"]
    listed = client.get(
        f"{API}/policies", params={"mine": 1, "status": "active"}, headers=headers_a_exec
    ).json()
    assert listed["total"] == mine["active_count"]
    assert soon.id in {row["id"] for row in listed["items"]}


def test_collections_summary_honours_owner_me(client, db, world, headers_a_exec):
    case = make_case_file(db, world.a)
    case.owner_user_id = world.a.executive.id
    make_policy_with_plan(db, world.a, world.insurer, policy_number="OWN-1", case_file_id=case.id)
    make_policy_with_plan(db, world.a, world.insurer, policy_number="TEAM-1")
    db.commit()
    team = client.get(f"{API}/collections/summary", headers=headers_a_exec).json()
    mine = client.get(f"{API}/collections/summary", params={"owner": "me"}, headers=headers_a_exec).json()
    alias = client.get(f"{API}/collections/summary", params={"mine": 1}, headers=headers_a_exec).json()
    assert team["owner_scope"] == "all" and mine["owner_scope"] == "mine"
    assert team["overdue"]["count"] == 6 and mine["overdue"]["count"] == 3 == alias["overdue"]["count"]
    board = client.get(
        f"{API}/collections/installments", params={"bucket": "overdue", "owner": "me"},
        headers=headers_a_exec,
    ).json()
    assert board["total"] == mine["overdue"]["count"]


# =============================================================================
# B6 — the confirm card's display rows; Spanish result line
# =============================================================================


def test_agent_action_display_is_spanish_and_never_shows_ids(client, db, world, headers_a):
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="AG-77",
                                         n_installments=10)
    db.commit()
    first = plan.installments[0]
    thread, action = _seed_action(
        db, tenant=world.a, user=world.a.admin, tool="register_payment",
        arguments={"installment_id": first.id, "amount": "13.90", "currency": "UF",
                   "paid_on": "2026-10-19", "method": "transfer", "confirmed_by_insurer": False},
        module="Collections", action="Edit",
    )
    listed = client.get(f"{API}/ai/agent/actions", params={"thread_id": thread.id}, headers=headers_a)
    assert listed.status_code == 200, listed.text
    display = listed.json()["items"][0]["display"]
    rows = {row["label"]: row["value"] for row in display}
    assert rows["Cuota"] == "1 de 10 · póliza N° AG-77"
    assert rows["Monto"] == "UF 13,90"
    assert rows["Medio de pago"] == "Transferencia"
    assert rows["Fecha de pago"] == "19-10-2026"
    assert rows["Evidencia"] == "Pago declarado (sin confirmación de la compañía)"
    flat = json.dumps(display, ensure_ascii=False)
    assert f"#{first.id}" not in flat and "installment" not in flat and "transfer\"" not in flat

    confirmed = client.post(f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == AgentActionStatus.CONFIRMED.value
    assert body["result"]["detail"] == "Pago registrado: pago declarado, cuota pagada"


def test_display_resolves_nothing_foreign():
    from app.services.agent_display import money, payment_result_line, pct

    assert money("1181.28") == "UF 1.181,28"
    assert money(45300, "CLP") == "$ 45.300"
    assert pct("10.000") == "10 %"
    assert payment_result_line("insurer_confirmed", "overdue") == (
        "Pago registrado: confirmado por la compañía, cuota vencida"
    )


def test_display_of_a_foreign_installment_is_omitted(db, world):
    from app.services.agent_display import action_display

    _policy, plan = make_policy_with_plan(db, world.b, world.insurer, policy_number="FOREIGN")
    db.commit()
    rows = action_display(
        db, broker_id=world.a.broker_id, tool="register_payment",
        arguments={"installment_id": plan.installments[0].id, "amount": "1", "paid_on": "2026-01-01"},
    )
    assert "Cuota" not in {row["label"] for row in rows}


def test_agent_system_prompt_forbids_tool_names_and_enums():
    assert "NUNCA escribas en tu respuesta el nombre de una herramienta" in ai_service._AGENT_SYSTEM
    assert "cuota 1 de 10" in ai_service._AGENT_SYSTEM


# =============================================================================
# B7 — downloads: mime + inline/attachment with the original name
# =============================================================================


def _stored(db, world, tmp_path, name="00C Cuadro de valores asegurados.pdf"):
    rel = "documents/case_file/1/insured_values_schedule-1.pdf"
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 test")
    document = Document(
        broker_id=world.a.broker_id, entity_type=EntityType.CLIENT, entity_id=world.a.client.id,
        s3_key=rel, bucket="radal-test-bucket", original_name=name,
        mime_type="application/pdf", category=DocumentCategory.INSURED_VALUES_SCHEDULE,
    )
    db.add(document)
    db.commit()
    return document


def test_local_download_modes_set_the_disposition(client, db, world, tmp_path, monkeypatch, headers_a):
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    document = _stored(db, world, tmp_path)
    preview = client.get(f"{API}/documents/{document.id}/download", params={"mode": "preview"},
                         headers=headers_a).json()
    assert preview["url"].endswith(f"/documents/{document.id}/content?mode=preview")
    default = client.get(f"{API}/documents/{document.id}/download", headers=headers_a).json()
    assert default["url"].endswith("?mode=download")
    assert client.get(f"{API}/documents/{document.id}/download", params={"mode": "x"},
                      headers=headers_a).status_code == 422

    inline = client.get(f"{API}/documents/{document.id}/content", params={"mode": "preview"},
                        headers=headers_a)
    assert inline.status_code == 200
    assert inline.headers["content-type"].startswith("application/pdf")
    disposition = inline.headers["content-disposition"]
    assert disposition.startswith("inline;")
    assert "filename*=UTF-8''00C%20Cuadro%20de%20valores%20asegurados.pdf" in disposition
    attachment = client.get(f"{API}/documents/{document.id}/content", headers=headers_a)
    assert attachment.headers["content-disposition"].startswith("attachment;")
    assert 'filename="00C Cuadro de valores asegurados.pdf"' in attachment.headers["content-disposition"]


def test_s3_presign_carries_mime_and_disposition(client, db, world, tmp_path, monkeypatch, headers_a):
    import sys
    import types

    captured: dict = {}

    class _Client:
        def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 - boto3 names
            captured.update(Params)
            return "https://bucket.s3.amazonaws.com/signed"

    fake = types.SimpleNamespace(client=lambda *a, **k: _Client())
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "s3")
    document = _stored(db, world, tmp_path, name="Póliza Viña.pdf")
    res = client.get(f"{API}/documents/{document.id}/download", params={"mode": "preview"},
                     headers=headers_a)
    assert res.status_code == 200, res.text
    assert captured["ResponseContentType"] == "application/pdf"
    assert captured["ResponseContentDisposition"].startswith("inline;")
    assert "filename*=UTF-8''P%C3%B3liza%20Vi%C3%B1a.pdf" in captured["ResponseContentDisposition"]
    client.get(f"{API}/documents/{document.id}/download", headers=headers_a)
    assert captured["ResponseContentDisposition"].startswith("attachment;")


# =============================================================================
# B8 — Spanish server copy
# =============================================================================


def test_insured_unresolved_reason_has_no_field_names(
    client, db, world, tmp_path, ai_key, monkeypatch, headers_a, catalog
):
    from app.models.catalog import InsuranceSubtype

    sub = db.scalars(select(InsuranceSubtype).where(InsuranceSubtype.code == "PRO-02")).one()
    document = _doc(db, world.a, tmp_path, user=world.a.admin)
    _fake(monkeypatch, {**_payload("UNRES-1"),
                        "insured": {"legal_name": "Nueva Empresa SpA", "rut": "76086428-5"}})
    response = _upload(client, headers_a, document, insurance_subtype_id=sub.id)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "insured_unresolved"
    assert detail["reason"] == (
        "El RUT del asegurado leído no está en tu cartera: créalo o elige el asegurado."
    )
    assert "client_id" not in detail["reason"]
    missing = client.post(f"{API}/policies/upload", json={"document_id": document.id},
                          headers=headers_a)
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "subtype_required"
