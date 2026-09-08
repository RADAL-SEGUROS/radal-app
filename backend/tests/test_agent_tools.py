"""The agentic turn (v4 agent spec §4–§7): registry, loop, pending actions.

Hermetic throughout — the provider is a scripted double monkeypatched over
``ai_service._chat_model`` (the same seam ``test_ai_streaming.py`` uses); no
test ever reaches DeepInfra. What this file encodes:

- READ tools execute inline under the CALLER's own grants; a missing grant or
  a foreign id FOLDS BACK to the model (``permission_denied`` / ``not_found``)
  instead of failing the turn.
- WRITE tools never execute in the loop: they persist an ``agent_action`` row
  in ``proposed`` and only the confirm endpoint — under the confirming user's
  real RBAC gate — runs the executor (CLAUDE.md rule 6, mechanically).
- An RBAC refusal at confirm is a clean 403 (never a 500) and the row stays
  ``proposed``; cross-tenant refs and action ids are 404, indistinguishable
  from absent.
- A provider failure mid-loop persists NOTHING; an unset key is a clean 503.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.roles_config import ACTIONS, MODULES
from app.models.account_group import AccountGroup
from app.models.activity import Activity
from app.models.ai import (
    AgentAction,
    AgentActionStatus,
    AgentMessage,
    AgentRole,
    AgentScope,
    AgentThread,
)
from app.models.case_file import CaseFile
from app.services import agent_tools
from app.services import ai as ai_service
from tests.conftest import API, make_case_file


# --- Provider double ---------------------------------------------------------


class _Answer:
    """One scripted provider answer, reduced to what the loop reads."""

    def __init__(self, content: str = "", tool_calls: list[dict] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.additional_kwargs: dict = {}
        self.response_metadata = {"model_name": "fake/agent-1", "token_usage": {}}
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 5}


def _call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "name": name, "args": args}


class _FakeToolChatModel:
    """``bind_tools`` records the schemas and returns self; ``invoke`` pops the
    next scripted item (an exception item raises — the mid-loop outage case)."""

    def __init__(self, script: list):
        self._script = list(script)
        self.bound: list[list[dict]] = []
        self.invocations = 0
        self.seen: list[list] = []

    def bind_tools(self, tools):
        self.bound.append(tools)
        return self

    def invoke(self, messages):
        self.invocations += 1
        self.seen.append(list(messages))
        assert self._script, "the fake's script is exhausted"
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


@pytest.fixture()
def no_ai_key(monkeypatch):
    """The outage case: the feature is configured off."""
    monkeypatch.setattr(settings, "AI_API_KEY", "")


def _use(monkeypatch, fake: _FakeToolChatModel) -> None:
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)


def _turn(client, headers, content: str, *, refs=None, thread_id=None):
    body: dict = {"content": content}
    if refs is not None:
        body["context_refs"] = refs
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post(f"{API}/ai/agent/messages", json=body, headers=headers)


def _seed_action(db, *, tenant, user, tool: str, arguments: dict,
                 module: str, action: str, summary: str = "Acción de prueba"):
    """A proposed action on a thread ``user`` owns — the confirm-side seam the
    spec names for lifecycle tests (no model needed)."""
    thread = AgentThread(
        broker_id=tenant.broker_id, user_id=user.id, scope=AgentScope.GENERAL
    )
    db.add(thread)
    db.flush()
    row = AgentAction(
        broker_id=tenant.broker_id,
        thread_id=thread.id,
        tool=tool,
        arguments=arguments,
        summary=summary,
        module=module,
        action=action,
        status=AgentActionStatus.PROPOSED,
        proposed_by_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return thread, row


# --- 1. Registry sanity ------------------------------------------------------


def test_registry_gates_and_schemas_are_sane():
    """Import-time typo guard: every gate names a real module/action; every
    write tool can summarize itself; the OpenAI export is well-formed."""
    assert agent_tools.TOOL_REGISTRY, "the registry must not be empty"
    for spec in agent_tools.TOOL_REGISTRY.values():
        assert spec.module in MODULES, spec.name
        assert spec.action in ACTIONS, spec.name
        if spec.kind == "write":
            assert spec.summarize is not None, spec.name

    tools = agent_tools.openai_tools()
    assert {t["function"]["name"] for t in tools} == set(agent_tools.TOOL_REGISTRY)
    for tool in tools:
        assert tool["type"] == "function"
        params = tool["function"]["parameters"]
        assert isinstance(params, dict) and "properties" in params
        assert "title" not in params, "schema noise must be pruned"

    assert set(agent_tools.WRITE_TOOLS) == {
        "create_group", "attach_client_to_group", "create_case_file",
        "renew_case", "add_note",
    }


def test_write_execute_is_never_reachable_from_the_loop_source():
    """Rule 6, grep-level: the loop dispatch proposes writes; the only call of
    a write spec's ``execute`` lives in the confirm path."""
    import inspect

    dispatch_src = inspect.getsource(ai_service._dispatch_tool_call)
    read_branch, _, write_branch = dispatch_src.partition('if spec.kind == "read":')
    assert write_branch, "the dispatch must branch on the tool kind"
    # After the read branch, the only executor calls are validate_proposal /
    # summarize — never spec.execute.
    tail = write_branch.split("spec.validate_proposal", 1)[-1]
    assert "spec.execute(" not in tail
    confirm_src = inspect.getsource(ai_service.confirm_agent_action)
    assert "spec.execute(" in confirm_src


# --- 2. A read-tool turn -----------------------------------------------------


def test_read_tool_turn_persists_the_full_transcript(
    client, world, headers_a, db, monkeypatch, ai_key
):
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call("search_entities", {"query": "Planta"})]),
        _Answer(content="Encontré la Planta Alfa."),
    ])
    _use(monkeypatch, fake)

    response = _turn(client, headers_a, "busca la planta")

    assert response.status_code == 201, response.text
    data = response.json()
    assert [m["role"] for m in data["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert data["messages"][1]["tool_calls"], "the model's ask is audited verbatim"
    folded = json.loads(data["messages"][2]["content"])
    assert "error" not in folded, folded
    assert data["messages"][3]["content"] == "Encontré la Planta Alfa."
    assert data["pending_actions"] == []
    assert fake.bound and fake.bound[0], "the registry schemas were bound as tools"

    db.expire_all()
    rows = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.thread_id == data["thread"]["id"])
        .order_by(AgentMessage.id)
    ).all()
    assert [r.role for r in rows] == [
        AgentRole.USER, AgentRole.ASSISTANT, AgentRole.TOOL, AgentRole.ASSISTANT
    ]
    assert rows[3].tokens == 30, "usage summed across both provider calls"


def test_permission_denied_read_folds_instead_of_403(
    client, world, db, monkeypatch, ai_key, headers_a_inspector
):
    """An inspector holds no Proposals.View: the comparator fold tells the
    model so, and the turn still answers 201 — never a 403, never a 500."""
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call(
            "get_quote_comparison", {"quote_request_id": world.a.quote.id}
        )]),
        _Answer(content="No tengo acceso a las propuestas con tu perfil."),
    ])
    _use(monkeypatch, fake)

    response = _turn(client, headers_a_inspector, "compara las ofertas")

    assert response.status_code == 201, response.text
    folded = json.loads(response.json()["messages"][2]["content"])
    assert folded["error"] == "permission_denied"
    assert folded["module"] == "Proposals"
    assert folded["action"] == "View"


def test_cross_tenant_read_folds_not_found_and_leaks_nothing(
    client, world, headers_a, db, monkeypatch, ai_key
):
    foreign = make_case_file(
        db, world.b, reference="EXP-B-SECRETO", title="Expediente Secreto Beta"
    )
    db.commit()
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call("get_case_file", {"case_file_id": foreign.id})]),
        _Answer(content="No encuentro ese expediente."),
    ])
    _use(monkeypatch, fake)

    response = _turn(client, headers_a, f"abre el expediente {foreign.id}")

    assert response.status_code == 201
    data = response.json()
    folded = json.loads(data["messages"][2]["content"])
    assert folded["error"] == "not_found"
    for message in data["messages"]:
        assert "SECRETO" not in (message["content"] or "")
        assert "Secreto Beta" not in (message["content"] or "")


# --- 3. A write proposes, never executes -------------------------------------


def _propose_create_group(client, headers, monkeypatch, name="Grupo Viña"):
    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call("create_group", {"name": name})]),
        _Answer(content="Propuse crear el grupo; confírmalo en la tarjeta."),
    ])
    _use(monkeypatch, fake)
    return _turn(client, headers, f"crea un grupo llamado {name}")


def test_write_tool_proposes_and_writes_nothing(
    client, world, headers_a, db, monkeypatch, ai_key
):
    response = _propose_create_group(client, headers_a, monkeypatch)

    assert response.status_code == 201, response.text
    data = response.json()
    assert len(data["pending_actions"]) == 1
    pending = data["pending_actions"][0]
    assert pending["tool"] == "create_group"
    assert pending["status"] == "proposed"
    assert pending["module"] == "Groups" and pending["action"] == "Create"
    assert pending["allowed"] is True, "the admin holds Groups.Create"
    assert "Grupo Viña" in (pending["summary"] or "")

    folded = json.loads(data["messages"][2]["content"])
    assert folded["status"] == "pending_confirmation"
    assert folded["action_id"] == pending["id"]

    db.expire_all()
    assert db.scalar(select(func.count(AccountGroup.id))) == 0, "NOTHING was written"
    row = db.get(AgentAction, pending["id"])
    assert row.status == AgentActionStatus.PROPOSED
    assert row.message_id == data["messages"][1]["id"], "linked to the proposing message"


# --- 4. Confirm: the COMMIT of rule 6 ----------------------------------------


def test_confirm_executes_the_real_transaction_with_the_trail(
    client, world, headers_a, db, monkeypatch, ai_key
):
    proposed = _propose_create_group(client, headers_a, monkeypatch).json()
    action_id = proposed["pending_actions"][0]["id"]

    response = client.post(
        f"{API}/ai/agent/actions/{action_id}/confirm", headers=headers_a
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["result"]["entity_type"] == "account_group"

    db.expire_all()
    groups = db.scalars(select(AccountGroup)).all()
    assert len(groups) == 1 and groups[0].name == "Grupo Viña"
    assert body["result"]["entity_id"] == groups[0].id

    provenance = db.scalars(
        select(Activity).where(Activity.action == "agent.action_confirmed")
    ).all()
    assert len(provenance) == 1
    assert provenance[0].meta["tool"] == "create_group"
    assert provenance[0].meta["action_id"] == action_id

    note = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.thread_id == proposed["thread"]["id"])
        .order_by(AgentMessage.id.desc())
    ).first()
    assert note.role == AgentRole.TOOL
    assert json.loads(note.content)["status"] == "confirmed"


def test_confirm_without_the_grant_is_a_clean_403_and_stays_proposed(
    client, world, db, headers_a_inspector
):
    """The refusal path: 403 with the gate named — not a 500 — and the row
    stays ``proposed`` so someone with the grant may still confirm it."""
    inspector = world.a.inspector  # created by the headers fixture
    thread, action = _seed_action(
        db, tenant=world.a, user=inspector,
        tool="create_case_file",
        arguments={
            "client_id": world.a.client.id, "kind": "account",
            "title": "Cuenta Propuesta", "period_start": "2026-01-01",
            "period_end": "2027-01-01",
        },
        module="CaseFiles", action="Create",
    )

    response = client.post(
        f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a_inspector
    )

    assert response.status_code == 403, response.text
    assert "Not permitted to Create on CaseFiles" in response.json()["detail"]

    db.expire_all()
    assert db.get(AgentAction, action.id).status == AgentActionStatus.PROPOSED
    assert db.scalar(
        select(func.count(CaseFile.id)).where(CaseFile.title == "Cuenta Propuesta")
    ) == 0

    # The card knows too: rehydration computes allowed=False for this caller.
    listing = client.get(
        f"{API}/ai/agent/actions",
        params={"thread_id": thread.id},
        headers=headers_a_inspector,
    )
    assert listing.status_code == 200
    assert listing.json()["items"][0]["allowed"] is False


def test_discard_needs_no_grant_and_double_resolution_is_409(
    client, world, db, headers_a_inspector
):
    inspector = world.a.inspector
    _, action = _seed_action(
        db, tenant=world.a, user=inspector,
        tool="create_case_file",
        arguments={
            "client_id": world.a.client.id, "kind": "account",
            "title": "Cuenta Descartada", "period_start": "2026-01-01",
            "period_end": "2027-01-01",
        },
        module="CaseFiles", action="Create",
    )

    discarded = client.post(
        f"{API}/ai/agent/actions/{action.id}/discard", headers=headers_a_inspector
    )
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["status"] == "discarded"

    db.expire_all()
    assert db.scalar(
        select(func.count(Activity.id)).where(Activity.action == "agent.action_discarded")
    ) == 1

    replay = client.post(
        f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a_inspector
    )
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "action_not_pending"


def test_action_scoping_foreign_broker_404_foreign_user_403(
    client, world, db, headers_a, headers_b, headers_a_exec
):
    _, action = _seed_action(
        db, tenant=world.a, user=world.a.admin,
        tool="create_group", arguments={"name": "Grupo Escondido"},
        module="Groups", action="Create",
    )

    # Another tenant: indistinguishable from absent.
    foreign = client.post(
        f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_b
    )
    assert foreign.status_code == 404

    # Same broker, another user's thread: the thread privacy rule extends.
    other_user = client.post(
        f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a_exec
    )
    assert other_user.status_code == 403

    db.expire_all()
    assert db.get(AgentAction, action.id).status == AgentActionStatus.PROPOSED
    assert db.scalar(select(func.count(AccountGroup.id))) == 0


# --- 5. context_refs ---------------------------------------------------------


def test_unsupported_and_foreign_context_refs_answer_before_the_model(
    client, world, db, headers_a, monkeypatch, ai_key
):
    fake = _FakeToolChatModel([])  # must never be reached
    _use(monkeypatch, fake)

    unsupported = _turn(
        client, headers_a, "hola",
        refs=[{"entity_type": "asset", "entity_id": world.a.asset.id}],
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_ref"

    foreign_case = make_case_file(db, world.b)
    db.commit()
    foreign = _turn(
        client, headers_a, "hola",
        refs=[{"entity_type": "case_file", "entity_id": foreign_case.id}],
    )
    assert foreign.status_code == 404

    assert fake.invocations == 0
    db.expire_all()
    assert db.scalar(select(func.count(AgentMessage.id))) == 0, "nothing persisted"


def test_own_case_ref_reaches_the_prompt_as_a_context_block(
    client, world, db, headers_a, monkeypatch, ai_key
):
    case = make_case_file(db, world.a)
    db.commit()
    fake = _FakeToolChatModel([_Answer(content="Listo.")])
    _use(monkeypatch, fake)

    response = _turn(
        client, headers_a, "resume este expediente",
        refs=[{"entity_type": "case_file", "entity_id": case.id}],
    )

    assert response.status_code == 201, response.text
    blocks = [
        m.get("content") for m in fake.seen[0]
        if isinstance(m, dict) and m.get("role") == "system"
    ]
    assert any("CONTEXTO · expediente" in (b or "") for b in blocks)
    # The user message keeps the refs as provenance.
    user_message = response.json()["messages"][0]
    assert user_message["context_refs"] == [
        {"entity_type": "case_file", "entity_id": case.id}
    ]


# --- 6. Outages: never a 500, nothing persisted ------------------------------


def test_unset_key_is_a_clean_503_before_any_model_call(
    client, world, headers_a, db, no_ai_key
):
    response = _turn(client, headers_a, "hola")
    assert response.status_code == 503
    assert response.headers["X-Radal-AI-Error"] == "ai_not_configured"
    db.expire_all()
    assert db.scalar(select(func.count(AgentMessage.id))) == 0


def test_provider_failure_mid_loop_persists_nothing(
    client, world, headers_a, db, monkeypatch, ai_key
):
    thread_id = client.post(
        f"{API}/ai/threads", json={"scope": "general"}, headers=headers_a
    ).json()["id"]

    fake = _FakeToolChatModel([
        _Answer(tool_calls=[_call("search_entities", {"query": "Planta"})]),
        ConnectionError("boom"),
    ])
    _use(monkeypatch, fake)

    response = _turn(client, headers_a, "busca la planta", thread_id=thread_id)

    assert response.status_code == 502
    assert response.headers["X-Radal-AI-Error"] == "ai_provider"

    db.expire_all()
    assert db.scalar(
        select(func.count(AgentMessage.id)).where(AgentMessage.thread_id == thread_id)
    ) == 0, "a failed turn persists no message"
    assert db.scalar(select(func.count(AgentAction.id))) == 0


# --- 7. Budgets --------------------------------------------------------------


def test_iteration_cap_always_ends_in_prose(
    client, world, headers_a, db, monkeypatch, ai_key
):
    script = [
        _Answer(tool_calls=[_call("search_entities", {"query": "Planta"}, f"c{i}")])
        for i in range(ai_service.MAX_TOOL_ITERATIONS)
    ]
    script.append(_Answer(content="Con lo que tengo: la Planta Alfa."))
    fake = _FakeToolChatModel(script)
    _use(monkeypatch, fake)

    response = _turn(client, headers_a, "busca sin parar")

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["messages"][-1]["content"] == "Con lo que tengo: la Planta Alfa."
    assert fake.invocations == ai_service.MAX_TOOL_ITERATIONS + 1
    # The final, nudged call was made WITHOUT tools bound.
    assert len(fake.bound) == ai_service.MAX_TOOL_ITERATIONS
    nudged = [
        m for m in fake.seen[-1]
        if isinstance(m, dict) and m.get("role") == "system"
        and "no hay más herramientas" in (m.get("content") or "")
    ]
    assert nudged, "the budget-exhaustion nudge was appended"
