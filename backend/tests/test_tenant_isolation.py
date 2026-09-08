"""Multi-tenancy: broker A can never reach broker B's records.

The tenant comes from the JWT's user, never from the request. A cross-tenant id
must behave as if the record did not exist (404) or be refused (403) — it must
never leak the row, and it must never be writable.

These tests are the reason the fixture world holds TWO brokers with mirror-image
records: asserting "A sees 1 client" proves nothing unless B also owns one.
"""
from __future__ import annotations

import pytest

from tests.conftest import API

DENIED = (403, 404)


class TestListingsAreScoped:
    @pytest.mark.parametrize(
        "path", ["clients", "assets", "placements", "quotes", "proposals", "documents"]
    )
    def test_a_listing_never_includes_the_other_brokers_rows(
        self, client, world, headers_a, headers_b, path
    ) -> None:
        response_a = client.get(f"{API}/{path}", headers=headers_a)
        response_b = client.get(f"{API}/{path}", headers=headers_b)
        assert response_a.status_code == 200, response_a.text
        assert response_b.status_code == 200, response_b.text

        ids_a = {item["id"] for item in response_a.json()["items"]}
        ids_b = {item["id"] for item in response_b.json()["items"]}
        assert ids_a.isdisjoint(ids_b)

    def test_each_broker_sees_exactly_its_own_client(
        self, client, world, headers_a, headers_b
    ) -> None:
        ids_a = {i["id"] for i in client.get(f"{API}/clients", headers=headers_a).json()["items"]}
        ids_b = {i["id"] for i in client.get(f"{API}/clients", headers=headers_b).json()["items"]}
        assert ids_a == {world.a.client.id}
        assert ids_b == {world.b.client.id}


class TestCrossTenantReads:
    def test_broker_a_cannot_read_broker_b_client(
        self, client, world, headers_a
    ) -> None:
        response = client.get(f"{API}/clients/{world.b.client.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_asset(self, client, world, headers_a) -> None:
        response = client.get(f"{API}/assets/{world.b.asset.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_placement(
        self, client, world, headers_a
    ) -> None:
        response = client.get(f"{API}/placements/{world.b.placement.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_quote(self, client, world, headers_a) -> None:
        response = client.get(f"{API}/quotes/{world.b.quote.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_document(
        self, client, world, headers_a
    ) -> None:
        response = client.get(f"{API}/documents/{world.b.document.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_download_broker_b_document(
        self, client, world, headers_a
    ) -> None:
        response = client.get(
            f"{API}/documents/{world.b.document.id}/download", headers=headers_a
        )
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_proposal(
        self, client, world, headers_a, headers_b
    ) -> None:
        proposal_id = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": world.b.quote.id,
                "insurer_id": world.insurer.id,
                "source_document_id": world.b.document.id,
            },
            headers=headers_b,
        ).json()["id"]

        assert client.get(f"{API}/proposals/{proposal_id}", headers=headers_b).status_code == 200
        response = client.get(f"{API}/proposals/{proposal_id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_quote_comparison(
        self, client, world, headers_a
    ) -> None:
        response = client.get(
            f"{API}/quotes/{world.b.quote.id}/comparison", headers=headers_a
        )
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_read_broker_b_line_items(
        self, client, world, headers_a
    ) -> None:
        response = client.get(
            f"{API}/quotes/{world.b.quote.id}/line-items", headers=headers_a
        )
        assert response.status_code in DENIED, response.text


class TestCrossTenantWrites:
    def test_broker_a_cannot_patch_broker_b_client(
        self, client, world, headers_a, headers_b
    ) -> None:
        response = client.patch(
            f"{API}/clients/{world.b.client.id}",
            json={"sector": "hijacked"},
            headers=headers_a,
        )
        assert response.status_code in DENIED, response.text

        untouched = client.get(
            f"{API}/clients/{world.b.client.id}", headers=headers_b
        ).json()
        assert untouched["sector"] == "Industrial"

    def test_broker_a_cannot_delete_broker_b_client(
        self, client, world, headers_a, headers_b
    ) -> None:
        response = client.delete(f"{API}/clients/{world.b.client.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text
        assert (
            client.get(f"{API}/clients/{world.b.client.id}", headers=headers_b).status_code
            == 200
        )

    def test_broker_a_cannot_quote_against_broker_b_placement(
        self, client, world, headers_a
    ) -> None:
        response = client.post(
            f"{API}/quotes", json={"placement_id": world.b.placement.id}, headers=headers_a
        )
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_propose_against_broker_b_quote(
        self, client, world, headers_a
    ) -> None:
        response = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": world.b.quote.id,
                "insurer_id": world.insurer.id,
                "source_document_id": world.a.document.id,
            },
            headers=headers_a,
        )
        # B's quote is invisible to A, so it is "not found" — never a 422 that
        # would admit the row exists.
        assert response.status_code == 404, response.text

    def test_broker_a_cannot_borrow_broker_b_document_as_a_source(
        self, client, world, headers_a
    ) -> None:
        response = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": world.a.quote.id,
                "insurer_id": world.insurer.id,
                "source_document_id": world.b.document.id,
            },
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "source_document_not_found"

    def test_a_forged_broker_id_in_the_body_is_ignored(
        self, client, world, headers_a, headers_b
    ) -> None:
        """The tenant comes from the token, never from the payload."""
        response = client.post(
            f"{API}/quotes",
            json={"placement_id": world.a.placement.id, "broker_id": world.b.broker_id},
            headers=headers_a,
        )
        assert response.status_code == 201, response.text
        # The row landed in A's workspace with A's broker_id, not B's.
        body = response.json()
        assert body["broker_id"] == world.a.broker_id
        assert body["broker_id"] != world.b.broker_id

        quote_id = body["id"]
        assert client.get(f"{API}/quotes/{quote_id}", headers=headers_a).status_code == 200
        assert client.get(f"{API}/quotes/{quote_id}", headers=headers_b).status_code in DENIED


class TestUserIsolation:
    def test_broker_a_cannot_read_broker_b_user(self, client, world, headers_a) -> None:
        response = client.get(f"{API}/users/{world.b.admin.id}", headers=headers_a)
        assert response.status_code in DENIED, response.text

    def test_user_listing_is_scoped_to_the_own_broker(
        self, client, world, headers_a
    ) -> None:
        response = client.get(f"{API}/users", headers=headers_a)
        assert response.status_code == 200, response.text
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {world.a.admin.id, world.a.executive.id}


class TestUnauthenticated:
    @pytest.mark.parametrize("path", ["clients", "quotes", "proposals", "documents"])
    def test_no_token_is_401(self, client, world, path) -> None:
        assert client.get(f"{API}/{path}").status_code == 401

    def test_a_refresh_token_cannot_be_used_as_an_access_token(
        self, client, world
    ) -> None:
        login = client.post(
            f"{API}/auth/login",
            json={"email": world.a.admin.email, "password": "radal1234"},
        ).json()
        refresh = login["refresh_token"]

        response = client.get(
            f"{API}/clients", headers={"Authorization": f"Bearer {refresh}"}
        )
        assert response.status_code == 401


# =============================================================================
# Groups & accounts (v3, spec §7) — the new surfaces must be scoped too
# =============================================================================
#
# ``account_group`` is broker-PRIVATE (spec §2.1): unlike ``insured`` and
# ``insurer`` it is not canonical, so there is no legitimate path by which one
# tenant reaches another's group — not through the group router, not through
# the navigator rail, not through the topbar search.


def _make_group(db, tenant, name: str, slug: str):
    from app.models.account_group import AccountGroup

    group = AccountGroup(broker_id=tenant.broker_id, name=name, slug=slug)
    db.add(group)
    db.flush()
    return group


@pytest.fixture()
def groups(db, world):
    """The same group name in BOTH brokers — the sharpest isolation probe.

    ``ix_account_group_broker_slug`` is unique per (broker, slug), so an
    identical name in two tenants is two rows; anything that leaks would show
    up as a duplicate rather than as a foreign id.
    """
    group_a = _make_group(db, world.a, "Grupo Viña Indómita", "grupo-vina-indomita")
    group_b = _make_group(db, world.b, "Grupo Viña Indómita", "grupo-vina-indomita")
    world.a.client.account_group_id = group_a.id
    world.b.client.account_group_id = group_b.id
    db.commit()
    db.refresh(group_a)
    db.refresh(group_b)
    return group_a, group_b


class TestGroupIsolation:
    def test_each_broker_lists_exactly_its_own_group(
        self, client, world, groups, headers_a, headers_b
    ) -> None:
        group_a, group_b = groups
        body_a = client.get(f"{API}/account-groups", headers=headers_a)
        body_b = client.get(f"{API}/account-groups", headers=headers_b)
        assert body_a.status_code == 200, body_a.text
        assert body_b.status_code == 200, body_b.text
        assert {g["id"] for g in body_a.json()["items"]} == {group_a.id}
        assert {g["id"] for g in body_b.json()["items"]} == {group_b.id}

    def test_broker_a_cannot_read_broker_b_group(
        self, client, world, groups, headers_a
    ) -> None:
        _, group_b = groups
        assert (
            client.get(f"{API}/account-groups/{group_b.id}", headers=headers_a).status_code
            in DENIED
        )

    @pytest.mark.parametrize("suffix", ["/tree", "/timeline"])
    def test_broker_a_cannot_read_broker_b_group_views(
        self, client, world, groups, headers_a, suffix
    ) -> None:
        _, group_b = groups
        response = client.get(
            f"{API}/account-groups/{group_b.id}{suffix}", headers=headers_a
        )
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_archive_broker_b_group(
        self, client, world, groups, headers_a
    ) -> None:
        _, group_b = groups
        response = client.post(
            f"{API}/account-groups/{group_b.id}/archives", json={}, headers=headers_a
        )
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_patch_broker_b_group(
        self, client, world, groups, headers_a, headers_b
    ) -> None:
        _, group_b = groups
        response = client.patch(
            f"{API}/account-groups/{group_b.id}",
            json={"name": "hijacked"},
            headers=headers_a,
        )
        assert response.status_code in DENIED, response.text
        untouched = client.get(
            f"{API}/account-groups/{group_b.id}", headers=headers_b
        ).json()
        assert untouched["name"] == "Grupo Viña Indómita"

    def test_broker_a_cannot_attach_its_client_to_broker_b_group(
        self, client, world, groups, headers_a
    ) -> None:
        _, group_b = groups
        response = client.post(
            f"{API}/account-groups/{group_b.id}/clients",
            json={"client_id": world.a.client.id},
            headers=headers_a,
        )
        assert response.status_code in DENIED, response.text

    def test_broker_a_cannot_attach_broker_b_client_to_its_own_group(
        self, client, world, groups, headers_a
    ) -> None:
        group_a, _ = groups
        response = client.post(
            f"{API}/account-groups/{group_a.id}/clients",
            json={"client_id": world.b.client.id},
            headers=headers_a,
        )
        assert response.status_code in DENIED, response.text

    def test_the_same_name_in_two_brokers_is_two_independent_rows(
        self, client, world, groups, headers_a, headers_b
    ) -> None:
        group_a, group_b = groups
        assert group_a.id != group_b.id
        assert group_a.slug == group_b.slug
        detail_a = client.get(f"{API}/account-groups/{group_a.id}", headers=headers_a)
        detail_b = client.get(f"{API}/account-groups/{group_b.id}", headers=headers_b)
        assert detail_a.status_code == 200, detail_a.text
        assert detail_b.status_code == 200, detail_b.text
        assert detail_a.json()["id"] == group_a.id
        assert detail_b.json()["id"] == group_b.id
        # ...and each is invisible to the other tenant despite the shared slug.
        assert (
            client.get(f"{API}/account-groups/{group_a.id}", headers=headers_b).status_code
            in DENIED
        )
        assert (
            client.get(f"{API}/account-groups/{group_b.id}", headers=headers_a).status_code
            in DENIED
        )


class TestNavigatorIsolation:
    def test_the_rail_only_carries_the_callers_groups(
        self, client, world, groups, headers_a, headers_b
    ) -> None:
        group_a, group_b = groups
        body_a = client.get(f"{API}/navigator", headers=headers_a)
        body_b = client.get(f"{API}/navigator", headers=headers_b)
        assert body_a.status_code == 200, body_a.text
        assert body_b.status_code == 200, body_b.text

        ids_a = {g["id"] for g in body_a.json()["groups"]}
        ids_b = {g["id"] for g in body_b.json()["groups"]}
        assert ids_a == {group_a.id}
        assert ids_b == {group_b.id}
        assert ids_a.isdisjoint(ids_b)

    def test_the_rail_never_counts_the_other_brokers_cases(
        self, client, world, db, groups, headers_a
    ) -> None:
        from tests.conftest import make_case_file

        group_a, group_b = groups
        foreign = make_case_file(
            db, world.b, account_group_id=group_b.id, client_id=world.b.client.id
        )
        mine = make_case_file(
            db, world.a, account_group_id=group_a.id, client_id=world.a.client.id
        )
        db.commit()

        body = client.get(f"{API}/navigator", headers=headers_a).json()
        assert [g["id"] for g in body["groups"]] == [group_a.id]
        recent_ids = {entry["case_file_id"] for entry in body["recent"]}
        assert mine.id in recent_ids
        assert foreign.id not in recent_ids
        assert all(entry["group_id"] in (None, group_a.id) for entry in body["recent"])


class TestSearchIsolation:
    def test_search_never_returns_the_other_brokers_group(
        self, client, world, groups, headers_a, headers_b
    ) -> None:
        group_a, group_b = groups
        hits_a = client.get(f"{API}/search", params={"q": "Indómita"}, headers=headers_a)
        hits_b = client.get(f"{API}/search", params={"q": "Indómita"}, headers=headers_b)
        assert hits_a.status_code == 200, hits_a.text
        assert hits_b.status_code == 200, hits_b.text

        ids_a = {hit["id"] for hit in hits_a.json()["groups"]}
        ids_b = {hit["id"] for hit in hits_b.json()["groups"]}
        assert ids_a == {group_a.id}
        assert ids_b == {group_b.id}
        assert ids_a.isdisjoint(ids_b)

    def test_a_role_without_the_groups_module_gets_no_group_hits(
        self, client, world, db, groups, headers_a_inspector
    ) -> None:
        """The inspector's ``Groups`` grant is ``partial``; a denied role is
        simply absent from the payload rather than 403-ing the whole search."""
        response = client.get(
            f"{API}/search", params={"q": "Indómita"}, headers=headers_a_inspector
        )
        assert response.status_code == 200, response.text
        assert isinstance(response.json()["groups"], list)
