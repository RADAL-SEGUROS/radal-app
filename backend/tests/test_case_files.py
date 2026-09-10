"""The expediente: CRUD, tenancy, the stage machine and its seven guards."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.case_file import CaseFileStageEvent
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    CaseFileKind,
    CaseOrigin,
    CaseSection,
    CaseStage,
    EntityType,
)
from app.models.placement import PlacementStatus
from app.models.proposal import Proposal, ProposalStatus
from app.services import case_files as machine
from tests.conftest import API, make_case_file


# --- The machine itself (pure, no HTTP) --------------------------------------

def test_account_chain_moves_forward_one_step():
    allowed = machine.ALLOWED_CASE_TRANSITIONS[CaseFileKind.ACCOUNT]
    assert CaseStage.PRE_UNDERWRITING in allowed[CaseStage.INTAKE]
    # The documented skip: no inspection required.
    assert CaseStage.TECHNICAL_BASIS in allowed[CaseStage.INTAKE]
    # One step back is always allowed.
    assert CaseStage.INTAKE in allowed[CaseStage.PRE_UNDERWRITING]
    # closed is reachable from anywhere and terminal.
    assert CaseStage.CLOSED in allowed[CaseStage.INTAKE]
    assert allowed[CaseStage.CLOSED] == ()


def test_two_steps_forward_is_not_allowed():
    allowed = machine.ALLOWED_CASE_TRANSITIONS[CaseFileKind.ACCOUNT]
    assert CaseStage.MARKET_SUBMISSION not in allowed[CaseStage.INTAKE]


@pytest.mark.parametrize(
    "kind, first",
    [
        (CaseFileKind.ACCOUNT, CaseStage.LEAD),
        (CaseFileKind.ENDORSEMENT, CaseStage.ENDORSEMENT_REQUESTED),
        (CaseFileKind.COLLECTION, CaseStage.COLLECTION_SCHEDULED),
        (CaseFileKind.CLAIM, CaseStage.CLAIM_REPORTED),
        (CaseFileKind.RENEWAL, CaseStage.RENEWAL_REVIEW),
    ],
)
def test_every_kind_has_its_own_chain(kind, first):
    assert machine.CASE_STAGE_FLOW[kind][0] is first
    assert machine.initial_stage_for(kind) is first


def test_collection_happy_path_skips_overdue():
    allowed = machine.ALLOWED_CASE_TRANSITIONS[CaseFileKind.COLLECTION]
    assert CaseStage.COLLECTION_SETTLED in allowed[CaseStage.COLLECTION_IN_PROGRESS]
    assert CaseStage.COLLECTION_SUSPENDED in allowed[CaseStage.COLLECTION_IN_PROGRESS]


# --- HTTP: creation, tenancy, transitions ------------------------------------

def test_create_case_file_assigns_reference_and_opens_timeline(client, world, headers_a):
    response = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={"kind": "account", "placement_id": world.a.placement.id},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reference"].startswith("EXP-")
    assert body["stage"] == "lead"
    assert body["sequence_no"] == 1 and body["version"] == 1
    assert body["client_id"] == world.a.client.id

    timeline = client.get(
        f"{API}/case-files/{body['id']}/timeline", headers=headers_a
    ).json()
    assert any(entry["kind"] == "stage" for entry in timeline["entries"])
    assert any(entry["kind"] == "activity" for entry in timeline["entries"])


def test_one_account_case_per_placement(client, world, headers_a):
    payload = {"kind": "account", "placement_id": world.a.placement.id}
    assert client.post(f"{API}/case-files", headers=headers_a, json=payload).status_code == 201
    second = client.post(f"{API}/case-files", headers=headers_a, json=payload)
    assert second.status_code == 422
    assert "already has account case file" in second.json()["detail"]


def test_post_sale_case_requires_a_policy(client, world, headers_a):
    response = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={"kind": "endorsement", "client_id": world.a.client.id},
    )
    assert response.status_code == 422
    assert "requires a policy_id" in response.json()["detail"]


def test_another_brokers_case_is_404_not_403(client, world, db, headers_b):
    case = make_case_file(db, world.a)
    db.commit()
    assert client.get(f"{API}/case-files/{case.id}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{API}/case-files/{case.id}", headers=headers_b, json={"title": "hijack"}
        ).status_code
        == 404
    )


def test_transition_writes_event_and_moves_the_placement(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.INTAKE)
    world.a.placement.status = PlacementStatus.DRAFT
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "pre_underwriting", "note": "inspección pedida"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "pre_underwriting"

    db.expire_all()
    events = db.query(CaseFileStageEvent).filter_by(case_file_id=case.id).all()
    assert [e.to_stage for e in events] == [CaseStage.PRE_UNDERWRITING]
    assert world.a.placement.status is PlacementStatus.PRE_UNDERWRITING


def test_transition_two_steps_forward_is_422(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.INTAKE)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "quotes_received"},
    )
    assert response.status_code == 422
    assert "No se puede mover" in response.json()["detail"]


def test_transitions_endpoint_reports_reasons(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.TECHNICAL_BASIS)
    db.commit()
    body = client.get(f"{API}/case-files/{case.id}/transitions", headers=headers_a).json()
    market = next(o for o in body["options"] if o["to_stage"] == "market_submission")
    assert market["allowed"] is False
    assert "bases técnicas" in market["reason"]


# --- The seven guards ---------------------------------------------------------

def test_market_submission_guard_needs_a_technical_brief(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.TECHNICAL_BASIS)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "market_submission"},
    )
    assert response.status_code == 422
    assert "bases técnicas" in response.json()["detail"]


def test_market_submission_guard_passes_with_brief_and_recipients(
    client, world, db, headers_a
):
    case = make_case_file(db, world.a, stage=CaseStage.TECHNICAL_BASIS)
    db.add(
        Document(
            broker_id=world.a.broker_id,
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            case_file_id=case.id,
            section=CaseSection.SUBMISSION,
            s3_key=f"documents/case_file/{case.id}/technical_brief-1.pdf",
            bucket="radal-test-bucket",
            original_name="01 bases tecnicas.pdf",
            category=DocumentCategory.TECHNICAL_BRIEF,
        )
    )
    world.a.quote.recipient_insurer_ids = [world.insurer.id]
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "market_submission"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "market_submission"


def test_comparison_guard_needs_a_confirmed_proposal(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.QUOTES_RECEIVED)
    proposal = Proposal(
        broker_id=world.a.broker_id,
        quote_request_id=world.a.quote.id,
        insurer_id=world.insurer.id,
        source_document_id=world.a.document.id,
        status=ProposalStatus.SUBMITTED,
        is_confirmed=False,
        total_premium_uf=Decimal("139.0000"),
    )
    db.add(proposal)
    db.commit()

    blocked = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "comparison"},
    )
    assert blocked.status_code == 422
    assert "cotización confirmada" in blocked.json()["detail"]

    proposal.is_confirmed = True
    db.commit()
    ok = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "comparison"},
    )
    assert ok.status_code == 200, ok.text


def test_proposal_issued_guard_needs_at_least_one_accepted(client, world, db, headers_a):
    """Zero winners is still a refusal — the wording moved from "exactly" to
    "at least" once a folder could wrap N placements (spec v3 §2.5)."""
    case = make_case_file(db, world.a, stage=CaseStage.INSURED_DECISION)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "proposal_issued"},
    )
    assert response.status_code == 422
    assert "al menos una cotización aceptada" in response.json()["detail"]


def test_proposal_issued_refuses_two_winners_on_one_placement(
    client, world, db, headers_a
):
    """One placement cannot award two carriers — that is a contradiction, not
    a second ramo."""
    case = make_case_file(db, world.a, stage=CaseStage.INSURED_DECISION)
    for _ in range(2):
        db.add(
            Proposal(
                broker_id=world.a.broker_id,
                quote_request_id=world.a.quote.id,
                insurer_id=world.insurer.id,
                source_document_id=world.a.document.id,
                status=ProposalStatus.ACCEPTED,
                is_confirmed=True,
                total_premium_uf=Decimal("139.0000"),
            )
        )
    db.commit()
    blocked = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "proposal_issued"},
    )
    assert blocked.status_code == 422
    assert "como máximo una cotización aceptada por placement" in blocked.json()["detail"]


def test_proposal_issued_passes_with_one_winner(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.INSURED_DECISION)
    db.add(
        Proposal(
            broker_id=world.a.broker_id,
            quote_request_id=world.a.quote.id,
            insurer_id=world.insurer.id,
            source_document_id=world.a.document.id,
            status=ProposalStatus.ACCEPTED,
            is_confirmed=True,
            total_premium_uf=Decimal("139.0000"),
        )
    )
    db.commit()
    ok = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "proposal_issued"},
    )
    assert ok.status_code == 200, ok.text


def test_claim_final_guard_needs_the_final_report(client, world, db, headers_a, insurer_policy):
    case = make_case_file(
        db,
        world.a,
        kind=CaseFileKind.CLAIM,
        stage=CaseStage.CLAIM_PRELIMINARY,
        placement_id=None,
        policy_id=insurer_policy.id,
        n=9,
    )
    db.commit()
    blocked = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "claim_final"},
    )
    assert blocked.status_code == 422
    assert "informe final" in blocked.json()["detail"]

    db.add(
        Document(
            broker_id=world.a.broker_id,
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            case_file_id=case.id,
            section=CaseSection.CLAIM,
            s3_key=f"documents/case_file/{case.id}/claim_final_report-1.pdf",
            bucket="radal-test-bucket",
            original_name="13 informe final.pdf",
            category=DocumentCategory.CLAIM_FINAL_REPORT,
        )
    )
    db.commit()
    ok = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "claim_final"},
    )
    assert ok.status_code == 200, ok.text


# --- Versions, deletion, documents, permissions --------------------------------

def test_version_supersedes_and_keeps_the_sequence(client, world, db, headers_a):
    case = make_case_file(db, world.a, stage=CaseStage.TECHNICAL_BASIS, sequence_no=2)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case.id}/versions", headers=headers_a, json={}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 2
    assert body["sequence_no"] == 2
    assert body["supersedes_case_file_id"] == case.id

    again = client.post(f"{API}/case-files/{case.id}/versions", headers=headers_a, json={})
    assert again.status_code == 422


def test_delete_refuses_a_case_with_documents(client, world, db, headers_a):
    case = make_case_file(db, world.a)
    db.add(
        Document(
            broker_id=world.a.broker_id,
            entity_type=EntityType.CASE_FILE,
            entity_id=case.id,
            case_file_id=case.id,
            s3_key=f"documents/case_file/{case.id}/other-1.pdf",
            bucket="radal-test-bucket",
            original_name="algo.pdf",
            category=DocumentCategory.OTHER,
        )
    )
    db.commit()
    response = client.delete(f"{API}/case-files/{case.id}", headers=headers_a)
    assert response.status_code == 422
    assert "close it instead" in response.json()["detail"]


def test_documents_are_grouped_by_section(client, world, db, headers_a):
    case = make_case_file(db, world.a)
    for code, category, section in (
        ("00A", DocumentCategory.PROSPECT_REQUEST, CaseSection.ROOT_PROSPECT),
        ("01", DocumentCategory.TECHNICAL_BRIEF, CaseSection.SUBMISSION),
        ("02", DocumentCategory.SUBMISSION_LETTER, CaseSection.SUBMISSION),
    ):
        db.add(
            Document(
                broker_id=world.a.broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=case.id,
                case_file_id=case.id,
                section=section,
                document_code=code,
                s3_key=f"documents/case_file/{case.id}/{category.value}-1.pdf",
                bucket="radal-test-bucket",
                original_name=f"{code} archivo.pdf",
                category=category,
            )
        )
    db.commit()

    body = client.get(f"{API}/case-files/{case.id}/documents", headers=headers_a).json()
    assert body["total"] == 3
    sections = {item["section"]: len(item["documents"]) for item in body["sections"]}
    assert sections == {"root_prospect": 1, "submission": 2}
    assert body["sections"][0]["documents"][0]["category_label"]

    filtered = client.get(
        f"{API}/documents?case_file_id={case.id}&section=submission", headers=headers_a
    ).json()
    assert filtered["total"] == 2


def test_summary_counts_by_kind_and_stage(client, world, db, headers_a):
    make_case_file(db, world.a, stage=CaseStage.INTAKE, n=1)
    make_case_file(
        db, world.a, stage=CaseStage.TECHNICAL_BASIS, placement_id=None, n=2
    )
    db.commit()
    body = client.get(f"{API}/case-files/summary", headers=headers_a).json()
    assert body["total"] == 2
    assert body["by_stage"]["intake"] == 1
    assert body["by_stage"]["technical_basis"] == 1
    assert body["by_kind"]["account"] == 2


def test_inspector_only_sees_inspected_cases(client, world, db, headers_a_inspector):
    """``partial`` on CaseFiles.View passes the gate; the router narrows the set."""
    from app.models.inspection import Inspection

    plain = make_case_file(db, world.a, n=1)
    inspected = make_case_file(db, world.a, placement_id=None, n=2)
    db.add(
        Inspection(
            broker_id=world.a.broker_id,
            asset_id=world.a.asset.id,
            case_file_id=inspected.id,
        )
    )
    db.commit()

    listing = client.get(f"{API}/case-files", headers=headers_a_inspector).json()
    assert [item["id"] for item in listing["items"]] == [inspected.id]
    assert client.get(f"{API}/case-files/{plain.id}", headers=headers_a_inspector).status_code == 404
    assert (
        client.get(f"{API}/case-files/{inspected.id}", headers=headers_a_inspector).status_code
        == 200
    )


def test_inspector_cannot_reach_leads_endorsements_or_collections(
    client, headers_a_inspector
):
    """Acceptance criterion 6: the sidebar is server-filtered, and so is the API."""
    for path in ("/leads", "/endorsements", "/collections"):
        assert client.get(f"{API}{path}", headers=headers_a_inspector).status_code == 403


def test_notes_and_activities_are_real(client, world, db, headers_a):
    case = make_case_file(db, world.a)
    db.commit()
    created = client.post(
        f"{API}/notes",
        headers=headers_a,
        json={
            "entity_type": "case_file",
            "entity_id": case.id,
            "body": "Falta la ficha 00B",
            "is_internal": True,
            "follow_up_on": str(date(2026, 9, 1)),
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["follow_up_on"] == "2026-09-01"

    listing = client.get(
        f"{API}/notes?entity_type=case_file&entity_id={case.id}", headers=headers_a
    ).json()
    assert listing["total"] == 1

    activities = client.get(
        f"{API}/activities?entity_type=case_file&entity_id={case.id}", headers=headers_a
    ).json()
    assert any(item["action"] == "note.created" for item in activities["items"])


def test_notes_on_another_tenants_case_are_404(client, world, db, headers_b):
    case = make_case_file(db, world.a)
    db.commit()
    response = client.post(
        f"{API}/notes",
        headers=headers_b,
        json={"entity_type": "case_file", "entity_id": case.id, "body": "x"},
    )
    assert response.status_code == 404


# --- Leads ---------------------------------------------------------------------

def test_lead_rut_is_optional_but_validated(client, headers_a):
    ok = client.post(
        f"{API}/leads", headers=headers_a, json={"name": "Panadería sin RUT"}
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["rut"] is None

    bad = client.post(
        f"{API}/leads", headers=headers_a, json={"name": "Con RUT malo", "rut": "76827029-0"}
    )
    assert bad.status_code == 422


def test_lead_convert_builds_the_whole_chain(client, world, headers_a):
    lead = client.post(
        f"{API}/leads",
        headers=headers_a,
        json={"name": "Coccolino", "insurance_line_id": world.a.line.id},
    ).json()

    response = client.post(
        f"{API}/leads/{lead['id']}/convert",
        headers=headers_a,
        json={
            "insured_rut": "76827029-5",
            "insured_legal_name": "Coccolino Pastelería SpA",
            "insurance_line_id": world.a.line.id,
            "period_start": "2026-08-01",
            "period_end": "2027-08-01",
            "asset": {"name": "Planta Quilicura", "asset_type": "property"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lead"]["status"] == "converted"
    assert body["case_file_id"] and body["placement_id"] and body["client_id"]

    case = client.get(f"{API}/case-files/{body['case_file_id']}", headers=headers_a).json()
    assert case["stage"] == "intake"
    assert case["period_start"] == "2026-08-01"
    assert case["period_end"] == "2027-08-01"


def test_lead_convert_requires_a_vigencia(client, world, headers_a):
    """Rule 1 — the vigencia IS the folder, on every door that opens one.

    Conversion is the second creation path for ``case_file(kind=account)``. If
    it could open a period-less folder, that folder would sit outside every
    group tree (which groups by period) and outside ``/reperiod`` (which needs a
    period to move), with no way back.
    """
    lead = client.post(
        f"{API}/leads",
        headers=headers_a,
        json={"name": "Sin vigencia", "insurance_line_id": world.a.line.id},
    ).json()

    response = client.post(
        f"{API}/leads/{lead['id']}/convert",
        headers=headers_a,
        json={
            "insured_rut": "76827029-5",
            "insured_legal_name": "Coccolino Pastelería SpA",
            "insurance_line_id": world.a.line.id,
        },
    )
    assert response.status_code == 422, response.text
    assert "vigencia" in response.json()["detail"]


def test_lead_convert_refuses_a_duplicate_folder(client, world, headers_a):
    """Rule 5 — one OPEN folder per (group, line, vigencia), on every door.

    The uniqueness key is scoped to a GROUP (three of its five columns are
    nullable, so a group-less folder has no key to collide on — exactly as
    ``POST /case-files`` behaves). This walks the real path: convert once, file
    the resulting RUT under a group, then convert the same RUT onto the same
    line and dates. Attaching the client must also ADOPT its existing folder,
    or the second conversion would see an empty group and open a twin.
    """
    payload = {
        "insured_rut": "76827029-5",
        "insured_legal_name": "Coccolino Pastelería SpA",
        "insurance_line_id": world.a.line.id,
        "period_start": "2026-08-01",
        "period_end": "2027-08-01",
    }
    first_lead = client.post(
        f"{API}/leads", headers=headers_a, json={"name": "Coccolino 1"}
    ).json()
    first = client.post(
        f"{API}/leads/{first_lead['id']}/convert", headers=headers_a, json=payload
    )
    assert first.status_code == 200, first.text

    group = client.post(
        f"{API}/account-groups", headers=headers_a, json={"name": "COCCOLINO"}
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    attach = client.post(
        f"{API}/account-groups/{group_id}/clients",
        headers=headers_a,
        json={"client_id": first.json()["client_id"]},
    )
    assert attach.status_code == 200, attach.text

    # The back-fill: the folder that existed BEFORE the group did is now inside it.
    adopted = client.get(
        f"{API}/case-files/{first.json()['case_file_id']}", headers=headers_a
    ).json()
    assert adopted["account_group_id"] == group_id

    second_lead = client.post(
        f"{API}/leads", headers=headers_a, json={"name": "Coccolino 2"}
    ).json()
    second = client.post(
        f"{API}/leads/{second_lead['id']}/convert", headers=headers_a, json=payload
    )
    assert second.status_code == 422, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "folder_exists"
    assert detail["case_file_id"] == first.json()["case_file_id"]


# =============================================================================
# Groups & accounts (spec v3 §2.5, §4.3) — the vigencia IS the folder
# =============================================================================

def make_group(db, tenant, name="Grupo Alfa", slug="grupo-alfa"):
    """A broker-private group. Deliberately local: ``conftest`` is shared."""
    from app.models.account_group import AccountGroup

    group = AccountGroup(broker_id=tenant.broker_id, name=name, slug=slug)
    db.add(group)
    db.flush()
    return group


def make_client(db, tenant, rut, legal_name, group=None):
    """A second RUT inside the tenant, optionally in ``group``."""
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


def test_account_case_requires_a_vigencia(client, world, db, headers_a):
    """The period is not decoration: an account folder without one cannot be
    grouped, sorted or renewed."""
    response = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={"kind": "account", "client_id": world.a.client.id},
    )
    assert response.status_code == 422
    assert "period_start and period_end are required" in response.json()["detail"]


def test_create_stores_period_label_and_group(client, world, db, headers_a):
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    db.commit()

    response = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={
            "kind": "account",
            "client_id": world.a.client.id,
            "period_start": "2026-04-01",
            "period_end": "2027-04-01",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # The group defaults from the client; the label is derived, not typed.
    assert body["account_group_id"] == group.id
    assert body["account_group_name"] == group.name
    assert body["period_label"] == "2026-2027"
    assert body["origin"] == "new"
    assert body["period_locked"] is False
    assert body["client_ids"] == [world.a.client.id]


def test_folder_uniqueness_is_422_with_the_existing_id(client, world, db, headers_a):
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    db.commit()
    payload = {
        "kind": "account",
        "client_id": world.a.client.id,
        "insurance_line_id": world.a.line.id,
        "period_start": "2026-04-01",
        "period_end": "2027-04-01",
    }
    first = client.post(f"{API}/case-files", headers=headers_a, json=payload)
    assert first.status_code == 201, first.text

    second = client.post(f"{API}/case-files", headers=headers_a, json=payload)
    assert second.status_code == 422
    detail = second.json()["detail"]
    assert detail["code"] == "folder_exists"
    assert detail["case_file_id"] == first.json()["id"]


def test_two_lines_share_one_period_label_with_different_dates(
    client, world, db, headers_a
):
    """Vigencia is a LABEL over per-account full dates (spec §1): Coccolino runs
    Vehículos ago-ago and Incendio abr-abr, both under 2026-2027."""
    from app.models.insurance_line import InsuranceLine

    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    other = InsuranceLine(broker_id=world.a.broker_id, name="Vehículos Alfa")
    db.add(other)
    db.commit()

    for line_id, start, end in (
        (world.a.line.id, "2026-04-01", "2027-04-01"),
        (other.id, "2026-08-01", "2027-08-01"),
    ):
        created = client.post(
            f"{API}/case-files",
            headers=headers_a,
            json={
                "kind": "account",
                "client_id": world.a.client.id,
                "insurance_line_id": line_id,
                "period_start": start,
                "period_end": end,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["period_label"] == "2026-2027"

    listed = client.get(
        f"{API}/case-files",
        headers=headers_a,
        params={"account_group_id": group.id, "period_label": "2026-2027"},
    ).json()
    assert listed["total"] == 2


def test_client_ids_become_account_members(client, world, db, headers_a):
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    second = make_client(db, world.a, "76555555-5", "Viña Hermana SpA", group)
    db.commit()

    created = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={
            "kind": "account",
            "client_id": world.a.client.id,
            "period_start": "2026-04-01",
            "period_end": "2027-04-01",
            "client_ids": [second.id],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["client_ids"][0] == world.a.client.id  # contratante first
    assert second.id in body["client_ids"]

    from app.models.account_client import AccountClient

    rows = db.query(AccountClient).filter_by(case_file_id=body["id"]).all()
    assert {row.client_id for row in rows} == {world.a.client.id, second.id}
    assert [row.client_id for row in rows if row.is_primary] == [world.a.client.id]


def test_client_not_in_group_is_422(client, world, db, headers_a):
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    stranger = make_client(db, world.a, "76666666-K", "Ajena SpA", None)
    db.commit()

    created = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={
            "kind": "account",
            "client_id": world.a.client.id,
            "period_start": "2026-04-01",
            "period_end": "2027-04-01",
        },
    )
    case_id = created.json()["id"]
    refused = client.post(
        f"{API}/case-files/{case_id}/clients",
        headers=headers_a,
        json={"client_id": stranger.id},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "client_not_in_group"


def test_members_can_be_attached_and_detached_but_not_the_contratante(
    client, world, db, headers_a
):
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    second = make_client(db, world.a, "76777777-4", "Filial SpA", group)
    db.commit()

    case_id = client.post(
        f"{API}/case-files",
        headers=headers_a,
        json={
            "kind": "account",
            "client_id": world.a.client.id,
            "period_start": "2026-04-01",
            "period_end": "2027-04-01",
        },
    ).json()["id"]

    added = client.post(
        f"{API}/case-files/{case_id}/clients",
        headers=headers_a,
        json={"client_id": second.id, "role": "insured"},
    )
    assert added.status_code == 201, added.text
    assert second.id in added.json()["client_ids"]

    assert (
        client.delete(
            f"{API}/case-files/{case_id}/clients/{world.a.client.id}", headers=headers_a
        ).status_code
        == 422
    )
    assert (
        client.delete(
            f"{API}/case-files/{case_id}/clients/{second.id}", headers=headers_a
        ).status_code
        == 204
    )


def test_period_is_editable_at_intake_and_syncs_to_the_folder(
    client, world, db, headers_a
):
    case = make_case_file(db, world.a, stage=CaseStage.INTAKE)
    case.period_start = date(2026, 1, 1)
    case.period_end = date(2027, 1, 1)
    case.period_label = "2026-2027"
    world.a.placement.case_file_id = case.id
    db.commit()

    patched = client.patch(
        f"{API}/placements/{world.a.placement.id}",
        headers=headers_a,
        json={"period_start": "2026-03-01", "period_end": "2027-03-01"},
    )
    assert patched.status_code == 200, patched.text
    db.refresh(case)
    assert case.period_start == date(2026, 3, 1)
    assert case.period_end == date(2027, 3, 1)
    assert case.period_label == "2026-2027"


def test_unlocked_period_edit_still_respects_folder_uniqueness(
    client, world, db, headers_a
):
    """The one open period-mutation path must not become the way around rule 5.

    While a folder is still at ``intake`` its dates may be edited through the
    placement, and the edit SYNCS up to the folder. Since ``period_start`` and
    ``period_end`` are two of the five columns of the uniqueness key, that sync
    is the only way to slide one folder onto another's key — so it answers the
    same ``folder_exists`` 422 the create door does.
    """
    group = make_group(db, world.a, name="Grupo Sync", slug="grupo-sync")
    from app.models.insurance_line import InsuranceLine

    taken = make_case_file(db, world.a, stage=CaseStage.INTAKE, n=21)
    # It occupies the target vigencia but wraps no placement, so the sync below
    # resolves the OTHER folder and the collision is a real one.
    taken.placement_id = None
    taken.account_group_id = group.id
    taken.period_start = date(2027, 6, 1)
    taken.period_end = date(2028, 6, 1)
    taken.period_label = "2027-2028"

    editable = make_case_file(db, world.a, stage=CaseStage.INTAKE, n=22)
    editable.account_group_id = group.id
    editable.period_start = date(2026, 6, 1)
    editable.period_end = date(2027, 6, 1)
    editable.period_label = "2026-2027"
    world.a.placement.case_file_id = editable.id
    world.a.placement.period_start = date(2026, 6, 1)
    world.a.placement.period_end = date(2027, 6, 1)
    db.commit()

    clash = client.patch(
        f"{API}/placements/{world.a.placement.id}",
        headers=headers_a,
        json={"period_start": "2027-06-01", "period_end": "2028-06-01"},
    )
    assert clash.status_code == 422, clash.text
    assert clash.json()["detail"]["code"] == "folder_exists"
    assert clash.json()["detail"]["case_file_id"] == taken.id

    db.refresh(editable)
    assert editable.period_start == date(2026, 6, 1)

    # A free vigencia still syncs: the guard is about collisions, not locking.
    ok = client.patch(
        f"{API}/placements/{world.a.placement.id}",
        headers=headers_a,
        json={"period_start": "2026-07-01", "period_end": "2027-07-01"},
    )
    assert ok.status_code == 200, ok.text
    db.refresh(editable)
    assert editable.period_start == date(2026, 7, 1)
    assert editable.period_label == "2026-2027"


def test_period_is_locked_once_the_folder_moved_past_intake(
    client, world, db, headers_a
):
    """Rule 1: a vigencia date change creates a new folder, never an edit."""
    case = make_case_file(db, world.a, stage=CaseStage.INTAKE)
    world.a.placement.case_file_id = case.id
    world.a.placement.status = PlacementStatus.DRAFT
    db.commit()

    moved = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "technical_basis"},
    )
    assert moved.status_code == 200, moved.text

    refused = client.patch(
        f"{API}/placements/{world.a.placement.id}",
        headers=headers_a,
        json={"period_end": "2028-01-01"},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["code"] == "period_locked"
    assert detail["case_file_id"] == case.id
    # A non-period edit on the same locked folder still goes through.
    assert (
        client.patch(
            f"{API}/placements/{world.a.placement.id}",
            headers=headers_a,
            json={"notes": "sin cambio de vigencia"},
        ).status_code
        == 200
    )
    assert client.get(f"{API}/case-files/{case.id}", headers=headers_a).json()[
        "period_locked"
    ] is True


def test_transition_fans_out_over_every_placement_of_the_folder(
    client, world, db, headers_a
):
    """An account wraps N placements; the status move is all of them or none."""
    from app.models.placement import Placement

    case = make_case_file(db, world.a, stage=CaseStage.INTAKE)
    world.a.placement.case_file_id = case.id
    world.a.placement.status = PlacementStatus.DRAFT
    second = Placement(
        broker_id=world.a.broker_id,
        client_id=world.a.client.id,
        asset_id=world.a.asset.id,
        insurance_line_id=world.a.line.id,
        status=PlacementStatus.DRAFT,
        case_file_id=case.id,
    )
    db.add(second)
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/transition",
        headers=headers_a,
        json={"to_stage": "pre_underwriting"},
    )
    assert response.status_code == 200, response.text
    db.refresh(world.a.placement)
    db.refresh(second)
    assert world.a.placement.status is PlacementStatus.PRE_UNDERWRITING
    assert second.status is PlacementStatus.PRE_UNDERWRITING


def test_placements_can_be_filtered_by_group(client, world, db, headers_a):
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    db.commit()
    listed = client.get(
        f"{API}/placements", headers=headers_a, params={"account_group_id": group.id}
    ).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == world.a.placement.id

    empty = client.get(
        f"{API}/placements", headers=headers_a, params={"account_group_id": group.id + 999}
    ).json()
    assert empty["total"] == 0


@pytest.fixture()
def local_media(tmp_path, monkeypatch):
    """Point the document store at a temp dir (``backend/.env`` says s3)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    return tmp_path


def store_case_document(db, tenant, case, *, category, section, root, body=b"%PDF-1.4 x"):
    """A filed document whose bytes really exist, so a copy can read them."""
    import os

    key = f"documents/case_file/{case.id}/{category.value}-{case.id}.pdf"
    path = os.path.join(str(root), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
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


def test_reperiod_closes_the_source_and_opens_a_sibling(
    client, world, db, headers_a, local_media
):
    """The documents already filed quote the old dates — so the honest move is
    a sibling folder, not an in-place edit."""
    group = make_group(db, world.a)
    world.a.client.account_group_id = group.id
    case = make_case_file(db, world.a, stage=CaseStage.PRE_UNDERWRITING)
    case.account_group_id = group.id
    case.period_start = date(2026, 1, 1)
    case.period_end = date(2027, 1, 1)
    case.period_label = "2026-2027"
    world.a.placement.case_file_id = case.id
    db.flush()
    store_case_document(
        db,
        world.a,
        case,
        category=DocumentCategory.INSURED_VALUES_SCHEDULE,
        section=CaseSection.SUBMISSION,
        root=local_media,
    )
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/reperiod",
        headers=headers_a,
        json={
            "period_start": "2026-03-01",
            "period_end": "2027-03-01",
            "reason": "la compañía movió el inicio de vigencia",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin"] == "period_change"
    assert body["origin_case_file_id"] == case.id
    assert body["period_start"] == "2026-03-01"
    assert body["account_group_id"] == group.id
    assert body["reference"].startswith("EXP-2026-")

    db.refresh(case)
    assert case.status.value == "closed"
    assert case.meta["closed_reason"] == "period_change"
    assert case.stage is CaseStage.CLOSED

    # The antecedentes travelled as NEW rows on NEW keys (rule 8).
    source_keys = {
        row.s3_key for row in db.query(Document).filter_by(case_file_id=case.id).all()
    }
    copied = db.query(Document).filter_by(case_file_id=body["id"]).all()
    assert len(copied) == 1
    assert copied[0].s3_key not in source_keys
    assert copied[0].category is DocumentCategory.INSURED_VALUES_SCHEDULE
    assert copied[0].section is CaseSection.SUBMISSION

    # And the placements were cloned onto the new vigencia, left as drafts.
    from app.models.placement import Placement

    clones = db.query(Placement).filter_by(case_file_id=body["id"]).all()
    assert len(clones) == 1
    assert clones[0].id != world.a.placement.id
    assert clones[0].period_start == date(2026, 3, 1)
    assert clones[0].status is PlacementStatus.DRAFT


def test_reperiod_refuses_a_closed_folder(client, world, db, headers_a):
    from app.models.enums import CaseFileStatus

    case = make_case_file(db, world.a, stage=CaseStage.INTAKE, status=CaseFileStatus.CLOSED)
    db.commit()
    response = client.post(
        f"{API}/case-files/{case.id}/reperiod",
        headers=headers_a,
        json={
            "period_start": "2026-03-01",
            "period_end": "2027-03-01",
            "reason": "tarde",
        },
    )
    assert response.status_code == 422
    assert "not open" in response.json()["detail"]


def test_history_of_a_new_folder_has_no_prior(client, world, db, headers_a):
    case = make_case_file(db, world.a)
    db.commit()
    body = client.get(f"{API}/case-files/{case.id}/history", headers=headers_a).json()
    assert body["prior"] is None
    assert [entry["case_file_id"] for entry in body["origin_chain"]] == [case.id]


def test_version_carries_the_group_and_the_vigencia(client, world, db, headers_a):
    """A version is the SAME folder reworked, so it stays in the same drawer.

    Dropping ``account_group_id``/``period_*`` on the clone would file the
    rework outside every group view — which all filter on that column — while
    the source it supersedes is closed. The account would simply vanish from
    the tree the moment someone reworked it.
    """
    group = make_group(db, world.a, name="Grupo Versión", slug="grupo-version")
    case = make_case_file(db, world.a, stage=CaseStage.TECHNICAL_BASIS, n=10)
    case.account_group_id = group.id
    case.period_start = date(2026, 3, 1)
    case.period_end = date(2027, 3, 1)
    case.period_label = "2026-2027"
    case.origin = CaseOrigin.NEW
    db.commit()

    body = client.post(
        f"{API}/case-files/{case.id}/versions", headers=headers_a, json={}
    ).json()
    assert body["account_group_id"] == group.id
    assert body["period_start"] == "2026-03-01"
    assert body["period_end"] == "2027-03-01"
    assert body["period_label"] == "2026-2027"
    assert body["origin"] == "new"


def test_patch_cannot_move_a_folder_onto_another_folders_line(
    client, world, db, headers_a
):
    """Rule 5 has one key, so it needs one guard on every door that touches it.

    ``insurance_line_id`` is part of that key, and PATCH is the only place it
    can change. Without the check, patching the line is a silent way to end up
    with two open folders for the same group, line and vigencia — exactly the
    hybrid state the create door refuses.
    """
    from app.models.insurance_line import InsuranceLine

    group = make_group(db, world.a, name="Grupo Patch", slug="grupo-patch")
    other_line = InsuranceLine(broker_id=world.a.broker_id, name="Responsabilidad Civil")
    db.add(other_line)
    db.flush()

    common = dict(
        account_group_id=group.id,
        period_start=date(2026, 5, 1),
        period_end=date(2027, 5, 1),
        period_label="2026-2027",
    )
    taken = make_case_file(db, world.a, stage=CaseStage.INTAKE, n=11)
    for field, value in common.items():
        setattr(taken, field, value)
    taken.insurance_line_id = other_line.id

    mover = make_case_file(db, world.a, stage=CaseStage.INTAKE, n=12)
    for field, value in common.items():
        setattr(mover, field, value)
    db.commit()

    clash = client.patch(
        f"{API}/case-files/{mover.id}",
        headers=headers_a,
        json={"insurance_line_id": other_line.id},
    )
    assert clash.status_code == 422, clash.text
    assert clash.json()["detail"]["code"] == "folder_exists"
    assert clash.json()["detail"]["case_file_id"] == taken.id

    # A free line still moves: the guard is about collisions, not about locking.
    free_line = InsuranceLine(broker_id=world.a.broker_id, name="Transporte")
    db.add(free_line)
    db.commit()
    ok = client.patch(
        f"{API}/case-files/{mover.id}",
        headers=headers_a,
        json={"insurance_line_id": free_line.id},
    )
    assert ok.status_code == 200, ok.text
