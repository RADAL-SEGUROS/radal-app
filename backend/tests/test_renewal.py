"""Renewal and the origin axis — spec v3 §4.3 (rules 1, 4, 5, 6).

A renewal folder is a **sibling in time** of the prior vigencia, never a child
of it and never a child of a policy: ``origin=renewal`` +
``origin_case_file_id``, ``policy_id=NULL``, cloned placements, the same RUTs.
The prior period is read through ``GET /case-files/{id}/history`` and never
constrains the new one.
"""
from __future__ import annotations

import os
from datetime import date

import pytest

from app.models.account_client import AccountClient
from app.models.case_file import CaseFile
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseOrigin, CaseSection, CaseStage
from app.models.placement import Placement, PlacementStatus
from tests.conftest import API, make_case_file, make_policy


# --- Local fixtures (conftest is shared; this pass stays additive) ------------

@pytest.fixture()
def local_media(tmp_path, monkeypatch):
    """Point the document store at a temp dir — ``backend/.env`` says s3."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    return tmp_path


def make_group(db, tenant, name="GRUPO VIÑA", slug="grupo-vina"):
    from app.models.account_group import AccountGroup

    group = AccountGroup(broker_id=tenant.broker_id, name=name, slug=slug)
    db.add(group)
    db.flush()
    return group


def make_client(db, tenant, rut, legal_name, group):
    from app.models.client import Client
    from app.models.insured import Insured

    insured = Insured(rut=rut, legal_name=legal_name)
    db.add(insured)
    db.flush()
    row = Client(
        broker_id=tenant.broker_id,
        insured_id=insured.id,
        account_group_id=group.id if group is not None else None,
    )
    db.add(row)
    db.flush()
    return row


def store_document(db, tenant, case, *, category, section, root, body=b"%PDF-1.4 prior"):
    key = f"documents/case_file/{case.id}/{category.value}-{case.id}.pdf"
    path = os.path.join(str(root), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    from app.models.enums import EntityType

    doc = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        case_file_id=case.id,
        section=section,
        s3_key=key,
        bucket="radal-test-bucket",
        original_name=f"{category.value}.pdf",
        mime_type="application/pdf",
        category=category,
    )
    db.add(doc)
    db.flush()
    return doc


@pytest.fixture()
def active_account(db, world):
    """An ACTIVE account folder of broker A, in a group, with one placement."""
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    case = make_case_file(db, world.a, stage=CaseStage.ACTIVE)
    case.account_group_id = group.id
    case.period_start = date(2025, 10, 15)
    case.period_end = date(2026, 10, 15)
    case.period_label = "2025-2026"
    world.a.placement.case_file_id = case.id
    world.a.placement.status = PlacementStatus.ACTIVE
    world.a.placement.period_start = case.period_start
    world.a.placement.period_end = case.period_end
    db.add(
        AccountClient(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            client_id=world.a.client.id,
            is_primary=True,
        )
    )
    db.commit()
    db.refresh(case)
    return case


NEXT_PERIOD = {"period_start": "2026-10-15", "period_end": "2027-10-15"}


# --- The happy path -----------------------------------------------------------

def test_renew_opens_a_sibling_folder_in_the_next_vigencia(
    client, world, db, headers_a, active_account
):
    response = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["kind"] == "renewal"
    assert body["stage"] == "renewal_review"
    assert body["origin"] == "renewal"
    assert body["origin_case_file_id"] == active_account.id
    # A renewal hangs off nothing: not a policy, not the prior folder.
    assert body["policy_id"] is None
    assert body["parent_case_file_id"] is None
    assert body["account_group_id"] == active_account.account_group_id
    assert body["period_start"] == "2026-10-15"
    assert body["period_label"] == "2026-2027"
    # The reference carries the YEAR OF THE VIGENCIA, not of the click.
    assert body["reference"].startswith("EXP-2026-")

    # Placements were cloned as drafts on the new period.
    clones = db.query(Placement).filter_by(case_file_id=body["id"]).all()
    assert len(clones) == 1
    clone = clones[0]
    assert clone.id != world.a.placement.id
    assert clone.asset_id == world.a.placement.asset_id
    assert clone.insurance_line_id == world.a.placement.insurance_line_id
    assert clone.status is PlacementStatus.DRAFT
    assert clone.period_start == date(2026, 10, 15)
    assert body["placement_id"] == clone.id

    # Members travelled.
    members = db.query(AccountClient).filter_by(case_file_id=body["id"]).all()
    assert [row.client_id for row in members] == [world.a.client.id]
    assert members[0].is_primary is True
    assert body["client_ids"] == [world.a.client.id]


def test_renew_leaves_the_source_untouched(client, world, db, headers_a, active_account):
    before = (
        active_account.stage,
        active_account.status,
        active_account.period_start,
        active_account.period_end,
    )
    response = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    )
    assert response.status_code == 201, response.text
    db.refresh(active_account)
    assert (
        active_account.stage,
        active_account.status,
        active_account.period_start,
        active_account.period_end,
    ) == before
    db.refresh(world.a.placement)
    assert world.a.placement.status is PlacementStatus.ACTIVE
    assert world.a.placement.case_file_id == active_account.id


def test_renew_records_the_origin_on_the_stage_event(
    client, world, db, headers_a, active_account, insurer_policy
):
    insurer_policy.case_file_id = active_account.id
    db.commit()
    new_id = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    ).json()["id"]

    from app.models.case_file import CaseFileStageEvent

    event = db.query(CaseFileStageEvent).filter_by(case_file_id=new_id).one()
    assert event.to_stage is CaseStage.RENEWAL_REVIEW
    assert event.meta["origin_case_file_id"] == active_account.id
    assert event.meta["prior_policy_ids"] == [insurer_policy.id]


# --- Guards -------------------------------------------------------------------

def test_renew_refuses_a_folder_that_is_still_in_the_market(
    client, world, db, headers_a
):
    case = make_case_file(db, world.a, stage=CaseStage.COMPARISON)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case.id}/renew", headers=headers_a, json=NEXT_PERIOD
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "renewal_not_allowed"
    assert detail["reason"] == "account_not_active"


def test_renew_twice_is_already_renewed(client, world, db, headers_a, active_account):
    first = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json={"period_start": "2027-10-15", "period_end": "2028-10-15"},
    )
    assert second.status_code == 422
    assert second.json()["detail"]["reason"] == "already_renewed"


def test_renew_into_an_occupied_vigencia_is_folder_exists(
    client, world, db, headers_a, active_account
):
    """Rule 5 — one open folder per (group, line, vigencia)."""
    twin = make_case_file(db, world.a, stage=CaseStage.INTAKE, n=2, placement_id=None)
    twin.account_group_id = active_account.account_group_id
    twin.period_start = date(2026, 10, 15)
    twin.period_end = date(2027, 10, 15)
    twin.period_label = "2026-2027"
    db.commit()

    response = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "folder_exists"
    assert detail["case_file_id"] == twin.id


def test_renew_on_another_tenants_folder_is_404(
    client, world, db, headers_b, active_account
):
    response = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_b,
        json=NEXT_PERIOD,
    )
    assert response.status_code == 404


# --- Document copies ----------------------------------------------------------

def test_copy_sections_writes_new_rows_on_new_keys(
    client, world, db, headers_a, active_account, local_media
):
    """Rule 8: a copy is never a second row pointing at the source's key."""
    source_doc = store_document(
        db,
        world.a,
        active_account,
        category=DocumentCategory.PROSPECT_REQUEST,
        section=CaseSection.ROOT_PROSPECT,
        root=local_media,
    )
    # A document in a section NOT requested must stay behind.
    store_document(
        db,
        world.a,
        active_account,
        category=DocumentCategory.POLICY,
        section=CaseSection.POLICY_FILE,
        root=local_media,
    )
    db.commit()

    body = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json={**NEXT_PERIOD, "copy_sections": ["root_prospect"]},
    ).json()

    copies = db.query(Document).filter_by(case_file_id=body["id"]).all()
    assert len(copies) == 1
    copy = copies[0]
    assert copy.id != source_doc.id
    assert copy.s3_key != source_doc.s3_key
    assert copy.category is DocumentCategory.PROSPECT_REQUEST
    assert copy.section is CaseSection.ROOT_PROSPECT
    # The bytes really followed the row to the new key.
    with open(os.path.join(str(local_media), copy.s3_key), "rb") as fh:
        assert fh.read() == b"%PDF-1.4 prior"


def test_renew_without_copy_sections_copies_nothing(
    client, world, db, headers_a, active_account, local_media
):
    store_document(
        db,
        world.a,
        active_account,
        category=DocumentCategory.PROSPECT_REQUEST,
        section=CaseSection.ROOT_PROSPECT,
        root=local_media,
    )
    db.commit()
    body = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json={**NEXT_PERIOD, "copy_sections": []},
    ).json()
    assert db.query(Document).filter_by(case_file_id=body["id"]).count() == 0


# --- POST /case-files with kind=renewal ---------------------------------------

def test_renewal_case_is_accepted_without_a_policy_id(
    client, world, db, headers_a, active_account
):
    """Relaxes the post-sale rule: a renewal is a sibling in time, not a child
    of a policy."""
    response = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={
            "kind": "renewal",
            "client_id": world.a.client.id,
            "origin_case_file_id": active_account.id,
            "period_start": "2026-10-15",
            "period_end": "2027-10-15",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["policy_id"] is None
    assert body["origin"] == "renewal"
    assert body["origin_case_file_id"] == active_account.id
    assert body["account_group_id"] == active_account.account_group_id


def test_renewal_case_without_an_origin_still_needs_a_policy(
    client, world, db, headers_a
):
    response = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={"kind": "renewal", "client_id": world.a.client.id},
    )
    assert response.status_code == 422
    assert "requires a policy_id" in response.json()["detail"]


# --- History ------------------------------------------------------------------

def test_history_returns_the_origin_chain_and_the_prior_context(
    client, world, db, headers_a, active_account, insurer_policy, local_media
):
    insurer_policy.case_file_id = active_account.id
    store_document(
        db,
        world.a,
        active_account,
        category=DocumentCategory.LOSS_HISTORY,
        section=CaseSection.SUBMISSION,
        root=local_media,
    )
    db.commit()

    new_id = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    ).json()["id"]

    body = client.get(f"{API}/case-files/{new_id}/history", headers=headers_a).json()
    assert [entry["case_file_id"] for entry in body["origin_chain"]] == [
        active_account.id,
        new_id,
    ]
    assert body["origin_chain"][0]["origin"] == "new"
    assert body["origin_chain"][1]["origin"] == "renewal"

    prior = body["prior"]
    assert prior["case_file_id"] == active_account.id
    assert len(prior["records"]["loss_history"]) == 1
    assert prior["records"]["amounts"] == []
    assert [policy["id"] for policy in prior["policies"]] == [insurer_policy.id]


# --- renews_policy_id ---------------------------------------------------------

def test_policy_on_a_renewal_folder_threads_renews_policy_id(
    client, world, db, headers_a, active_account, insurer_policy
):
    insurer_policy.case_file_id = active_account.id
    db.commit()
    renewal_id = client.post(
        f"{API}/case-files/{active_account.id}/renew",
        headers=headers_a,
        json=NEXT_PERIOD,
    ).json()["id"]

    created = client.post(
        f"{API}/policies",
        headers=headers_a,
        json={
            "insurer_id": world.insurer.id,
            "client_id": world.a.client.id,
            "case_file_id": renewal_id,
            "policy_number": "REN-2026-0001",
            "start_date": "2026-10-15",
            "end_date": "2027-10-15",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["renews_policy_id"] == insurer_policy.id


def test_policy_on_a_plain_account_folder_has_no_renewal_link(
    client, world, db, headers_a, active_account
):
    created = client.post(
        f"{API}/policies",
        headers=headers_a,
        json={
            "insurer_id": world.insurer.id,
            "client_id": world.a.client.id,
            "case_file_id": active_account.id,
            "policy_number": "NUE-2025-0001",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["renews_policy_id"] is None


# --- Lead conversion carries the group and the vigencia -----------------------

def test_lead_convert_forwards_the_group_and_the_period(client, world, db, headers_a):
    from app.models.sales_lead import SalesLead

    group = make_group(db, world.a, name="LA FAVORITA", slug="la-favorita")
    lead = SalesLead(
        broker_id=world.a.broker_id,
        name="La Favorita",
        insurance_line_id=world.a.line.id,
        account_group_id=group.id,
        period_start=date(2027, 1, 5),
        period_end=date(2028, 1, 5),
    )
    db.add(lead)
    db.commit()

    response = client.post(
        f"{API}/leads/{lead.id}/convert",
        headers=headers_a,
        json={"insured_rut": "76789935-1", "insured_legal_name": "La Favorita Ltda"},
    )
    assert response.status_code == 200, response.text
    case = db.get(CaseFile, response.json()["case_file_id"])
    assert case.account_group_id == group.id
    assert case.period_start == date(2027, 1, 5)
    assert case.period_end == date(2028, 1, 5)
    assert case.period_label == "2027-2028"
    assert case.reference.startswith("EXP-2027-")

    members = db.query(AccountClient).filter_by(case_file_id=case.id).all()
    assert len(members) == 1 and members[0].is_primary is True
    assert members[0].client_id == case.client_id
