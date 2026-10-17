"""Regressions for the v12 QA pass (docs/v12 QA defects fixed by the QA fixer).

Each test names the defect it pins. Collections-search, the insurer override
RUT and the money-rule 422 live next to their siblings; this file holds the
ones with no natural home.
"""
from __future__ import annotations

from tests.conftest import API


# --- RBAC: the corporate logo takes Settings.Manage -------------------------------

def _upload_logo(client, headers, broker_id):
    return client.post(
        f"{API}/documents",
        headers=headers,
        data={"entity_type": "broker", "entity_id": str(broker_id), "category": "logo"},
        files={"file": ("logo.pdf", b"%PDF-1.4\n%fake logo\n", "application/pdf")},
    )


def test_executive_cannot_replace_the_broker_logo(client, world, headers_a_exec):
    res = _upload_logo(client, headers_a_exec, world.a.broker_id)
    assert res.status_code == 403
    assert "administrador" in res.json()["detail"]


def test_admin_can_replace_the_broker_logo(client, world, headers_a):
    res = _upload_logo(client, headers_a, world.a.broker_id)
    assert res.status_code == 201, res.text


def test_executive_still_uploads_ordinary_broker_documents(client, world, headers_a_exec):
    res = client.post(
        f"{API}/documents",
        headers=headers_a_exec,
        data={"entity_type": "broker", "entity_id": str(world.a.broker_id), "category": "other"},
        files={"file": ("nota.pdf", b"%PDF-1.4\n%nota\n", "application/pdf")},
    )
    assert res.status_code == 201, res.text


# --- Platform users get a Spanish 403 on broker endpoints ---------------------------

def test_broker_scope_403_is_spanish(client, db):
    from tests.conftest import auth_headers
    from tests.test_v12_payments import make_platform_user

    user = make_platform_user(db)
    res = client.get(f"{API}/account-groups", headers=auth_headers(client, user.email))
    assert res.status_code == 403
    assert "corredora" in res.json()["detail"]


# --- "Otras etapas": the unreachable reason never leaks the enum code --------------

def test_unreachable_stage_reason_is_spanish(client, world, db, headers_a):
    from app.models.enums import CaseStage
    from tests.conftest import make_case_file

    case = make_case_file(db, world.a, stage=CaseStage.ACTIVE)
    db.commit()
    body = client.get(f"{API}/case-files/{case.id}/transitions", headers=headers_a).json()
    blocked = [o for o in body["options"] if not o["allowed"] and o["reason"] != "etapa actual"]
    assert blocked
    for option in blocked:
        assert "'active'" not in option["reason"]
    assert any("«Vigente»" in o["reason"] for o in blocked)


# --- Acciones pendientes respect skipped and past stages --------------------------

def _codes(client, headers, case_id):
    res = client.get(f"{API}/case-files/{case_id}/pending-actions", headers=headers)
    assert res.status_code == 200, res.text
    return {a["code"] for a in res.json()["actions"]}


def test_pending_actions_skip_past_stages_on_a_vigente_account(client, world, db, headers_a):
    from app.models.enums import CaseStage
    from tests.conftest import make_case_file

    case = make_case_file(db, world.a, stage=CaseStage.ACTIVE)
    db.commit()
    codes = _codes(client, headers_a, case.id)
    assert "antecedentes_missing" not in codes
    assert "propuesta_missing" not in codes


def test_pending_actions_after_skip_to_drop_the_omitted_antecedentes(client, world, db, headers_a):
    from tests.test_v12_journey import _open_account

    case_id = _open_account(client, world, headers_a)
    assert "antecedentes_missing" in _codes(client, headers_a, case_id)
    res = client.post(
        f"{API}/case-files/{case_id}/skip-to",
        json={"target_stage": "quotes_received"},
        headers=headers_a,
    )
    assert res.status_code == 200, res.text
    assert "antecedentes_missing" not in _codes(client, headers_a, case_id)


def test_pending_actions_still_flag_a_missing_propuesta_at_decision(client, world, db, headers_a):
    from app.models.enums import CaseStage
    from tests.conftest import make_case_file

    case = make_case_file(db, world.a, stage=CaseStage.INSURED_DECISION)
    db.commit()
    assert "propuesta_missing" in _codes(client, headers_a, case.id)


# --- A ramo with no record schema answers null, not a 404 ------------------------

def test_line_record_schema_absent_is_null_not_404(client, headers_a):
    res = client.get(f"{API}/line-record-schemas/999999", headers=headers_a)
    assert res.status_code == 200
    assert res.json() is None
