"""v12 copilot pass: feature flags, agent sessions and the copilot home.

Hermetic: the provider is a scripted double (``_FakeToolChatModel`` for the
agent loop, ``_run_completion`` / ``_chat_model`` patched for the plain chat
paths); no test reaches DeepInfra, a portal or a browser. What this encodes:

1. ``PORTAL_SEARCH_ENABLED`` (off by default) — ``GET /meta/features``; runs
   and retries 409 with the Spanish reason; portals ``coming_soon``; ``rpa.run``
   skips; the portal tools are not offered, a stray call folds "pronto"; the
   ``portal_quotes`` skill is neither listed nor loadable; an old proposal is
   not confirmable.
2. Sessions — list (no empty threads, fallback title, count, preview, order,
   archived, q, paging, own-only), detail / PATCH / DELETE (404 for anyone
   else's), auto-title on every message path.
3. ``GET /ai/copilot/home`` — scope mine/all, indicators that match the owning
   summaries, ranked suggestions (4..6), platform 403.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.ai import AgentAction, AgentActionStatus, AgentMessage, AgentRole, AgentScope, AgentThread
from app.models.enums import NotificationStatus, NotificationTray, PortalRunStatus, UserType
from app.models.notification import Notification
from app.models.rpa import PortalSearchRun
from app.models.user import User
from app.services import agent_sessions, agent_skills, agent_tools
from app.services import ai as ai_service
from app.services.features import PORTAL_SEARCH_DISABLED_REASON
from tests.conftest import API, _password_hash, auth_headers, ensure_role_user, make_case_file
from tests.fakes_v12 import make_policy_with_plan
from tests.test_agent_tools import _Answer, _call, _FakeToolChatModel

PORTAL_TOOLS = {"run_portal_search", "list_portal_searches", "get_portal_search"}
TODAY = date(2026, 2, 12)
REASON = (
    "Pronto: la búsqueda en portales necesita el usuario y la clave de cada "
    "compañía para aplicar filtros."
)


# --- helpers ------------------------------------------------------------------------


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


def _platform_headers(client, db) -> dict[str, str]:
    user = User(
        broker_id=None,
        email="ops@radal.cl",
        hashed_password=_password_hash(),
        full_name="Radal Ops",
        user_type=UserType.PLATFORM,
        role="platform_admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    return auth_headers(client, user.email)


def _agent_turn(client, headers, monkeypatch, script, content="hola", thread_id=None):
    fake = _FakeToolChatModel(script)
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    body: dict = {"content": content}
    if thread_id is not None:
        body["thread_id"] = thread_id
    response = client.post(f"{API}/ai/agent/messages", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json(), fake


def _thread(db, tenant, user, *, title=None, archived=False, messages=(), last=None, created=None):
    thread = AgentThread(
        broker_id=tenant.broker_id,
        user_id=user.id,
        scope=AgentScope.GENERAL,
        title=title,
        is_archived=archived,
        last_message_at=last,
    )
    if created is not None:
        thread.created_at = created
    db.add(thread)
    db.flush()
    for role, content in messages:
        db.add(AgentMessage(thread_id=thread.id, role=role, content=content))
    db.commit()
    return thread


def _list(client, headers, **params):
    response = client.get(f"{API}/ai/threads", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# =============================================================================
# 1. Feature flags
# =============================================================================


def test_flag_defaults_off_and_reason_is_the_product_copy():
    assert settings.PORTAL_SEARCH_ENABLED is False
    assert PORTAL_SEARCH_DISABLED_REASON == REASON


def test_meta_features_any_authenticated_user(client, db, world, headers_a, monkeypatch):
    body = client.get(f"{API}/meta/features", headers=headers_a).json()
    assert body == {"portal_search": {"enabled": False, "reason": REASON}}

    platform = _platform_headers(client, db)
    response = client.get(f"{API}/meta/features", headers=platform)
    assert response.status_code == 200
    assert response.json()["portal_search"]["enabled"] is False

    assert client.get(f"{API}/meta/features").status_code == 401

    monkeypatch.setattr(settings, "PORTAL_SEARCH_ENABLED", True)
    body = client.get(f"{API}/meta/features", headers=headers_a).json()
    assert body == {"portal_search": {"enabled": True, "reason": None}}


def test_meta_features_accepts_the_cloudfront_header(client, world, headers_a):
    token = headers_a["Authorization"].split(" ", 1)[1]
    response = client.get(f"{API}/meta/features", headers={"X-Radal-Token": token})
    assert response.status_code == 200


def test_portal_runs_and_retries_are_409_while_disabled(client, db, world, catalog, headers_a, headers_b):
    from app.models.catalog import InsuranceSubtype

    subtype_id = db.scalar(select(InsuranceSubtype.id).where(InsuranceSubtype.code == "VEH-01"))
    payload = {"portal": "hdi", "insurance_subtype_id": subtype_id, "input": {"plate": "BCDF12"}}
    response = client.post(f"{API}/portal-searches", json=payload, headers=headers_a)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail == {"code": "feature_disabled", "feature": "portal_search", "message": REASON}
    assert db.scalar(select(PortalSearchRun.id)) is None

    failed = PortalSearchRun(
        broker_id=world.a.broker_id, portal="hdi", requested_by_user_id=world.a.admin.id,
        input={"plate": "BCDF12"}, status=PortalRunStatus.FAILED,
    )
    db.add(failed)
    db.commit()
    response = client.post(f"{API}/portal-searches/{failed.id}/retry", headers=headers_a)
    assert response.status_code == 409
    assert response.json()["detail"]["message"] == REASON
    # Another tenant's run stays a 404 — the flag never turns a miss into a 409.
    assert client.post(f"{API}/portal-searches/{failed.id}/retry", headers=headers_b).status_code == 404

    # Reads keep working, and say why nothing will happen.
    run = client.get(f"{API}/portal-searches/{failed.id}", headers=headers_a).json()
    assert run["can_retry"] is False
    retry = next(a for a in run["alternatives"] if a["kind"] == "retry")
    assert retry["available"] is False and retry["reason"] == REASON


def test_queued_run_reads_will_not_run_with_the_flag_reason(client, db, world, headers_a):
    run = PortalSearchRun(
        broker_id=world.a.broker_id, portal="hdi", requested_by_user_id=world.a.admin.id,
        input={"plate": "BCDF12"}, status=PortalRunStatus.QUEUED,
    )
    db.add(run)
    db.commit()
    body = client.get(f"{API}/portal-searches/{run.id}", headers=headers_a).json()
    assert body["will_not_run"] is True and body["will_not_run_reason"] == REASON


def test_portals_are_coming_soon_with_the_reason(client, world, headers_a, monkeypatch):
    portals = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()
    assert portals and all(p["status"] == "coming_soon" for p in portals)
    assert all(p["maintenance_reason"] == REASON and p["coming_soon"] for p in portals)
    assert all(p["status_label"] == "Pronto" for p in portals)

    monkeypatch.setattr(settings, "PORTAL_SEARCH_ENABLED", True)
    portals = client.get(f"{API}/portal-searches/portals", headers=headers_a).json()
    hdi = next(p for p in portals if p["key"] == "hdi")
    assert hdi["status"] == "maintenance" and hdi["maintenance_code"] == "rpa_disabled"


def test_rpa_job_is_a_noop_while_disabled(db, world, monkeypatch):
    from app.jobs.rpa import rpa_run

    monkeypatch.setattr(settings, "RPA_ENABLED", True)  # even with the engine on
    run = PortalSearchRun(
        broker_id=world.a.broker_id, portal="hdi", requested_by_user_id=world.a.admin.id,
        input={"plate": "BCDF12"}, status=PortalRunStatus.QUEUED,
    )
    db.add(run)
    db.commit()
    result = rpa_run(db, datetime.now(timezone.utc))
    assert result == {"skipped": True, "reason": "PORTAL_SEARCH_ENABLED is off"}
    db.expire_all()
    assert db.get(PortalSearchRun, run.id).status == PortalRunStatus.QUEUED


def test_portal_tools_are_not_offered_and_a_stray_call_folds_pronto(
    client, world, headers_a, ai_key, monkeypatch
):
    offered = {t["function"]["name"] for t in agent_tools.openai_tools()}
    assert not (offered & PORTAL_TOOLS)
    assert PORTAL_TOOLS <= set(agent_tools.TOOL_REGISTRY), "the code stays registered"

    data, fake = _agent_turn(
        client, headers_a, monkeypatch,
        [_Answer(tool_calls=[_call("run_portal_search", {"insurance_subtype_id": 1, "input": {}})]),
         _Answer(content="Eso viene pronto.")],
    )
    bound = {t["function"]["name"] for t in fake.bound[0]}
    assert not (bound & PORTAL_TOOLS)
    folded = json.loads(data["messages"][2]["content"])
    assert folded["error"] == "feature_disabled" and folded["detail"] == REASON
    assert data["pending_actions"] == []

    monkeypatch.setattr(settings, "PORTAL_SEARCH_ENABLED", True)
    offered = {t["function"]["name"] for t in agent_tools.openai_tools()}
    assert PORTAL_TOOLS <= offered


def test_portal_quotes_skill_is_hidden_and_not_loadable(
    client, db, world, headers_a, ai_key, monkeypatch
):
    keys = [s["key"] for s in client.get(f"{API}/ai/skills", headers=headers_a).json()]
    assert "portal_quotes" not in keys and "collections_review" in keys
    assert "portal_quotes" not in agent_skills.skills_index_prompt()

    data, _ = _agent_turn(
        client, headers_a, monkeypatch,
        [_Answer(tool_calls=[_call("load_skill", {"key": "portal_quotes"})]),
         _Answer(content="Pronto.")],
    )
    folded = json.loads(data["messages"][2]["content"])
    assert folded["error"] == "feature_disabled" and folded["detail"] == REASON
    thread = db.get(AgentThread, data["thread"]["id"])
    assert not (thread.active_skills or [])

    response = client.post(
        f"{API}/ai/threads/{thread.id}/skills", json={"skills": ["portal_quotes"]}, headers=headers_a
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "skill_disabled"
    assert response.json()["detail"]["reason"] == REASON

    # A thread that loaded it while the flag was on no longer carries it.
    thread.active_skills = ["portal_quotes", "file_check"]
    db.commit()
    active = client.get(f"{API}/ai/threads/{thread.id}/skills", headers=headers_a).json()
    assert [s["key"] for s in active["active_skills"]] == ["file_check"]

    monkeypatch.setattr(settings, "PORTAL_SEARCH_ENABLED", True)
    keys = [s["key"] for s in client.get(f"{API}/ai/skills", headers=headers_a).json()]
    assert "portal_quotes" in keys


def test_an_old_portal_proposal_is_not_confirmable(client, db, world, headers_a):
    thread = _thread(db, world.a, world.a.admin, messages=[(AgentRole.USER, "cotiza")])
    action = AgentAction(
        broker_id=world.a.broker_id, thread_id=thread.id, tool="run_portal_search",
        arguments={"insurance_subtype_id": 1, "input": {"plate": "BCDF12"}},
        summary="Buscar en HDI", module="Integrations", action="Submit",
        status=AgentActionStatus.PROPOSED, proposed_by_id=world.a.admin.id,
    )
    db.add(action)
    db.commit()
    listed = client.get(f"{API}/ai/agent/actions", headers=headers_a).json()["items"]
    assert listed[0]["allowed"] is False
    response = client.post(f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a)
    assert response.status_code == 409
    assert response.json()["detail"]["message"] == REASON
    db.expire_all()
    assert db.get(AgentAction, action.id).status == AgentActionStatus.PROPOSED


# =============================================================================
# 2. Sessions
# =============================================================================


def test_auto_title_rules():
    assert agent_sessions.auto_title("  hola   mundo ") == "hola mundo"
    long = "Necesito revisar la cobranza vencida de todas las pólizas de incendio del grupo Alfa"
    title = agent_sessions.auto_title(long)
    assert len(title) <= 60 and title.endswith("…")
    assert long.startswith(title[:-1].rstrip())
    assert not title[:-1].endswith(" ")
    assert " ".join(long.split()[:3]) in title
    # The cut is on a word boundary: the last word kept is a whole word.
    assert title[:-1].split()[-1] in long.split()
    exact = "x" * 60
    assert agent_sessions.auto_title(exact) == exact
    assert len(agent_sessions.auto_title("y" * 200)) == 60
    assert agent_sessions.auto_title("   ") is None


def test_plain_preview_strips_markdown():
    text = "## Resumen\n\n**3 cuotas** vencidas en [POL-1](/policies/1).\n- `UF 12,5`\n| a | b |\n|---|---|"
    preview = agent_sessions.plain_preview(text)
    assert preview == "Resumen 3 cuotas vencidas en POL-1. UF 12,5 a b"
    assert len(agent_sessions.plain_preview("palabra " * 50)) <= 120


def test_session_list_shape_order_and_scope(client, db, world, headers_a, headers_b):
    admin, other = world.a.admin, world.a.executive
    t0 = datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)
    older = _thread(
        db, world.a, admin, title="Cobranza Alfa", last=t0,
        messages=[(AgentRole.USER, "¿qué cuotas vencen?"), (AgentRole.ASSISTANT, "**Dos** cuotas.")],
    )
    newer = _thread(
        db, world.a, admin, last=t0 + timedelta(hours=1),
        created=datetime(2026, 3, 4, 2, 30, tzinfo=timezone.utc),  # Santiago: 03-03 23:30
        messages=[(AgentRole.USER, "hola"), (AgentRole.TOOL, "{}"), (AgentRole.ASSISTANT, "")],
    )
    never = _thread(db, world.a, admin, messages=[(AgentRole.USER, "sin fecha")])
    _thread(db, world.a, admin)  # no messages -> never listed
    _thread(db, world.a, admin, title="Archivada", archived=True, messages=[(AgentRole.USER, "x")])
    _thread(db, world.a, other, title="De otra persona", messages=[(AgentRole.USER, "x")])
    _thread(db, world.b, world.b.admin, title="Otro tenant", messages=[(AgentRole.USER, "x")])

    body = _list(client, headers_a)
    assert [item["id"] for item in body["items"]] == [newer.id, older.id, never.id]
    assert body["total"] == 3 and body["limit"] == 50 and body["offset"] == 0
    first, second, third = body["items"]
    assert first["title"] == "Conversación del 03-03"  # fallback, never null
    assert first["message_count"] == 1  # tool rows and empty carriers don't count
    assert first["preview"] == "hola"
    assert second["title"] == "Cobranza Alfa" and second["message_count"] == 2
    assert second["preview"] == "Dos cuotas."
    assert second["last_message_at"].startswith("2026-03-01T15:00")
    assert third["last_message_at"] is None  # nulls last

    archived = _list(client, headers_a, archived="true")
    assert [item["title"] for item in archived["items"]] == ["Archivada"]

    assert [i["id"] for i in _list(client, headers_a, q="cobranza")["items"]] == [older.id]
    assert [i["id"] for i in _list(client, headers_a, q="CUOTAS VENCEN")["items"]] == [older.id]
    assert _list(client, headers_a, q="%")["total"] == 0  # LIKE wildcards are escaped

    page = _list(client, headers_a, limit=1, offset=1)
    assert [i["id"] for i in page["items"]] == [older.id] and page["total"] == 3

    other_tenant = _list(client, headers_b)
    assert [i["title"] for i in other_tenant["items"]] == ["Otro tenant"]


def test_session_detail_patch_delete(client, db, world, headers_a, headers_a_exec, headers_b):
    thread = _thread(db, world.a, world.a.admin, messages=[(AgentRole.USER, "hola")])
    url = f"{API}/ai/threads/{thread.id}"

    one = client.get(url, headers=headers_a).json()
    assert one["id"] == thread.id and one["message_count"] == 1 and one["title"].startswith("Conversación del ")
    for headers in (headers_a_exec, headers_b):  # colleague and other tenant alike
        assert client.get(url, headers=headers).status_code == 404
        assert client.patch(url, json={"title": "x"}, headers=headers).status_code == 404
        assert client.delete(url, headers=headers).status_code == 404

    renamed = client.patch(url, json={"title": "  Renovación Alfa  "}, headers=headers_a)
    assert renamed.status_code == 200 and renamed.json()["title"] == "Renovación Alfa"
    for bad in ({"title": ""}, {"title": "   "}, {"title": "x" * 121}, {"title": None}):
        assert client.patch(url, json=bad, headers=headers_a).status_code == 422, bad

    assert client.delete(url, headers=headers_a).status_code == 204
    assert client.delete(url, headers=headers_a).status_code == 204  # idempotent
    db.expire_all()
    assert db.get(AgentThread, thread.id).is_archived is True
    assert db.scalar(select(AgentMessage.id).where(AgentMessage.thread_id == thread.id))  # soft
    assert _list(client, headers_a)["total"] == 0
    assert client.get(url, headers=headers_a).json()["is_archived"] is True  # still readable

    restored = client.patch(url, json={"is_archived": False}, headers=headers_a)
    assert restored.status_code == 200 and restored.json()["is_archived"] is False
    assert _list(client, headers_a)["total"] == 1


LONG = "Quiero saber qué pólizas de incendio del grupo Alfa vencen antes de fin de mes y cuánto suman"


def _expected_title() -> str:
    return agent_sessions.auto_title(LONG)


def test_auto_title_on_the_agent_path(client, db, world, headers_a, ai_key, monkeypatch):
    data, _ = _agent_turn(client, headers_a, monkeypatch, [_Answer(content="Revisemos.")], content=LONG)
    assert data["thread"]["title"] == _expected_title()
    assert len(data["thread"]["title"]) <= 60 and data["thread"]["message_count"] == 2
    # A second message never renames the session.
    data, _ = _agent_turn(
        client, headers_a, monkeypatch, [_Answer(content="Ok.")],
        content="otra cosa", thread_id=data["thread"]["id"],
    )
    assert data["thread"]["title"] == _expected_title()


def test_auto_title_on_the_messages_path(client, db, world, headers_a, ai_key, monkeypatch):
    monkeypatch.setattr(
        ai_service, "_run_completion",
        lambda messages, **kw: ai_service.Completion(content="Claro.", model="fake/model-1"),
    )
    thread = client.post(f"{API}/ai/threads", json={"scope": "general"}, headers=headers_a).json()
    response = client.post(
        f"{API}/ai/threads/{thread['id']}/messages", json={"content": LONG}, headers=headers_a
    )
    assert response.status_code == 201, response.text
    assert client.get(f"{API}/ai/threads/{thread['id']}", headers=headers_a).json()["title"] == _expected_title()


def test_auto_title_on_the_stream_path(client, db, world, headers_a, ai_key, monkeypatch):
    class _Chunk:
        def __init__(self, content):
            self.content = content
            self.response_metadata = {"model_name": "fake/model-1"}
            self.usage_metadata = {}

    class _Streamer:
        async def astream(self, messages):
            for piece in ("Hola ", "Alfa."):
                yield _Chunk(piece)

    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: _Streamer())
    thread = client.post(f"{API}/ai/threads", json={"scope": "general"}, headers=headers_a).json()
    response = client.post(
        f"{API}/ai/threads/{thread['id']}/stream", json={"content": LONG}, headers=headers_a
    )
    assert response.status_code == 200 and "event: done" in response.text, response.text
    read = client.get(f"{API}/ai/threads/{thread['id']}", headers=headers_a).json()
    assert read["title"] == _expected_title()
    # Regression: the streamed body runs after the request session closed; the
    # thread row must still get its activity stamp (the sidebar sorts on it).
    assert read["last_message_at"] is not None and read["message_count"] == 2


def test_an_explicit_title_is_never_overwritten(client, db, world, headers_a, ai_key, monkeypatch):
    thread = client.post(
        f"{API}/ai/threads", json={"scope": "general", "title": "Mi sesión"}, headers=headers_a
    ).json()
    _agent_turn(client, headers_a, monkeypatch, [_Answer(content="Ok.")], content=LONG, thread_id=thread["id"])
    assert client.get(f"{API}/ai/threads/{thread['id']}", headers=headers_a).json()["title"] == "Mi sesión"


# =============================================================================
# 3. Copilot home
# =============================================================================


@pytest.fixture()
def pinned_today(monkeypatch):
    from app.services import policy_registry
    from app.services.collection import clock

    monkeypatch.setattr(clock, "business_today", lambda now=None: TODAY)
    monkeypatch.setattr(policy_registry, "today_santiago", lambda *a, **k: TODAY)
    return TODAY


@pytest.fixture()
def book(db, world):
    """Broker A: one policy in the executive's book (overdue cuotas, ends in
    20 days) and one admin-only policy; broker B: a mirror that must not leak."""
    case = make_case_file(db, world.a, owner_user_id=world.a.executive.id)
    mine, _ = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="POL-MINE",
        start=date(2025, 3, 4), end_date=TODAY + timedelta(days=20), case_file_id=case.id,
    )
    theirs, _ = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="POL-TEAM", start=date(2026, 1, 5),
    )
    make_policy_with_plan(db, world.b, world.insurer, policy_number="POL-B", start=date(2026, 1, 5))
    db.add(
        Notification(
            broker_id=world.a.broker_id, user_id=world.a.executive.id, tray=NotificationTray.TODO,
            kind="installment_due", level="warning", status=NotificationStatus.SEEN,
            origin_type="installment", origin_id=1, params={},
        )
    )
    db.commit()
    return {"mine": mine, "theirs": theirs}


def _home(client, headers):
    response = client.get(f"{API}/ai/copilot/home", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _by_key(body):
    return {item["key"]: item for item in body["indicators"]}


def test_home_admin_sees_the_broker_and_matches_the_summaries(
    client, db, world, headers_a, pinned_today, book
):
    body = _home(client, headers_a)
    assert body["first_name"] == world.a.admin.full_name.split()[0]
    assert body["role"] == "broker_admin" and body["scope"] == "all"
    indicators = _by_key(body)
    assert list(indicators) == [
        "overdue_installments", "pending_evidence", "todo_notifications",
        "expiring_policies_30", "active_policies",
    ]

    collections = client.get(f"{API}/collections/summary", headers=headers_a).json()
    policies = client.get(f"{API}/policies/summary", headers=headers_a).json()
    overdue = indicators["overdue_installments"]
    assert overdue["value"] == collections["overdue"]["count"] > 0
    assert overdue["secondary"] == pytest.approx(float(collections["overdue"]["uf"]), abs=0.01)
    assert overdue["tone"] == "danger" and overdue["unit"] == "count"
    assert all(isinstance(i["value"], int) for i in body["indicators"])  # counts stay integers
    assert overdue["link"] == "/collections?tab=overdue"
    assert indicators["pending_evidence"]["value"] == collections["pending_evidence"]
    assert indicators["pending_evidence"]["link"] == "/collections/evidence"
    assert indicators["active_policies"]["value"] == policies["active_count"]
    assert indicators["active_policies"]["secondary"] == pytest.approx(float(policies["active_premium_uf"]))
    assert indicators["active_policies"]["link"] == "/policies?status=active"
    assert indicators["expiring_policies_30"]["value"] == 1
    assert indicators["todo_notifications"]["value"] == 0  # the exec's row is theirs
    assert indicators["todo_notifications"]["link"] == "/notifications?tray=todo"

    keys = [s["key"] for s in body["suggestions"]]
    assert 4 <= len(keys) <= 6 and len(set(keys)) == len(keys)
    assert keys[0] == "overdue_review"
    assert {"key": "overdue_review", "skill": "collections_review"} in body["suggestions"]
    assert "ask_insurer_status" in keys and "renewals_upcoming" in keys


def test_home_executive_sees_their_own_book(client, db, world, headers_a_exec, pinned_today, book):
    body = _home(client, headers_a_exec)
    assert body["scope"] == "mine" and body["role"] == "broker_executive"
    indicators = _by_key(body)
    board = client.get(
        f"{API}/collections/installments",
        params={"bucket": "overdue", "owner": "me", "limit": 200}, headers=headers_a_exec,
    ).json()
    assert indicators["overdue_installments"]["value"] == board["total"] > 0
    assert indicators["overdue_installments"]["link"] == "/collections?tab=overdue&owner=me"
    mine = client.get(
        f"{API}/policies", params={"owner": "me", "status": "active"}, headers=headers_a_exec
    ).json()
    assert indicators["active_policies"]["value"] == mine["total"] == 1
    assert indicators["active_policies"]["link"] == "/policies?status=active&mine=1"
    assert indicators["expiring_policies_30"]["value"] == 1
    assert indicators["todo_notifications"]["value"] == 1


def test_home_other_executive_has_an_empty_book_but_four_suggestions(
    client, db, world, pinned_today, book
):
    user = ensure_role_user(db, world.a, "broker_executive", "junior")
    body = _home(client, auth_headers(client, user.email))
    indicators = _by_key(body)
    assert indicators["overdue_installments"]["value"] == 0
    assert indicators["overdue_installments"]["tone"] == "ok"
    assert indicators["active_policies"]["value"] == 0
    keys = [s["key"] for s in body["suggestions"]]
    assert 4 <= len(keys) <= 6
    assert "overdue_review" not in keys and "pending_evidence_review" not in keys
    assert keys[0] == "register_policy"  # an empty book: start by loading a policy
    assert all(s["skill"] in (None, *agent_skills.SKILLS) for s in body["suggestions"])


def test_home_omits_what_the_role_cannot_view(client, db, world, headers_a_inspector, pinned_today, book):
    body = _home(client, headers_a_inspector)
    keys = set(_by_key(body))
    assert "overdue_installments" not in keys and "pending_evidence" not in keys
    assert {"todo_notifications", "active_policies", "expiring_policies_30"} <= keys
    assert len(body["suggestions"]) >= 4


def test_home_is_broker_only(client, db, world):
    assert client.get(f"{API}/ai/copilot/home").status_code == 401
    platform = _platform_headers(client, db)
    assert client.get(f"{API}/ai/copilot/home", headers=platform).status_code == 403


def test_home_tenant_isolation(client, db, world, headers_b, pinned_today, book):
    indicators = _by_key(_home(client, headers_b))
    assert indicators["active_policies"]["value"] == 1  # only POL-B
