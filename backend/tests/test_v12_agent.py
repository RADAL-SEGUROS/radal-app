"""v12 agent (lane B7, docs/v12/05-agent.md): the grown registry + skills.

Hermetic: the provider is the scripted double from ``tests/test_agent_tools.py``
monkeypatched over ``ai_service._chat_model``; no test reaches DeepInfra, mail,
Forpay or a portal. What this file encodes:

- registry sanity for every v12 tool (gates name real modules/actions, every
  write tool summarises and validates, schemas stay compact);
- each new READ tool returns broker-scoped data, folds ``not_found`` for
  another tenant's id and ``permission_denied`` for a role without the grant;
- each new WRITE tool PROPOSES (nothing written) and executes only on confirm,
  through the lane's own router function (same rows, same activity);
- skills: the catalogue endpoint, the thread's active skills endpoint, the
  ``load_skill`` loop, and the playbooks reaching the system prompt (budgeted).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.roles_config import ACTIONS, MODULES
from app.models.activity import Activity
from app.models.ai import AgentAction, AgentActionStatus, AgentThread
from app.models.case_file import CaseFile
from app.models.collection import CollectionInstallment, CollectionPlan
from app.models.collection_settings import CollectionNotice
from app.models.enums import CaseStage, PartyRole
from app.models.mail import StatusInquiry
from app.models.payment import Payment, PaymentIntent
from app.models.policy import Policy
from app.models.rpa import PortalSearchRun
from app.models.share_link import ShareLink
from app.services import agent_skills, agent_tools
from app.services import ai as ai_service
from tests.conftest import API, make_case_file
from tests.fakes_v12 import make_insurer_profile, make_party, make_policy_with_plan
from tests.test_agent_tools import _Answer, _call, _FakeToolChatModel, _seed_action

TODAY = date(2026, 2, 12)

V12_READ = {
    "list_policies", "get_policy", "list_installments", "get_collection_overview",
    "get_collection_mode", "list_status_inquiries", "list_pending_evidence",
    "check_case_files", "check_policy_files", "get_catalog", "list_portal_searches",
    "get_portal_search", "list_notifications", "load_skill",
}
V12_WRITE = {
    "create_policy_manual", "update_policy_fields", "register_payment",
    "record_payment_promise", "start_collection", "send_status_inquiry",
    "send_payment_reminder", "create_share_link", "create_payment_link",
    "skip_case_to_stage", "activate_case_stage", "run_portal_search",
}
V4_WRITE = {"create_group", "attach_client_to_group", "create_case_file", "renew_case", "add_note"}


# --- fixtures & helpers ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _portal_search_feature_on(portal_search_on):
    """This suite asserts the FULL registry (portal tools + skill included);
    the flag-off behaviour lives in tests/test_copilot_home_sessions.py."""


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


@pytest.fixture()
def today(monkeypatch):
    from app.services.collection import clock

    monkeypatch.setattr(clock, "business_today", lambda now=None: TODAY)
    return TODAY


@pytest.fixture()
def cobranza(db, world):
    """Broker A: a policy + 3 cuotas (01-05 / 02-05 / 03-05). Broker B: a mirror."""
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-A-77")
    b_policy, b_plan = make_policy_with_plan(db, world.b, world.insurer, policy_number="POL-B-77")
    db.commit()
    return {
        "policy": policy,
        "plan": plan,
        "inst": plan.installments,
        "b_policy": b_policy,
        "b_plan": b_plan,
        "b_inst": b_plan.installments,
    }


def _use(monkeypatch, fake: _FakeToolChatModel) -> None:
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)


def _turn(client, headers, content: str, *, thread_id=None):
    body: dict = {"content": content}
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post(f"{API}/ai/agent/messages", json=body, headers=headers)


def _one_call(client, headers, monkeypatch, name: str, args: dict, *, thread_id=None):
    """One turn in which the model calls ``name`` once, then answers."""
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call(name, args)]),
        _Answer(content="Listo."),
    ])
    _use(monkeypatch, fake)
    response = _turn(client, headers, f"usa {name}", thread_id=thread_id)
    assert response.status_code == 201, response.text
    data = response.json()
    folded = json.loads(data["messages"][2]["content"])
    return data, folded, fake


def _propose(client, headers, monkeypatch, name: str, args: dict) -> dict:
    data, folded, _ = _one_call(client, headers, monkeypatch, name, args)
    assert folded.get("status") == "pending_confirmation", folded
    assert len(data["pending_actions"]) == 1
    pending = data["pending_actions"][0]
    assert pending["tool"] == name and pending["status"] == "proposed"
    assert pending["summary"], "the Spanish card sentence is built server-side"
    return pending


def _confirm(client, headers, action_id: int):
    return client.post(f"{API}/ai/agent/actions/{action_id}/confirm", headers=headers)


def _count(db, model, *where) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# --- 1. Registry sanity -------------------------------------------------------------


def test_v12_registry_is_complete_and_sane():
    registry = agent_tools.TOOL_REGISTRY
    assert V12_READ <= set(agent_tools.READ_TOOLS)
    assert set(agent_tools.WRITE_TOOLS) == V4_WRITE | V12_WRITE
    for spec in registry.values():
        assert spec.module in MODULES, spec.name
        assert spec.action in ACTIONS, spec.name
        if spec.kind == "write":
            assert spec.summarize is not None, spec.name
            assert spec.validate_proposal is not None, spec.name
            assert spec.anchor is not None, spec.name
            assert not spec.thread_aware, spec.name
    assert registry["load_skill"].thread_aware and registry["load_skill"].kind == "read"

    tools = agent_tools.openai_tools()
    assert {t["function"]["name"] for t in tools} == set(registry)
    rendered = json.dumps(tools, ensure_ascii=False)

    def _noise(node) -> bool:  # a schema "title" annotation (a str), not a field named title
        if isinstance(node, dict):
            return isinstance(node.get("title"), str) or any(_noise(v) for v in node.values())
        if isinstance(node, list):
            return any(_noise(v) for v in node)
        return False

    # Compact: no schema noise and no Decimal regex in the money fields.
    assert not _noise([t["function"]["parameters"] for t in tools])
    assert "(?!^[-+.]*$)" not in rendered


def test_every_skill_names_only_real_tools_and_spanish_copy():
    assert set(agent_skills.SKILLS) == {
        "collections_review", "policy_intake", "partial_journey", "file_check",
        "portfolio_analysis", "portal_quotes",
    }
    for spec in agent_skills.SKILLS.values():
        assert spec.tools and set(spec.tools) <= set(agent_tools.TOOL_REGISTRY), spec.key
        assert spec.title and spec.description and spec.instructions and spec.triggers
    with pytest.raises(agent_skills.UnknownSkill):
        agent_skills.normalize_skill_keys(["collections_review", "nope"])
    assert agent_skills.normalize_skill_keys(
        [" file_check ", "file_check", "policy_intake"]
    ) == ["file_check", "policy_intake"]


# --- 2. Skills endpoints ---------------------------------------------------------------


def test_skill_catalogue_endpoint(client, headers_a):
    response = client.get(f"{API}/ai/skills", headers=headers_a)
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["key"] for item in items] == list(agent_skills.SKILLS)
    review = items[0]
    assert review["title"] == "Revisar cobranza"
    assert "get_collection_overview" in review["tools"]
    assert "cobranza" in review["triggers"]
    assert client.get(f"{API}/ai/skills").status_code == 401


def test_thread_skills_set_read_and_scope(client, db, world, headers_a, headers_b, headers_a_exec):
    thread_id = client.post(
        f"{API}/ai/threads", json={"scope": "general"}, headers=headers_a
    ).json()["id"]

    response = client.post(
        f"{API}/ai/threads/{thread_id}/skills",
        json={"skills": ["collections_review", "file_check", "collections_review"]},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    assert [s["key"] for s in response.json()["active_skills"]] == [
        "collections_review", "file_check",
    ]
    read = client.get(f"{API}/ai/threads/{thread_id}/skills", headers=headers_a).json()
    assert [s["key"] for s in read["active_skills"]] == ["collections_review", "file_check"]

    bad = client.post(
        f"{API}/ai/threads/{thread_id}/skills", json={"skills": ["hackear"]}, headers=headers_a
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "unknown_skill"
    db.expire_all()
    assert db.get(AgentThread, thread_id).active_skills == ["collections_review", "file_check"]

    # Another tenant: 404. A colleague in the same broker: 403 (thread privacy).
    assert client.post(
        f"{API}/ai/threads/{thread_id}/skills", json={"skills": []}, headers=headers_b
    ).status_code == 404
    assert client.get(
        f"{API}/ai/threads/{thread_id}/skills", headers=headers_a_exec
    ).status_code == 403

    cleared = client.post(
        f"{API}/ai/threads/{thread_id}/skills", json={"skills": []}, headers=headers_a
    )
    assert cleared.json()["active_skills"] == []


def test_active_skills_reach_the_system_prompt_within_budget(
    client, db, world, headers_a, monkeypatch, ai_key
):
    thread_id = client.post(
        f"{API}/ai/threads", json={"scope": "general"}, headers=headers_a
    ).json()["id"]

    fake = _FakeToolChatModel([_Answer(content="Hola.")])
    _use(monkeypatch, fake)
    assert _turn(client, headers_a, "hola", thread_id=thread_id).status_code == 201
    base = fake.seen[0][0]["content"]
    assert "HABILIDADES" in base and "load_skill" in base
    assert "collections_review" in base, "the catalogue lists every skill"
    assert "PLAYBOOK · REVISAR COBRANZA" not in base, "playbooks load on demand"
    assert "NUNCA afirmes que una cuota está pagada sin evidencia" in base
    assert "PREGUNTA A LA COMPAÑÍA" in base

    client.post(
        f"{API}/ai/threads/{thread_id}/skills",
        json={"skills": ["collections_review"]},
        headers=headers_a,
    )
    fake = _FakeToolChatModel([_Answer(content="Hola.")])
    _use(monkeypatch, fake)
    assert _turn(client, headers_a, "y ahora?", thread_id=thread_id).status_code == 201
    assert "PLAYBOOK · REVISAR COBRANZA" in fake.seen[0][0]["content"]

    # Budgeted: a tiny budget cuts the playbook with the loop's marker.
    monkeypatch.setattr(agent_skills, "MAX_ACTIVE_SKILLS_CHARS", 300)
    fake = _FakeToolChatModel([_Answer(content="Hola.")])
    _use(monkeypatch, fake)
    assert _turn(client, headers_a, "otra vez", thread_id=thread_id).status_code == 201
    system = fake.seen[0][0]["content"]
    assert ai_service._TRUNCATION_MARKER in system
    assert "7. Cierra con un resumen" not in system


def test_load_skill_loop_activates_the_skill_on_the_thread(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today
):
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call("load_skill", {"key": "collections_review"}, "c1")]),
        _Answer(tool_calls=[_call("get_collection_overview", {}, "c2")]),
        _Answer(content="Tienes dos cuotas vencidas."),
    ])
    _use(monkeypatch, fake)

    response = _turn(client, headers_a, "revisa mi cobranza")

    assert response.status_code == 201, response.text
    data = response.json()
    assert [m["role"] for m in data["messages"]] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]
    loaded = json.loads(data["messages"][2]["content"])
    assert loaded["key"] == "collections_review"
    assert loaded["instructions"].startswith("PLAYBOOK · REVISAR COBRANZA")
    assert loaded["active_skills"] == ["collections_review"]
    overview = json.loads(data["messages"][4]["content"])
    assert overview["summary"]["overdue"]["count"] == 2  # cuotas 01-05 and 02-05
    assert overview["top_overdue"][0]["policy_number"] == "POL-A-77"

    thread_id = data["thread"]["id"]
    db.expire_all()
    assert db.get(AgentThread, thread_id).active_skills == ["collections_review"]

    # The next turn carries the playbook in the system prompt; loading the
    # same skill twice does not duplicate it.
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call("load_skill", {"key": "collections_review"})]),
        _Answer(content="Ya está cargada."),
    ])
    _use(monkeypatch, fake)
    assert _turn(client, headers_a, "sigue", thread_id=thread_id).status_code == 201
    assert "PLAYBOOK · REVISAR COBRANZA" in fake.seen[0][0]["content"]
    db.expire_all()
    assert db.get(AgentThread, thread_id).active_skills == ["collections_review"]


def test_load_skill_unknown_key_folds_and_activates_nothing(
    client, db, world, headers_a, monkeypatch, ai_key
):
    data, folded, _ = _one_call(client, headers_a, monkeypatch, "load_skill", {"key": "magia"})
    assert folded["error"] == "invalid_arguments"
    assert "Habilidad desconocida" in folded["detail"]
    db.expire_all()
    assert db.get(AgentThread, data["thread"]["id"]).active_skills is None


# --- 3. READ tools: broker-scoped, foreign 404 folds, grants fold ------------------------


def test_list_policies_and_get_policy_are_broker_scoped(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today
):
    _, folded, _ = _one_call(client, headers_a, monkeypatch, "list_policies", {"limit": 50})
    numbers = [row["policy_number"] for row in folded["policies"]]
    assert numbers == ["POL-A-77"], "never another tenant's policy; limit clamps, not 422s"
    assert folded["total"] == 1
    row = folded["policies"][0]
    assert row["collection"]["state"] == "overdue"
    assert row["insurer_name"] == "HDI Seguros S.A."

    _, mine, _ = _one_call(
        client, headers_a, monkeypatch, "get_policy", {"policy_id": cobranza["policy"].id}
    )
    assert mine["policy_number"] == "POL-A-77"
    assert "payload" not in mine and "broker_id" not in mine
    assert set(mine["missing_reasons"]) == set(mine["missing"])

    _, foreign, _ = _one_call(
        client, headers_a, monkeypatch, "get_policy", {"policy_id": cobranza["b_policy"].id}
    )
    assert foreign["error"] == "not_found"


def test_collection_reads_are_scoped_and_gated(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today, headers_a_inspector
):
    _, rows, _ = _one_call(
        client, headers_a, monkeypatch, "list_installments", {"bucket": "overdue"}
    )
    assert {r["policy_number"] for r in rows["installments"]} == {"POL-A-77"}
    assert sorted(r["number"] for r in rows["installments"]) == [1, 2]
    assert rows["installments"][0]["level"] in ("record", "assisted", "native")

    second = cobranza["inst"][1]
    _, mode, _ = _one_call(
        client, headers_a, monkeypatch, "get_collection_mode", {"installment_id": second.id}
    )
    assert mode["level"] == "assisted" and mode["reason"]
    assert "POL-A-77" in mode["installment"]

    _, foreign, _ = _one_call(
        client, headers_a, monkeypatch, "get_collection_mode",
        {"installment_id": cobranza["b_inst"][1].id},
    )
    assert foreign["error"] == "not_found"

    for name in ("list_status_inquiries", "list_pending_evidence"):
        _, empty, _ = _one_call(client, headers_a, monkeypatch, name, {})
        assert empty["total"] == 0, name

    # The inspector holds no Collections grant: the read folds, never 403s.
    _, denied, _ = _one_call(
        client, headers_a_inspector, monkeypatch, "list_installments", {}
    )
    assert denied["error"] == "permission_denied"
    assert denied["module"] == "Collections"


def test_check_policy_files_lists_the_falta(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today
):
    _, folded, _ = _one_call(
        client, headers_a, monkeypatch, "check_policy_files",
        {"policy_id": cobranza["policy"].id},
    )
    keys = {item["key"] for item in folded["missing"]}
    assert "source_document" in keys and "payer_contact" in keys
    assert "commission" in keys
    assert folded["has_plan"] is True and folded["installments_count"] == 3
    assert all(item["reason"] for item in folded["missing"])
    assert {f["feature"] for f in folded["features"]} >= {"collections", "payer_notices"}

    _, foreign, _ = _one_call(
        client, headers_a, monkeypatch, "check_policy_files",
        {"policy_id": cobranza["b_policy"].id},
    )
    assert foreign["error"] == "not_found"


def test_check_case_files_reports_stages_and_files(
    client, db, world, headers_a, monkeypatch, ai_key
):
    case = make_case_file(db, world.a, reference="EXP-A-9")
    foreign = make_case_file(db, world.b, reference="EXP-B-9")
    db.commit()

    _, folded, _ = _one_call(
        client, headers_a, monkeypatch, "check_case_files", {"case_file_id": case.id}
    )
    assert folded["reference"] == "EXP-A-9"
    assert folded["stage"] == "intake"
    assert {"stage": "intake", "state": "current"}.items() <= next(
        row for row in folded["stages"] if row["stage"] == "intake"
    ).items()
    assert "quotes_received" in folded["skip_targets"]
    assert folded["has_policy"] is False
    assert "recommended_files" in folded and "missing_required" in folded

    _, other, _ = _one_call(
        client, headers_a, monkeypatch, "check_case_files", {"case_file_id": foreign.id}
    )
    assert other["error"] == "not_found"
    assert "EXP-B-9" not in json.dumps(other)


def test_catalog_notifications_and_portal_reads(
    client, db, world, headers_a, monkeypatch, ai_key, catalog, headers_a_tech
):
    _, cat, _ = _one_call(client, headers_a, monkeypatch, "get_catalog", {"line_code": "veh"})
    assert cat["total"] == 1
    assert any(s["code"] == "VEH-01" for s in cat["lines"][0]["subtypes"])

    from app.services.notifications import notify

    notify(
        db, broker_id=world.a.broker_id, user_ids=[world.a.admin.id], kind="plan_missing",
        tray="todo", origin_type="policy", origin_id=1, params={"policy_number": "X"},
    )
    notify(
        db, broker_id=world.b.broker_id, user_ids=[world.b.admin.id], kind="plan_missing",
        tray="todo", origin_type="policy", origin_id=2,
    )
    db.commit()
    _, notes, _ = _one_call(client, headers_a, monkeypatch, "list_notifications", {})
    assert notes["total"] == 1
    assert notes["notifications"][0]["origin_id"] == 1

    from tests.test_v12_rpa import make_run

    run_b = make_run(db, world.b)
    run_a = make_run(db, world.a, user=world.a.admin)
    _, runs, _ = _one_call(client, headers_a, monkeypatch, "list_portal_searches", {})
    assert [r["id"] for r in runs["runs"]] == [run_a.id]
    _, one, _ = _one_call(client, headers_a, monkeypatch, "get_portal_search", {"run_id": run_a.id})
    assert one["id"] == run_a.id and one["status"] == "queued"
    _, other, _ = _one_call(
        client, headers_a, monkeypatch, "get_portal_search", {"run_id": run_b.id}
    )
    assert other["error"] == "not_found"

    # The technician holds no Integrations grant.
    _, denied, _ = _one_call(client, headers_a_tech, monkeypatch, "list_portal_searches", {})
    assert denied["error"] == "permission_denied" and denied["module"] == "Integrations"


# --- 4. WRITE tools: propose, then execute only on confirm ----------------------------------


def test_register_payment_proposes_then_commits_through_the_ledger(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today
):
    second = cobranza["inst"][1]
    pending = _propose(
        client, headers_a, monkeypatch, "register_payment",
        {"installment_id": second.id, "amount": "46.33", "paid_on": "2026-02-10"},
    )
    assert pending["module"] == "Collections" and pending["action"] == "Edit"
    assert "pago declarado" in pending["summary"]
    assert "POL-A-77" in pending["summary"]
    assert _count(db, Payment) == 0, "a proposal writes NOTHING"

    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["result"]["entity_type"] == "collection_plan"
    assert body["result"]["evidence"] == "declared"
    payments = db.scalars(select(Payment)).all()
    assert len(payments) == 1 and payments[0].installment_id == second.id
    assert payments[0].amount == Decimal("46.3300")
    assert _count(db, Activity, Activity.action == "payment.registered") == 1
    assert _count(db, Activity, Activity.action == "agent.action_confirmed") == 1


def test_write_proposals_fold_foreign_ids_and_missing_grants(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today, headers_a_tech
):
    _, foreign, _ = _one_call(
        client, headers_a, monkeypatch, "register_payment",
        {"installment_id": cobranza["b_inst"][0].id, "amount": 1, "paid_on": "2026-02-01"},
    )
    assert foreign["error"] == "not_found"
    # The technician only VIEWS Collections: proposing a payment folds.
    _, denied, _ = _one_call(
        client, headers_a_tech, monkeypatch, "record_payment_promise",
        {"installment_id": cobranza["inst"][1].id, "promise_until": "2026-02-20"},
    )
    assert denied["error"] == "permission_denied"
    assert _count(db, AgentAction) == 0


def test_confirm_by_a_role_without_the_grant_is_403_and_stays_proposed(
    client, db, world, cobranza, headers_a_tech, today
):
    tech = world.a.technician
    _, action = _seed_action(
        db, tenant=world.a, user=tech, tool="register_payment",
        arguments={
            "installment_id": cobranza["inst"][1].id, "amount": "10", "paid_on": "2026-02-10",
        },
        module="Collections", action="Edit",
    )
    response = _confirm(client, headers_a_tech, action.id)
    assert response.status_code == 403
    db.expire_all()
    assert db.get(AgentAction, action.id).status == AgentActionStatus.PROPOSED
    assert _count(db, Payment) == 0


def test_promise_and_start_collection(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today
):
    second = cobranza["inst"][1]
    promise = _propose(
        client, headers_a, monkeypatch, "record_payment_promise",
        {"installment_id": second.id, "promise_until": "2026-02-20", "note": "Llamó el pagador"},
    )
    db.expire_all()
    assert db.get(CollectionInstallment, second.id).promise_until is None
    assert _confirm(client, headers_a, promise["id"]).status_code == 200
    db.expire_all()
    assert db.get(CollectionInstallment, second.id).promise_until == date(2026, 2, 20)

    start = _propose(
        client, headers_a, monkeypatch, "start_collection",
        {"policy_id": cobranza["policy"].id, "level_preference": "assisted"},
    )
    assert "POL-A-77" in start["summary"]
    db.expire_all()
    assert db.get(CollectionPlan, cobranza["plan"].id).notices_enabled is False
    confirmed = _confirm(client, headers_a, start["id"])
    assert confirmed.status_code == 200, confirmed.text
    db.expire_all()
    plan = db.get(CollectionPlan, cobranza["plan"].id)
    assert plan.notices_enabled is True
    assert plan.level_preference.value == "assisted"

    # A policy without a plan: refused at proposal time, with the reason.
    bare = make_policy_with_plan(db, world.a, world.insurer, policy_number="POL-A-88")[0]
    db.query(CollectionInstallment).filter(
        CollectionInstallment.collection_plan_id.in_(
            select(CollectionPlan.id).where(CollectionPlan.policy_id == bare.id)
        )
    ).delete(synchronize_session=False)
    db.query(CollectionPlan).filter(CollectionPlan.policy_id == bare.id).delete()
    db.commit()
    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "start_collection", {"policy_id": bare.id}
    )
    assert refused["error"] == "invalid_arguments"
    assert "no tiene plan de pago" in refused["detail"]


def test_status_inquiry_resolves_open_cuotas_and_asks_the_insurer(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today, fake_transport
):
    policy = cobranza["policy"]
    # Without a collections e-mail for the insurer the proposal is refused.
    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "send_status_inquiry", {"policy_id": policy.id}
    )
    assert refused["error"] == "invalid_arguments"
    assert "No hay un correo de cobranza" in refused["detail"]

    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.test")
    db.commit()
    pending = _propose(
        client, headers_a, monkeypatch, "send_status_inquiry", {"policy_id": policy.id}
    )
    action = db.get(AgentAction, pending["id"])
    assert action.arguments["installment_ids"] == [i.id for i in cobranza["inst"]]
    assert "las cuotas 1, 2, 3" in pending["summary"]
    assert _count(db, StatusInquiry) == 0

    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["entity_type"] == "status_inquiry"
    inquiry = db.get(StatusInquiry, result["entity_id"])
    assert inquiry.broker_id == world.a.broker_id
    assert inquiry.to_address == "cobranza@hdi.test"
    assert _count(db, Activity, Activity.action == "status_inquiry.created") == 1


def test_payment_reminder_needs_a_payer_email_and_sends_on_confirm(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, today, fake_transport
):
    second = cobranza["inst"][1]
    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "send_payment_reminder",
        {"installment_id": second.id, "kind": "payer_overdue"},
    )
    assert refused["error"] == "invalid_arguments"
    assert "correo de pagador" in refused["detail"]

    make_party(db, cobranza["policy"], role=PartyRole.PAYER, email="pagador@alfa.test")
    db.commit()
    pending = _propose(
        client, headers_a, monkeypatch, "send_payment_reminder",
        {"installment_id": second.id, "kind": "payer_overdue"},
    )
    assert "pagador@alfa.test" in pending["summary"]
    assert _count(db, CollectionNotice) == 0
    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    notices = db.scalars(select(CollectionNotice)).all()
    assert len(notices) == 1 and notices[0].to_address == "pagador@alfa.test"


def test_share_link_returns_public_and_whatsapp_urls(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza
):
    pending = _propose(
        client, headers_a, monkeypatch, "create_share_link",
        {"entity_type": "policy", "entity_id": cobranza["policy"].id, "audience": "insured"},
    )
    assert pending["module"] == "Policies" and pending["action"] == "View"
    assert _count(db, ShareLink) == 0
    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert "/s/" in result["url"]
    assert result["whatsapp_url"].startswith("https://wa.me/")
    assert _count(db, ShareLink) == 1

    # A case spans several insurers: an insurer link to it is refused up front.
    case = make_case_file(db, world.a)
    db.commit()
    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "create_share_link",
        {"entity_type": "case_file", "entity_id": case.id, "audience": "insurer"},
    )
    assert refused["error"] == "invalid_arguments"
    _, foreign, _ = _one_call(
        client, headers_a, monkeypatch, "create_share_link",
        {"entity_type": "policy", "entity_id": cobranza["b_policy"].id, "audience": "insured"},
    )
    assert foreign["error"] == "not_found"


def test_payment_link_refuses_non_native_and_issues_native(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza, fake_provider
):
    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "create_payment_link",
        {"installment_ids": [cobranza["inst"][1].id]},
    )
    assert refused["error"] == "invalid_arguments"
    assert "no se cobra con link de pago" in refused["detail"]

    from tests.test_v12_payments import native_policy

    _policy, plan, _acct = native_policy(db, world.a, world.insurer)
    first = sorted(plan.installments, key=lambda i: i.number)[0]
    pending = _propose(
        client, headers_a, monkeypatch, "create_payment_link", {"installment_ids": [first.id]}
    )
    assert _count(db, PaymentIntent) == 0
    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["checkout_url"].startswith("https://pay.fake-provider.test/checkout/")
    assert _count(db, PaymentIntent) == 1


def test_create_policy_manual_proposes_then_registers(
    client, db, world, headers_a, monkeypatch, ai_key, catalog
):
    from app.models.catalog import InsuranceSubtype

    subtype_id = db.scalar(select(InsuranceSubtype.id).where(InsuranceSubtype.code == "VEH-01"))
    before = _count(db, Policy)
    args = {
        "insured_rut": "12.345.678-5",
        "insured_name": "Transportes Ñuble SpA",
        "insurer_id": world.insurer.id,
        "insurance_subtype_id": subtype_id,
        "start_date": "2026-03-01",
        "end_date": "2027-03-01",
        "total_premium_uf": 25,
    }
    pending = _propose(client, headers_a, monkeypatch, "create_policy_manual", args)
    assert "sin n°" in pending["summary"] and "Transportes Ñuble SpA" in pending["summary"]
    assert _count(db, Policy) == before

    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    policy = db.get(Policy, result["entity_id"])
    assert policy.broker_id == world.a.broker_id
    assert policy.origin.value == "manual"
    assert policy.total_premium_uf == Decimal("25.0000")
    assert result["url"] == f"/policies/{policy.id}"

    # A foreign client folds not_found; a bad RUT is invalid arguments.
    _, foreign, _ = _one_call(
        client, headers_a, monkeypatch, "create_policy_manual",
        {**args, "client_id": world.b.client.id},
    )
    assert foreign["error"] == "not_found"
    _, bad, _ = _one_call(
        client, headers_a, monkeypatch, "create_policy_manual",
        {**args, "insured_rut": "12.345.678-9"},
    )
    assert bad["error"] == "invalid_arguments"


def test_update_policy_fields_only_completes_empty_fields(
    client, db, world, headers_a, monkeypatch, ai_key, cobranza
):
    policy = cobranza["policy"]
    pending = _propose(
        client, headers_a, monkeypatch, "update_policy_fields",
        {"policy_id": policy.id, "commission_pct": 12.5},
    )
    assert "% de comisión = 12.5" in pending["summary"]
    db.expire_all()
    assert db.get(Policy, policy.id).commission_pct is None
    assert _confirm(client, headers_a, pending["id"]).status_code == 200
    db.expire_all()
    assert db.get(Policy, policy.id).commission_pct == Decimal("12.50")
    assert _count(db, Activity, Activity.action == "policy.completed") == 1

    # Changing a value already on file is a correction: never via the agent.
    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "update_policy_fields",
        {"policy_id": policy.id, "policy_number": "OTRO-1"},
    )
    assert refused["error"] == "invalid_arguments"
    assert "Corregir dato" in refused["detail"]


def test_skip_and_activate_stage_follow_the_partial_journey(
    client, db, world, headers_a, monkeypatch, ai_key
):
    case = make_case_file(db, world.a, reference="EXP-A-SKIP")
    db.commit()

    _, refused, _ = _one_call(
        client, headers_a, monkeypatch, "skip_case_to_stage",
        {"case_file_id": case.id, "target_stage": "lead"},
    )
    assert refused["error"] == "invalid_arguments", "lead is not a skip target"

    pending = _propose(
        client, headers_a, monkeypatch, "skip_case_to_stage",
        {"case_file_id": case.id, "target_stage": "quotes_received"},
    )
    assert "EXP-A-SKIP" in pending["summary"]
    db.expire_all()
    assert db.get(CaseFile, case.id).stage == CaseStage.INTAKE
    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(CaseFile, case.id).stage == CaseStage.QUOTES_RECEIVED

    _, check, _ = _one_call(
        client, headers_a, monkeypatch, "check_case_files", {"case_file_id": case.id}
    )
    assert "technical_basis" in check["skipped_stages"]

    pending = _propose(
        client, headers_a, monkeypatch, "activate_case_stage",
        {"case_file_id": case.id, "stage": "technical_basis"},
    )
    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(CaseFile, case.id).stage == CaseStage.TECHNICAL_BASIS

    _, not_skipped, _ = _one_call(
        client, headers_a, monkeypatch, "activate_case_stage",
        {"case_file_id": case.id, "stage": "comparison"},
    )
    assert not_skipped["error"] == "invalid_arguments"


def test_run_portal_search_queues_only_on_confirm(
    client, db, world, headers_a, monkeypatch, ai_key, catalog
):
    from app.models.catalog import InsuranceSubtype
    from tests.test_v12_rpa import VALID_INPUT, rpa_on

    subtype_id = db.scalar(select(InsuranceSubtype.id).where(InsuranceSubtype.code == "VEH-01"))
    args = {"portal": "hdi", "insurance_subtype_id": subtype_id, "input": VALID_INPUT}

    # RPA switched off (the hermetic default): refused with the maintenance reason.
    _, off, _ = _one_call(client, headers_a, monkeypatch, "run_portal_search", args)
    assert off["error"] == "invalid_arguments"

    rpa_on(monkeypatch)
    _, bad, _ = _one_call(
        client, headers_a, monkeypatch, "run_portal_search",
        {**args, "input": {**VALID_INPUT, "year": "antiguo"}},
    )
    assert bad["error"] == "invalid_arguments"

    pending = _propose(client, headers_a, monkeypatch, "run_portal_search", args)
    assert "BCDF" in pending["summary"].upper()
    assert _count(db, PortalSearchRun) == 0
    response = _confirm(client, headers_a, pending["id"])
    assert response.status_code == 200, response.text
    run = db.get(PortalSearchRun, response.json()["result"]["entity_id"])
    assert run.broker_id == world.a.broker_id and run.status.value == "queued"


def test_action_cards_are_disabled_for_a_caller_without_the_grant(
    client, db, world, headers_a_tech, cobranza
):
    tech = world.a.technician
    thread, _action = _seed_action(
        db, tenant=world.a, user=tech, tool="send_status_inquiry",
        arguments={"policy_id": cobranza["policy"].id, "installment_ids": None},
        module="Collections", action="Edit",
    )
    listing = client.get(
        f"{API}/ai/agent/actions", params={"thread_id": thread.id}, headers=headers_a_tech
    )
    assert listing.status_code == 200
    assert listing.json()["items"][0]["allowed"] is False
