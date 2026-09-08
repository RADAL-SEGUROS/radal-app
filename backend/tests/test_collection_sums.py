"""The collection invariant: Σ instalments == policy gross + Σ endorsement deltas."""
from __future__ import annotations

from decimal import Decimal

from app.schemas.collection import check_installment_total
from tests.conftest import API


def _plan_payload(policy_id: int, amounts: list[str]) -> dict:
    return {
        "policy_id": policy_id,
        "plan_number": "PP-1",
        "payment_mode": "coupon_book",
        "installments": [
            {
                "number": index,
                "coupon_number": f"{index:03d}",
                "due_date": f"2026-{index:02d}-05",
                "gross_amount_uf": amount,
                "status": "pending",
            }
            for index, amount in enumerate(amounts, start=1)
        ],
    }


# --- The pure check ---------------------------------------------------------------

def test_check_passes_within_tolerance():
    assert (
        check_installment_total(
            installments_total=Decimal("139.01"),
            policy_gross_uf=Decimal("139.00"),
        )
        is None
    )


def test_check_reports_the_difference():
    error = check_installment_total(
        installments_total=Decimal("100"), policy_gross_uf=Decimal("139")
    )
    assert error is not None
    assert error["expected"] == "139"
    assert error["difference"] == "-39"


def test_check_is_skipped_without_a_policy_premium():
    assert check_installment_total(installments_total=Decimal("10"), policy_gross_uf=None) is None


def test_check_includes_the_endorsement_deltas():
    assert (
        check_installment_total(
            installments_total=Decimal("150.9"),
            policy_gross_uf=Decimal("139"),
            endorsement_delta_uf=Decimal("11.9"),
        )
        is None
    )


# --- Through the API ----------------------------------------------------------------

def test_create_plan_refuses_an_unbalanced_ledger(client, headers_a, insurer_policy):
    """Policy gross is 139; three cuotas of 40 are 120 — that is a 422, not a fix."""
    response = client.post(
        f"{API}/collections",
        headers=headers_a,
        json=_plan_payload(insurer_policy.id, ["40", "40", "40"]),
    )
    assert response.status_code == 422
    detail = response.json()["detail"][0]
    assert detail["field"] == "installments"
    assert detail["expected"] == "139.0000"


def test_create_plan_accepts_a_balanced_ledger(client, headers_a, insurer_policy):
    response = client.post(
        f"{API}/collections",
        headers=headers_a,
        json=_plan_payload(insurer_policy.id, ["46.33", "46.33", "46.34"]),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["installments"]) == 3
    assert Decimal(body["total_premium_uf"]) == Decimal("139.00")


def test_replace_installments_validates_before_writing(client, headers_a, insurer_policy):
    plan = client.post(
        f"{API}/collections",
        headers=headers_a,
        json=_plan_payload(insurer_policy.id, ["139"]),
    ).json()

    bad = client.put(
        f"{API}/collections/{plan['id']}/installments",
        headers=headers_a,
        json={"installments": [{"number": 1, "gross_amount_uf": "1"}]},
    )
    assert bad.status_code == 422

    unchanged = client.get(f"{API}/collections/{plan['id']}", headers=headers_a).json()
    assert Decimal(unchanged["installments"][0]["gross_amount_uf"]) == Decimal("139")

    good = client.put(
        f"{API}/collections/{plan['id']}/installments",
        headers=headers_a,
        json={
            "installments": [
                {"number": 1, "gross_amount_uf": "69.5"},
                {"number": 2, "gross_amount_uf": "69.5"},
            ]
        },
    )
    assert good.status_code == 200, good.text
    assert len(good.json()["installments"]) == 2


def test_duplicate_instalment_numbers_are_422(client, headers_a, insurer_policy):
    response = client.post(
        f"{API}/collections",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "installments": [
                {"number": 1, "gross_amount_uf": "69.5"},
                {"number": 1, "gross_amount_uf": "69.5"},
            ],
        },
    )
    assert response.status_code == 422
    assert "Duplicate instalment number" in response.json()["detail"]


def test_marking_a_cuota_paid_derives_days_late(client, headers_a, insurer_policy):
    plan = client.post(
        f"{API}/collections",
        headers=headers_a,
        json=_plan_payload(insurer_policy.id, ["139"]),
    ).json()
    response = client.patch(
        f"{API}/collections/{plan['id']}/installments/1",
        headers=headers_a,
        json={"paid_on": "2026-01-20"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days_late"] == 15
    assert body["status"] == "paid_late"


def test_status_dashboard_reports_arrears_and_compliance(client, headers_a, insurer_policy):
    plan = client.post(
        f"{API}/collections",
        headers=headers_a,
        json={
            **_plan_payload(insurer_policy.id, ["69.5", "69.5"]),
            "as_of_date": "2026-03-01",
        },
    ).json()
    client.patch(
        f"{API}/collections/{plan['id']}/installments/1",
        headers=headers_a,
        json={"paid_on": "2026-01-05"},
    )

    body = client.get(f"{API}/collections/{plan['id']}/status", headers=headers_a).json()
    assert Decimal(body["total_scheduled_uf"]) == Decimal("139")
    assert Decimal(body["paid_uf"]) == Decimal("69.5")
    assert Decimal(body["outstanding_uf"]) == Decimal("69.5")
    assert body["overdue_count"] == 1
    assert Decimal(body["compliance_pct"]) == Decimal("50.00")
    assert body["balances"] is True
    assert any(alert["code"] == "installments_overdue" for alert in body["alerts"])


def test_issuing_an_endorsement_keeps_the_ledger_balanced(
    client, world, headers_a, insurer_policy
):
    plan = client.post(
        f"{API}/collections",
        headers=headers_a,
        json=_plan_payload(insurer_policy.id, ["69.5", "69.5"]),
    ).json()

    endorsement = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "kind": "location_inclusion",
            "taxable_premium_delta_uf": "10",
            "exempt_premium_delta_uf": "0",
        },
    ).json()
    issued = client.post(
        f"{API}/endorsements/{endorsement['id']}/issue",
        headers=headers_a,
        json={"issued_document_id": world.a.document.id},
    )
    assert issued.status_code == 200, issued.text
    # 139 + 11.9 = 150.9, absorbed by the last open cuota.
    assert Decimal(issued.json()["collection_total_premium_uf"]) == Decimal("150.9")

    body = client.get(f"{API}/collections/{plan['id']}/status", headers=headers_a).json()
    assert Decimal(body["total_scheduled_uf"]) == Decimal("150.9")
    assert Decimal(body["expected_total_uf"]) == Decimal("150.9")
    assert body["balances"] is True


def test_collections_tenancy_and_permissions(
    client, world, headers_a, headers_b, headers_a_exec, insurer_policy
):
    plan = client.post(
        f"{API}/collections",
        headers=headers_a,
        json=_plan_payload(insurer_policy.id, ["139"]),
    ).json()
    assert client.get(f"{API}/collections/{plan['id']}", headers=headers_b).status_code == 404
    # The executive may read the ledger but not edit it.
    assert client.get(f"{API}/collections/{plan['id']}", headers=headers_a_exec).status_code == 200
    denied = client.patch(
        f"{API}/collections/{plan['id']}/installments/1",
        headers=headers_a_exec,
        json={"paid_on": "2026-01-05"},
    )
    assert denied.status_code == 403
