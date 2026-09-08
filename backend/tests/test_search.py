"""``GET /search`` — the topbar's one query across the broker's book.

Covers the v3 addition (``groups[]``, spec §4.1/§8 row B4) alongside the
pre-existing kinds, and the three list filters that ship with it:
``?account_group_id`` on ``/clients`` and ``?case_file_id`` on ``/quotes`` and
``/proposals``. Every assertion doubles as a tenant-isolation assertion: broker
B owns a mirror-image row for each one, and broker A must never reach it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.account_group import AccountGroup
from app.models.proposal import Proposal
from tests.conftest import API, Tenant, World, make_case_file


# --- Fixtures ----------------------------------------------------------------

def make_group(db: Session, tenant: Tenant, name: str, slug: str) -> AccountGroup:
    group = AccountGroup(broker_id=tenant.broker_id, name=name, slug=slug)
    db.add(group)
    db.flush()
    return group


@pytest.fixture()
def groups(db: Session, world: World) -> dict[str, AccountGroup]:
    """One group per broker, both matching the search term "vina"."""
    a = make_group(db, world.a, "GRUPO VIÑA INDÓMITA", "grupo-vina-indomita")
    b = make_group(db, world.b, "VIÑA BETA", "vina-beta")
    # Broker A's group carries its client and one account folder, so the
    # derived sublabel (primary client · latest vigencia) has something to say.
    world.a.client.account_group_id = a.id
    case = make_case_file(
        db,
        world.a,
        account_group_id=a.id,
        period_start=date(2026, 10, 15),
        period_end=date(2027, 10, 15),
        period_label="2026-2027",
    )
    # An older folder of the same group must NOT win the "latest" derivation.
    make_case_file(
        db,
        world.a,
        n=2,
        account_group_id=a.id,
        period_start=date(2025, 10, 15),
        period_end=date(2026, 10, 15),
        period_label="2025-2026",
    )
    db.commit()
    db.refresh(a)
    db.refresh(b)
    db.refresh(case)
    return {"a": a, "b": b, "case": case}


# --- GET /search : groups ----------------------------------------------------

def test_search_returns_the_brokers_own_group(client, headers_a, world, groups):
    body = client.get(f"{API}/search", params={"q": "vina"}, headers=headers_a).json()

    hits = body["groups"]
    assert [hit["id"] for hit in hits] == [groups["a"].id]
    assert hits[0]["label"] == "GRUPO VIÑA INDÓMITA"
    assert hits[0]["url"] == f"/groups/{groups['a'].id}"


def test_group_sublabel_is_primary_client_and_latest_period(
    client, headers_a, world, groups
):
    """Derived, never stored: the client of the folder with the latest period."""
    # Match on the slug: SQLite's ``lower()`` is ASCII-only, so an accented
    # needle would not fold against the upper-case name.
    body = client.get(f"{API}/search", params={"q": "vina"}, headers=headers_a).json()

    sublabel = body["groups"][0]["sublabel"]
    assert world.a.client.insured.legal_name in sublabel
    assert "2026-2027" in sublabel
    assert "2025-2026" not in sublabel


def test_group_matches_on_slug_too(client, headers_a, groups):
    body = client.get(
        f"{API}/search", params={"q": "grupo-vina"}, headers=headers_a
    ).json()
    assert [hit["id"] for hit in body["groups"]] == [groups["a"].id]


def test_another_brokers_group_is_invisible(client, headers_a, headers_b, groups):
    a_body = client.get(f"{API}/search", params={"q": "vina"}, headers=headers_a).json()
    b_body = client.get(f"{API}/search", params={"q": "vina"}, headers=headers_b).json()

    assert [hit["id"] for hit in a_body["groups"]] == [groups["a"].id]
    assert [hit["id"] for hit in b_body["groups"]] == [groups["b"].id]


def test_groups_key_is_always_present(client, headers_a, world):
    """No groups in the world -> an empty list, never a missing key."""
    body = client.get(f"{API}/search", params={"q": "planta"}, headers=headers_a).json()
    assert body["groups"] == []


def test_existing_kinds_are_unchanged(client, headers_a, world, groups):
    """Adding groups must not disturb the four pre-existing hit groups."""
    body = client.get(f"{API}/search", params={"q": "Planta"}, headers=headers_a).json()

    assert [hit["id"] for hit in body["assets"]] == [world.a.asset.id]
    assert [hit["id"] for hit in body["quotes"]] == [world.a.quote.id]
    assert body["clients"] == []


def test_client_is_found_by_rut_in_either_notation(client, headers_a, world):
    rut = world.a.client.insured.rut  # normalized "BODY-DV"
    body = client.get(f"{API}/search", params={"q": rut}, headers=headers_a).json()
    assert [hit["id"] for hit in body["clients"]] == [world.a.client.id]


def test_short_query_is_rejected(client, headers_a):
    assert client.get(f"{API}/search", params={"q": "a"}, headers=headers_a).status_code == 422


# --- GET /clients?account_group_id ------------------------------------------

def test_clients_filter_by_account_group(client, headers_a, world, groups):
    attached = client.get(
        f"{API}/clients",
        params={"account_group_id": groups["a"].id},
        headers=headers_a,
    ).json()
    assert [item["id"] for item in attached["items"]] == [world.a.client.id]
    assert attached["total"] == 1


def test_clients_filter_with_a_foreign_group_returns_nothing(
    client, headers_a, groups
):
    """A foreign group id is an empty page, never another tenant's clients."""
    body = client.get(
        f"{API}/clients",
        params={"account_group_id": groups["b"].id},
        headers=headers_a,
    ).json()
    assert body["items"] == []
    assert body["total"] == 0


# --- GET /quotes?case_file_id -----------------------------------------------

def test_quotes_filter_by_case_file(client, headers_a, db, world, groups):
    case = groups["case"]
    world.a.quote.case_file_id = case.id
    db.commit()

    body = client.get(
        f"{API}/quotes", params={"case_file_id": case.id}, headers=headers_a
    ).json()
    assert [item["id"] for item in body["items"]] == [world.a.quote.id]
    assert body["total"] == 1

    other = client.get(
        f"{API}/quotes", params={"case_file_id": case.id + 9999}, headers=headers_a
    ).json()
    assert other["items"] == []


def test_quotes_case_file_filter_does_not_cross_tenants(
    client, headers_b, db, world, groups
):
    case = groups["case"]  # broker A's folder
    world.a.quote.case_file_id = case.id
    db.commit()

    body = client.get(
        f"{API}/quotes", params={"case_file_id": case.id}, headers=headers_b
    ).json()
    assert body["items"] == []


# --- GET /proposals?case_file_id --------------------------------------------

def make_proposal(db: Session, tenant: Tenant, insurer, **overrides) -> Proposal:
    fields = {
        "broker_id": tenant.broker_id,
        "quote_request_id": tenant.quote.id,
        "insurer_id": insurer.id,
        "source_document_id": tenant.document.id,
        "total_premium_uf": Decimal("120.0000"),
    }
    fields.update(overrides)
    proposal = Proposal(**fields)
    db.add(proposal)
    db.flush()
    return proposal


def test_proposals_filter_by_case_file(client, headers_a, db, world, groups):
    case = groups["case"]
    attached = make_proposal(db, world.a, world.insurer, case_file_id=case.id)
    make_proposal(db, world.a, world.insurer)  # same broker, no folder
    db.commit()

    body = client.get(
        f"{API}/proposals", params={"case_file_id": case.id}, headers=headers_a
    ).json()
    assert [item["id"] for item in body["items"]] == [attached.id]
    assert body["total"] == 1


def test_proposals_case_file_filter_does_not_cross_tenants(
    client, headers_b, db, world, groups
):
    case = groups["case"]  # broker A's folder
    make_proposal(db, world.a, world.insurer, case_file_id=case.id)
    db.commit()

    body = client.get(
        f"{API}/proposals", params={"case_file_id": case.id}, headers=headers_b
    ).json()
    assert body["items"] == []
    assert body["total"] == 0
