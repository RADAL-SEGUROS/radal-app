"""v12 lane B5 — share links (broker side) and the public projections.

Covers: the per-target View gate, tenant isolation (404 by every path),
revoke / expiry / unknown -> 404, the insured and insurer projections and what
they must NEVER carry, insurer reports and payer receipts becoming suggested
``payment_evidence`` + an ``evidence_pending`` to-do, and the checkout
delegation to lane B3 (monkeypatched — no provider is reached).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.routers import public_share as public_share_router
from app.core.config import settings
from app.models.activity import Activity
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    EntityType,
    EvidenceSource,
    EvidenceStatus,
    InquiryStatus,
    NotificationKind,
    ProviderOwner,
    ShareAudience,
    ShareEntity,
)
from app.models.mail import PaymentEvidence, StatusInquiry
from app.models.notification import Notification
from app.models.share_link import ShareLink
from app.services.fx import upsert_rates
from app.services.share_links import ShareAudienceNotAllowed, business_today, create_share_link
from tests.conftest import API, auth_headers, ensure_role_user, make_case_file
from tests.fakes_v12 import (
    make_broker_override,
    make_insurer_profile,
    make_native_channel,
    make_payment,
    make_policy_with_plan,
    make_provider_account,
)

UF_RATE = Decimal("39500.0000")
PDF = b"%PDF-1.4\n%fake comprobante\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --- fixtures -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _local_media(tmp_path, monkeypatch):
    """Keep receipt bytes / logos inside the test's tmp dir."""
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    public_share_router._writes.clear()
    yield
    public_share_router._writes.clear()


@pytest.fixture()
def setup(db, world):
    """Broker A: one policy with a 3-cuota plan (cuota 1 paid) + a second policy
    of the same client that no link may ever reveal; broker B: its own policy."""
    policy, plan = make_policy_with_plan(
        db, world.a, world.insurer, n_installments=3, policy_number="POL-A-SHARE-1",
        commission_pct=Decimal("12.500"), notes="NOTA-INTERNA-SECRETA",
    )
    inst = sorted(plan.installments, key=lambda i: i.number)
    for row in inst:
        row.commission_uf = Decimal("4.4444")
        row.note = "NOTA-CUOTA-INTERNA"
    make_payment(db, inst[0])
    other, _ = make_policy_with_plan(
        db, world.a, world.insurer, n_installments=1, policy_number="POL-A-OTRA-999"
    )
    policy_b, plan_b = make_policy_with_plan(
        db, world.b, world.insurer, n_installments=2, policy_number="POL-B-1"
    )
    world.a.client.internal_notes = "CLIENTE-NOTA-SECRETA"
    upsert_rates(db, [(business_today(datetime.now(timezone.utc)), UF_RATE, Decimal("950"), "test")])
    db.commit()
    return {
        "policy": policy,
        "plan": plan,
        "inst": inst,
        "other": other,
        "policy_b": policy_b,
        "plan_b": plan_b,
    }


def _create(client, headers, entity_type, entity_id, audience="insured", **extra):
    body = {"entity_type": entity_type, "entity_id": entity_id, "audience": audience, **extra}
    return client.post(f"{API}/share-links", json=body, headers=headers)


def _public(client, token):
    return client.get(f"{API}/public/share/{token}")


def _mint(db, world, entity_type, entity_id, audience, **kw):
    link = create_share_link(
        db,
        broker_id=world.a.broker_id,
        user=world.a.admin,
        entity_type=entity_type,
        entity_id=entity_id,
        audience=audience,
        **kw,
    )
    db.commit()
    return link


def _inquiry(db, world, setup, installment_ids):
    inq = StatusInquiry(
        broker_id=world.a.broker_id,
        insurer_id=world.insurer.id,
        policy_id=setup["policy"].id,
        token="INQ-ABC123",
        installment_ids=installment_ids,
        to_address="cobranza@hdi.test",
        status=InquiryStatus.SENT,
    )
    db.add(inq)
    db.flush()
    link = create_share_link(
        db,
        broker_id=world.a.broker_id,
        user=world.a.admin,
        entity_type=ShareEntity.STATUS_INQUIRY,
        entity_id=inq.id,
        audience=ShareAudience.INSURER,
    )
    inq.share_link_id = link.id
    db.commit()
    return inq, link


def _assert_no_leak(payload: dict, *forbidden: str) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for needle in (
        "commission",
        "s3_key",
        "documents/",
        "broker_id",
        "NOTA-INTERNA-SECRETA",
        "NOTA-CUOTA-INTERNA",
        "CLIENTE-NOTA-SECRETA",
        "POL-A-OTRA-999",
        "POL-B-1",
        "Beta",
        "net_premium",
        "pagador@example.test",
        *forbidden,
    ):
        assert needle not in text, f"leaked {needle!r}"


# --- broker side: create / gate / isolation -----------------------------------


def test_create_policy_link_returns_urls_and_audits(client, db, world, setup, headers_a):
    res = _create(client, headers_a, "policy", setup["policy"].id, label="Cuotas 2026")
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["id"] and len(data["token"]) >= 32
    assert data["url"].endswith(f"/s/{data['token']}")
    assert data["whatsapp_url"].startswith("https://wa.me/?text=")
    assert "POL-A-SHARE-1" in data["whatsapp_text"] and data["url"] in data["whatsapp_text"]
    assert "póliza" in data["whatsapp_text"]
    assert "S.A.." not in data["whatsapp_text"]  # insurer name ends in a period
    assert data["permissions"] == {
        "can_pay": True, "can_upload_receipt": True, "can_report_status": False, "show_documents": False,
    }
    assert data["is_live"] is True and data["label"] == "Cuotas 2026"
    act = db.scalar(select(Activity).where(Activity.action == "share_link.created"))
    assert act is not None and act.entity_type is EntityType.SHARE_LINK and act.entity_id == data["id"]


def test_insurer_link_message_and_forced_permissions(client, setup, headers_a):
    res = _create(
        client, headers_a, "policy", setup["policy"].id, audience="insurer",
        permissions={"can_pay": True, "can_upload_receipt": True},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["permissions"]["can_pay"] is False
    assert data["permissions"]["can_upload_receipt"] is False
    assert data["permissions"]["can_report_status"] is True
    assert "estado de pago" in data["whatsapp_text"]


def test_cross_tenant_targets_are_404(client, setup, headers_a, headers_b):
    assert _create(client, headers_a, "policy", setup["policy_b"].id).status_code == 404
    assert _create(client, headers_a, "collection_plan", setup["plan_b"].id).status_code == 404
    assert _create(client, headers_a, "installment", setup["plan_b"].installments[0].id).status_code == 404
    assert _create(client, headers_a, "policy", 999999).status_code == 404
    # B cannot list A's entity either.
    res = client.get(
        f"{API}/share-links", params={"entity_type": "policy", "entity_id": setup["policy"].id},
        headers=headers_b,
    )
    assert res.status_code == 404


def test_gate_is_view_on_target_module(client, db, world, setup):
    inspector = ensure_role_user(db, world.a, "broker_inspector", "inspector")
    headers = auth_headers(client, inspector.email)
    # broker_inspector: Policies.View yes, Collections.View no.
    assert _create(client, headers, "policy", setup["policy"].id).status_code == 201
    assert _create(client, headers, "collection_plan", setup["plan"].id).status_code == 403
    assert _create(client, headers, "installment", setup["inst"][1].id).status_code == 403
    res = client.get(
        f"{API}/share-links", params={"entity_type": "collection_plan", "entity_id": setup["plan"].id},
        headers=headers,
    )
    assert res.status_code == 403


def test_case_file_link_respects_case_view_scope(client, db, world, setup, headers_a):
    case = make_case_file(db, world.a, n=7)
    setup["policy"].case_file_id = case.id
    db.commit()
    # Admin sees every case.
    res = _create(client, headers_a, "case_file", case.id)
    assert res.status_code == 201, res.text
    token = res.json()["token"]
    # The inspector's partial CaseFiles.View narrows to inspected cases -> 404.
    inspector = ensure_role_user(db, world.a, "broker_inspector", "inspector")
    headers = auth_headers(client, inspector.email)
    assert _create(client, headers, "case_file", case.id).status_code == 404
    link_id = res.json()["id"]
    assert client.post(f"{API}/share-links/{link_id}/revoke", headers=headers).status_code == 404
    # The public projection shows the case's policy, never the other one.
    pub = _public(client, token)
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert [p["policy_number"] for p in body["policies"]] == ["POL-A-SHARE-1"]
    assert body["case_file"]["reference"] == case.reference
    _assert_no_leak(body)


def _two_insurer_case(db, world, setup):
    """An account case holding the setup policy (HDI) + one placed with another insurer."""
    case = make_case_file(db, world.a, n=8)
    setup["policy"].case_file_id = case.id
    foreign, _ = make_policy_with_plan(
        db, world.a, world.insurer_no_cmf, n_installments=2, policy_number="P-OTHER-INS-2"
    )
    foreign.case_file_id = case.id
    db.commit()
    return case, foreign


def test_insurer_case_file_link_is_refused_by_every_door(client, db, world, setup, headers_a):
    """A case spans insurers: an insurer link to it would show X the policies of Y."""
    case, foreign = _two_insurer_case(db, world, setup)
    res = _create(client, headers_a, "case_file", case.id, audience="insurer")
    assert res.status_code == 422
    assert res.json()["detail"] == (
        "Un expediente solo se comparte con el asegurado; para la compañía comparte la póliza."
    )
    # The service door (used directly by other lanes) refuses too.
    with pytest.raises(ShareAudienceNotAllowed):
        create_share_link(
            db, broker_id=world.a.broker_id, user=world.a.admin,
            entity_type=ShareEntity.CASE_FILE, entity_id=case.id, audience=ShareAudience.INSURER,
        )
    db.rollback()
    # A row minted around both doors (legacy / raw insert) still projects nothing.
    legacy = ShareLink(
        broker_id=world.a.broker_id, token="legacy-insurer-case-token-0001",
        entity_type=ShareEntity.CASE_FILE, entity_id=case.id, audience=ShareAudience.INSURER,
        permissions={"can_report_status": True},
        expires_at=datetime.now(timezone.utc) + timedelta(days=5), view_count=0,
    )
    db.add(legacy)
    db.commit()
    pub = _public(client, legacy.token)
    assert pub.status_code == 404
    assert "P-OTHER-INS-2" not in pub.text and "POL-A-SHARE-1" not in pub.text
    report = client.post(
        f"{API}/public/share/{legacy.token}/report",
        json={"installment_id": foreign.collection_plans[0].installments[0].id, "status": "paid"},
    )
    assert report.status_code == 404
    assert db.scalars(select(PaymentEvidence)).all() == []
    # The insured's own case link keeps working: both policies are theirs.
    insured = _create(client, headers_a, "case_file", case.id, audience="insured")
    assert insured.status_code == 201, insured.text
    numbers = {p["policy_number"] for p in _public(client, insured.json()["token"]).json()["policies"]}
    assert numbers == {"POL-A-SHARE-1", "P-OTHER-INS-2"}


def test_case_file_pay_button_matches_lane_b3_scope(client, db, world, setup, fake_provider):
    """No dead buttons: the page and B3's checkout read ONE scope (resolve_share_scope)."""
    from app.models.enums import CaseFileKind, PartyRole
    from tests.fakes_v12 import make_party

    make_party(db, setup["policy"], role=PartyRole.PAYER, rut="11111111-1", email="paga@example.test")
    account = make_provider_account(db, owner=ProviderOwner.RADAL_MALL)
    make_native_channel(db, world.insurer, account)
    account_case = make_case_file(db, world.a, n=9)
    setup["policy"].case_file_id = account_case.id
    # A collection child case: the plan is filed in it, the policy stays on the account.
    collection_case = make_case_file(
        db, world.a, n=10, kind=CaseFileKind.COLLECTION, placement_id=None,
        policy_id=setup["policy"].id, parent_case_file_id=account_case.id,
    )
    setup["plan"].case_file_id = collection_case.id
    db.commit()

    child = _mint(db, world, ShareEntity.CASE_FILE, collection_case.id, ShareAudience.INSURED)
    body = _public(client, child.token).json()
    open_cuota = next(c for c in body["installments"] if c["id"] == setup["inst"][1].id)
    assert open_cuota["level"] == "native"
    # The collection child case reaches its plan's cuotas, and B3 accepts them too.
    assert open_cuota["action"] == "pay"
    res = client.post(
        f"{API}/public/share/{child.token}/checkout", json={"installment_ids": [open_cuota["id"]]}
    )
    assert res.status_code == 200, res.text

    parent = _mint(db, world, ShareEntity.CASE_FILE, account_case.id, ShareAudience.INSURED)
    body = _public(client, parent.token).json()
    open_cuota = next(c for c in body["installments"] if c["id"] == setup["inst"][1].id)
    assert open_cuota["action"] == "pay"
    res = client.post(
        f"{API}/public/share/{parent.token}/checkout", json={"installment_ids": [open_cuota["id"]]}
    )
    assert res.status_code == 200, res.text
    assert res.json()["checkout_url"]


def test_status_inquiry_link_must_target_the_insurer(client, db, world, setup, headers_a):
    inq, _link = _inquiry(db, world, setup, [setup["inst"][1].id])
    res = _create(client, headers_a, "status_inquiry", inq.id, audience="insured")
    assert res.status_code == 422
    assert _create(client, headers_a, "status_inquiry", inq.id, audience="insurer").status_code == 201


def test_list_and_revoke(client, db, world, setup, headers_a, headers_b):
    first = _create(client, headers_a, "policy", setup["policy"].id).json()
    second = _create(client, headers_a, "policy", setup["policy"].id, audience="insurer").json()
    _create(client, headers_a, "policy", setup["other"].id)
    res = client.get(
        f"{API}/share-links", params={"entity_type": "policy", "entity_id": setup["policy"].id},
        headers=headers_a,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2 and [i["id"] for i in data["items"]] == [second["id"], first["id"]]

    # Another tenant cannot revoke it.
    assert client.post(f"{API}/share-links/{first['id']}/revoke", headers=headers_b).status_code == 404
    assert _public(client, first["token"]).status_code == 200

    res = client.post(f"{API}/share-links/{first['id']}/revoke", headers=headers_a)
    assert res.status_code == 200
    assert res.json()["is_live"] is False and res.json()["revoked_at"]
    # Idempotent; one audit row only.
    assert client.post(f"{API}/share-links/{first['id']}/revoke", headers=headers_a).status_code == 200
    assert len(db.scalars(select(Activity).where(Activity.action == "share_link.revoked")).all()) == 1
    assert _public(client, first["token"]).status_code == 404
    assert client.post(f"{API}/share-links/999999/revoke", headers=headers_a).status_code == 404


def test_create_rejects_bad_expiry(client, setup, headers_a):
    assert _create(client, headers_a, "policy", setup["policy"].id, expires_in_days=0).status_code == 422
    assert _create(client, headers_a, "policy", setup["policy"].id, expires_in_days=400).status_code == 422
    assert _create(client, headers_a, "policy", setup["policy"].id, expires_in_days=7).status_code == 201


def test_unauthenticated_broker_side_is_401(client, setup):
    assert _create(client, {}, "policy", setup["policy"].id).status_code == 401


# --- public: 404s -------------------------------------------------------------


def test_public_unknown_revoked_expired_are_404(client, db, world, setup):
    assert _public(client, "no-such-token").status_code == 404
    assert _public(client, "x" * 80).status_code == 404
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    assert _public(client, link.token).status_code == 200
    link.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert _public(client, link.token).status_code == 404
    assert client.post(
        f"{API}/public/share/{link.token}/checkout", json={"installment_ids": [setup["inst"][1].id]}
    ).status_code == 404


def test_public_404_when_target_is_gone(client, db, world, setup):
    link = _mint(db, world, ShareEntity.INSTALLMENT, setup["inst"][2].id, ShareAudience.INSURED)
    db.delete(db.get(CollectionInstallment, setup["inst"][2].id))
    db.commit()
    assert _public(client, link.token).status_code == 404


# --- public: insured projection -----------------------------------------------


def test_insured_projection_assisted(client, db, world, setup):
    make_insurer_profile(
        db, world.insurer,
        payment_instructions={"bank_name": "Banco Perfil", "account_number": "111", "portal_url": "https://hdi.test/pagar"},
        status_note="NOTA-PERFIL-INTERNA",
    )
    make_broker_override(
        db, world.a.broker_id, world.insurer,
        payment_instructions={"bank_name": "Banco Override", "account_holder": "HDI S.A.", "account_number": "  "},
        notes="OVERRIDE-NOTA-INTERNA",
    )
    db.commit()
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED, label="Mis cuotas")
    res = _public(client, link.token)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["audience"] == "insured" and body["label"] == "Mis cuotas"
    assert body["broker"] == {"name": "Alfa", "logo_url": None}
    assert body["policy"]["policy_number"] == "POL-A-SHARE-1"
    assert body["policy"]["insurer_name"] == "HDI Seguros S.A."
    assert body["policy"]["insured_name"] == "Asegurado Alfa S.A."
    assert [p["policy_number"] for p in body["policies"]] == ["POL-A-SHARE-1"]
    assert Decimal(body["uf_rate"]) == UF_RATE

    cuotas = body["installments"]
    assert [c["number"] for c in cuotas] == [1, 2, 3]
    paid, open_ = cuotas[0], cuotas[1]
    assert paid["state"] == "paid" and paid["action"] == "none" and paid["balance_clp"] == 0
    assert open_["state"] != "paid"
    assert open_["level"] == "assisted" and open_["action"] == "instructions"
    assert open_["can_upload_receipt"] is True and open_["can_report"] is False
    amount = Decimal(open_["amount_uf"])
    assert open_["amount_clp"] == int((amount * UF_RATE).quantize(Decimal("1")))

    instr = body["policy"]["payment_instructions"]
    assert instr["bank_name"] == "Banco Override"         # override wins
    assert instr["account_number"] == "111"               # a blank override never erases
    assert instr["account_holder"] == "HDI S.A."
    assert instr["portal_url"] == "https://hdi.test/pagar"
    assert body["report_statuses"] == [] and body["inquiry"] is None
    _assert_no_leak(body, "NOTA-PERFIL-INTERNA", "OVERRIDE-NOTA-INTERNA", "onboarding", "status_note")

    db.refresh(link)
    assert link.view_count == 1 and link.last_viewed_at is not None
    _public(client, link.token)
    db.refresh(link)
    assert link.view_count == 2


def test_insured_projection_native_offers_pay(client, db, world, setup):
    account = make_provider_account(db, owner=ProviderOwner.RADAL_MALL)
    make_native_channel(db, world.insurer, account)
    db.commit()
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    body = _public(client, link.token).json()
    open_ = body["installments"][1]
    assert open_["level"] == "native" and open_["action"] == "pay"
    assert body["policy"]["payment_instructions"] is None
    _assert_no_leak(body, "test-api-key", "webhook", "597000000001")

    # Same cuotas, can_pay off -> instructions instead of the pay button.
    no_pay = _mint(
        db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED,
        permissions={"can_pay": False},
    )
    body = _public(client, no_pay.token).json()
    assert body["installments"][1]["action"] == "instructions"
    assert body["permissions"]["can_pay"] is False


def test_plan_and_installment_links_narrow_the_cuotas(client, db, world, setup):
    # A second plan (e.g. an endorsement's) on the same policy.
    extra = CollectionPlan(
        broker_id=world.a.broker_id, policy_id=setup["policy"].id, payment_mode=setup["plan"].payment_mode,
        installment_count=1, total_premium_uf=Decimal("10.0000"),
    )
    extra.installments.append(
        CollectionInstallment(broker_id=world.a.broker_id, number=1, gross_amount_uf=Decimal("10.0000"),
                              due_date=date(2026, 12, 1))
    )
    db.add(extra)
    db.commit()

    whole = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    assert len(_public(client, whole.token).json()["installments"]) == 4

    plan_link = _mint(db, world, ShareEntity.COLLECTION_PLAN, setup["plan"].id, ShareAudience.INSURED)
    ids = [c["id"] for c in _public(client, plan_link.token).json()["installments"]]
    assert ids == [i.id for i in setup["inst"]]

    one = _mint(db, world, ShareEntity.INSTALLMENT, setup["inst"][2].id, ShareAudience.INSURED)
    body = _public(client, one.token).json()
    assert [c["id"] for c in body["installments"]] == [setup["inst"][2].id]
    _assert_no_leak(body)


def test_logo_route_serves_bytes_never_the_key(client, db, world, setup, tmp_path):
    key = f"media/broker/{world.a.broker_id}/logo.webp"
    path = tmp_path / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF0000WEBPVP8 fake")
    world.a.broker.logo_key = key
    db.commit()
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    body = _public(client, link.token).json()
    assert body["broker"]["logo_url"] == f"{API}/public/share/{link.token}/logo"
    assert key not in json.dumps(body)
    res = client.get(body["broker"]["logo_url"])
    assert res.status_code == 200 and res.content.startswith(b"RIFF")
    assert res.headers["content-type"] == "image/webp"
    assert client.get(f"{API}/public/share/nope/logo").status_code == 404


# --- public: insurer projection + report --------------------------------------


def test_insurer_projection_lists_only_the_inquiry_cuotas(client, db, world, setup):
    inq, link = _inquiry(db, world, setup, [setup["inst"][1].id, setup["plan_b"].installments[0].id])
    res = _public(client, link.token)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["audience"] == "insurer"
    assert body["policy"]["id"] == setup["policy"].id
    assert body["policy"]["insured_name"] == "Asegurado Alfa S.A."
    # Broker B's cuota id in the inquiry JSON is ignored; only A's listed one shows.
    assert [c["id"] for c in body["installments"]] == [setup["inst"][1].id]
    cuota = body["installments"][0]
    assert cuota["level"] is None and cuota["action"] == "report" and cuota["can_report"] is True
    assert cuota["can_upload_receipt"] is False
    assert body["policy"]["payment_instructions"] is None
    assert body["report_statuses"] == ["paid", "unpaid", "partial", "cancelled"]
    assert body["inquiry"] == {"reference": "INQ-ABC123", "status": "sent"}
    _assert_no_leak(body, "cobranza@hdi.test")


def test_insurer_report_creates_evidence_notification_and_marks_inquiry(client, db, world, setup):
    inq, link = _inquiry(db, world, setup, [setup["inst"][1].id])
    url = f"{API}/public/share/{link.token}/report"
    body = {
        "installment_id": setup["inst"][1].id,
        "status": "paid",
        "amount": "46.3333",
        "paid_on": "2026-02-04",
        "comment": "Pagada por transferencia",
    }
    res = client.post(url, json=body)
    assert res.status_code == 201, res.text
    assert res.json()["created"] is True and res.json()["reported_status"] == "paid"
    assert "id" not in res.json()

    ev = db.scalar(select(PaymentEvidence))
    assert ev.broker_id == world.a.broker_id and ev.installment_id == setup["inst"][1].id
    assert ev.source is EvidenceSource.INSURER_SHARE_LINK
    assert ev.status is EvidenceStatus.SUGGESTED
    assert ev.reported_amount == Decimal("46.3333") and ev.reported_paid_on == date(2026, 2, 4)
    assert ev.status_inquiry_id == inq.id and ev.note == "Pagada por transferencia"
    assert ev.payment_id is None  # a suggestion, never a payment

    db.refresh(inq)
    assert inq.status is InquiryStatus.REPLIED and inq.replied_at is not None

    notes = db.scalars(select(Notification).where(Notification.origin_type == "payment_evidence")).all()
    assert len(notes) == 1
    assert notes[0].kind is NotificationKind.EVIDENCE_PENDING and notes[0].user_id == world.a.admin.id
    assert notes[0].origin_id == ev.id and notes[0].link_path == "/collections/evidence"
    assert notes[0].params["policy_number"] == "POL-A-SHARE-1"

    # The projection now shows what this link reported.
    cuota = _public(client, link.token).json()["installments"][0]
    assert cuota["reported"]["status"] == "paid"

    # The same report again: idempotent.
    again = client.post(url, json=body)
    assert again.status_code == 200 and again.json()["created"] is False
    assert len(db.scalars(select(PaymentEvidence)).all()) == 1
    # A different report is a new suggestion.
    assert client.post(url, json={**body, "status": "partial"}).status_code == 201
    assert len(db.scalars(select(PaymentEvidence)).all()) == 2


def test_report_guards(client, db, world, setup):
    _inq, link = _inquiry(db, world, setup, [setup["inst"][1].id])
    url = f"{API}/public/share/{link.token}/report"
    # A cuota of the same policy NOT listed in the inquiry, and broker B's cuota.
    assert client.post(url, json={"installment_id": setup["inst"][2].id, "status": "paid"}).status_code == 404
    assert client.post(
        url, json={"installment_id": setup["plan_b"].installments[0].id, "status": "paid"}
    ).status_code == 404
    assert client.post(url, json={"installment_id": setup["inst"][1].id, "status": "unknown"}).status_code == 422
    future = (date.today() + timedelta(days=10)).isoformat()
    assert client.post(
        url, json={"installment_id": setup["inst"][1].id, "status": "paid", "paid_on": future}
    ).status_code == 422
    # An insured link cannot report.
    insured = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    assert client.post(
        f"{API}/public/share/{insured.token}/report",
        json={"installment_id": setup["inst"][1].id, "status": "paid"},
    ).status_code == 403
    # An insurer link with reporting switched off.
    mute = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURER,
                 permissions={"can_report_status": False})
    assert client.post(
        f"{API}/public/share/{mute.token}/report",
        json={"installment_id": setup["inst"][1].id, "status": "paid"},
    ).status_code == 403
    assert db.scalars(select(PaymentEvidence)).all() == []


def test_write_throttle(client, db, world, setup, monkeypatch):
    monkeypatch.setattr(public_share_router, "THROTTLE_MAX_WRITES", 2)
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURER)
    url = f"{API}/public/share/{link.token}/report"
    for status_ in ("paid", "unpaid"):
        assert client.post(url, json={"installment_id": setup["inst"][1].id, "status": status_}).status_code == 201
    assert client.post(url, json={"installment_id": setup["inst"][1].id, "status": "partial"}).status_code == 429


# --- public: receipt ----------------------------------------------------------


def _upload(client, token, installment_id, content=PDF, mime="application/pdf", name="comprobante.pdf", **form):
    return client.post(
        f"{API}/public/share/{token}/receipt",
        files={"file": (name, content, mime)},
        data={"installment_id": str(installment_id), **form},
    )


def test_receipt_creates_document_evidence_and_notification(client, db, world, setup, tmp_path):
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    res = _upload(client, link.token, setup["inst"][1].id, amount="46,3333", paid_on="2026-02-03",
                  comment="Transferí ayer")
    assert res.status_code == 201, res.text
    assert res.json()["reported_status"] == "paid"
    assert "documents/" not in res.text

    doc = db.scalar(select(Document).where(Document.category == DocumentCategory.PAYMENT_RECEIPT))
    assert doc is not None and doc.broker_id == world.a.broker_id
    assert doc.entity_type is EntityType.INSTALLMENT and doc.entity_id == setup["inst"][1].id
    assert doc.mime_type == "application/pdf" and doc.size_bytes == len(PDF)
    assert (tmp_path / doc.s3_key).read_bytes() == PDF

    ev = db.scalar(select(PaymentEvidence))
    assert ev.source is EvidenceSource.PAYER_UPLOAD and ev.status is EvidenceStatus.SUGGESTED
    assert ev.document_id == doc.id and ev.reported_amount == Decimal("46.3333")
    assert ev.reported_paid_on == date(2026, 2, 3) and ev.note == "Transferí ayer"

    note = db.scalar(select(Notification).where(Notification.origin_id == ev.id))
    assert note.kind is NotificationKind.EVIDENCE_PENDING and note.user_id == world.a.admin.id

    # PNG works too.
    assert _upload(client, link.token, setup["inst"][2].id, PNG, "image/png", "foto.png").status_code == 201


def test_insured_page_shows_a_pending_receipt_after_reload(client, db, world, setup, tmp_path):
    """QA v12: after an upload the reload offered the upload again, no 'en revisión'."""
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    cuota = setup["inst"][1].id
    before = {c["id"]: c for c in _public(client, link.token).json()["installments"]}
    assert before[cuota]["receipt_pending"] is None
    assert _upload(client, link.token, cuota).status_code == 201
    after = {c["id"]: c for c in _public(client, link.token).json()["installments"]}
    pending = after[cuota]["receipt_pending"]
    assert pending is not None and pending["status"] == "suggested"
    assert pending["reported_at"].endswith("Z") or pending["reported_at"].endswith("+00:00")
    assert after[setup["inst"][2].id]["receipt_pending"] is None


def test_receipt_guards(client, db, world, setup, monkeypatch):
    link = _mint(db, world, ShareEntity.INSTALLMENT, setup["inst"][1].id, ShareAudience.INSURED)
    cuota = setup["inst"][1].id
    assert _upload(client, link.token, cuota, b"hello", "text/plain", "x.txt").status_code == 415
    assert _upload(client, link.token, cuota, b"not a pdf at all", "application/pdf").status_code == 415
    assert _upload(client, link.token, cuota, b"", "application/pdf").status_code == 422
    assert _upload(client, link.token, cuota, amount="abc").status_code == 422
    assert _upload(client, link.token, cuota, paid_on="ayer").status_code == 422
    # Another cuota of the same policy is outside an installment link.
    assert _upload(client, link.token, setup["inst"][2].id).status_code == 404
    monkeypatch.setattr(public_share_router, "RECEIPT_MAX_BYTES", 32)
    assert _upload(client, link.token, cuota, PDF + b"x" * 64).status_code == 413
    monkeypatch.setattr(public_share_router, "RECEIPT_MAX_BYTES", 10 * 1024 * 1024)
    # Insurer links and links without the permission cannot upload.
    insurer = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURER)
    assert _upload(client, insurer.token, cuota).status_code == 403
    no_upload = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED,
                      permissions={"can_upload_receipt": False})
    assert _upload(client, no_upload.token, cuota).status_code == 403
    assert db.scalars(select(PaymentEvidence)).all() == []
    assert db.scalars(select(Document).where(Document.category == DocumentCategory.PAYMENT_RECEIPT)).all() == []


# --- public: checkout delegation ----------------------------------------------


def test_checkout_delegates_to_lane_b3(client, db, world, setup, monkeypatch):
    calls = []

    def fake_checkout(db_, link_, installment_ids, *, now=None):
        calls.append((link_.id, list(installment_ids), now))
        return {"checkout_url": "https://pay.fake-provider.test/checkout/rdl-intent-1"}

    monkeypatch.setattr(public_share_router, "_checkout_service", lambda: fake_checkout)
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    url = f"{API}/public/share/{link.token}/checkout"
    ids = [setup["inst"][1].id, setup["inst"][2].id, setup["inst"][1].id]
    res = client.post(url, json={"installment_ids": ids})
    assert res.status_code == 200, res.text
    assert res.json() == {"checkout_url": "https://pay.fake-provider.test/checkout/rdl-intent-1"}
    assert calls[0][0] == link.id and calls[0][1] == [setup["inst"][1].id, setup["inst"][2].id]
    assert calls[0][2].tzinfo is not None

    # Objects with a checkout_url attribute work too; B3's ValueError -> 409.
    class _Intent:
        checkout_url = "https://pay.fake-provider.test/checkout/rdl-intent-2"

    monkeypatch.setattr(public_share_router, "_checkout_service", lambda: lambda *a, **k: _Intent())
    assert client.post(url, json={"installment_ids": ids[:1]}).json()["checkout_url"].endswith("intent-2")

    def refuse(*_a, **_k):
        raise ValueError("Paga por separado las cuotas de HDI")

    monkeypatch.setattr(public_share_router, "_checkout_service", lambda: refuse)
    res = client.post(url, json={"installment_ids": ids[:1]})
    assert res.status_code == 409 and "separado" in res.json()["detail"]

    # Lane B3's CheckoutError(status_code, detail) keeps its status and Spanish detail.
    class _Refusal(Exception):
        def __init__(self, status_code, detail):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail

    def refuse_b3(*_a, **_k):
        raise _Refusal(409, "La cuota 2 ya está pagada")

    monkeypatch.setattr(public_share_router, "_checkout_service", lambda: refuse_b3)
    res = client.post(url, json={"installment_ids": ids[:1]})
    assert res.status_code == 409 and res.json()["detail"] == "La cuota 2 ya está pagada"


def test_checkout_end_to_end_through_lane_b3(client, db, world, setup, fake_provider):
    """The real B3 service behind the fake provider."""
    from tests.fakes_v12 import make_party
    from app.models.enums import PartyRole

    make_party(db, setup["policy"], role=PartyRole.PAYER, rut="11111111-1", email="paga@example.test")
    account = make_provider_account(db, owner=ProviderOwner.RADAL_MALL)
    make_native_channel(db, world.insurer, account)
    db.commit()
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    body = _public(client, link.token).json()
    assert body["installments"][1]["action"] == "pay"
    res = client.post(
        f"{API}/public/share/{link.token}/checkout", json={"installment_ids": [setup["inst"][1].id]}
    )
    assert res.status_code == 200, res.text
    assert res.json()["checkout_url"].startswith("https://pay.fake-provider.test/checkout/")
    # A paid cuota is refused by B3 with its Spanish reason (not a 500).
    res = client.post(
        f"{API}/public/share/{link.token}/checkout", json={"installment_ids": [setup["inst"][0].id]}
    )
    assert res.status_code == 409 and res.json()["detail"]


def test_checkout_guards_and_unavailable(client, db, world, setup, monkeypatch):
    # A service answering without a checkout URL is a 502 with a Spanish reason.
    monkeypatch.setattr(public_share_router, "_checkout_service", lambda: lambda *a, **k: {})
    link = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED)
    url = f"{API}/public/share/{link.token}/checkout"
    res = client.post(url, json={"installment_ids": [setup["inst"][1].id]})
    assert res.status_code == 502 and "No pudimos abrir" in res.json()["detail"]
    # Out-of-scope cuota (broker B's / another policy's) -> 404 before delegation.
    assert client.post(url, json={"installment_ids": [setup["plan_b"].installments[0].id]}).status_code == 404
    assert client.post(url, json={"installment_ids": []}).status_code == 422
    insurer = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURER)
    assert client.post(
        f"{API}/public/share/{insurer.token}/checkout", json={"installment_ids": [setup["inst"][1].id]}
    ).status_code == 403
    no_pay = _mint(db, world, ShareEntity.POLICY, setup["policy"].id, ShareAudience.INSURED,
                   permissions={"can_pay": False})
    assert client.post(
        f"{API}/public/share/{no_pay.token}/checkout", json={"installment_ids": [setup["inst"][1].id]}
    ).status_code == 403


def test_checkout_service_is_lane_b3s():
    from app.services.payments.checkout import create_checkout_for_share

    assert public_share_router._checkout_service() is create_checkout_for_share

