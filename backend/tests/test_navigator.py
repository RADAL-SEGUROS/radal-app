"""``GET /navigator`` and ``GET /account-groups/{id}/tree`` — the two sidebars.

The corpus shapes every assertion here (spec §3.3): Grupo Viña Indómita is ONE
group over TWO RUTs and two vigencias, and Coccolino proves the modelling
decision — *vigencia is a label over per-account full dates*, not a shared range
(Vehículos ago→ago and Incendio abr→abr both live under "2026-2027").
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.account_client import AccountClient, AccountClientRole
from app.models.asset import Asset
from app.models.case_file import CaseFile, CaseFileStageEvent, CasePack
from app.models.client import Client
from app.models.document import DocumentCategory
from app.models.endorsement import Endorsement, EndorsementKind, EndorsementStatus
from app.models.enums import (
    CaseFileKind,
    CaseFileStatus,
    CaseOrigin,
    CaseStage,
    PackKind,
    PackStatus,
)
from app.models.insurance_line import InsuranceLine
from app.models.insured import Insured
from app.models.placement import Placement, PlacementStatus
from app.schemas.navigator import RECORD_FOLDERS
from app.services import navigator as navigator_service
from tests.conftest import API, make_case_file, make_policy
from tests.test_account_groups import (  # noqa: F401  (the local_media fixture)
    attach_document,
    local_media,
    make_account,
    make_group,
)


# =============================================================================
# GET /navigator — the main rail
# =============================================================================

def test_navigator_is_empty_but_well_shaped_for_a_new_tenant(client, world, headers_a):
    body = client.get(f"{API}/navigator", headers=headers_a).json()
    assert body["groups"] == []
    assert body["recent"] == []
    # The starter client of the conftest world belongs to no group yet.
    assert body["ungrouped_clients_count"] == 1
    assert [folder["key"] for folder in body["record_folders"]] == [
        "amounts",
        "loss_history",
        "report",
        "questionnaire",
        "slip",
    ]


def test_record_folders_are_server_owned_and_every_category_is_real(client, headers_a):
    """The UI never hardcodes the ANTECEDENTES mapping (spec §4.2)."""
    body = client.get(f"{API}/navigator", headers=headers_a).json()
    valid = {category.value for category in DocumentCategory}
    seen: set[str] = set()
    for folder in body["record_folders"]:
        assert folder["categories"], folder["key"]
        for category in folder["categories"]:
            assert category in valid
            # A category belongs to exactly one folder, or a count would double.
            assert category not in seen
            seen.add(category)
    assert len(body["record_folders"]) == len(RECORD_FOLDERS)


def test_navigator_groups_carry_their_vigencias_newest_first(
    client, world, db, headers_a
):
    group = make_group(db, world.a, "Grupo Viña Indómita")
    make_account(db, world.a, group, n=1)  # 2026-2027
    older = make_account(
        db,
        world.a,
        group,
        n=2,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
    )
    older.status = CaseFileStatus.CLOSED
    db.commit()

    body = client.get(f"{API}/navigator", headers=headers_a).json()
    assert len(body["groups"]) == 1
    node = body["groups"][0]
    assert node["slug"] == "grupo-vina-indomita"
    assert node["accounts_count"] == 2
    assert node["open_count"] == 1
    assert node["latest_period_label"] == "2026-2027"
    assert node["latest_period_start"] == "2026-10-15"
    assert [period["label"] for period in node["periods"]] == ["2026-2027", "2025-2026"]
    assert [period["accounts_count"] for period in node["periods"]] == [1, 1]


def test_navigator_counts_match_the_database(client, world, db, headers_a):
    group = make_group(db, world.a)
    for n in range(1, 4):
        make_account(db, world.a, group, n=n)
    db.commit()

    node = client.get(f"{API}/navigator", headers=headers_a).json()["groups"][0]
    expected = db.query(CaseFile).filter(
        CaseFile.account_group_id == group.id,
        CaseFile.kind.in_(list(navigator_service.ACCOUNT_KINDS)),
    ).count()
    assert node["accounts_count"] == expected == 3


def test_navigator_recent_lists_the_five_most_recent_account_folders(
    client, world, db, headers_a
):
    group = make_group(db, world.a)
    for n in range(1, 8):
        make_account(db, world.a, group, n=n)
    db.commit()

    body = client.get(f"{API}/navigator", headers=headers_a).json()
    assert len(body["recent"]) == 5
    first = body["recent"][0]
    assert first["group_id"] == group.id
    assert first["period_label"] == "2026-2027"
    assert first["line_name"] == world.a.line.name
    assert first["stage"] == CaseStage.INTAKE.value
    assert first["origin"] == CaseOrigin.NEW.value


def test_navigator_shows_at_most_eight_groups(client, world, db, headers_a):
    for index in range(10):
        group = make_group(db, world.a, f"Grupo {index}")
        make_account(
            db,
            world.a,
            group,
            n=index + 1,
            period_start=date(2020 + index, 1, 1),
            period_end=date(2021 + index, 1, 1),
        )
    db.commit()

    body = client.get(f"{API}/navigator", headers=headers_a).json()
    assert len(body["groups"]) == navigator_service.MAX_NAVIGATOR_GROUPS == 8
    # Ordered by the latest vigencia, descending.
    starts = [group["latest_period_start"] for group in body["groups"]]
    assert starts == sorted(starts, reverse=True)
    assert body["groups"][0]["name"] == "Grupo 9"


def test_navigator_stays_within_its_query_budget(client, world, db, headers_a):
    """The rail is on every screen: it must not fan out per group (spec §4.2)."""
    from sqlalchemy import event

    from app.db.session import engine

    for index in range(6):
        group = make_group(db, world.a, f"Grupo {index}")
        make_account(db, world.a, group, n=index + 1)
    db.commit()

    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        response = client.get(f"{API}/navigator", headers=headers_a)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert response.status_code == 200
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    # 4 for the payload (groups, per-vigencia aggregate, recent, ungrouped) plus
    # the auth round trip; a per-group query would blow straight past this.
    assert len(selects) <= 8, selects


def test_navigator_never_leaks_another_tenants_groups(client, world, db, headers_a):
    mine = make_group(db, world.a, "La Favorita")
    theirs = make_group(db, world.b, "Coccolino")
    make_account(db, world.a, mine, n=1)
    make_account(db, world.b, theirs, n=1)
    db.commit()

    body = client.get(f"{API}/navigator", headers=headers_a).json()
    assert [group["slug"] for group in body["groups"]] == ["la-favorita"]
    assert all(case["group_id"] == mine.id for case in body["recent"])


def test_navigator_narrows_for_the_inspector(client, world, db, headers_a_inspector):
    from app.models.inspection import Inspection

    visible = make_group(db, world.a, "La Favorita")
    hidden = make_group(db, world.a, "Coccolino")
    inspected = make_account(db, world.a, visible, n=1)
    make_account(db, world.a, hidden, n=2)
    db.add(
        Inspection(
            broker_id=world.a.broker_id,
            asset_id=world.a.asset.id,
            case_file_id=inspected.id,
            version=1,
        )
    )
    db.commit()

    body = client.get(f"{API}/navigator", headers=headers_a_inspector).json()
    assert [group["slug"] for group in body["groups"]] == ["la-favorita"]
    assert [case["case_file_id"] for case in body["recent"]] == [inspected.id]


# =============================================================================
# GET /account-groups/{id}/tree — the contextual rail
# =============================================================================

def test_tree_is_grouped_by_vigencia_with_the_latest_first(
    client, world, db, headers_a
):
    group = make_group(db, world.a, "Grupo Viña Indómita")
    current = make_account(db, world.a, group, n=1)
    previous = make_account(
        db,
        world.a,
        group,
        n=2,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
        stage=CaseStage.ACTIVE,
    )
    world.a.client.account_group_id = group.id
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).json()
    assert body["group"]["id"] == group.id
    assert [c["id"] for c in body["group"]["clients"]] == [world.a.client.id]
    assert [p["label"] for p in body["periods"]] == ["2026-2027", "2025-2026"]
    assert body["periods"][0]["is_latest"] is True
    assert body["periods"][1]["is_latest"] is False
    # Full dates travel with every vigencia — the label alone never decides.
    assert body["periods"][0]["start"] == "2026-10-15"
    assert body["periods"][1]["end"] == "2026-10-15"
    assert body["periods"][0]["lines"][0]["account"]["case_file_id"] == current.id
    assert body["periods"][1]["lines"][0]["account"]["case_file_id"] == previous.id


def test_one_group_two_ruts_two_vigencias(client, world, db, headers_a):
    """Grupo Viña Indómita: one group, two clients, two folders (spec §3.3)."""
    group = make_group(db, world.a, "Grupo Viña Indómita")
    second_insured = Insured(rut="96688830-K", legal_name="Viña Santa Alicia SpA")
    db.add(second_insured)
    db.flush()
    second_client = Client(
        broker_id=world.a.broker_id,
        insured_id=second_insured.id,
        account_group_id=group.id,
    )
    world.a.client.account_group_id = group.id
    db.add(second_client)
    db.flush()

    first = make_account(db, world.a, group, n=1)
    second = make_account(
        db,
        world.a,
        group,
        n=2,
        client_id=second_client.id,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
    )
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).json()
    assert len(body["group"]["clients"]) == 2
    assert len(body["periods"]) == 2
    ids = {
        period["lines"][0]["account"]["case_file_id"]: period["label"]
        for period in body["periods"]
    }
    assert ids == {first.id: "2026-2027", second.id: "2025-2026"}


def test_two_lines_with_different_dates_share_one_vigencia_label(
    client, world, db, headers_a
):
    """Coccolino: Vehículos ago→ago and Incendio abr→abr, both "2026-2027"."""
    group = make_group(db, world.a, "Coccolino")
    fleet = InsuranceLine(broker_id=world.a.broker_id, name="Vehículos Motorizados")
    db.add(fleet)
    db.flush()

    vehicles = make_account(
        db,
        world.a,
        group,
        n=1,
        insurance_line_id=fleet.id,
        period_start=date(2026, 8, 1),
        period_end=date(2027, 8, 1),
    )
    fire = make_account(
        db,
        world.a,
        group,
        n=2,
        period_start=date(2026, 4, 1),
        period_end=date(2027, 4, 1),
    )
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).json()
    assert len(body["periods"]) == 1
    period = body["periods"][0]
    assert period["label"] == "2026-2027"
    # The label groups them; the full dates keep them apart.
    dates = {
        line["account"]["case_file_id"]: (
            line["account"]["period_start"],
            line["account"]["period_end"],
        )
        for line in period["lines"]
    }
    assert dates[vehicles.id] == ("2026-08-01", "2027-08-01")
    assert dates[fire.id] == ("2026-04-01", "2027-04-01")
    assert period["start"] == "2026-04-01" and period["end"] == "2027-08-01"


def test_account_node_carries_every_record_folder_key(
    client, world, db, headers_a, local_media
):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group)
    attach_document(
        db, world.a, case, code="00C", category=DocumentCategory.INSURED_VALUES_SCHEDULE
    )
    attach_document(
        db, world.a, case, code="00D", category=DocumentCategory.LOSS_HISTORY
    )
    attach_document(
        db, world.a, case, code="01", category=DocumentCategory.TECHNICAL_BRIEF
    )
    db.add(
        CasePack(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            kind=PackKind.SUBMISSION,
            status=PackStatus.GENERATED,
        )
    )
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).json()
    account = body["periods"][0]["lines"][0]["account"]
    assert account["documents_count"] == 3
    # Every key is present so an empty folder is greyed but still navigable.
    assert set(account["record_counts"]) == {
        folder.key for folder in RECORD_FOLDERS
    }
    assert account["record_counts"]["amounts"] == 1
    assert account["record_counts"]["loss_history"] == 1
    assert account["record_counts"]["slip"] == 1
    assert account["record_counts"]["report"] == 0
    assert account["packs_count"] == 1
    assert account["client_ids"] == [world.a.client.id]
    assert account["placement_ids"] == [world.a.placement.id]


def test_account_node_counts_quotes_and_proposals_through_its_placements(
    client, world, db, headers_a
):
    from app.models.proposal import Proposal

    group = make_group(db, world.a)
    case = make_account(db, world.a, group)
    world.a.placement.case_file_id = case.id
    db.add(
        Proposal(
            broker_id=world.a.broker_id,
            quote_request_id=world.a.quote.id,
            insurer_id=world.insurer.id,
            source_document_id=world.a.document.id,
            total_premium_uf=Decimal("139.0000"),
        )
    )
    db.commit()

    account = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["account"]
    assert account["quotes_count"] == 1
    assert account["proposals_count"] == 1


def test_period_locked_flips_once_the_folder_moves_past_intake(
    client, world, db, headers_a
):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group)
    db.add(
        CaseFileStageEvent(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            from_stage=None,
            to_stage=CaseStage.INTAKE,
            occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.commit()
    account = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["account"]
    assert account["period_locked"] is False

    db.add(
        CaseFileStageEvent(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            from_stage=CaseStage.INTAKE,
            to_stage=CaseStage.TECHNICAL_BASIS,
            occurred_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
    )
    db.commit()
    account = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["account"]
    assert account["period_locked"] is True


def test_policies_hang_off_their_account_with_post_sale_children(
    client, world, db, headers_a
):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group, stage=CaseStage.ACTIVE)
    policy = make_policy(db, world.a, world.insurer, case_file_id=case.id)
    child = make_case_file(
        db,
        world.a,
        n=50,
        kind=CaseFileKind.ENDORSEMENT,
        stage=CaseStage.ENDORSEMENT_APPLIED,
        policy_id=policy.id,
        parent_case_file_id=case.id,
        account_group_id=group.id,
        placement_id=None,
    )
    db.add(
        Endorsement(
            broker_id=world.a.broker_id,
            policy_id=policy.id,
            case_file_id=child.id,
            sequence_no=1,
            kind=EndorsementKind.SUM_INSURED_INCREASE,
            status=EndorsementStatus.APPLIED,
            effective_at=datetime(2026, 3, 20, 12, 0, 0),
        )
    )
    db.commit()

    line = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]
    assert len(line["policies"]) == 1
    node = line["policies"][0]
    assert node["policy_number"] == policy.policy_number
    assert node["insurer_name"] == world.insurer.legal_name
    assert node["extended_end_date"] is None
    assert [c["case_file_id"] for c in node["children"]["endorsement"]] == [child.id]
    assert node["children"]["endorsement"][0]["batch_key"] is None
    assert node["children"]["collection"] == []
    assert node["children"]["claim"] == []


def test_a_prorroga_marks_the_policy_as_extended(client, world, db, headers_a):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group, stage=CaseStage.ACTIVE)
    policy = make_policy(
        db, world.a, world.insurer, case_file_id=case.id, end_date=date(2027, 12, 15)
    )
    db.add(
        Endorsement(
            broker_id=world.a.broker_id,
            policy_id=policy.id,
            sequence_no=1,
            kind=EndorsementKind.PERIOD_EXTENSION,
            status=EndorsementStatus.APPLIED,
            batch_key="0f2b1a5e-0000-4000-8000-000000000001",
        )
    )
    db.commit()

    node = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["policies"][0]
    assert node["extended_end_date"] == "2027-12-15"


def test_post_sale_children_are_hidden_without_the_module_grant(
    client, world, db, headers_a, headers_a_inspector
):
    """The inspector holds Claims.View but neither Endorsements nor Collections."""
    from app.models.inspection import Inspection

    group = make_group(db, world.a)
    case = make_account(db, world.a, group, stage=CaseStage.ACTIVE)
    policy = make_policy(db, world.a, world.insurer, case_file_id=case.id)
    endorsement_case = make_case_file(
        db,
        world.a,
        n=50,
        kind=CaseFileKind.ENDORSEMENT,
        stage=CaseStage.ENDORSEMENT_APPLIED,
        policy_id=policy.id,
        account_group_id=group.id,
        placement_id=None,
    )
    claim_case = make_case_file(
        db,
        world.a,
        n=51,
        kind=CaseFileKind.CLAIM,
        stage=CaseStage.CLAIM_SETTLED,
        policy_id=policy.id,
        account_group_id=group.id,
        placement_id=None,
    )
    db.add(
        Inspection(
            broker_id=world.a.broker_id,
            asset_id=world.a.asset.id,
            case_file_id=case.id,
            version=1,
        )
    )
    db.commit()

    admin_children = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["policies"][0]["children"]
    assert [c["case_file_id"] for c in admin_children["endorsement"]] == [
        endorsement_case.id
    ]
    assert [c["case_file_id"] for c in admin_children["claim"]] == [claim_case.id]

    inspector = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a_inspector
    ).json()
    children = inspector["periods"][0]["lines"][0]["policies"][0]["children"]
    assert children["endorsement"] == []
    assert children["collection"] == []


def test_renewal_leaf_explains_why_it_is_disabled(client, world, db, headers_a):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group)  # stage=intake
    db.commit()

    renewal = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["renewal"]
    assert renewal == {
        "case_file_id": None,
        "allowed": False,
        "reason": "account_not_active",
    }

    case.stage = CaseStage.ACTIVE
    db.commit()
    renewal = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["renewal"]
    assert renewal["allowed"] is True and renewal["reason"] is None


def test_a_renewed_folder_links_forward_and_backward(client, world, db, headers_a):
    group = make_group(db, world.a)
    source = make_account(
        db,
        world.a,
        group,
        n=1,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
        stage=CaseStage.ACTIVE,
    )
    successor = make_account(
        db,
        world.a,
        group,
        n=2,
        kind=CaseFileKind.RENEWAL,
        stage=CaseStage.RENEWAL_REVIEW,
        origin=CaseOrigin.RENEWAL,
        origin_case_file_id=source.id,
    )
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).json()
    by_case = {
        period["lines"][0]["account"]["case_file_id"]: period["lines"][0]
        for period in body["periods"]
    }
    assert by_case[source.id]["account"]["renewed_by_case_file_id"] == successor.id
    assert by_case[source.id]["renewal"] == {
        "case_file_id": successor.id,
        "allowed": False,
        "reason": "already_renewed",
    }
    assert by_case[successor.id]["account"]["origin"] == CaseOrigin.RENEWAL.value
    assert by_case[successor.id]["account"]["origin_case_file_id"] == source.id


def test_membership_is_account_client_union_the_placements(client, world, db, headers_a):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group)
    second_insured = Insured(rut="96688830-K", legal_name="Viña Santa Alicia SpA")
    db.add(second_insured)
    db.flush()
    second_client = Client(
        broker_id=world.a.broker_id,
        insured_id=second_insured.id,
        account_group_id=group.id,
    )
    db.add(second_client)
    db.flush()
    db.add(
        AccountClient(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            client_id=second_client.id,
            role=AccountClientRole.INSURED,
        )
    )

    asset = Asset(
        broker_id=world.a.broker_id,
        client_id=second_client.id,
        asset_type="property",
        name="Segunda planta",
    )
    db.add(asset)
    db.flush()
    db.add(
        Placement(
            broker_id=world.a.broker_id,
            client_id=second_client.id,
            asset_id=asset.id,
            insurance_line_id=world.a.line.id,
            case_file_id=case.id,
            status=PlacementStatus.DRAFT,
        )
    )
    db.commit()

    account = client.get(
        f"{API}/account-groups/{group.id}/tree", headers=headers_a
    ).json()["periods"][0]["lines"][0]["account"]
    # The contratante is first (is_primary), then the other RUT.
    assert account["client_ids"] == [world.a.client.id, second_client.id]
    # One folder -> N placements (spec §2.5).
    assert len(account["placement_ids"]) == 2


def test_tree_of_another_tenants_group_is_404(client, world, db, headers_a):
    group = make_group(db, world.b, "Grupo de Beta")
    make_account(db, world.b, group, n=1)
    db.commit()
    assert (
        client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).status_code
        == 404
    )


def test_a_folder_without_a_period_still_appears(client, world, db, headers_a):
    """A folder with no vigencia must stay reachable, sorted last."""
    group = make_group(db, world.a)
    dated = make_account(db, world.a, group, n=1)
    orphan = make_case_file(
        db, world.a, n=2, account_group_id=group.id, placement_id=None
    )
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}/tree", headers=headers_a).json()
    labels = [period["label"] for period in body["periods"]]
    assert labels[0] == "2026-2027"
    assert labels[-1] == navigator_service.UNSCHEDULED_PERIOD_LABEL
    assert body["periods"][-1]["lines"][0]["account"]["case_file_id"] == orphan.id
    assert body["periods"][0]["lines"][0]["account"]["case_file_id"] == dated.id
