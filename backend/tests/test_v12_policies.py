"""v12 lane B1 — policies are the main view (02 §2).

Covers: the vía Manual (five mandatory data, colocación on demand with skipped
stage events, default parties, 409 duplicates, placeholder number + R5 advice),
the list filters and row fields, the summary cards, the ficha (parties,
operability, journey), PATCH "Completar" vs "Corregir dato", PUT parties, the
reader upload (origin, respaldo, colocación on demand, cuotas committed when Σ
balances), tenant isolation and permission gates. Hermetic: the chat model is
faked, no network.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.account_group import AccountGroup
from app.models.activity import Activity
from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.catalog import InsuranceSubtype
from app.models.client import Client
from app.models.collection import CollectionPlan
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    CaseEntryOrigin,
    CaseStage,
    CollectionPlanOrigin,
    CollectionPlanSource,
    EntityType,
    PolicyOrigin,
)
from app.models.placement import Placement
from app.models.policy import Policy, PolicyStatus
from app.models.policy_party import PolicyParty
from app.services import ai as ai_service
from app.services.policy_registry import today_santiago
from tests.conftest import API, make_case_file, make_policy
from tests.fakes_v12 import make_policy_with_plan
from tests.test_v8_broker_journey import _FakeChatModel

TODAY = today_santiago()


def _sub(db, code: str) -> InsuranceSubtype:
    return db.scalars(select(InsuranceSubtype).where(InsuranceSubtype.code == code)).one()


def _manual_body(world, sub, **extra):
    body = {
        "client_id": world.a.client.id,
        "insurer_id": world.insurer.id,
        "insurance_subtype_id": sub.id,
        "start_date": "2026-03-01",
        "end_date": "2027-03-01",
        "policy_number": "MAN-001",
        "taxable_premium_uf": "100",
        "exempt_premium_uf": "20",
    }
    body.update(extra)
    return {k: v for k, v in body.items() if v is not None}


def _events(db, case_id):
    return db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case_id)
        .order_by(CaseFileStageEvent.occurred_at, CaseFileStageEvent.id)
    ).all()


# =============================================================================
# Manual entry + colocación on demand
# =============================================================================

def test_manual_entry_creates_the_colocacion_with_skipped_stages(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    response = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin"] == PolicyOrigin.MANUAL.value
    assert body["has_source_document"] is False
    assert body["insurance_subtype_id"] == sub.id
    assert body["insurance_line_id"] == sub.insurance_line_id
    assert body["subtype"]["code"] == "PRO-02"
    assert body["line"]["code"] == "PRO"
    assert Decimal(body["total_premium_uf"]) == Decimal("139")
    assert "source_document" in body["missing"] and "plan" in body["missing"]

    case = db.get(CaseFile, body["case_file_id"])
    assert case is not None and case.broker_id == world.a.broker_id
    assert case.entry_origin == CaseEntryOrigin.MANUAL_ENTRY
    assert case.origin.value == "new"
    # The door decides the vía, not the subtipo (PRO-02 suggests traditional).
    assert sub.suggested_route.value == "traditional"
    assert case.route.value == "manual"
    assert case.stage == CaseStage.ACTIVE
    assert case.insurance_subtype_id == sub.id
    assert case.policy_id == body["id"]
    assert (case.period_start, case.period_end) == (date(2026, 3, 1), date(2027, 3, 1))

    events = _events(db, case.id)
    skipped = [e.to_stage for e in events if e.is_skipped]
    entered = [e.to_stage for e in events if not e.is_skipped]
    assert skipped == [
        CaseStage.LEAD, CaseStage.INTAKE, CaseStage.PRE_UNDERWRITING,
        CaseStage.TECHNICAL_BASIS, CaseStage.MARKET_SUBMISSION,
        CaseStage.QUOTES_RECEIVED, CaseStage.COMPARISON, CaseStage.INSURED_DECISION,
        CaseStage.PROPOSAL_ISSUED, CaseStage.RATIFIED, CaseStage.MIRROR_VALIDATION,
    ]
    assert entered == [CaseStage.POLICY_ISSUED, CaseStage.ACTIVE]
    assert all(e.user_id is None for e in events if e.is_skipped)

    # The client had no group: one named after it is created and adopted.
    group = db.get(AccountGroup, case.account_group_id)
    assert group is not None and group.name == "Asegurado Alfa S.A."
    db.refresh(world.a.client)
    assert world.a.client.account_group_id == group.id
    assert body["group_id"] == group.id

    # Default parties: insured = policyholder = payer = the client.
    detail = client.get(f"{API}/policies/{body['id']}", headers=headers_a).json()
    roles = sorted(p["role"] for p in detail["parties"])
    assert roles == ["insured", "payer", "policyholder"]
    assert all(p["client_id"] == world.a.client.id for p in detail["parties"])
    assert detail["journey"]["case_file_id"] == case.id
    assert "policy_issued" in detail["journey"]["entered_stages"]
    assert "intake" in detail["journey"]["skipped_stages"]
    assert detail["computed_status"] in ("active", "expired")


def test_manual_entry_attaches_to_an_existing_open_account(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    opened = client.post(
        f"{API}/case-files",
        json={
            "kind": "account",
            "client_id": world.a.client.id,
            "insurance_subtype_id": sub.id,
            "period_start": "2026-01-01",
            "period_end": "2027-01-01",
        },
        headers=headers_a,
    )
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["id"]

    response = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["case_file_id"] == case_id
    assert db.scalars(select(CaseFile)).all().__len__() == 1

    case = db.get(CaseFile, case_id)
    db.refresh(case)
    assert case.stage == CaseStage.ACTIVE
    events = _events(db, case_id)
    # The opening ``lead`` event is a REAL entry: kept, never re-written as skipped.
    assert [e.to_stage for e in events if not e.is_skipped] == [
        CaseStage.LEAD, CaseStage.POLICY_ISSUED, CaseStage.ACTIVE
    ]
    assert CaseStage.INTAKE in [e.to_stage for e in events if e.is_skipped]


def test_manual_entry_outside_the_open_vigencia_opens_a_new_folder(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    first = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a)
    second = client.post(
        f"{API}/policies",
        json=_manual_body(
            world, sub, policy_number="MAN-002", start_date="2027-03-01", end_date="2028-03-01"
        ),
        headers=headers_a,
    )
    assert first.status_code == 201 and second.status_code == 201, second.text
    assert first.json()["case_file_id"] != second.json()["case_file_id"]


def test_manual_entry_from_a_new_rut_creates_the_client(client, world, db, catalog, headers_a):
    sub = _sub(db, "VEH-01")
    body = _manual_body(world, sub, client_id=None, insured_rut="12.345.678-5", insured_name="Juana Pérez")
    response = client.post(f"{API}/policies", json=body, headers=headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["insured_rut"] == "12345678-5"
    new_client = db.get(Client, response.json()["client_id"])
    assert new_client.broker_id == world.a.broker_id

    # Unknown RUT without a name -> 422 (Spanish reason); invalid RUT -> 422.
    nameless = _manual_body(world, sub, client_id=None, insured_rut="11.111.111-1", policy_number="X2")
    assert client.post(f"{API}/policies", json=nameless, headers=headers_a).status_code == 422
    bad = _manual_body(world, sub, client_id=None, insured_rut="12.345.678-9", insured_name="X")
    assert client.post(f"{API}/policies", json=bad, headers=headers_a).status_code == 422


def test_manual_entry_requires_the_five_data(client, world, db, catalog, headers_a):
    sub = _sub(db, "PRO-02")
    no_dates = _manual_body(world, sub, start_date=None)
    assert client.post(f"{API}/policies", json=no_dates, headers=headers_a).status_code == 422
    backwards = _manual_body(world, sub, start_date="2027-03-01", end_date="2026-03-01")
    assert client.post(f"{API}/policies", json=backwards, headers=headers_a).status_code == 422
    no_insured = _manual_body(world, sub, client_id=None)
    assert client.post(f"{API}/policies", json=no_insured, headers=headers_a).status_code == 422
    retired = _sub(db, "PRO-05")
    retired.is_active = False
    db.commit()
    gone = _manual_body(world, retired)
    response = client.post(f"{API}/policies", json=gone, headers=headers_a)
    assert response.status_code == 422
    assert "subtipo" in response.json()["detail"]
    assert db.scalars(select(Policy)).all() == []


def test_manual_entry_duplicate_number_is_409(client, world, db, catalog, headers_a, headers_b):
    sub = _sub(db, "PRO-02")
    assert client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a).status_code == 201
    dupe = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a)
    assert dupe.status_code == 409, dupe.text
    # Another broker may hold the same folio.
    other = _manual_body(world, sub, client_id=world.b.client.id)
    assert client.post(f"{API}/policies", json=other, headers=headers_b).status_code == 201


def test_manual_entry_without_number_uses_a_placeholder_and_warns_on_twins(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    first = client.post(
        f"{API}/policies", json=_manual_body(world, sub, policy_number=None), headers=headers_a
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["has_policy_number"] is False
    assert "policy_number" in body["missing"]
    assert body["policy_number"].startswith("SIN-N-")
    endorse = next(f for f in body["operability"] if f["feature"] == "endorsements")
    assert endorse["enabled"] is False and "n° de póliza" in endorse["reason"]
    claims = next(f for f in body["operability"] if f["feature"] == "claims")
    assert claims["enabled"] is True

    twin = client.post(
        f"{API}/policies", json=_manual_body(world, sub, policy_number=None), headers=headers_a
    )
    assert twin.status_code == 201, twin.text
    assert any("duplicado" in w for w in twin.json()["warnings"])


def test_manual_entry_with_explicit_policyholder_and_payer(client, world, db, catalog, headers_a):
    sub = _sub(db, "PRO-02")
    body = _manual_body(
        world, sub,
        policyholder={"name": "Inmobiliaria Contratante SpA", "rut": "76.086.428-5",
                      "email": "pagos@contratante.cl", "phone": "+56911111111"},
        payer={"name": "Banco Pagador", "email": "cobranza@banco.cl"},
        payment_method="direct_debit",
        commission_pct="12.5",
    )
    response = client.post(f"{API}/policies", json=body, headers=headers_a)
    assert response.status_code == 201, response.text
    out = response.json()
    by_role = {p["role"]: p for p in out["parties"]}
    assert by_role["policyholder"]["rut"] == "76086428-5"
    assert by_role["policyholder"]["client_id"] is None
    assert by_role["payer"]["email"] == "cobranza@banco.cl"
    assert by_role["insured"]["client_id"] == world.a.client.id
    assert out["payment_method"] == "direct_debit"
    assert "payer_contact" not in out["missing"]
    assert "commission" not in out["missing"]


def test_legacy_register_still_works(client, world, db, headers_a):
    """No subtipo: the pre-v12 register path (422 on duplicates) is unchanged."""
    payload = {"client_id": world.a.client.id, "insurer_id": world.insurer.id, "policy_number": "LEG-1"}
    first = client.post(f"{API}/policies", json=payload, headers=headers_a)
    assert first.status_code == 201, first.text
    assert first.json()["origin"] == "manual"
    assert first.json()["case_file_id"] is None
    assert client.post(f"{API}/policies", json=payload, headers=headers_a).status_code == 422
    missing_number = {"client_id": world.a.client.id, "insurer_id": world.insurer.id}
    assert client.post(f"{API}/policies", json=missing_number, headers=headers_a).status_code == 422


# =============================================================================
# List filters + row fields
# =============================================================================

@pytest.fixture()
def portfolio(db, world, catalog):
    """A small book for broker A with every computed state, and one B policy."""
    pro = _sub(db, "PRO-02")
    veh = _sub(db, "VEH-01")
    overdue, _ = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="P-OVERDUE",
        start=TODAY - timedelta(days=90), start_date=TODAY - timedelta(days=95),
        end_date=TODAY + timedelta(days=60), insurance_subtype_id=pro.id,
        has_source_document=True, commission_pct=Decimal("10"),
        origin=PolicyOrigin.READER,
    )
    soon, _ = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="P-SOON",
        start=TODAY + timedelta(days=10), start_date=TODAY - timedelta(days=20),
        end_date=TODAY + timedelta(days=345), insurance_subtype_id=veh.id,
    )
    ok, _ = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="P-OK",
        start=TODAY + timedelta(days=60), start_date=TODAY - timedelta(days=5),
        end_date=TODAY + timedelta(days=360),
    )
    no_plan = make_policy(
        db, world.a, world.insurer, policy_number="P-NOPLAN",
        start_date=TODAY - timedelta(days=10), end_date=TODAY + timedelta(days=200),
        is_provisional=True, origin=PolicyOrigin.MANUAL,
    )
    upcoming = make_policy(
        db, world.a, world.insurer, policy_number="P-UPCOMING",
        start_date=TODAY + timedelta(days=10), end_date=TODAY + timedelta(days=375),
    )
    cancelled = make_policy(
        db, world.a, world.insurer, policy_number="P-CANCELLED",
        status=PolicyStatus.CANCELLED, start_date=TODAY - timedelta(days=10),
        end_date=TODAY + timedelta(days=300),
    )
    expired = make_policy(
        db, world.a, world.insurer, policy_number="P-EXPIRED",
        start_date=TODAY - timedelta(days=400), end_date=TODAY - timedelta(days=5),
    )
    foreign = make_policy(
        db, world.b, world.insurer, policy_number="P-FOREIGN",
        start_date=TODAY - timedelta(days=10), end_date=TODAY + timedelta(days=200),
    )
    db.commit()
    return {
        "overdue": overdue, "soon": soon, "ok": ok, "no_plan": no_plan,
        "upcoming": upcoming, "cancelled": cancelled, "expired": expired, "foreign": foreign,
    }


def _numbers(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {row["policy_number"] for row in response.json()["items"]}


def test_list_rows_carry_the_main_view_fields(client, portfolio, headers_a):
    rows = {r["policy_number"]: r for r in client.get(f"{API}/policies", headers=headers_a).json()["items"]}
    assert "P-FOREIGN" not in rows
    row = rows["P-OVERDUE"]
    assert row["computed_status"] == "active"
    assert row["subtype"]["code"] == "PRO-02" and row["insured_name"] == "Asegurado Alfa S.A."
    summary = row["collection_summary"]
    assert summary["state"] == "overdue"
    assert summary["overdue_count"] == 3
    assert Decimal(summary["overdue_uf"]) == Decimal("139.0000")
    assert summary["level"] == "assisted"
    assert summary["plan_id"] is not None
    assert row["has_source_document"] is True
    assert rows["P-SOON"]["collection_summary"]["state"] == "due_soon"
    assert rows["P-SOON"]["collection_summary"]["next_due_date"] == str(TODAY + timedelta(days=10))
    assert rows["P-OK"]["collection_summary"]["state"] == "ok"
    assert rows["P-NOPLAN"]["collection_summary"]["state"] == "no_plan"
    assert rows["P-UPCOMING"]["computed_status"] == "upcoming"
    assert rows["P-CANCELLED"]["computed_status"] == "cancelled"
    assert rows["P-EXPIRED"]["computed_status"] == "expired"


def test_list_filters(client, world, db, portfolio, headers_a):
    base = f"{API}/policies"
    assert _numbers(client.get(base, params={"status": "upcoming"}, headers=headers_a)) == {"P-UPCOMING"}
    assert _numbers(client.get(base, params={"status": ["expired", "cancelled"]}, headers=headers_a)) == {
        "P-EXPIRED", "P-CANCELLED"
    }
    assert _numbers(client.get(base, params={"collection": "overdue"}, headers=headers_a)) == {"P-OVERDUE"}
    assert _numbers(client.get(base, params={"collection": "due_soon"}, headers=headers_a)) == {"P-SOON"}
    assert "P-NOPLAN" in _numbers(client.get(base, params={"collection": "no_plan"}, headers=headers_a))
    assert _numbers(client.get(base, params={"line_code": "VEH"}, headers=headers_a)) == {"P-SOON"}
    assert _numbers(
        client.get(base, params={"subtype_id": portfolio["overdue"].insurance_subtype_id}, headers=headers_a)
    ) == {"P-OVERDUE"}
    assert _numbers(client.get(base, params={"origin": "manual"}, headers=headers_a)) == {"P-NOPLAN"}
    without_doc = _numbers(client.get(base, params={"missing": "source_document"}, headers=headers_a))
    assert "P-OVERDUE" not in without_doc and "P-NOPLAN" in without_doc
    no_commission = _numbers(client.get(base, params={"missing": "commission"}, headers=headers_a))
    assert "P-OVERDUE" not in no_commission and "P-OK" in no_commission
    # q matches the insured's RUT in its display form too.
    assert len(_numbers(client.get(base, params={"q": "77.111.111-4"}, headers=headers_a))) == 7
    assert _numbers(client.get(base, params={"q": "P-SOON"}, headers=headers_a)) == {"P-SOON"}
    # Paging over a computed filter.
    page = client.get(base, params={"status": "active", "limit": 2}, headers=headers_a).json()
    assert page["total"] == 4 and len(page["items"]) == 2
    # Unknown computed values are a 422, never a silent empty page.
    assert client.get(base, params={"status": "zombie"}, headers=headers_a).status_code == 422
    assert client.get(base, params={"collection": "nope"}, headers=headers_a).status_code == 422
    assert client.get(base, params={"missing": "nope"}, headers=headers_a).status_code == 422


def test_sql_narrowed_filters_match_the_python_rule(client, world, db, portfolio, headers_a):
    """``collection`` / ``missing`` are narrowed in SQL; whatever reaches the
    page must equal the per-row Python rule for every value and combination."""
    from app.services import policy_registry as registry

    rows = client.get(f"{API}/policies", params={"limit": 200}, headers=headers_a).json()["items"]
    state = {r["policy_number"]: r["collection_summary"]["state"] for r in rows}
    lacking = {r["policy_number"]: set(r["missing"]) for r in rows}
    base = f"{API}/policies"
    for values in (["overdue"], ["due_soon"], ["ok"], ["no_plan"], ["no_plan", "overdue"], ["ok", "due_soon"]):
        got = client.get(base, params={"collection": values}, headers=headers_a).json()
        expected = {n for n, s in state.items() if s in values}
        assert {r["policy_number"] for r in got["items"]} == expected, values
        assert got["total"] == len(expected)
    for keys in [[k] for k in registry.MISSING_KEYS] + [["plan", "payer_contact"], ["commission", "gross_premium"]]:
        got = client.get(base, params={"missing": keys}, headers=headers_a).json()
        expected = {n for n, miss in lacking.items() if miss & set(keys)}
        assert {r["policy_number"] for r in got["items"]} == expected, keys
        assert got["total"] == len(expected)
    # Combined with a computed status and paging.
    got = client.get(
        base, params={"status": "active", "missing": "plan", "limit": 1}, headers=headers_a
    ).json()
    assert got["total"] == 1 and [r["policy_number"] for r in got["items"]] == ["P-NOPLAN"]


def test_a_negative_cadence_makes_a_cuota_late_before_it_falls_due(
    client, world, db, catalog, headers_a
):
    """``termination_risk`` fires at ``today > due + max(cadence)``: with an
    all-negative cadence a cuota due in two days is already late, and both the
    list filter and the summary card must see it."""
    from tests.fakes_v12 import make_collection_settings

    make_collection_settings(db, world.a.broker_id, cadence_days=[-15, -5])
    make_policy_with_plan(
        db, world.a, world.insurer, policy_number="P-EARLY", n_installments=1,
        start=TODAY + timedelta(days=2), start_date=TODAY - timedelta(days=5),
        end_date=TODAY + timedelta(days=360),
    )
    db.commit()
    listed = client.get(f"{API}/policies", params={"collection": "overdue"}, headers=headers_a)
    assert _numbers(listed) == {"P-EARLY"}
    cards = client.get(f"{API}/policies/summary", headers=headers_a).json()
    assert cards["overdue_installments"] == 1


def test_owner_me_filter(client, world, db, portfolio, headers_a, headers_a_exec):
    client_row = db.get(Client, world.a.client.id)
    client_row.account_manager_id = world.a.executive.id
    db.commit()
    assert len(_numbers(client.get(f"{API}/policies", params={"owner": "me"}, headers=headers_a_exec))) == 7
    assert _numbers(client.get(f"{API}/policies", params={"owner": "me"}, headers=headers_a)) == set()


def test_summary_cards(client, world, db, portfolio, headers_a, headers_b):
    body = client.get(f"{API}/policies/summary", headers=headers_a).json()
    assert body["active_count"] == 4  # overdue, soon, ok, no_plan
    assert Decimal(body["active_premium_uf"]) == Decimal("556.0000")
    assert body["expiring_90"] == 1
    assert body["no_plan"] == 2  # no_plan + upcoming
    assert body["overdue_installments"] == 3
    assert Decimal(body["overdue_uf"]) == Decimal("139.0000")
    assert body["without_source_document"] == 4  # soon, ok, no_plan, upcoming
    assert body["provisional"] == 1
    # Legacy fields keep their stored-status meaning.
    assert body["total"] == 7
    other = client.get(f"{API}/policies/summary", headers=headers_b).json()
    assert other["active_count"] == 1 and other["overdue_installments"] == 0


# =============================================================================
# The ficha + PATCH semantics
# =============================================================================

def test_detail_operability_reasons(client, portfolio, headers_a):
    detail = client.get(f"{API}/policies/{portfolio['no_plan'].id}", headers=headers_a).json()
    ops = {item["feature"]: item for item in detail["operability"]}
    assert ops["collections"]["enabled"] is False
    assert ops["collections"]["reason"].startswith("Falta plan de pago")
    assert ops["source_document"]["enabled"] is False
    assert ops["endorsements"]["enabled"] is True
    assert detail["journey"] is None  # no colocación yet (legacy fixture)


def test_patch_completar_needs_no_reason(client, world, db, catalog, headers_a):
    sub = _sub(db, "PRO-02")
    created = client.post(
        f"{API}/policies", json=_manual_body(world, sub, policy_number=None), headers=headers_a
    ).json()
    pid = created["id"]
    response = client.patch(
        f"{API}/policies/{pid}",
        json={"commission_pct": "10", "policy_number": "REAL-123", "is_provisional": True},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy_number"] == "REAL-123" and body["has_policy_number"] is True
    assert "commission" not in body["missing"] and "policy_number" not in body["missing"]
    activity = db.scalars(
        select(Activity).where(Activity.action == "policy.completed", Activity.entity_id == pid)
    ).one()
    assert set(activity.meta["fields"]) == {"commission_pct", "policy_number", "is_provisional"}

    # Same value again = no-op (no new activity).
    again = client.patch(f"{API}/policies/{pid}", json={"commission_pct": "10.000"}, headers=headers_a)
    assert again.status_code == 200
    assert len(db.scalars(select(Activity).where(
        Activity.entity_id == pid, Activity.action.in_(["policy.completed", "policy.corrected"])
    )).all()) == 1


def test_patch_correction_requires_a_reason_and_logs_before_after(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    pid = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a).json()["id"]

    denied = client.patch(
        f"{API}/policies/{pid}", json={"taxable_premium_uf": "200", "end_date": "2027-04-01"},
        headers=headers_a,
    )
    assert denied.status_code == 422, denied.text
    detail = denied.json()["detail"]
    assert detail["code"] == "correction_reason_required"
    assert set(detail["fields"]) >= {"taxable_premium_uf", "end_date"}

    ok = client.patch(
        f"{API}/policies/{pid}",
        json={"taxable_premium_uf": "200", "end_date": "2027-04-01",
              "correction_reason": "Error de digitación"},
        headers=headers_a,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert Decimal(body["vat_uf"]) == Decimal("38")
    assert body["end_date"] == "2027-04-01"
    assert body["period_end_at"].startswith("2027-04-01")
    activity = db.scalars(
        select(Activity).where(Activity.action == "policy.corrected", Activity.entity_id == pid)
    ).one()
    assert activity.meta["reason"] == "Error de digitación"
    assert activity.meta["before"]["end_date"] == "2027-03-01"
    assert activity.meta["after"]["end_date"] == "2027-04-01"

    # Changing the number to another policy's -> 409.
    client.post(f"{API}/policies", json=_manual_body(world, sub, policy_number="MAN-OTHER"), headers=headers_a)
    clash = client.patch(
        f"{API}/policies/{pid}", json={"policy_number": "MAN-OTHER", "correction_reason": "x"},
        headers=headers_a,
    )
    assert clash.status_code == 409


# =============================================================================
# PUT parties
# =============================================================================

def test_put_parties_validates_and_replaces(client, world, db, catalog, headers_a):
    sub = _sub(db, "PRO-02")
    pid = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a).json()["id"]
    url = f"{API}/policies/{pid}/parties"
    insured = {"role": "insured", "name": "Asegurado Alfa S.A.", "rut": "77111111-4"}
    holder = {"role": "policyholder", "name": "Contratante", "rut": "76086428-5", "email": "c@x.cl"}

    for bad in (
        [insured],                                   # no policyholder
        [insured, holder, {**holder, "name": "Otro"}],  # two policyholders
        [holder],                                    # no insured
        [insured, holder, {"role": "payer", "name": "A"}, {"role": "payer", "name": "B"}],
    ):
        assert client.put(url, json={"parties": bad}, headers=headers_a).status_code == 422
    invalid_rut = [insured, {**holder, "rut": "76.086.428-1"}]
    assert client.put(url, json={"parties": invalid_rut}, headers=headers_a).status_code == 422
    foreign = [insured, {**holder, "client_id": world.b.client.id}]
    assert client.put(url, json={"parties": foreign}, headers=headers_a).status_code == 404

    beneficiary = {"role": "beneficiary", "name": "Banco Hipotecario", "beneficiary_kind": "hipotecario",
                   "share_pct": "100"}
    ok = client.put(url, json={"parties": [insured, holder, beneficiary]}, headers=headers_a)
    assert ok.status_code == 200, ok.text
    parties = {p["role"]: p for p in ok.json()["parties"]}
    assert set(parties) == {"insured", "policyholder", "beneficiary"}
    # A RUT that is the broker's client links its client_id.
    assert parties["insured"]["client_id"] == world.a.client.id
    assert parties["policyholder"]["client_id"] is None
    rows = db.scalars(select(PolicyParty).where(PolicyParty.policy_id == pid)).all()
    assert len(rows) == 3
    assert db.scalars(
        select(Activity).where(Activity.action == "policy.parties_replaced", Activity.entity_id == pid)
    ).one().meta["before"]


# =============================================================================
# Upload: reader origin, colocación on demand, cuotas commit
# =============================================================================

@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


def _fake(monkeypatch, payload: dict):
    monkeypatch.setattr(
        ai_service, "_chat_model", lambda **kwargs: _FakeChatModel(content=json.dumps(payload))
    )


def _doc(db, tenant, tmp_path, *, case_id=None, name="poliza.txt"):
    path = tmp_path / name
    path.write_text("Póliza emitida por la aseguradora. " * 20, encoding="utf-8")
    document = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CASE_FILE if case_id else EntityType.CLIENT,
        entity_id=case_id or tenant.client.id,
        case_file_id=case_id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=DocumentCategory.POLICY,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


_INSURER = {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"}


def _policy_payload(number: str, installments: list[str] | None = None, **extra) -> dict:
    payload = {
        "policy_number": number,
        "insurer": _INSURER,
        "broker": {"name": "Corredora Alfa SpA"},
        "period_start_at": "2026-01-01T12:00:00Z",
        "period_end_at": "2027-01-01T12:00:00Z",
        "total_insured_amount_uf": 17920,
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 20},
        "payment_mode": "PAC",
    }
    if installments is not None:
        payload["installments"] = [
            {"number": i + 1, "due_date": f"2026-0{i + 1}-05", "gross_amount_uf": amount}
            for i, amount in enumerate(installments)
        ]
    payload.update(extra)
    return payload


def test_upload_on_a_folder_commits_the_balanced_cuotas(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    case = make_case_file(db, world.a)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id)
    _fake(monkeypatch, _policy_payload("UP-OK", ["69.5", "69.5"]))

    response = client.post(
        f"{API}/policies/upload", json={"document_id": document.id, "case_file_id": case.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    policy = body["policy"]
    assert policy["origin"] == "reader" and policy["has_source_document"] is True
    assert "source_document" not in policy["missing"] and "plan" not in policy["missing"]
    assert any("Plan de pago registrado" in w for w in body["warnings"])
    assert policy["payment_method"] == "direct_debit"

    plan = db.scalars(select(CollectionPlan).where(CollectionPlan.policy_id == policy["id"])).one()
    assert plan.source == CollectionPlanSource.READER
    assert plan.origin == CollectionPlanOrigin.POLICY
    assert plan.source_document_id == document.id
    assert [i.gross_amount_uf for i in plan.installments] == [Decimal("69.5000"), Decimal("69.5000")]
    assert all(i.currency.value == "UF" for i in plan.installments)

    db.refresh(case)
    assert case.stage == CaseStage.ACTIVE
    placement = db.get(Placement, world.a.placement.id)
    assert placement.status.value == "active"
    parties = db.scalars(select(PolicyParty).where(PolicyParty.policy_id == policy["id"])).all()
    assert {p.role.value for p in parties} == {"insured", "policyholder", "payer"}


def test_upload_with_unbalanced_cuotas_keeps_the_warning(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    case = make_case_file(db, world.a)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id)
    _fake(monkeypatch, _policy_payload("UP-BAD", ["50", "50"]))
    response = client.post(
        f"{API}/policies/upload", json={"document_id": document.id}, headers=headers_a
    )
    assert response.status_code == 201, response.text
    warnings = response.json()["warnings"]
    assert any("no cuadran" in w for w in warnings)
    assert any("plan de pago de la póliza no se registró" in w for w in warnings)
    assert db.scalars(select(CollectionPlan)).all() == []


def test_upload_without_folder_creates_the_colocacion(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db, "VEH-01")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-NEW", ["139"]))

    missing_subtype = client.post(
        f"{API}/policies/upload", json={"document_id": document.id}, headers=headers_a
    )
    assert missing_subtype.status_code == 422

    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "client_id": world.a.client.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    policy = response.json()["policy"]
    case = db.get(CaseFile, policy["case_file_id"])
    assert case.entry_origin == CaseEntryOrigin.POLICY_UPLOAD
    assert case.route.value == "digital"
    assert case.stage == CaseStage.ACTIVE
    assert policy["insurance_subtype_id"] == sub.id
    skipped = [e.to_stage for e in _events(db, case.id) if e.is_skipped]
    assert CaseStage.LEAD in skipped and CaseStage.RATIFIED in skipped
    db.refresh(document)
    assert document.case_file_id == case.id


def test_upload_without_folder_resolves_the_insured_by_rut(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db, "PRO-02")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-RUT", insured={"legal_name": "Asegurado Alfa S.A.", "rut": "77.111.111-4"}))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    assert response.json()["policy"]["client_id"] == world.a.client.id

    # No RUT, no client_id -> 422 insured_unresolved, nothing committed.
    other = _doc(db, world.a, tmp_path, name="otra.txt")
    _fake(monkeypatch, _policy_payload("UP-NORUT"))
    unresolved = client.post(
        f"{API}/policies/upload",
        json={"document_id": other.id, "insurance_subtype_id": sub.id},
        headers=headers_a,
    )
    assert unresolved.status_code == 422
    assert unresolved.json()["detail"]["code"] == "insured_unresolved"
    assert db.scalars(select(Policy).where(Policy.policy_number == "UP-NORUT")).first() is None


def test_upload_with_a_foreign_client_is_404(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db, "PRO-02")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-X"))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "client_id": world.b.client.id},
        headers=headers_a,
    )
    assert response.status_code == 404


# =============================================================================
# Isolation + permissions
# =============================================================================

def test_another_tenants_policy_is_404_everywhere(client, world, db, catalog, headers_a, headers_b):
    sub = _sub(db, "PRO-02")
    pid = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a).json()["id"]
    assert client.get(f"{API}/policies/{pid}", headers=headers_b).status_code == 404
    assert client.patch(
        f"{API}/policies/{pid}", json={"commission_pct": "5"}, headers=headers_b
    ).status_code == 404
    parties = {"parties": [{"role": "insured", "name": "X"}, {"role": "policyholder", "name": "Y"}]}
    assert client.put(f"{API}/policies/{pid}/parties", json=parties, headers=headers_b).status_code == 404
    assert client.get(f"{API}/policies", headers=headers_b).json()["total"] == 0
    # Broker B naming A's client or A's folder is a 404, never a write.
    foreign_client = _manual_body(world, sub, policy_number="B-1")
    assert client.post(f"{API}/policies", json=foreign_client, headers=headers_b).status_code == 404
    case_a = db.scalars(select(CaseFile).where(CaseFile.broker_id == world.a.broker_id)).first()
    foreign_case = _manual_body(world, sub, client_id=world.b.client.id, policy_number="B-2", case_file_id=case_a.id)
    assert client.post(f"{API}/policies", json=foreign_case, headers=headers_b).status_code == 404


def test_permission_gates(client, world, db, catalog, headers_a, headers_a_inspector):
    sub = _sub(db, "PRO-02")
    pid = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a).json()["id"]
    assert client.get(f"{API}/policies/{pid}", headers=headers_a_inspector).status_code == 200
    assert client.get(f"{API}/policies/summary", headers=headers_a_inspector).status_code == 200
    assert client.post(
        f"{API}/policies", json=_manual_body(world, sub, policy_number="INS-1"), headers=headers_a_inspector
    ).status_code == 403
    assert client.patch(
        f"{API}/policies/{pid}", json={"commission_pct": "5"}, headers=headers_a_inspector
    ).status_code == 403
    parties = {"parties": [{"role": "insured", "name": "X"}, {"role": "policyholder", "name": "Y"}]}
    assert client.put(
        f"{API}/policies/{pid}/parties", json=parties, headers=headers_a_inspector
    ).status_code == 403


# =============================================================================
# Fixer regressions (review of lane B1)
# =============================================================================

def _second_client(db, world, rut="11111111-1", name="Segundo RUT SpA"):
    from app.models.insured import Insured

    insured = Insured(rut=rut, legal_name=name)
    db.add(insured)
    db.flush()
    row = Client(broker_id=world.a.broker_id, insured_id=insured.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_upload_of_another_number_into_the_same_folder_keeps_both_policies(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    """A second policy of the same client, subtipo and vigencia (a fleet) is a
    NEW policy in the same folder — never an overwrite of the first one."""
    sub = _sub(db, "VEH-01")
    manual = client.post(
        f"{API}/policies",
        json=_manual_body(
            world, sub, policy_number="MAN-VEH-1", start_date="2026-01-01",
            end_date="2027-01-01", taxable_premium_uf="10", exempt_premium_uf="0",
        ),
        headers=headers_a,
    )
    assert manual.status_code == 201, manual.text
    first = manual.json()

    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-VEH-2"))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "client_id": world.a.client.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    second = response.json()["policy"]
    assert second["id"] != first["id"]
    assert second["policy_number"] == "UP-VEH-2"
    assert second["case_file_id"] == first["case_file_id"]

    kept = db.get(Policy, first["id"])
    db.refresh(kept)
    assert kept.policy_number == "MAN-VEH-1"
    assert kept.total_premium_uf == Decimal("11.9000")
    assert kept.source_document_id is None
    assert len(db.scalars(select(Policy).where(Policy.broker_id == world.a.broker_id)).all()) == 2

    # Re-uploading the SAME number is a duplicate (review r2, D2): 409, no
    # third row and the existing policy untouched.
    again = _doc(db, world.a, tmp_path, name="again.txt")
    _fake(monkeypatch, _policy_payload("UP-VEH-2"))
    redo = client.post(
        f"{API}/policies/upload",
        json={"document_id": again.id, "insurance_subtype_id": sub.id, "client_id": world.a.client.id},
        headers=headers_a,
    )
    assert redo.status_code == 409, redo.text
    assert redo.json()["detail"]["code"] == "policy_duplicate"
    assert redo.json()["detail"]["policy_id"] == second["id"]
    assert len(db.scalars(select(Policy).where(Policy.broker_id == world.a.broker_id)).all()) == 2


def test_upload_replaces_a_numberless_stand_in_of_the_folder(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    """The definitiva replaces the manual placeholder (``SIN-N-…``) it was
    registered for — the one case where the folder's policy is updated."""
    sub = _sub(db, "VEH-01")
    manual = client.post(
        f"{API}/policies",
        json=_manual_body(world, sub, policy_number=None, start_date="2026-01-01", end_date="2027-01-01"),
        headers=headers_a,
    ).json()
    assert manual["has_policy_number"] is False
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-DEF-1"))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "client_id": world.a.client.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    assert response.json()["policy"]["id"] == manual["id"]
    assert response.json()["policy"]["policy_number"] == "UP-DEF-1"


def test_upload_of_a_folio_registered_in_another_folder_is_422(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db, "VEH-01")
    client.post(
        f"{API}/policies",
        json=_manual_body(world, sub, policy_number="FOLIO-1", start_date="2026-01-01", end_date="2027-01-01"),
        headers=headers_a,
    )
    other_case = make_case_file(db, world.a, n=9)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=other_case.id)
    _fake(monkeypatch, _policy_payload("FOLIO-1"))
    response = client.post(
        f"{API}/policies/upload", json={"document_id": document.id}, headers=headers_a
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "policy_in_other_account"
    assert db.scalars(select(Policy).where(Policy.policy_number == "FOLIO-1")).one().case_file_id != other_case.id


def test_upload_into_a_multi_rut_folder_registers_the_requested_insured(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    from app.models.account_client import AccountClient, AccountClientRole

    sub = _sub(db, "PRO-02")
    member = _second_client(db, world)
    opened = client.post(
        f"{API}/case-files",
        json={
            "kind": "account", "client_id": world.a.client.id, "insurance_subtype_id": sub.id,
            "period_start": "2026-01-01", "period_end": "2027-01-01",
        },
        headers=headers_a,
    )
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["id"]
    db.add(AccountClient(
        broker_id=world.a.broker_id, case_file_id=case_id, client_id=member.id,
        role=AccountClientRole.INSURED,
    ))
    db.commit()

    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-MEMBER"))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "client_id": member.id, "insurance_subtype_id": sub.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    policy = response.json()["policy"]
    assert policy["case_file_id"] == case_id
    assert policy["client_id"] == member.id
    insured = db.scalars(
        select(PolicyParty).where(PolicyParty.policy_id == policy["id"], PolicyParty.role == "insured")
    ).one()
    assert insured.client_id == member.id and insured.rut == "11111111-1"

    # A folder upload naming a client that is NOT a member is refused.
    stranger = _second_client(db, world, rut="22222222-2", name="Ajeno Ltda.")
    folder_doc = _doc(db, world.a, tmp_path, case_id=case_id, name="folder.txt")
    _fake(monkeypatch, _policy_payload("UP-STRANGER"))
    refused = client.post(
        f"{API}/policies/upload",
        json={"document_id": folder_doc.id, "case_file_id": case_id, "client_id": stranger.id},
        headers=headers_a,
    )
    assert refused.status_code == 422


def test_upload_never_creates_an_insured_without_confirmation(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    from app.models.insured import Insured

    sub = _sub(db, "PRO-02")
    clients_before = len(db.scalars(select(Client)).all())
    document = _doc(db, world.a, tmp_path)
    payload = _policy_payload(
        "UP-NEWRUT", insured={"legal_name": "Nuevo Asegurado SpA", "rut": "12.345.678-5"}
    )
    _fake(monkeypatch, payload)
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id},
        headers=headers_a,
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "insured_unresolved"
    assert detail["suggestion"] == {"rut": "12345678-5", "legal_name": "Nuevo Asegurado SpA"}
    assert len(db.scalars(select(Client)).all()) == clients_before
    assert db.scalars(select(Insured).where(Insured.rut == "12345678-5")).first() is None

    # The broker confirms "Crear ahí mismo, prellenado" -> the client is created.
    _fake(monkeypatch, payload)
    confirmed = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "create_client": True},
        headers=headers_a,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["policy"]["insured_rut"] == "12345678-5"


def test_upload_never_takes_the_policyholder_as_the_insured(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    """A contratante distinct from the insured is a party, never the asegurado
    (R14/R40) — even when its RUT is a client of the broker."""
    sub = _sub(db, "PRO-02")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload(
        "UP-HOLDER", policyholder={"legal_name": "Asegurado Alfa S.A.", "rut": "77.111.111-4"}
    ))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "create_client": True},
        headers=headers_a,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "insured_unresolved"
    assert db.scalars(select(Policy).where(Policy.policy_number == "UP-HOLDER")).first() is None


def test_manual_entry_into_a_named_folder_must_fit_it(client, world, db, catalog, headers_a):
    from app.models.enums import CaseFileStatus

    pro = _sub(db, "PRO-02")
    # A folder on the tenant's own (non-PRO) line, 2026-05-01..2027-05-01.
    opened = client.post(
        f"{API}/case-files",
        json={
            "kind": "account", "client_id": world.a.client.id,
            "insurance_line_id": world.a.line.id,
            "period_start": "2026-05-01", "period_end": "2027-05-01",
        },
        headers=headers_a,
    )
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["id"]

    wrong_line = client.post(
        f"{API}/policies", json=_manual_body(world, pro, case_file_id=case_id), headers=headers_a
    )
    assert wrong_line.status_code == 422, wrong_line.text
    case = db.get(CaseFile, case_id)
    db.refresh(case)
    assert case.insurance_subtype_id is None and case.stage == CaseStage.LEAD

    # A subtipo of the folder's ramo, but outside its vigencia.
    same_line = db.scalars(
        select(InsuranceSubtype).where(InsuranceSubtype.insurance_line_id == world.a.line.id)
    ).first()
    if same_line is not None:
        outside = client.post(
            f"{API}/policies",
            json=_manual_body(world, same_line, case_file_id=case_id, policy_number="OUT-1"),
            headers=headers_a,
        )
        assert outside.status_code == 422, outside.text

    # A stranger to the folder, and a closed folder.
    stranger = _second_client(db, world)
    not_member = client.post(
        f"{API}/policies",
        json=_manual_body(world, pro, client_id=stranger.id, case_file_id=case_id, policy_number="NM-1"),
        headers=headers_a,
    )
    assert not_member.status_code == 422
    case.status = CaseFileStatus.CLOSED
    db.commit()
    closed = client.post(
        f"{API}/policies",
        json=_manual_body(world, pro, case_file_id=case_id, policy_number="CL-1"),
        headers=headers_a,
    )
    assert closed.status_code == 422
    assert db.scalars(select(Policy).where(Policy.broker_id == world.a.broker_id)).all() == []


def test_patch_completar_of_an_input_never_erases_the_stated_total(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    created = client.post(
        f"{API}/policies",
        json=_manual_body(
            world, sub, taxable_premium_uf=None, exempt_premium_uf=None, total_premium_uf="119"
        ),
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    # Filling taxable=110 cannot fit the stated 119 (net = 119 - 20.9 = 98.1
    # < taxable, so exempt would be negative) -> 422, nothing written.
    contradicts = client.patch(
        f"{API}/policies/{pid}", json={"taxable_premium_uf": "110"}, headers=headers_a
    )
    assert contradicts.status_code == 422, contradicts.text
    row = db.get(Policy, pid)
    db.refresh(row)
    assert row.total_premium_uf == Decimal("119.0000") and row.taxable_premium_uf is None

    # A consistent fill derives only the EMPTY figures, with no reason.
    fills = client.patch(
        f"{API}/policies/{pid}",
        json={"taxable_premium_uf": "100", "exempt_premium_uf": "0"},
        headers=headers_a,
    )
    assert fills.status_code == 200, fills.text
    body = fills.json()
    assert Decimal(body["total_premium_uf"]) == Decimal("119")
    assert Decimal(body["net_premium_uf"]) == Decimal("100")
    assert Decimal(body["vat_uf"]) == Decimal("19")
    assert "gross_premium" not in body["missing"]
    done = db.scalars(
        select(Activity).where(Activity.action == "policy.completed", Activity.entity_id == pid)
    ).one()
    assert set(done.meta["fields"]) == {
        "taxable_premium_uf", "exempt_premium_uf", "net_premium_uf", "vat_uf"
    }


def test_patch_completar_of_taxable_on_a_total_only_policy_completes_the_rest(
    client, world, db, catalog, headers_a
):
    """The reviewer's repro: total=119 only, then Completar taxable=50. The total
    is kept and the empty figures are derived from it (vat 9.5, net 109.5,
    exempt 59.5) — never erased, never left half-derived."""
    sub = _sub(db, "PRO-02")
    created = client.post(
        f"{API}/policies",
        json=_manual_body(
            world, sub, taxable_premium_uf=None, exempt_premium_uf=None, total_premium_uf="119"
        ),
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    resp = client.patch(
        f"{API}/policies/{pid}", json={"taxable_premium_uf": "50"}, headers=headers_a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["total_premium_uf"]) == Decimal("119")
    assert Decimal(body["vat_uf"]) == Decimal("9.5")
    assert Decimal(body["net_premium_uf"]) == Decimal("109.5")
    assert Decimal(body["exempt_premium_uf"]) == Decimal("59.5")
    assert "gross_premium" not in body["missing"] and "premiums" not in body["missing"]
    assert not db.scalars(
        select(Activity).where(Activity.action == "policy.corrected", Activity.entity_id == pid)
    ).all()
    done = db.scalars(
        select(Activity).where(Activity.action == "policy.completed", Activity.entity_id == pid)
    ).one()
    assert set(done.meta["fields"]) == {
        "taxable_premium_uf", "exempt_premium_uf", "net_premium_uf", "vat_uf"
    }


def test_patch_correction_records_the_derived_money_before_after(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    pid = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a).json()["id"]
    denied = client.patch(f"{API}/policies/{pid}", json={"taxable_premium_uf": "200"}, headers=headers_a)
    assert denied.status_code == 422
    assert set(denied.json()["detail"]["fields"]) == {
        "taxable_premium_uf", "net_premium_uf", "vat_uf", "total_premium_uf"
    }
    ok = client.patch(
        f"{API}/policies/{pid}",
        json={"taxable_premium_uf": "200", "correction_reason": "Prima mal digitada"},
        headers=headers_a,
    )
    assert ok.status_code == 200, ok.text
    assert Decimal(ok.json()["total_premium_uf"]) == Decimal("258")
    activity = db.scalars(
        select(Activity).where(Activity.action == "policy.corrected", Activity.entity_id == pid)
    ).one()
    assert Decimal(activity.meta["before"]["total_premium_uf"]) == Decimal("139")
    assert Decimal(activity.meta["after"]["total_premium_uf"]) == Decimal("258")
    assert Decimal(activity.meta["before"]["net_premium_uf"]) == Decimal("120")


def test_reupload_keeps_reader_cuotas_that_already_carry_work(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    from app.models.enums import EvidenceStatus
    from app.models.mail import PaymentEvidence

    case = make_case_file(db, world.a)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id)
    _fake(monkeypatch, _policy_payload("UP-LOCK", ["69.5", "69.5"]))
    first = client.post(
        f"{API}/policies/upload", json={"document_id": document.id}, headers=headers_a
    )
    assert first.status_code == 201, first.text
    plan = db.scalars(select(CollectionPlan).where(CollectionPlan.policy_id == first.json()["policy"]["id"])).one()
    original = [inst.id for inst in plan.installments]
    db.add(PaymentEvidence(
        broker_id=world.a.broker_id, installment_id=original[0],
        status=list(EvidenceStatus)[0],
    ))
    db.commit()

    again = _doc(db, world.a, tmp_path, case_id=case.id, name="again.txt")
    _fake(monkeypatch, _policy_payload("UP-LOCK", ["139"]))
    second = client.post(
        f"{API}/policies/upload", json={"document_id": again.id}, headers=headers_a
    )
    # Review r2 (D2): the same policy uploaded again is a 409 with no side
    # effect — the cuotas carrying work are untouched a fortiori.
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "policy_duplicate"
    db.expire_all()
    plan = db.get(CollectionPlan, plan.id)
    assert [inst.id for inst in plan.installments] == original

    # Started avisos pin the plan too.
    plan.notices_enabled = True
    db.commit()


def test_computed_status_clause_matches_the_python_rule(db, world, catalog, portfolio):
    from app.services.policy_registry import computed_status, computed_status_clause

    extra = [
        make_policy(db, world.a, world.insurer, policy_number="X-DRAFT",
                    status=PolicyStatus.DRAFT, start_date=TODAY - timedelta(days=3),
                    end_date=TODAY + timedelta(days=30)),
        make_policy(db, world.a, world.insurer, policy_number="X-DRAFT-OLD",
                    status=PolicyStatus.DRAFT, start_date=TODAY - timedelta(days=400),
                    end_date=TODAY - timedelta(days=30)),
        make_policy(db, world.a, world.insurer, policy_number="X-TERM",
                    start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=30),
                    terminated_on=TODAY),
        make_policy(db, world.a, world.insurer, policy_number="X-TERM-LATER",
                    start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=30),
                    terminated_on=TODAY + timedelta(days=5)),
        make_policy(db, world.a, world.insurer, policy_number="X-RENEWED",
                    status=PolicyStatus.RENEWED, start_date=None, end_date=None),
        make_policy(db, world.a, world.insurer, policy_number="X-NODATES",
                    start_date=None, end_date=None),
        make_policy(db, world.a, world.insurer, policy_number="X-ENDS-TODAY",
                    start_date=TODAY - timedelta(days=365), end_date=TODAY),
    ]
    db.commit()
    assert extra
    policies = db.scalars(select(Policy)).all()
    for state in ("upcoming", "active", "expired", "cancelled"):
        in_sql = set(db.scalars(select(Policy.id).where(computed_status_clause(state, TODAY))).all())
        in_python = {p.id for p in policies if computed_status(p, TODAY) == state}
        assert in_sql == in_python, state
