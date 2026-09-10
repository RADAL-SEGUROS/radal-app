"""``/account-groups`` — CRUD, membership, the merged timeline and the archive.

The group is broker-private and carries no money and no stage: everything here
asserts that it only *counts* and *groups* folders, that detaching never
cascades, and that the archive contains exactly the documents of the cases the
caller can see.
"""
from __future__ import annotations

import os
import zipfile
from datetime import date, datetime, timedelta

import pytest

from app.core.config import settings
from app.models.account_client import AccountClient, AccountClientRole
from app.models.account_group import AccountGroup, AccountGroupStatus
from app.models.activity import Note
from app.models.case_file import CaseFileStageEvent
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    CaseFileStatus,
    CaseSection,
    CaseStage,
    EntityType,
)
from app.services import navigator as navigator_service
from tests.conftest import API, make_case_file

# =============================================================================
# Local fixtures — conftest.py belongs to another work package, so the groups
# world is built here (and imported by tests/test_navigator.py).
# =============================================================================


@pytest.fixture()
def local_media(tmp_path, monkeypatch):
    """Point the document store at a temp dir for the whole test."""
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    return tmp_path


def make_group(db, tenant, name="Grupo Viña Indómita", **overrides) -> AccountGroup:
    """One broker-private group, slugged exactly as the importers slug it."""
    group = AccountGroup(
        broker_id=tenant.broker_id,
        name=name,
        slug=overrides.pop("slug", navigator_service.slugify_group_name(name)),
        status=overrides.pop("status", AccountGroupStatus.ACTIVE),
        **overrides,
    )
    db.add(group)
    db.flush()
    return group


def make_account(
    db,
    tenant,
    group,
    *,
    n=1,
    period_start=date(2026, 10, 15),
    period_end=date(2027, 10, 15),
    **overrides,
):
    """An account folder inside ``group``, with its vigencia label."""
    case = make_case_file(
        db,
        tenant,
        n=n,
        account_group_id=group.id,
        period_start=period_start,
        period_end=period_end,
        period_label=navigator_service.period_label_for(period_start, period_end),
        **overrides,
    )
    db.add(
        AccountClient(
            broker_id=tenant.broker_id,
            case_file_id=case.id,
            client_id=tenant.client.id,
            role=AccountClientRole.POLICYHOLDER,
            is_primary=True,
        )
    )
    return case


def attach_document(
    db,
    tenant,
    case,
    *,
    code="00C",
    category=DocumentCategory.INSURED_VALUES_SCHEDULE,
    section=CaseSection.SUBMISSION,
    body=b"%PDF-1.4 fake\n",
    root=None,
):
    """Register a document and write its bytes where the local backend reads."""
    key = f"documents/case_file/{case.id}/{category.value}-{code}.pdf"
    doc = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        case_file_id=case.id,
        section=section,
        document_code=code,
        s3_key=key,
        bucket="radal-test-bucket",
        original_name=f"{code} documento.pdf",
        mime_type="application/pdf",
        size_bytes=len(body),
        category=category,
    )
    db.add(doc)
    db.flush()
    if root is not None:
        path = os.path.join(str(root), key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(body)
    return doc


# =============================================================================
# CRUD
# =============================================================================

def test_create_group_derives_the_slug_and_returns_the_detail(client, world, headers_a):
    response = client.post(
        f"{API}/account-groups",
        headers=headers_a,
        json={"name": "GRUPO VIÑA INDÓMITA", "notes": "cuenta comercial con dos RUT"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "grupo-vina-indomita"
    assert body["status"] == "active"
    assert body["accounts_count"] == 0 and body["open_count"] == 0
    assert body["clients"] == []
    assert body["notes"] == "cuenta comercial con dos RUT"


def test_create_group_attaches_clients_in_the_same_call(client, world, db, headers_a):
    response = client.post(
        f"{API}/account-groups",
        headers=headers_a,
        json={"name": "La Favorita", "client_ids": [world.a.client.id]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["clients_count"] == 1
    assert body["clients"][0]["id"] == world.a.client.id
    db.expire_all()
    assert db.get(type(world.a.client), world.a.client.id).account_group_id == body["id"]


def test_the_same_name_is_free_in_another_broker(client, world, headers_a, headers_b):
    """``slug`` is unique per BROKER — two tenants may both hold 'La Favorita'."""
    first = client.post(
        f"{API}/account-groups", headers=headers_a, json={"name": "La Favorita"}
    )
    second = client.post(
        f"{API}/account-groups", headers=headers_b, json={"name": "La Favorita"}
    )
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["slug"] == second.json()["slug"] == "la-favorita"
    assert first.json()["id"] != second.json()["id"]


def test_a_repeated_name_inside_one_broker_gets_a_distinct_slug(client, headers_a):
    first = client.post(
        f"{API}/account-groups", headers=headers_a, json={"name": "La Favorita"}
    ).json()
    second = client.post(
        f"{API}/account-groups", headers=headers_a, json={"name": "La Favorita"}
    ).json()
    assert first["slug"] == "la-favorita"
    assert second["slug"] != first["slug"]


def test_list_filters_by_query_and_status(client, world, db, headers_a):
    make_group(db, world.a, "La Favorita")
    archived = make_group(db, world.a, "Coccolino")
    archived.status = AccountGroupStatus.ARCHIVED
    db.commit()

    page = client.get(f"{API}/account-groups", headers=headers_a).json()
    assert page["total"] == 2 and page["page"] == 1 and page["pages"] == 1

    filtered = client.get(
        f"{API}/account-groups", headers=headers_a, params={"q": "favor"}
    ).json()
    assert [item["slug"] for item in filtered["items"]] == ["la-favorita"]

    by_status = client.get(
        f"{API}/account-groups", headers=headers_a, params={"status": "archived"}
    ).json()
    assert [item["slug"] for item in by_status["items"]] == ["coccolino"]


def test_counts_come_from_the_folders_not_from_the_group(client, world, db, headers_a):
    group = make_group(db, world.a)
    make_account(db, world.a, group, n=1)
    closed = make_account(
        db,
        world.a,
        group,
        n=2,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
    )
    closed.status = CaseFileStatus.CLOSED
    db.commit()

    body = client.get(f"{API}/account-groups/{group.id}", headers=headers_a).json()
    assert body["accounts_count"] == 2
    assert body["open_count"] == 1
    # The primary RUT is DERIVED: the contratante of the latest vigencia.
    assert body["latest_period_label"] == "2026-2027"
    assert body["latest_period_start"] == "2026-10-15"
    assert body["primary_client"]["id"] == world.a.client.id


def test_patch_renames_reslugs_and_archives(client, world, db, headers_a):
    group = make_group(db, world.a, "Coccolino")
    db.commit()
    body = client.patch(
        f"{API}/account-groups/{group.id}",
        headers=headers_a,
        json={"name": "Coccolino Pastelería", "status": "archived"},
    ).json()
    assert body["slug"] == "coccolino-pasteleria"
    assert body["status"] == "archived"


def test_archiving_a_group_never_cascades(client, world, db, headers_a):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group)
    db.commit()

    client.patch(
        f"{API}/account-groups/{group.id}", headers=headers_a, json={"status": "archived"}
    )
    db.expire_all()
    assert db.get(type(case), case.id) is not None
    assert db.get(type(case), case.id).account_group_id == group.id


# =============================================================================
# Membership
# =============================================================================

def test_attaching_a_foreign_broker_client_is_404(client, world, db, headers_a):
    group = make_group(db, world.a)
    db.commit()
    response = client.post(
        f"{API}/account-groups/{group.id}/clients",
        headers=headers_a,
        json={"client_id": world.b.client.id},
    )
    assert response.status_code == 404


def test_clients_list_exposes_the_group_a_company_belongs_to(
    client, world, db, headers_a
):
    """The empresa pickers need this to tell "free" from "already spoken for".

    Without it the group picker can only offer every company and let the wrong
    pick come back as 422 ``client_in_other_group`` — a dead button by another
    name.
    """
    group = make_group(db, world.a)
    db.commit()

    before = client.get(f"{API}/clients", headers=headers_a)
    assert before.status_code == 200
    row = next(
        item for item in before.json()["items"] if item["id"] == world.a.client.id
    )
    assert row["account_group_id"] is None

    attached = client.post(
        f"{API}/account-groups/{group.id}/clients",
        headers=headers_a,
        json={"client_id": world.a.client.id},
    )
    assert attached.status_code == 200

    after = client.get(f"{API}/clients/{world.a.client.id}", headers=headers_a)
    assert after.status_code == 200
    assert after.json()["account_group_id"] == group.id


def test_a_client_in_another_group_is_422(client, world, db, headers_a):
    first = make_group(db, world.a, "La Favorita")
    second = make_group(db, world.a, "Coccolino")
    world.a.client.account_group_id = first.id
    db.commit()

    response = client.post(
        f"{API}/account-groups/{second.id}/clients",
        headers=headers_a,
        json={"client_id": world.a.client.id},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "client_in_other_group"
    assert response.json()["detail"]["account_group_id"] == first.id


def test_detach_is_refused_while_an_open_folder_remains(client, world, db, headers_a):
    group = make_group(db, world.a)
    case = make_account(db, world.a, group)
    world.a.client.account_group_id = group.id
    db.commit()

    refused = client.delete(
        f"{API}/account-groups/{group.id}/clients/{world.a.client.id}", headers=headers_a
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "client_has_open_case_files"
    assert refused.json()["detail"]["open_case_files"] == 1

    case.status = CaseFileStatus.CLOSED
    db.commit()
    accepted = client.delete(
        f"{API}/account-groups/{group.id}/clients/{world.a.client.id}", headers=headers_a
    )
    assert accepted.status_code == 204
    db.expire_all()
    assert db.get(type(world.a.client), world.a.client.id).account_group_id is None


def test_detaching_a_client_of_another_group_is_404(client, world, db, headers_a):
    group = make_group(db, world.a)
    db.commit()
    response = client.delete(
        f"{API}/account-groups/{group.id}/clients/{world.a.client.id}", headers=headers_a
    )
    assert response.status_code == 404


# =============================================================================
# Tenancy
# =============================================================================

@pytest.mark.parametrize(
    "method, suffix",
    [
        ("get", ""),
        ("get", "/tree"),
        ("get", "/timeline"),
    ],
)
def test_another_brokers_group_is_404(client, world, db, headers_a, method, suffix):
    group = make_group(db, world.b, "Grupo de Beta")
    db.commit()
    response = getattr(client, method)(
        f"{API}/account-groups/{group.id}{suffix}", headers=headers_a
    )
    assert response.status_code == 404


def test_another_brokers_group_cannot_be_patched(client, world, db, headers_a):
    group = make_group(db, world.b, "Grupo de Beta")
    db.commit()
    response = client.patch(
        f"{API}/account-groups/{group.id}", headers=headers_a, json={"name": "mío"}
    )
    assert response.status_code == 404


def test_a_brokers_list_never_shows_another_tenants_groups(
    client, world, db, headers_a, headers_b
):
    make_group(db, world.a, "La Favorita")
    make_group(db, world.b, "Coccolino")
    db.commit()
    a_slugs = {
        item["slug"]
        for item in client.get(f"{API}/account-groups", headers=headers_a).json()["items"]
    }
    b_slugs = {
        item["slug"]
        for item in client.get(f"{API}/account-groups", headers=headers_b).json()["items"]
    }
    assert a_slugs == {"la-favorita"}
    assert b_slugs == {"coccolino"}


# =============================================================================
# Timeline
# =============================================================================

def _seed_timeline(db, world, group):
    case = make_account(db, world.a, group)
    base = datetime(2026, 5, 1, 12, 0, 0)
    for index, stage in enumerate(
        (CaseStage.INTAKE, CaseStage.PRE_UNDERWRITING, CaseStage.TECHNICAL_BASIS)
    ):
        db.add(
            CaseFileStageEvent(
                broker_id=world.a.broker_id,
                case_file_id=case.id,
                from_stage=None,
                to_stage=stage,
                occurred_at=base + timedelta(days=index),
            )
        )
    note = Note(
        broker_id=world.a.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        body="pendiente de montos",
        is_internal=True,
        created_at=base + timedelta(days=10),
    )
    db.add(note)
    db.commit()
    return case


def test_timeline_is_sorted_descending_before_truncation(client, world, db, headers_a):
    group = make_group(db, world.a)
    _seed_timeline(db, world, group)

    body = client.get(
        f"{API}/account-groups/{group.id}/timeline", headers=headers_a, params={"limit": 2}
    ).json()
    assert len(body["entries"]) == 2
    # Newest first: the note (day 10) precedes the last stage event (day 2).
    assert body["entries"][0]["kind"] == "note"
    assert body["entries"][1]["title"].endswith("technical_basis")
    stamps = [entry["occurred_at"] for entry in body["entries"]]
    assert stamps == sorted(stamps, reverse=True)
    assert body["next_before"] == body["entries"][-1]["occurred_at"]


def test_timeline_cursor_pages_backwards_without_repeating(client, world, db, headers_a):
    group = make_group(db, world.a)
    _seed_timeline(db, world, group)

    first = client.get(
        f"{API}/account-groups/{group.id}/timeline", headers=headers_a, params={"limit": 2}
    ).json()
    second = client.get(
        f"{API}/account-groups/{group.id}/timeline",
        headers=headers_a,
        params={"limit": 2, "before": first["next_before"]},
    ).json()

    seen = {(e["kind"], e["occurred_at"], e["title"]) for e in first["entries"]}
    for entry in second["entries"]:
        assert (entry["kind"], entry["occurred_at"], entry["title"]) not in seen
        assert entry["occurred_at"] < first["next_before"]


def test_timeline_only_covers_the_groups_own_cases(client, world, db, headers_a):
    group = make_group(db, world.a, "La Favorita")
    other = make_group(db, world.a, "Coccolino")
    _seed_timeline(db, world, group)
    stray = make_account(db, world.a, other, n=9)
    db.add(
        CaseFileStageEvent(
            broker_id=world.a.broker_id,
            case_file_id=stray.id,
            from_stage=None,
            to_stage=CaseStage.INTAKE,
            occurred_at=datetime(2027, 1, 1, 12, 0, 0),
        )
    )
    db.commit()

    body = client.get(
        f"{API}/account-groups/{group.id}/timeline", headers=headers_a
    ).json()
    assert body["entries"]
    assert all(entry["case_file_id"] != stray.id for entry in body["entries"])


# =============================================================================
# Archive
# =============================================================================

def test_archive_is_an_account_group_document_holding_the_visible_files(
    client, world, db, headers_a, local_media
):
    group = make_group(db, world.a, "La Favorita")
    case = make_account(db, world.a, group)
    attach_document(db, world.a, case, root=local_media, body=b"montos\n")
    attach_document(
        db,
        world.a,
        case,
        code="01",
        category=DocumentCategory.TECHNICAL_BRIEF,
        body=b"bases\n",
        root=local_media,
    )
    # Another group's file must never land in this archive.
    other = make_group(db, world.a, "Coccolino")
    other_case = make_account(db, world.a, other, n=9)
    attach_document(
        db, world.a, other_case, code="00D", body=b"ajeno\n", root=local_media
    )
    db.commit()

    response = client.post(
        f"{API}/account-groups/{group.id}/archives", headers=headers_a, json={}
    )
    assert response.status_code == 201, response.text
    body = response.json()

    document = db.get(Document, body["document_id"])
    assert document.entity_type == EntityType.ACCOUNT_GROUP
    assert document.entity_id == group.id
    assert document.category == DocumentCategory.ARCHIVE_PACK
    assert document.s3_key.endswith(".zip")

    with open(os.path.join(str(local_media), document.s3_key), "rb") as fh:
        with zipfile.ZipFile(fh) as archive:
            names = archive.namelist()
    assert len(names) == 2
    assert all(name.startswith("la-favorita/2026-2027/") for name in names)
    assert not any("ajeno" in name for name in names)


def test_archive_downloads_through_the_documents_route(
    client, world, db, headers_a, local_media
):
    group = make_group(db, world.a, "La Favorita")
    case = make_account(db, world.a, group)
    attach_document(db, world.a, case, root=local_media, body=b"montos\n")
    db.commit()

    body = client.post(
        f"{API}/account-groups/{group.id}/archives", headers=headers_a, json={}
    ).json()
    assert body["download"]["id"] == body["document_id"]

    content = client.get(
        f"{API}/documents/{body['document_id']}/content", headers=headers_a
    )
    assert content.status_code == 200
    assert content.content[:2] == b"PK"


def test_archive_can_be_scoped_to_one_vigencia(client, world, db, headers_a, local_media):
    group = make_group(db, world.a, "La Favorita")
    current = make_account(db, world.a, group, n=1)
    previous = make_account(
        db,
        world.a,
        group,
        n=2,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
    )
    attach_document(db, world.a, current, root=local_media, body=b"nuevo\n")
    attach_document(db, world.a, previous, root=local_media, body=b"viejo\n")
    db.commit()

    body = client.post(
        f"{API}/account-groups/{group.id}/archives",
        headers=headers_a,
        json={"period_label": "2025-2026"},
    ).json()
    document = db.get(Document, body["document_id"])
    with open(os.path.join(str(local_media), document.s3_key), "rb") as fh:
        with zipfile.ZipFile(fh) as archive:
            names = archive.namelist()
    assert len(names) == 1
    assert names[0].startswith("la-favorita/2025-2026/")


def test_archive_regenerates_onto_the_same_key(client, world, db, headers_a, local_media):
    group = make_group(db, world.a, "La Favorita")
    case = make_account(db, world.a, group)
    attach_document(db, world.a, case, root=local_media, body=b"montos\n")
    db.commit()

    first = client.post(
        f"{API}/account-groups/{group.id}/archives", headers=headers_a, json={}
    ).json()
    second = client.post(
        f"{API}/account-groups/{group.id}/archives", headers=headers_a, json={}
    ).json()
    assert first["document_id"] == second["document_id"]
    assert first["download"]["s3_key"] == second["download"]["s3_key"]


def test_archive_accepts_an_empty_body_meaning_the_whole_group(
    client, world, db, headers_a, local_media
):
    group = make_group(db, world.a, "La Favorita")
    case = make_account(db, world.a, group)
    attach_document(db, world.a, case, root=local_media, body=b"montos\n")
    db.commit()

    response = client.post(f"{API}/account-groups/{group.id}/archives", headers=headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["document_id"]


def test_archive_of_a_foreign_group_is_404(client, world, db, headers_a, local_media):
    group = make_group(db, world.b, "Grupo de Beta")
    db.commit()
    response = client.post(
        f"{API}/account-groups/{group.id}/archives", headers=headers_a, json={}
    )
    assert response.status_code == 404


# =============================================================================
# RBAC
# =============================================================================

def test_a_technician_may_read_but_not_create_a_group(
    client, world, db, headers_a_tech
):
    group = make_group(db, world.a)
    db.commit()
    assert (
        client.get(f"{API}/account-groups/{group.id}", headers=headers_a_tech).status_code
        == 200
    )
    refused = client.post(
        f"{API}/account-groups", headers=headers_a_tech, json={"name": "Nuevo"}
    )
    assert refused.status_code == 403


def test_an_inspector_only_sees_groups_holding_an_inspected_case(
    client, world, db, headers_a_inspector
):
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

    slugs = {
        item["slug"]
        for item in client.get(
            f"{API}/account-groups", headers=headers_a_inspector
        ).json()["items"]
    }
    assert slugs == {"la-favorita"}
    assert (
        client.get(
            f"{API}/account-groups/{hidden.id}/tree", headers=headers_a_inspector
        ).status_code
        == 404
    )


def test_the_slug_rule_matches_the_backfill(client):
    """Same input, same key — a backfilled RDS and a re-import must agree."""
    assert navigator_service.slugify_group_name("GRUPO VIÑA INDÓMITA") == "grupo-vina-indomita"
    assert navigator_service.slugify_group_name("JO PASTELERÍA") == "jo-pasteleria"
    assert navigator_service.slugify_group_name("  ") == "grupo"
    assert len(navigator_service.slugify_group_name("x" * 200)) == 80
