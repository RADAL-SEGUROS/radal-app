"""``quote_request.declared_value_uf`` must equal SUM(``quote_line_item.value_uf``).

This is the reason line items are a child table rather than a JSON array: the
invariant has to be checkable in SQL and enforceable on every mutation path —
create, patch, and each of the line-item sub-routes.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.models.quote import QuoteLineItem, QuoteRequest
from tests.conftest import API


def _create_quote(client, world, headers, **overrides) -> dict:
    body = {"placement_id": world.a.placement.id}
    body.update(overrides)
    return client.post(f"{API}/quotes", json=body, headers=headers)


ITEMS = [
    {"name": "Edificio", "value_uf": "60000", "sort_order": 0},
    {"name": "Maquinaria", "value_uf": "30000", "sort_order": 1},
    {"name": "Existencias", "value_uf": "10000", "sort_order": 2},
]


class TestDeclaredValueOnCreate:
    def test_matching_declared_value_is_accepted(self, client, world, headers_a) -> None:
        response = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert Decimal(body["declared_value_uf"]) == Decimal("100000")
        assert Decimal(body["line_items_total_uf"]) == Decimal("100000")
        assert len(body["line_items"]) == 3

    def test_mismatching_declared_value_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        response = _create_quote(
            client, world, headers_a, declared_value_uf="999999", line_items=ITEMS
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "declared_value_mismatch"
        assert detail["field"] == "declared_value_uf"
        assert Decimal(detail["line_items_total_uf"]) == Decimal("100000")

    def test_omitted_declared_value_is_derived_from_the_items(
        self, client, world, headers_a
    ) -> None:
        response = _create_quote(client, world, headers_a, line_items=ITEMS)
        assert response.status_code == 201, response.text
        assert Decimal(response.json()["declared_value_uf"]) == Decimal("100000")

    def test_a_quote_with_no_items_may_declare_any_value(
        self, client, world, headers_a
    ) -> None:
        # The itemised breakdown is optional until the first partida exists.
        response = _create_quote(client, world, headers_a, declared_value_uf="12345")
        assert response.status_code == 201, response.text
        assert Decimal(response.json()["declared_value_uf"]) == Decimal("12345")

    def test_rejected_quote_is_not_persisted(self, client, world, headers_a, db) -> None:
        before = db.scalar(select(func.count(QuoteRequest.id)))
        _create_quote(
            client, world, headers_a, declared_value_uf="999999", line_items=ITEMS
        )
        db.expire_all()
        assert db.scalar(select(func.count(QuoteRequest.id))) == before


class TestDeclaredValueOnUpdate:
    def test_replacing_items_resyncs_the_declared_value(
        self, client, world, headers_a
    ) -> None:
        quote_id = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()["id"]

        response = client.patch(
            f"{API}/quotes/{quote_id}",
            json={"line_items": [{"name": "Solo edificio", "value_uf": "70000"}]},
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        assert Decimal(response.json()["declared_value_uf"]) == Decimal("70000")

    def test_patching_the_declared_value_alone_is_rejected(
        self, client, world, headers_a
    ) -> None:
        quote_id = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()["id"]

        response = client.patch(
            f"{API}/quotes/{quote_id}", json={"declared_value_uf": "1"}, headers=headers_a
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "declared_value_mismatch"


class TestDeclaredValueOnLineItemRoutes:
    def test_adding_an_item_resyncs_the_declared_value(
        self, client, world, headers_a
    ) -> None:
        quote_id = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()["id"]

        response = client.post(
            f"{API}/quotes/{quote_id}/line-items",
            json={"name": "Equipos", "value_uf": "5000"},
            headers=headers_a,
        )
        assert response.status_code in (200, 201), response.text
        assert Decimal(response.json()["declared_value_uf"]) == Decimal("105000")

    def test_adding_an_item_with_sync_disabled_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        quote_id = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()["id"]

        response = client.post(
            f"{API}/quotes/{quote_id}/line-items?sync_declared_value=false",
            json={"name": "Equipos", "value_uf": "5000"},
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "declared_value_mismatch"

    def test_deleting_an_item_resyncs_the_declared_value(
        self, client, world, headers_a
    ) -> None:
        created = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()
        item_id = created["line_items"][0]["id"]        # Edificio, 60000

        response = client.delete(
            f"{API}/quotes/{created['id']}/line-items/{item_id}", headers=headers_a
        )
        assert response.status_code in (200, 204), response.text
        refreshed = client.get(f"{API}/quotes/{created['id']}", headers=headers_a).json()
        assert Decimal(refreshed["declared_value_uf"]) == Decimal("40000")

    def test_updating_an_item_value_resyncs_the_declared_value(
        self, client, world, headers_a
    ) -> None:
        created = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()
        item_id = created["line_items"][2]["id"]        # Existencias, 10000

        response = client.patch(
            f"{API}/quotes/{created['id']}/line-items/{item_id}",
            json={"value_uf": "20000"},
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        assert Decimal(response.json()["declared_value_uf"]) == Decimal("110000")


class TestInvariantHoldsInSql:
    def test_stored_rows_satisfy_the_invariant(self, client, world, headers_a, db) -> None:
        quote_id = _create_quote(
            client, world, headers_a, declared_value_uf="100000", line_items=ITEMS
        ).json()["id"]
        db.expire_all()

        declared = db.scalar(
            select(QuoteRequest.declared_value_uf).where(QuoteRequest.id == quote_id)
        )
        items_total = db.scalar(
            select(func.sum(QuoteLineItem.value_uf)).where(
                QuoteLineItem.quote_request_id == quote_id
            )
        )
        assert Decimal(declared) == Decimal(items_total) == Decimal("100000")
