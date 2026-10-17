"""v12 lane B1 — partial journeys (02 §3, team R6/R34).

``POST /case-files/{id}/skip-to`` writes ``is_skipped`` events for every stage
in between and walks the placements through their own machine;
``POST /case-files/{id}/activate-stage`` re-enters a skipped stage while the
folder has no policy (409 afterwards); ``GET /case-files/{id}`` carries the
``stages[]`` rail. Tenant isolation and the ``CaseFiles.Edit`` gate included.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.enums import CaseStage
from app.models.placement import Placement, PlacementStatus
from tests.conftest import API, make_policy


def _open_account(client, world, headers, *, placement_id=None):
    body = {
        "kind": "account",
        "client_id": world.a.client.id,
        "insurance_line_id": world.a.line.id,
        "period_start": "2026-05-01",
        "period_end": "2027-05-01",
    }
    if placement_id is not None:
        body["placement_id"] = placement_id
    response = client.post(f"{API}/case-files", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _draft_placement(db, world):
    placement = Placement(
        broker_id=world.a.broker_id,
        client_id=world.a.client.id,
        asset_id=world.a.asset.id,
        insurance_line_id=world.a.line.id,
        status=PlacementStatus.DRAFT,
        period_start=date(2026, 5, 1),
        period_end=date(2027, 5, 1),
    )
    db.add(placement)
    db.commit()
    return placement


def _states(body):
    return {row["stage"]: row["state"] for row in body["stages"]}


def test_skip_to_quotes_received_marks_the_gap_as_omitted(client, world, db, headers_a):
    case_id = _open_account(client, world, headers_a)
    before = client.get(f"{API}/case-files/{case_id}", headers=headers_a).json()
    assert before["skip_targets"] == ["quotes_received", "insured_decision", "policy_issued", "active"]
    assert _states(before)["lead"] == "current"
    assert _states(before)["intake"] == "pending"

    response = client.post(
        f"{API}/case-files/{case_id}/skip-to",
        json={"target_stage": "quotes_received", "note": "Cotizó en portales"},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "quotes_received"
    states = _states(body)
    assert states["lead"] == "done"
    for stage in ("intake", "pre_underwriting", "technical_basis", "market_submission"):
        assert states[stage] == "skipped"
    assert states["quotes_received"] == "current"
    assert states["comparison"] == "pending"
    assert body["skip_targets"] == ["insured_decision", "policy_issued", "active"]
    skipped_rows = [row for row in body["stages"] if row["skipped"]]
    assert all(row["entered_at"] for row in skipped_rows)

    events = db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case_id)
        .order_by(CaseFileStageEvent.occurred_at, CaseFileStageEvent.id)
    ).all()
    assert [(e.to_stage.value, e.is_skipped) for e in events] == [
        ("lead", False),
        ("intake", True),
        ("pre_underwriting", True),
        ("technical_basis", True),
        ("market_submission", True),
        ("quotes_received", False),
    ]
    assert events[-1].note == "Cotizó en portales"
    assert events[-1].user_id == world.a.admin.id
    assert all(e.user_id is None for e in events if e.is_skipped)

    # Behind (or equal to) the current stage -> 409; outside the targets -> 422.
    assert client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "quotes_received"},
        headers=headers_a,
    ).status_code == 409
    assert client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "comparison"},
        headers=headers_a,
    ).status_code == 422


def test_skip_to_walks_the_placement_machine(client, world, db, headers_a):
    placement = _draft_placement(db, world)
    case_id = _open_account(client, world, headers_a, placement_id=placement.id)
    response = client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "insured_decision"},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(Placement, placement.id).status == PlacementStatus.NEGOTIATING


def test_skip_to_a_policy_stage_needs_a_policy(client, world, db, headers_a):
    case_id = _open_account(client, world, headers_a)
    denied = client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "active"}, headers=headers_a
    )
    assert denied.status_code == 422
    assert "póliza" in denied.json()["detail"]

    make_policy(db, world.a, world.insurer, placement_id=None, case_file_id=case_id)
    db.commit()
    ok = client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "active"}, headers=headers_a
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["stage"] == "active"
    assert body["has_policy"] is True
    assert _states(body)["policy_issued"] == "skipped"
    assert body["skip_targets"] == []


def test_activate_a_skipped_stage(client, world, db, headers_a):
    case_id = _open_account(client, world, headers_a)
    client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "quotes_received"},
        headers=headers_a,
    )
    # A stage that was not skipped cannot be "activated".
    not_skipped = client.post(
        f"{API}/case-files/{case_id}/activate-stage", json={"stage": "comparison"}, headers=headers_a
    )
    assert not_skipped.status_code == 422

    response = client.post(
        f"{API}/case-files/{case_id}/activate-stage",
        json={"stage": "technical_basis", "note": "El asegurado pidió bases técnicas"},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "technical_basis"
    states = _states(body)
    assert states["technical_basis"] == "current"
    assert states["intake"] == "skipped"
    latest = db.scalars(
        select(CaseFileStageEvent)
        .where(CaseFileStageEvent.case_file_id == case_id)
        .order_by(CaseFileStageEvent.occurred_at.desc(), CaseFileStageEvent.id.desc())
    ).first()
    assert latest.to_stage == CaseStage.TECHNICAL_BASIS and latest.is_skipped is False
    assert latest.meta == {"activated": True}


def test_activate_stage_is_409_once_the_folder_has_a_policy(client, world, db, headers_a):
    case_id = _open_account(client, world, headers_a)
    client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "quotes_received"},
        headers=headers_a,
    )
    make_policy(db, world.a, world.insurer, placement_id=None, case_file_id=case_id)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case_id}/activate-stage", json={"stage": "intake"}, headers=headers_a
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Ya tiene póliza: los cambios se hacen por endoso o corrección"
    )


def test_skip_to_only_for_open_accounts(client, world, db, headers_a, insurer_policy):
    from app.models.enums import CaseFileKind

    post_sale = client.post(
        f"{API}/case-files",
        json={"kind": "endorsement", "policy_id": insurer_policy.id},
        headers=headers_a,
    )
    assert post_sale.status_code == 201, post_sale.text
    assert post_sale.json()["kind"] == CaseFileKind.ENDORSEMENT.value
    response = client.post(
        f"{API}/case-files/{post_sale.json()['id']}/skip-to",
        json={"target_stage": "quotes_received"},
        headers=headers_a,
    )
    assert response.status_code == 422


def test_skip_and_activate_are_tenant_scoped_and_gated(
    client, world, db, headers_a, headers_b, headers_a_inspector
):
    case_id = _open_account(client, world, headers_a)
    for path, body in (
        ("skip-to", {"target_stage": "quotes_received"}),
        ("activate-stage", {"stage": "intake"}),
    ):
        assert client.post(
            f"{API}/case-files/{case_id}/{path}", json=body, headers=headers_b
        ).status_code == 404
        assert client.post(
            f"{API}/case-files/{case_id}/{path}", json=body, headers=headers_a_inspector
        ).status_code == 403
    assert db.get(CaseFile, case_id).stage == CaseStage.LEAD


def test_detail_stage_rail_reports_legacy_gaps_as_skipped(client, world, db, headers_a):
    """A folder moved with a documented skip (intake -> technical_basis) shows
    the stage it never entered as omitted, with no event behind it."""
    case_id = _open_account(client, world, headers_a)
    assert client.post(
        f"{API}/case-files/{case_id}/transition", json={"to_stage": "intake"}, headers=headers_a
    ).status_code == 200
    assert client.post(
        f"{API}/case-files/{case_id}/transition", json={"to_stage": "technical_basis"},
        headers=headers_a,
    ).status_code == 200
    body = client.get(f"{API}/case-files/{case_id}", headers=headers_a).json()
    states = _states(body)
    assert states["pre_underwriting"] == "skipped"
    assert states["intake"] == "done" and states["technical_basis"] == "current"
    rail = {row["stage"]: row for row in body["stages"]}
    assert rail["pre_underwriting"]["entered_at"] is None


def test_activate_stage_refuses_a_closed_folder(client, world, db, headers_a):
    """Reviewer repro: skip-to, close the folder, activate -> 422, stage kept."""
    from app.models.enums import CaseFileStatus

    case_id = _open_account(client, world, headers_a)
    client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "quotes_received"},
        headers=headers_a,
    )
    case = db.get(CaseFile, case_id)
    case.status = CaseFileStatus.CLOSED
    db.commit()
    response = client.post(
        f"{API}/case-files/{case_id}/activate-stage", json={"stage": "pre_underwriting"},
        headers=headers_a,
    )
    assert response.status_code == 422, response.text
    db.expire_all()
    assert db.get(CaseFile, case_id).stage == CaseStage.QUOTES_RECEIVED


def test_activate_stage_never_jumps_forward(client, world, db, headers_a):
    """After re-entering technical_basis, market_submission is still *omitida*
    but AHEAD of the current stage: activating it would bypass skip-to's
    targets and guards -> 422."""
    case_id = _open_account(client, world, headers_a)
    client.post(
        f"{API}/case-files/{case_id}/skip-to", json={"target_stage": "quotes_received"},
        headers=headers_a,
    )
    back = client.post(
        f"{API}/case-files/{case_id}/activate-stage", json={"stage": "technical_basis"},
        headers=headers_a,
    )
    assert back.status_code == 200, back.text
    assert _states(back.json())["market_submission"] == "skipped"
    forward = client.post(
        f"{API}/case-files/{case_id}/activate-stage", json={"stage": "market_submission"},
        headers=headers_a,
    )
    assert forward.status_code == 422, forward.text
    db.expire_all()
    assert db.get(CaseFile, case_id).stage == CaseStage.TECHNICAL_BASIS


def test_activate_stage_only_for_accounts(client, world, db, headers_a, insurer_policy):
    post_sale = client.post(
        f"{API}/case-files",
        json={"kind": "endorsement", "policy_id": insurer_policy.id},
        headers=headers_a,
    )
    assert post_sale.status_code == 201, post_sale.text
    response = client.post(
        f"{API}/case-files/{post_sale.json()['id']}/activate-stage",
        json={"stage": "intake"},
        headers=headers_a,
    )
    assert response.status_code == 422
