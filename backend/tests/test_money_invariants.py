"""The Chilean premium arithmetic, at the pure-function and the HTTP layer.

    net   = taxable + exempt          # earthquake cover is VAT-exempt
    vat   = 0.19 * taxable            # on the TAXABLE part, NOT on net
    total = net + vat
    comprehensive_rate = taxable_rate + exempt_rate

A broken combination must be refused with 422, not silently stored or silently
"corrected".
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.proposal import MONEY_TOLERANCE, VAT_RATE, reconcile_money
from tests.conftest import API


def D(value: str) -> Decimal:
    return Decimal(value)


# --- The pure function -------------------------------------------------------

class TestReconcileMoneyDerivation:
    def test_derives_net_vat_and_total_from_taxable_and_exempt(self) -> None:
        derived, errors = reconcile_money(
            {"taxable_premium_uf": D("100"), "exempt_premium_uf": D("50")}
        )
        assert errors == []
        assert derived["net_premium_uf"] == D("150.0000")
        assert derived["vat_uf"] == D("19.0000")          # 0.19 * 100, not * 150
        assert derived["total_premium_uf"] == D("169.0000")

    def test_vat_is_computed_on_taxable_never_on_net(self) -> None:
        derived, errors = reconcile_money(
            {"taxable_premium_uf": D("200"), "exempt_premium_uf": D("800")}
        )
        assert errors == []
        # The trap: 0.19 * net would be 190. The rule says 0.19 * taxable = 38.
        assert derived["vat_uf"] == D("38.0000")
        assert derived["vat_uf"] != D("1000") * VAT_RATE
        assert derived["total_premium_uf"] == D("1038.0000")

    def test_derives_exempt_from_net_minus_taxable(self) -> None:
        derived, errors = reconcile_money(
            {"taxable_premium_uf": D("100"), "net_premium_uf": D("150")}
        )
        assert errors == []
        assert derived["exempt_premium_uf"] == D("50.0000")

    def test_derives_comprehensive_rate(self) -> None:
        derived, errors = reconcile_money(
            {"taxable_rate_permille": D("1.2"), "exempt_rate_permille": D("0.8")}
        )
        assert errors == []
        assert derived["comprehensive_rate_permille"] == D("2.0000")

    def test_zero_exempt_is_honoured_not_treated_as_missing(self) -> None:
        derived, errors = reconcile_money(
            {"taxable_premium_uf": D("100"), "exempt_premium_uf": D("0")}
        )
        assert errors == []
        assert derived["net_premium_uf"] == D("100.0000")
        assert derived["vat_uf"] == D("19.0000")

    def test_supplied_consistent_values_produce_no_errors(self) -> None:
        _, errors = reconcile_money(
            {
                "taxable_premium_uf": D("100"),
                "exempt_premium_uf": D("50"),
                "net_premium_uf": D("150"),
                "vat_uf": D("19"),
                "total_premium_uf": D("169"),
            }
        )
        assert errors == []

    def test_rounding_within_tolerance_is_accepted(self) -> None:
        # Insurer PDFs round to 2 decimals; a cent of drift must not be an error.
        _, errors = reconcile_money(
            {
                "taxable_premium_uf": D("100.005"),
                "exempt_premium_uf": D("50"),
                "net_premium_uf": D("150.01"),
                "vat_uf": D("19.00"),
                "total_premium_uf": D("169.01"),
            }
        )
        assert errors == []
        assert MONEY_TOLERANCE == D("0.02")


class TestReconcileMoneyRejection:
    def test_bad_net_is_reported(self) -> None:
        _, errors = reconcile_money(
            {
                "taxable_premium_uf": D("100"),
                "exempt_premium_uf": D("50"),
                "net_premium_uf": D("999"),
            }
        )
        assert [e["field"] for e in errors] == ["net_premium_uf"]

    def test_vat_charged_on_net_is_reported(self) -> None:
        # The single most likely real-world mistake: 0.19 * (taxable + exempt).
        _, errors = reconcile_money(
            {
                "taxable_premium_uf": D("100"),
                "exempt_premium_uf": D("50"),
                "net_premium_uf": D("150"),
                "vat_uf": D("28.50"),          # 0.19 * 150 — wrong
            }
        )
        fields = [e["field"] for e in errors]
        assert "vat_uf" in fields

    def test_bad_total_is_reported(self) -> None:
        _, errors = reconcile_money(
            {
                "taxable_premium_uf": D("100"),
                "exempt_premium_uf": D("50"),
                "net_premium_uf": D("150"),
                "vat_uf": D("19"),
                "total_premium_uf": D("200"),
            }
        )
        assert [e["field"] for e in errors] == ["total_premium_uf"]

    def test_bad_comprehensive_rate_is_reported(self) -> None:
        _, errors = reconcile_money(
            {
                "taxable_rate_permille": D("1.2"),
                "exempt_rate_permille": D("0.8"),
                "comprehensive_rate_permille": D("5.0"),
            }
        )
        assert [e["field"] for e in errors] == ["comprehensive_rate_permille"]

    def test_net_below_taxable_is_reported(self) -> None:
        # Implies a negative exempt premium, which is nonsense.
        _, errors = reconcile_money(
            {"taxable_premium_uf": D("100"), "net_premium_uf": D("40")}
        )
        assert errors != []

    def test_every_broken_invariant_is_reported_at_once(self) -> None:
        _, errors = reconcile_money(
            {
                "taxable_premium_uf": D("100"),
                "exempt_premium_uf": D("50"),
                "net_premium_uf": D("999"),
                "vat_uf": D("999"),
                "total_premium_uf": D("999"),
            }
        )
        # The broker should see all of it, not fix one error per round-trip.
        assert len(errors) >= 3


# --- Over HTTP ---------------------------------------------------------------

def _proposal_body(world, **overrides) -> dict:
    body = {
        "quote_request_id": world.a.quote.id,
        "insurer_id": world.insurer.id,
        "source_document_id": world.a.document.id,
    }
    body.update(overrides)
    return body


class TestMoneyInvariantsOverHttp:
    def test_create_derives_the_missing_money_fields(self, client, world, headers_a) -> None:
        response = client.post(
            f"{API}/proposals",
            json=_proposal_body(
                world, taxable_premium_uf="100", exempt_premium_uf="50"
            ),
            headers=headers_a,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert Decimal(body["net_premium_uf"]) == D("150")
        assert Decimal(body["vat_uf"]) == D("19")
        assert Decimal(body["total_premium_uf"]) == D("169")

    @pytest.mark.parametrize(
        ("payload", "bad_field"),
        [
            ({"taxable_premium_uf": "100", "exempt_premium_uf": "50",
              "net_premium_uf": "999"}, "net_premium_uf"),
            ({"taxable_premium_uf": "100", "exempt_premium_uf": "50",
              "net_premium_uf": "150", "vat_uf": "28.50"}, "vat_uf"),
            ({"taxable_premium_uf": "100", "exempt_premium_uf": "50",
              "net_premium_uf": "150", "vat_uf": "19",
              "total_premium_uf": "500"}, "total_premium_uf"),
            ({"taxable_rate_permille": "1.2", "exempt_rate_permille": "0.8",
              "comprehensive_rate_permille": "9"}, "comprehensive_rate_permille"),
        ],
    )
    def test_bad_combination_is_rejected_422(
        self, client, world, headers_a, payload, bad_field
    ) -> None:
        response = client.post(
            f"{API}/proposals", json=_proposal_body(world, **payload), headers=headers_a
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "money_invariant_violated"
        assert bad_field in [e["field"] for e in detail["errors"]]

    def test_rejected_proposal_is_not_persisted(self, client, world, headers_a) -> None:
        client.post(
            f"{API}/proposals",
            json=_proposal_body(
                world, taxable_premium_uf="100", exempt_premium_uf="50",
                net_premium_uf="999",
            ),
            headers=headers_a,
        )
        listing = client.get(f"{API}/proposals", headers=headers_a)
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

    def test_patch_recomputes_derived_values_from_a_new_taxable(
        self, client, world, headers_a
    ) -> None:
        created = client.post(
            f"{API}/proposals",
            json=_proposal_body(world, taxable_premium_uf="100", exempt_premium_uf="50"),
            headers=headers_a,
        )
        proposal_id = created.json()["id"]

        # Patching only taxable must re-derive net/vat/total, not 422 on staleness.
        patched = client.patch(
            f"{API}/proposals/{proposal_id}",
            json={"taxable_premium_uf": "200"},
            headers=headers_a,
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert Decimal(body["net_premium_uf"]) == D("250")
        assert Decimal(body["vat_uf"]) == D("38")
        assert Decimal(body["total_premium_uf"]) == D("288")

    def test_patch_with_an_explicit_contradiction_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        created = client.post(
            f"{API}/proposals",
            json=_proposal_body(world, taxable_premium_uf="100", exempt_premium_uf="50"),
            headers=headers_a,
        )
        proposal_id = created.json()["id"]

        patched = client.patch(
            f"{API}/proposals/{proposal_id}",
            json={"taxable_premium_uf": "200", "vat_uf": "19"},  # vat must be 38
            headers=headers_a,
        )
        assert patched.status_code == 422, patched.text
        assert patched.json()["detail"]["code"] == "money_invariant_violated"

    def test_negative_premium_is_rejected(self, client, world, headers_a) -> None:
        response = client.post(
            f"{API}/proposals",
            json=_proposal_body(world, taxable_premium_uf="-100"),
            headers=headers_a,
        )
        assert response.status_code == 422
