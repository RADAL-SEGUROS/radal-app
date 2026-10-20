"""Review round 3, R1 — the agent targets a cuota by policy number + cuota number.

The round-3 scenario (dev, /agent/17): "Registra un pago de la cuota 2 de la póliza
TRBF-0020126441 por transferencia". The cuota was NOT among ``list_installments``'
first 30 rows; the card proposed "Cuota: 5 de 10 · póliza N° V-FL-0091447", and on a
retry "Cuota: 1 de 10 · póliza N° 0020119904". The model guessed an internal id.

Now the cuota tools take ``policy_number`` + ``installment_number``, resolved
server-side within the broker; an id given WITH numbers must agree, or nothing is
proposed. Hermetic: the provider is a scripted fake (conftest blanks the real key).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.ai import AgentAction, AgentActionStatus
from app.models.mail import StatusInquiry
from app.models.payment import Payment
from app.services import agent_targets
from app.services import ai as ai_service
from tests.conftest import API
from tests.fakes_v12 import make_insurer_profile, make_policy_with_plan
from tests.test_agent_tools import _Answer, _call, _FakeToolChatModel

TODAY = date(2026, 10, 19)


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


@pytest.fixture()
def today(monkeypatch):
    from app.services.collection import clock

    monkeypatch.setattr(clock, "business_today", lambda now=None: TODAY)
    return TODAY


@pytest.fixture()
def book(db, world):
    """Broker A's book as on dev: V-FL-0091447 and 0020119904 (10 cuotas each), four
    more policies (40 cuotas) and TRBF-0020126441 (10 × UF 131,69 from 20-10-2026) —
    its cuotas sort AFTER the first 30 board rows. Broker B has its own TRBF policy."""
    vfl, vfl_plan = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="V-FL-0091447",
        n_installments=10, start=date(2026, 1, 5),
    )
    old, old_plan = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="0020119904",
        n_installments=10, start=date(2026, 1, 15),
    )
    for index in range(4):
        make_policy_with_plan(
            db, world.a, world.insurer, policy_number=f"FILL-{index}",
            n_installments=10, start=date(2026, 2, 1 + index),
        )
    trbf, trbf_plan = make_policy_with_plan(
        db, world.a, world.insurer, policy_number="TRBF-0020126441",
        n_installments=10, start=date(2026, 10, 20),
        total_premium_uf=Decimal("1316.9000"),
    )
    b_trbf, b_plan = make_policy_with_plan(
        db, world.b, world.insurer, policy_number="TRBF-0020126441", n_installments=10,
    )
    db.commit()
    return {
        "vfl": vfl, "vfl_inst": vfl_plan.installments,
        "old": old, "old_inst": old_plan.installments,
        "trbf": trbf, "trbf_inst": trbf_plan.installments,
        "b_trbf": b_trbf, "b_inst": b_plan.installments,
    }


def _use(monkeypatch, fake) -> None:
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)


def _one_call(client, headers, monkeypatch, name: str, args: dict):
    fake = _FakeToolChatModel([_Answer(tool_calls=[_call(name, args)]), _Answer(content="Listo.")])
    _use(monkeypatch, fake)
    response = client.post(
        f"{API}/ai/agent/messages", json={"content": f"usa {name}"}, headers=headers
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data, json.loads(data["messages"][2]["content"])


def _count(db, model, *where) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# --- the round-3 scenario -----------------------------------------------------------


def test_round3_cuota_2_of_trbf_is_resolved_by_number_never_by_a_guessed_id(
    client, db, world, headers_a, monkeypatch, ai_key, book, today
):
    target = book["trbf_inst"][1]  # cuota 2 of TRBF-0020126441
    # 1. The precondition of the bug: the default list does not carry that cuota.
    _, listed = _one_call(client, headers_a, monkeypatch, "list_installments", {})
    assert listed["total"] == 70 and listed["shown"] == 30
    assert target.id not in {row["id"] for row in listed["installments"]}
    assert all("policy_number" in row and "number" in row for row in listed["installments"])

    # 2. The guessed id of the round (cuota 5 of V-FL-0091447) WITH the numbers the
    #    person said: refused, nothing proposed.
    guessed = book["vfl_inst"][4]
    _, refused = _one_call(
        client, headers_a, monkeypatch, "register_payment",
        {
            "installment_id": guessed.id,
            "policy_number": "TRBF-0020126441",
            "installment_number": 2,
            "amount": 131.69,
            "paid_on": "2026-10-19",
            "method": "transfer",
        },
    )
    assert refused["error"] == "target_refused"
    assert refused["code"] == "target_mismatch"
    assert "V-FL-0091447" in refused["detail"] and "TRBF-0020126441" in refused["detail"]
    assert _count(db, AgentAction) == 0

    # 3. By number only (as the person typed it, any spacing/case): the right cuota.
    data, folded = _one_call(
        client, headers_a, monkeypatch, "register_payment",
        {
            "policy_number": "trbf 0020126441",
            "installment_number": 2,
            "amount": 131.69,
            "paid_on": "2026-10-19",
            "method": "transfer",
        },
    )
    assert folded["status"] == "pending_confirmation", folded
    pending = data["pending_actions"][0]
    action = db.get(AgentAction, pending["id"])
    assert action.arguments["installment_id"] == target.id
    assert action.arguments["policy_number"] == "TRBF-0020126441"
    assert action.arguments["installment_number"] == 2
    assert "la cuota 2 de la póliza N° TRBF-0020126441" in pending["summary"]
    assert "un pago de UF 131,69 el 19-10-2026" in pending["summary"]  # Chilean format
    display = {row["label"]: row["value"] for row in pending["display"]}
    assert display["Cuota"] == "2 de 10 · póliza N° TRBF-0020126441"
    assert display["Monto"] == "UF 131,69"
    assert "N° de póliza" not in display  # no duplicate row

    # 4. Confirm books THAT cuota and no other.
    response = client.post(f"{API}/ai/agent/actions/{pending['id']}/confirm", headers=headers_a)
    assert response.status_code == 200, response.text
    payments = db.scalars(select(Payment)).all()
    assert [p.installment_id for p in payments] == [target.id]


def test_list_installments_by_policy_number_returns_every_cuota_with_its_number(
    client, db, world, headers_a, monkeypatch, ai_key, book, today
):
    _, listed = _one_call(
        client, headers_a, monkeypatch, "list_installments", {"policy_number": "N° TRBF-0020126441"}
    )
    assert listed["total"] == 10 and listed["policies_matched"] == 1
    rows = listed["installments"]
    assert [row["number"] for row in rows] == list(range(1, 11))
    assert {row["policy_number"] for row in rows} == {"TRBF-0020126441"}
    assert rows[1]["id"] == book["trbf_inst"][1].id
    assert {row["policy_id"] for row in rows} == {book["trbf"].id}  # never broker B's

    _, one = _one_call(
        client, headers_a, monkeypatch, "list_installments",
        {"policy_number": "TRBF-0020126441", "installment_number": 2},
    )
    assert [row["number"] for row in one["installments"]] == [2]

    _, missing = _one_call(
        client, headers_a, monkeypatch, "list_installments", {"policy_number": "NO-EXISTE-1"}
    )
    assert missing["error"] == "target_refused" and missing["code"] == "policy_not_found"


def test_refusals_missing_unknown_ambiguous_and_foreign(
    client, db, world, headers_a, headers_b, monkeypatch, ai_key, book, today
):
    base = {"amount": 10, "paid_on": "2026-10-19"}
    # Neither an id nor both numbers: the arguments do not validate.
    _, bad = _one_call(
        client, headers_a, monkeypatch, "register_payment", {**base, "policy_number": "TRBF-0020126441"}
    )
    assert bad["error"] == "invalid_arguments"
    # A cuota number the policy does not have.
    _, absent = _one_call(
        client, headers_a, monkeypatch, "register_payment",
        {**base, "policy_number": "TRBF-0020126441", "installment_number": 11},
    )
    assert absent["code"] == "installment_not_found"
    assert "cuotas 1 a 10" in absent["detail"]
    # Leading zeros are significant: 20119904 is not 0020119904.
    _, zeros = _one_call(
        client, headers_a, monkeypatch, "register_payment",
        {**base, "policy_number": "20119904", "installment_number": 1},
    )
    assert zeros["code"] == "policy_not_found"
    # Broker B's own TRBF policy resolves to B's cuota, never A's (and vice versa).
    _, b_folded = _one_call(
        client, headers_b, monkeypatch, "register_payment",
        {**base, "policy_number": "TRBF-0020126441", "installment_number": 2},
    )
    assert b_folded["status"] == "pending_confirmation"
    b_action = db.scalars(select(AgentAction).where(AgentAction.broker_id == world.b.broker_id)).one()
    assert b_action.arguments["installment_id"] == book["b_inst"][1].id
    # Two policies of the broker share the number and both have a cuota 2: ambiguous.
    make_policy_with_plan(
        db, world.a, world.insurer_no_cmf, policy_number="TRBF 0020126441",
        n_installments=10, start=date(2025, 10, 20),
    )
    db.commit()
    _, ambiguous = _one_call(
        client, headers_a, monkeypatch, "register_payment",
        {**base, "policy_number": "TRBF-0020126441", "installment_number": 2},
    )
    assert ambiguous["code"] == "policy_ambiguous"
    assert len(ambiguous["candidates"]) == 2
    assert "Sin CMF Seguros" in ambiguous["detail"]
    assert _count(db, AgentAction, AgentAction.broker_id == world.a.broker_id) == 0


def test_a_tampered_stored_target_is_refused_at_confirm_and_stays_proposed(
    client, db, world, headers_a, monkeypatch, ai_key, book, today
):
    data, folded = _one_call(
        client, headers_a, monkeypatch, "record_payment_promise",
        {"policy_number": "TRBF-0020126441", "installment_number": 3, "promise_until": "2026-12-30"},
    )
    assert folded["status"] == "pending_confirmation"
    action = db.get(AgentAction, data["pending_actions"][0]["id"])
    assert action.arguments["installment_id"] == book["trbf_inst"][2].id
    action.arguments = {**action.arguments, "installment_id": book["vfl_inst"][4].id}
    db.commit()
    response = client.post(f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "target_mismatch"
    db.expire_all()
    assert db.get(AgentAction, action.id).status == AgentActionStatus.PROPOSED


def test_status_inquiry_and_reminder_take_numbers_too(
    client, db, world, headers_a, monkeypatch, ai_key, book, today, fake_transport
):
    make_insurer_profile(db, world.insurer, collections_email="cobranza@hdi.test")
    db.commit()
    data, folded = _one_call(
        client, headers_a, monkeypatch, "send_status_inquiry",
        {"policy_number": "TRBF-0020126441", "installment_numbers": [1, 2]},
    )
    assert folded["status"] == "pending_confirmation", folded
    action = db.get(AgentAction, data["pending_actions"][0]["id"])
    assert action.arguments["policy_id"] == book["trbf"].id
    assert action.arguments["installment_ids"] == [i.id for i in book["trbf_inst"][:2]]
    display = {row["label"]: row["value"] for row in data["pending_actions"][0]["display"]}
    assert display["Cuotas"] == "1 y 2 · póliza N° TRBF-0020126441"
    response = client.post(f"{API}/ai/agent/actions/{action.id}/confirm", headers=headers_a)
    assert response.status_code == 200, response.text
    assert _count(db, StatusInquiry) == 1

    _, mismatch = _one_call(
        client, headers_a, monkeypatch, "send_status_inquiry",
        {"policy_number": "TRBF-0020126441", "policy_id": book["vfl"].id},
    )
    assert mismatch["code"] == "target_mismatch"

    _, reminder = _one_call(
        client, headers_a, monkeypatch, "send_payment_reminder",
        {"policy_number": "TRBF-0020126441", "installment_number": 99},
    )
    assert reminder["code"] == "installment_not_found"


def test_policy_number_normalisation():
    norm = agent_targets.normalize_policy_number
    assert norm("TRBF-0020126441") == norm("trbf 0020126441") == norm("N° TRBF–0020126441")
    assert norm("Póliza N° 15-04-0091883") == "15040091883"
    assert norm("V-FL-0091447") == "VFL0091447"
    assert norm("0020119904") != norm("20119904")
    assert norm("") == "" and norm(None) == ""


def test_the_system_prompt_and_descriptions_prefer_numbers():
    from app.services import agent_tools

    assert "policy_number + installment_number" in ai_service._AGENT_SYSTEM
    assert "target_refused" in ai_service._AGENT_SYSTEM
    for name in ("register_payment", "record_payment_promise", "send_payment_reminder"):
        params = agent_tools.TOOL_REGISTRY[name].args_schema.model_json_schema()["properties"]
        assert list(params)[:3] == ["policy_number", "installment_number", "installment_id"]


def test_agent_prompt_carries_todays_chile_date():
    """Round 3: "con fecha de hoy" made the agent ask for the date."""
    from datetime import datetime, timezone

    from app.services.ai import _agent_today_line

    # 02:00 UTC on the 20th is still the 19th in Chile (UTC-3).
    line = _agent_today_line(datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc))
    assert "lunes 19-10-2026" in line and "no la preguntes" in line
