"""The five v12 product decisions applied on 2026-10-17 (handoff §7).

1. 2b.5 FYI ``policy_linked_insured`` — a policy registered with no folder that
   lands on an insured ALREADY in the cartera tells the client's executive (and
   the admins when the uploader is someone else).
2. Contractual datetimes are offset-less America/Santiago wall-clock: an aware
   value is converted, a naive one is kept, a derived one is 12:00.
3. The vía of a colocación opened on demand follows the door: upload →
   ``digital``, manual → ``manual``; only the full journey takes the subtipo's
   ``suggested_route`` (pinned in ``test_v12_catalog``).
4. Pending actions hide antecedentes gaps once the account reached *Póliza
   emitida* (confirmed as is).
5. "Prima vigente" uses the exact filter of "Vigentes" (confirmed as is).

Hermetic: the chat model is faked, no network.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.enums import (
    CaseEntryOrigin,
    CaseStage,
    NotificationKind,
    NotificationTray,
    PlacementRoute,
    sql_enum,
)
from app.models.notification import Notification
from app.models.policy import Policy
from app.schemas.wallclock import contract_noon, to_wall_clock
from app.services import policy_registry as registry
from tests.conftest import API, ensure_role_user, make_case_file, make_policy
from tests.test_v12_policies import (  # noqa: F401 - fixtures
    _doc,
    _fake,
    _manual_body,
    _policy_payload,
    _sub,
    ai_key,
    portfolio,
)


def _linked(db, policy_id: int) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(
                Notification.kind == NotificationKind.POLICY_LINKED_INSURED,
                Notification.origin_id == policy_id,
            )
            .order_by(Notification.user_id)
        )
    )


# =============================================================================
# 1. 2b.5 — "Póliza enlazada a un asegurado existente"
# =============================================================================

def test_the_new_kind_does_not_widen_the_enum_column():
    longest = max(len(member.value) for member in NotificationKind)
    assert len(NotificationKind.POLICY_LINKED_INSURED.value) < longest == 27
    assert sql_enum(NotificationKind).length == 39  # 27 + slack: no MODIFY owed


def test_upload_matched_by_rut_tells_the_executive_and_the_other_admins(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    second_admin = ensure_role_user(db, world.a, "broker_admin", "admin2")
    row = db.get(Client, world.a.client.id)
    row.account_manager_id = world.a.executive.id
    db.commit()
    sub = _sub(db, "PRO-02")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload(
        "UP-LINK", insured={"legal_name": "Asegurado Alfa S.A.", "rut": "77.111.111-4"}
    ))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    policy = response.json()["policy"]
    assert policy["client_id"] == world.a.client.id

    rows = _linked(db, policy["id"])
    # The executive, plus the admins — never the admin who uploaded it.
    assert {r.user_id for r in rows} == {world.a.executive.id, second_admin.id}
    for note in rows:
        assert note.tray == NotificationTray.FYI
        assert note.broker_id == world.a.broker_id
        assert note.origin_type == registry.LINKED_INSURED_ORIGIN
        assert note.link_path == f"/policies/{policy['id']}"
        assert note.params["policy_number"] == "UP-LINK"
        assert note.params["insured_name"]
        assert note.params["uploaded_by"] == world.a.admin.full_name

    # The executive sees it in their own bell, as an FYI.
    from tests.conftest import auth_headers

    bell = client.get(
        f"{API}/notifications", headers=auth_headers(client, world.a.executive.email)
    ).json()["items"]
    assert [n["kind"] for n in bell] == ["policy_linked_insured"]


def test_the_executive_uploading_for_their_own_client_alerts_no_admin(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a_exec
):
    row = db.get(Client, world.a.client.id)
    row.account_manager_id = world.a.executive.id
    db.commit()
    sub = _sub(db, "PRO-02")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-OWN"))
    response = client.post(
        f"{API}/policies/upload",
        json={
            "document_id": document.id,
            "insurance_subtype_id": sub.id,
            "client_id": world.a.client.id,
        },
        headers=headers_a_exec,
    )
    assert response.status_code == 201, response.text
    rows = _linked(db, response.json()["policy"]["id"])
    assert [r.user_id for r in rows] == [world.a.executive.id]


def test_upload_creating_a_new_insured_sends_nothing(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    ensure_role_user(db, world.a, "broker_admin", "admin2")
    db.commit()
    sub = _sub(db, "PRO-02")
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload(
        "UP-FRESH", insured={"legal_name": "Nuevo Asegurado SpA", "rut": "12.345.678-5"}
    ))
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id, "create_client": True},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    assert _linked(db, response.json()["policy"]["id"]) == []


def test_upload_into_a_folder_is_not_the_2b5_path(
    client, world, db, tmp_path, ai_key, monkeypatch, headers_a
):
    ensure_role_user(db, world.a, "broker_admin", "admin2")
    case = make_case_file(db, world.a)
    db.commit()
    document = _doc(db, world.a, tmp_path, case_id=case.id)
    _fake(monkeypatch, _policy_payload("UP-FOLDER"))
    response = client.post(
        f"{API}/policies/upload", json={"document_id": document.id}, headers=headers_a
    )
    assert response.status_code == 201, response.text
    assert _linked(db, response.json()["policy"]["id"]) == []


def test_manual_entry_for_an_existing_client_without_executive_tells_the_admins(
    client, world, db, catalog, headers_a_exec
):
    second_admin = ensure_role_user(db, world.a, "broker_admin", "admin2")
    db.commit()
    sub = _sub(db, "PRO-02")
    response = client.post(
        f"{API}/policies", json=_manual_body(world, sub), headers=headers_a_exec
    )
    assert response.status_code == 201, response.text
    rows = _linked(db, response.json()["id"])
    assert {r.user_id for r in rows} == {world.a.admin.id, second_admin.id}


def test_manual_entry_with_a_new_rut_sends_nothing(client, world, db, catalog, headers_a_exec):
    sub = _sub(db, "PRO-02")
    body = _manual_body(
        world, sub, client_id=None, insured_rut="12.345.678-5", insured_name="Juana Pérez"
    )
    response = client.post(f"{API}/policies", json=body, headers=headers_a_exec)
    assert response.status_code == 201, response.text
    assert _linked(db, response.json()["id"]) == []


def test_the_fyi_never_collides_with_the_policy_todo(client, world, db, catalog, headers_a_exec):
    """``plan_missing`` lives on ``("policy", id)``; the 2b.5 FYI has its own
    origin, so neither overwrites the other (one open row per user + origin)."""
    from app.services.notifications import notify

    sub = _sub(db, "PRO-02")
    response = client.post(
        f"{API}/policies", json=_manual_body(world, sub), headers=headers_a_exec
    )
    assert response.status_code == 201, response.text
    policy_id = response.json()["id"]
    notify(
        db,
        broker_id=world.a.broker_id,
        user_ids=[world.a.admin.id],
        kind=NotificationKind.PLAN_MISSING,
        tray=NotificationTray.TODO,
        origin_type="policy",
        origin_id=policy_id,
    )
    db.commit()
    kinds = set(
        db.scalars(
            select(Notification.kind).where(
                Notification.user_id == world.a.admin.id, Notification.origin_id == policy_id
            )
        )
    )
    assert kinds == {NotificationKind.PLAN_MISSING, NotificationKind.POLICY_LINKED_INSURED}


# =============================================================================
# 2. Contractual datetimes = Chile wall-clock, offset-less
# =============================================================================

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Summer (CLST, UTC-3): 15:00Z is noon in Santiago.
        (datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc), datetime(2026, 3, 1, 12, 0)),
        # Winter (CLT, UTC-4): 16:00Z is noon in Santiago.
        (datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc), datetime(2026, 7, 1, 12, 0)),
        # An explicit Chilean offset is already the wall-clock.
        (
            datetime(2026, 3, 1, 12, 0, tzinfo=timezone(timedelta(hours=-3))),
            datetime(2026, 3, 1, 12, 0),
        ),
        # Naive is wall-clock already: untouched.
        (datetime(2026, 3, 1, 12, 0), datetime(2026, 3, 1, 12, 0)),
        (None, None),
    ],
)
def test_to_wall_clock(value, expected):
    result = to_wall_clock(value)
    assert result == expected
    assert result is None or result.tzinfo is None


def test_contract_noon_is_offset_less():
    noon = contract_noon(date(2026, 3, 1))
    assert noon == datetime(2026, 3, 1, 12, 0) and noon.tzinfo is None


def test_an_endorsement_sent_as_utc_is_stored_and_returned_as_santiago_noon(
    client, db, world, headers_a, insurer_policy
):
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "kind": "other",
            "effective_at": "2027-01-01T15:00:00.000Z",  # what toISOString() sends for 12:00 CLST
            "ends_at": "2027-07-01T16:00:00Z",          # 12:00 CLT
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["effective_at"] == "2027-01-01T12:00:00"
    assert body["ends_at"] == "2027-07-01T12:00:00"
    from app.models.endorsement import Endorsement

    db.expire_all()
    row = db.get(Endorsement, body["id"])
    assert row.effective_at.replace(tzinfo=None) == datetime(2027, 1, 1, 12, 0)


def test_a_wall_clock_value_passes_through_unchanged(client, db, world, headers_a, insurer_policy):
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={"policy_id": insurer_policy.id, "kind": "other", "effective_at": "2027-01-01T12:00:00"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["effective_at"] == "2027-01-01T12:00:00"


def test_policy_period_patch_is_normalised_to_santiago(client, db, world, headers_a):
    policy = make_policy(
        db, world.a, world.insurer, start_date=date(2026, 3, 1), end_date=date(2027, 3, 1)
    )
    db.commit()
    response = client.patch(
        f"{API}/policies/{policy.id}",
        headers=headers_a,
        json={
            "period_start_at": "2026-03-01T15:00:00Z",
            "period_end_at": "2027-03-01T12:00:00-03:00",
            "correction_reason": "Hora de vigencia según la póliza",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period_start_at"] == "2026-03-01T12:00:00"
    assert body["period_end_at"] == "2027-03-01T12:00:00"


def test_a_derived_period_is_offset_less_noon(client, world, db, catalog, headers_a):
    sub = _sub(db, "PRO-02")
    response = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a)
    assert response.status_code == 201, response.text
    body = response.json()
    # Returned straight from the session (not reloaded): still no offset.
    assert body["period_start_at"] == "2026-03-01T12:00:00"
    assert body["period_end_at"] == "2027-03-01T12:00:00"


def test_event_timestamps_stay_utc(client, db, world, headers_a, insurer_policy):
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={"policy_id": insurer_policy.id, "kind": "other"},
    )
    assert response.status_code == 201, response.text
    created = response.json()["created_at"]
    assert created.endswith("Z") or created.endswith("+00:00")


# =============================================================================
# 3. The vía of a colocación opened on demand follows the door
# =============================================================================

def test_route_for_entry():
    class _Sub:
        suggested_route = PlacementRoute.TRADITIONAL

    assert registry.route_for_entry(CaseEntryOrigin.POLICY_UPLOAD, _Sub()) == PlacementRoute.DIGITAL
    assert registry.route_for_entry(CaseEntryOrigin.MANUAL_ENTRY, _Sub()) == PlacementRoute.MANUAL
    assert registry.route_for_entry(CaseEntryOrigin.FULL_JOURNEY, _Sub()) == PlacementRoute.TRADITIONAL
    assert registry.route_for_entry(CaseEntryOrigin.FULL_JOURNEY, None) == PlacementRoute.DIGITAL


def test_upload_of_a_traditional_subtype_opens_a_digital_colocacion(
    client, world, db, catalog, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db, "PRO-02")
    assert sub.suggested_route == PlacementRoute.TRADITIONAL
    document = _doc(db, world.a, tmp_path)
    _fake(monkeypatch, _policy_payload("UP-VIA"))
    response = client.post(
        f"{API}/policies/upload",
        json={
            "document_id": document.id,
            "insurance_subtype_id": sub.id,
            "client_id": world.a.client.id,
        },
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    case = db.get(CaseFile, response.json()["policy"]["case_file_id"])
    assert case.entry_origin == CaseEntryOrigin.POLICY_UPLOAD
    assert case.route == PlacementRoute.DIGITAL


def test_manual_entry_of_a_traditional_subtype_opens_a_manual_colocacion(
    client, world, db, catalog, headers_a
):
    sub = _sub(db, "PRO-02")
    response = client.post(f"{API}/policies", json=_manual_body(world, sub), headers=headers_a)
    assert response.status_code == 201, response.text
    case = db.get(CaseFile, response.json()["case_file_id"])
    assert case.entry_origin == CaseEntryOrigin.MANUAL_ENTRY
    assert case.route == PlacementRoute.MANUAL


# =============================================================================
# 4. Pending actions: antecedentes gaps end at "Póliza emitida" (confirmed)
# =============================================================================

def _codes(client, headers, case_id):
    res = client.get(f"{API}/case-files/{case_id}/pending-actions", headers=headers)
    assert res.status_code == 200, res.text
    return {a["code"] for a in res.json()["actions"]}


def test_antecedentes_gap_shows_until_policy_issued_and_hides_from_it(
    client, world, db, headers_a
):
    from app.services.case_files import CASE_STAGE_FLOW
    from app.models.enums import CaseFileKind

    chain = CASE_STAGE_FLOW[CaseFileKind.ACCOUNT]
    before = chain[chain.index(CaseStage.POLICY_ISSUED) - 1]
    early = make_case_file(db, world.a, stage=before, n=1)
    issued = make_case_file(db, world.a, stage=CaseStage.POLICY_ISSUED, n=2)
    db.commit()
    assert "antecedentes_missing" in _codes(client, headers_a, early.id)
    assert "antecedentes_missing" not in _codes(client, headers_a, issued.id)
    assert "antecedentes_unregistered" not in _codes(client, headers_a, issued.id)


# =============================================================================
# 5. "Prima vigente" = Σ premium over the "Vigentes" filter (confirmed)
# =============================================================================

def test_prima_vigente_is_the_premium_of_exactly_the_vigentes_list(
    client, world, db, portfolio, headers_a
):
    cards = client.get(f"{API}/policies/summary", headers=headers_a).json()
    listed = client.get(
        f"{API}/policies", params={"status": "active", "limit": 200}, headers=headers_a
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()["items"]
    assert len(rows) == cards["active_count"]
    total = sum((Decimal(r["total_premium_uf"] or 0) for r in rows), Decimal("0"))
    assert total.quantize(Decimal("0.0001")) == Decimal(cards["active_premium_uf"])
    assert {r["policy_number"] for r in rows} == {
        p.policy_number
        for p in db.scalars(select(Policy).where(Policy.broker_id == world.a.broker_id))
        if registry.computed_status(p) == "active"
    }
