"""Review round 3, R7 — team inboxes stay team-wide and say so (owner decision).

"Pagos sin confirmar" (``unconfirmed_payments``), "Respuestas por confirmar"
(``pending_evidence``) and ``plans_missing`` are TEAM inboxes: broker-wide whether or
not "Mías" is on. The summary marks every card's scope so the UI can label them
"Equipo"; the copilot marks its evidence card ``scope: "team"``.
"""
from __future__ import annotations

from datetime import date

from app.models.enums import EvidenceLevel
from tests.conftest import API
from tests.fakes_v12 import make_payment, make_policy_with_plan

TEAM = {"unconfirmed_payments": "team", "pending_evidence": "team", "plans_missing": "team"}


def test_collections_summary_marks_team_cards_in_both_scopes(client, db, world, headers_a_exec):
    policy, plan = make_policy_with_plan(db, world.a, world.insurer, policy_number="TEAM-1")
    make_payment(db, plan.installments[0], evidence=EvidenceLevel.DECLARED, paid_on=date(2026, 1, 5))
    db.commit()

    everyone = client.get(f"{API}/collections/summary", headers=headers_a_exec).json()
    mine = client.get(f"{API}/collections/summary?owner=me", headers=headers_a_exec).json()

    assert everyone["owner_scope"] == "all" and mine["owner_scope"] == "mine"
    for body, personal in ((everyone, "all"), (mine, "mine")):
        scopes = body["card_scopes"]
        assert {k: scopes[k] for k in TEAM} == TEAM
        assert {scopes[k] for k in ("due_7d", "due_30d", "overdue", "termination_risk",
                                    "past_installments_pending", "collected_month_uf")} == {personal}
    # Team inboxes are the same number in both scopes (the executive owns nothing here).
    assert mine["unconfirmed_payments"] == everyone["unconfirmed_payments"] == 1
    assert mine["pending_evidence"] == everyone["pending_evidence"]


def test_copilot_evidence_card_is_a_team_inbox(client, headers_a_exec, headers_a):
    for headers in (headers_a_exec, headers_a):
        home = client.get(f"{API}/ai/copilot/home", headers=headers).json()
        cards = {card["key"]: card for card in home["indicators"]}
        assert cards["pending_evidence"]["scope"] == "team"
        assert cards["todo_notifications"]["scope"] == home["scope"]
