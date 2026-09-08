"""Post-sale: policies, the mirror-diff, endorsements, warranties and claims."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models.ai import Extraction, ExtractionKind, ExtractionStatus
from app.models.case_file import CaseFile
from app.models.document import DocumentCategory
from app.models.endorsement import Endorsement
from app.models.enums import (
    CaseFileKind,
    EndorsementKind,
    EndorsementStatus,
)
from app.services import mirror
from tests.conftest import API, make_case_file, make_policy


# --- Policies -------------------------------------------------------------------

def test_create_policy_enforces_the_money_invariants(client, world, headers_a):
    bad = client.post(
        f"{API}/policies",
        headers=headers_a,
        json={
            "client_id": world.a.client.id,
            "insurer_id": world.insurer.id,
            "policy_number": "POL-BAD",
            "taxable_premium_uf": "100",
            "exempt_premium_uf": "20",
            # vat must be 0.19 * taxable = 19, not 0.19 * net
            "vat_uf": "22.8",
        },
    )
    assert bad.status_code == 422
    assert any(item["field"] == "vat_uf" for item in bad.json()["detail"])

    ok = client.post(
        f"{API}/policies",
        headers=headers_a,
        json={
            "client_id": world.a.client.id,
            "insurer_id": world.insurer.id,
            "policy_number": "POL-OK",
            "taxable_premium_uf": "100",
            "exempt_premium_uf": "20",
        },
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert Decimal(body["net_premium_uf"]) == Decimal("120")
    assert Decimal(body["vat_uf"]) == Decimal("19")
    assert Decimal(body["total_premium_uf"]) == Decimal("139")


def test_policy_number_is_unique_per_broker_only(client, world, headers_a, headers_b):
    payload = {
        "insurer_id": world.insurer.id,
        "policy_number": "0020119904",
    }
    first = client.post(
        f"{API}/policies", headers=headers_a, json={**payload, "client_id": world.a.client.id}
    )
    assert first.status_code == 201, first.text
    dupe = client.post(
        f"{API}/policies", headers=headers_a, json={**payload, "client_id": world.a.client.id}
    )
    assert dupe.status_code == 422
    # Another broker may hold the same carrier folio.
    other = client.post(
        f"{API}/policies", headers=headers_b, json={**payload, "client_id": world.b.client.id}
    )
    assert other.status_code == 201, other.text


def test_policy_of_another_tenant_is_404(client, insurer_policy, headers_b):
    assert client.get(f"{API}/policies/{insurer_policy.id}", headers=headers_b).status_code == 404


def test_post_sale_sub_funnel_is_ordered(client, world, db, headers_a, insurer_policy):
    from app.models.enums import CaseFileKind, CaseStage

    make_case_file(
        db, world.a, kind=CaseFileKind.ENDORSEMENT, stage=CaseStage.ENDORSEMENT_REQUESTED,
        placement_id=None, policy_id=insurer_policy.id, sequence_no=2, n=2,
    )
    make_case_file(
        db, world.a, kind=CaseFileKind.ENDORSEMENT, stage=CaseStage.ENDORSEMENT_ISSUED,
        placement_id=None, policy_id=insurer_policy.id, sequence_no=1, n=1,
    )
    db.commit()

    body = client.get(f"{API}/policies/{insurer_policy.id}/case-files", headers=headers_a).json()
    assert [item["sequence_no"] for item in body] == [1, 2]


# --- Endorsements ------------------------------------------------------------------

def test_endorsement_deltas_are_sign_preserving(client, headers_a, insurer_policy):
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "kind": "sum_insured_decrease",
            "taxable_premium_delta_uf": "-10",
            "exempt_premium_delta_uf": "-2",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(body["net_premium_delta_uf"]) == Decimal("-12")
    assert Decimal(body["vat_delta_uf"]) == Decimal("-1.9")
    assert Decimal(body["total_premium_delta_uf"]) == Decimal("-13.9")


def test_all_zero_administrative_endorsement_is_valid(client, headers_a, insurer_policy):
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "kind": "pledge_update",
            "taxable_premium_delta_uf": "0",
            "exempt_premium_delta_uf": "0",
            "net_premium_delta_uf": "0",
            "vat_delta_uf": "0",
            "total_premium_delta_uf": "0",
        },
    )
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["total_premium_delta_uf"]) == Decimal("0")


def test_endorsement_money_mismatch_is_422(client, headers_a, insurer_policy):
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "taxable_premium_delta_uf": "10",
            "exempt_premium_delta_uf": "0",
            "vat_delta_uf": "1.9",
            "total_premium_delta_uf": "99",
        },
    )
    assert response.status_code == 422
    assert any(item["field"] == "total_premium_delta_uf" for item in response.json()["detail"])


def test_issue_requires_the_carrier_document_and_applies_the_deltas(
    client, world, db, headers_a, insurer_policy
):
    created = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "kind": "location_inclusion",
            "insured_amount_delta_uf": "5000",
            "taxable_premium_delta_uf": "10",
            "exempt_premium_delta_uf": "0",
        },
    ).json()

    blocked = client.post(
        f"{API}/endorsements/{created['id']}/issue", headers=headers_a, json={}
    )
    assert blocked.status_code == 422
    assert "issued_document_id is required" in blocked.json()["detail"]

    issued = client.post(
        f"{API}/endorsements/{created['id']}/issue",
        headers=headers_a,
        json={
            "issued_document_id": world.a.document.id,
            "endorsement_number": "791237-4",
        },
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["endorsement"]["status"] == "applied"
    # 139 + (10 + 0 + 1.9) = 150.9
    assert Decimal(body["policy_total_premium_uf"]) == Decimal("150.9")
    assert Decimal(body["policy_insured_amount_uf"]) == Decimal("105000")


def test_issued_endorsement_deltas_are_frozen(client, world, headers_a, insurer_policy):
    created = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={"policy_id": insurer_policy.id, "taxable_premium_delta_uf": "10",
              "exempt_premium_delta_uf": "0"},
    ).json()
    client.post(
        f"{API}/endorsements/{created['id']}/issue",
        headers=headers_a,
        json={"issued_document_id": world.a.document.id},
    )
    patched = client.patch(
        f"{API}/endorsements/{created['id']}",
        headers=headers_a,
        json={"taxable_premium_delta_uf": "999"},
    )
    assert patched.status_code == 422


def test_endorsement_tenancy(client, db, world, insurer_policy, headers_b):
    endorsement = Endorsement(
        broker_id=world.a.broker_id,
        policy_id=insurer_policy.id,
        sequence_no=1,
        kind=EndorsementKind.OTHER,
        status=EndorsementStatus.DRAFT,
    )
    db.add(endorsement)
    db.commit()
    assert client.get(f"{API}/endorsements/{endorsement.id}", headers=headers_b).status_code == 404


# --- Prórroga: the batch endorsement (spec v3 §4.3) ----------------------------------

def _account_group(db, tenant, name: str, slug: str):
    from app.models.account_group import AccountGroup

    group = AccountGroup(broker_id=tenant.broker_id, name=name, slug=slug)
    db.add(group)
    db.flush()
    return group


def _grouped_policy(db, world, group, *, n: int, number: str, end_date: date):
    """An account folder in ``group`` plus the policy issued under it."""
    case = make_case_file(
        db,
        world.a,
        n=n,
        account_group_id=group.id,
        period_start=date(2026, 1, 1),
        period_end=date(2027, 1, 1),
        period_label="2026-2027",
    )
    policy = make_policy(
        db,
        world.a,
        world.insurer,
        policy_number=number,
        case_file_id=case.id,
        end_date=end_date,
        period_end_at=datetime(end_date.year, end_date.month, end_date.day, 12, 0),
    )
    return case, policy


def _batch_body(policy_ids: list[int], new_end: str = "2027-04-01") -> dict:
    return {
        "policy_ids": policy_ids,
        "kind": "period_extension",
        "new_end_date": new_end,
        "effective_at": "2027-01-01T12:00:00Z",
    }


def test_a_single_endorsement_refuses_the_batched_motive(client, headers_a, insurer_policy):
    """A prórroga is a manual multi-select — never a one-off POST (rule 3)."""
    response = client.post(
        f"{API}/endorsements",
        headers=headers_a,
        json={"policy_id": insurer_policy.id, "kind": "period_extension"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "use_endorsement_batch"


def test_batch_writes_one_endorsement_and_one_case_per_policy(client, db, world, headers_a):
    group = _account_group(db, world.a, "GRUPO VIÑA INDÓMITA", "grupo-vina-indomita")
    case_one, policy_one = _grouped_policy(
        db, world, group, n=1, number="POL-BATCH-1", end_date=date(2027, 1, 1)
    )
    case_two, policy_two = _grouped_policy(
        db, world, group, n=2, number="POL-BATCH-2", end_date=date(2027, 1, 1)
    )
    db.commit()

    response = client.post(
        f"{API}/endorsements/batch",
        headers=headers_a,
        json=_batch_body([policy_one.id, policy_two.id]),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "period_extension"
    assert body["new_end_date"] == "2027-04-01"
    assert {item["policy_id"] for item in body["items"]} == {policy_one.id, policy_two.id}
    assert all(item["case_file_id"] is not None for item in body["items"])
    batch_key = body["batch_key"]
    assert len(batch_key) == 36

    rows = db.scalars(
        select(Endorsement).where(Endorsement.batch_key == batch_key)
    ).all()
    assert len(rows) == 2
    for row in rows:
        assert row.kind is EndorsementKind.PERIOD_EXTENSION
        assert row.status is EndorsementStatus.DRAFT
        # A prórroga is a date, not money: nothing for the ledger to absorb.
        assert Decimal(row.total_premium_delta_uf) == Decimal("0")
        assert Decimal(row.taxable_premium_delta_uf) == Decimal("0")
        assert row.effect["period_end"] == {"before": "2027-01-01", "after": "2027-04-01"}
        assert row.case_file_id is not None

    # The N child folders inherit group and period from the policy's account.
    children = db.scalars(
        select(CaseFile).where(
            CaseFile.broker_id == world.a.broker_id,
            CaseFile.kind == CaseFileKind.ENDORSEMENT,
        )
    ).all()
    assert len(children) == 2
    assert {child.account_group_id for child in children} == {group.id}
    assert {child.period_label for child in children} == {"2026-2027"}
    assert {child.period_end for child in children} == {date(2027, 1, 1)}
    assert {child.parent_case_file_id for child in children} == {case_one.id, case_two.id}
    assert {child.policy_id for child in children} == {policy_one.id, policy_two.id}


def test_batch_refuses_policies_from_two_groups(client, db, world, headers_a):
    first = _account_group(db, world.a, "COCCOLINO", "coccolino")
    second = _account_group(db, world.a, "LA FAVORITA", "la-favorita")
    _, policy_one = _grouped_policy(
        db, world, first, n=1, number="POL-G1", end_date=date(2027, 1, 1)
    )
    _, policy_two = _grouped_policy(
        db, world, second, n=2, number="POL-G2", end_date=date(2027, 1, 1)
    )
    db.commit()

    response = client.post(
        f"{API}/endorsements/batch",
        headers=headers_a,
        json=_batch_body([policy_one.id, policy_two.id]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "policies_span_groups"
    # Nothing was written.
    assert db.scalars(select(Endorsement)).all() == []


def test_batch_of_another_tenants_policy_is_404(client, db, world, headers_b, insurer_policy):
    response = client.post(
        f"{API}/endorsements/batch",
        headers=headers_b,
        json=_batch_body([insurer_policy.id]),
    )
    assert response.status_code == 404


def test_batch_issue_moves_every_end_date_and_leaves_the_ledger_alone(
    client, db, world, headers_a
):
    group = _account_group(db, world.a, "COCCOLINO", "coccolino")
    _, policy_one = _grouped_policy(
        db, world, group, n=1, number="POL-EXT-1", end_date=date(2027, 1, 1)
    )
    _, policy_two = _grouped_policy(
        db, world, group, n=2, number="POL-EXT-2", end_date=date(2027, 2, 15)
    )
    db.commit()

    # A payment plan whose Σ instalments already equals the policy gross.
    plan = client.post(
        f"{API}/collections",
        headers=headers_a,
        json={
            "policy_id": policy_one.id,
            "total_premium_uf": "139",
            "installments": [
                {"number": 1, "gross_amount_uf": "70"},
                {"number": 2, "gross_amount_uf": "69"},
            ],
        },
    )
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]

    created = client.post(
        f"{API}/endorsements/batch",
        headers=headers_a,
        json=_batch_body([policy_one.id, policy_two.id]),
    ).json()
    batch_key = created["batch_key"]

    issued = client.post(
        f"{API}/endorsements/batch/{batch_key}/issue",
        headers=headers_a,
        json={"issued_document_id": world.a.document.id, "issued_at": "2027-01-02"},
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["batch_key"] == batch_key
    assert {item["policy_end_date"] for item in body["items"]} == {"2027-04-01"}

    db.expire_all()
    for policy in (policy_one, policy_two):
        db.refresh(policy)
        # Only the policy's own end date moved (rule 3).
        assert policy.end_date == date(2027, 4, 1)
        assert policy.period_end_at.date() == date(2027, 4, 1)
        # ...and the premium is exactly where it was.
        assert Decimal(policy.total_premium_uf) == Decimal("139")

    members = db.scalars(
        select(Endorsement).where(Endorsement.batch_key == batch_key)
    ).all()
    assert {row.status for row in members} == {EndorsementStatus.APPLIED}
    assert {row.issued_document_id for row in members} == {world.a.document.id}

    ledger = client.get(f"{API}/collections/{plan_id}", headers=headers_a).json()
    assert Decimal(ledger["total_premium_uf"]) == Decimal("139")
    assert [Decimal(row["gross_amount_uf"]) for row in ledger["installments"]] == [
        Decimal("70"),
        Decimal("69"),
    ]
    status_body = client.get(
        f"{API}/collections/{plan_id}/status", headers=headers_a
    ).json()
    assert status_body["balances"] is True


def test_batch_members_are_listed_by_batch_key(client, db, world, headers_a):
    group = _account_group(db, world.a, "LA FAVORITA", "la-favorita")
    _, policy_one = _grouped_policy(
        db, world, group, n=1, number="POL-KEY-1", end_date=date(2027, 1, 1)
    )
    _, policy_two = _grouped_policy(
        db, world, group, n=2, number="POL-KEY-2", end_date=date(2027, 1, 1)
    )
    db.commit()

    batch_key = client.post(
        f"{API}/endorsements/batch",
        headers=headers_a,
        json=_batch_body([policy_one.id, policy_two.id]),
    ).json()["batch_key"]

    listed = client.get(
        f"{API}/endorsements?batch_key={batch_key}", headers=headers_a
    ).json()
    assert listed["total"] == 2
    assert {item["batch_key"] for item in listed["items"]} == {batch_key}

    empty = client.get(
        f"{API}/endorsements?batch_key=no-such-batch", headers=headers_a
    ).json()
    assert empty["total"] == 0

    missing = client.post(
        f"{API}/endorsements/batch/no-such-batch/issue", headers=headers_a, json={}
    )
    assert missing.status_code == 404


def test_batch_issue_needs_the_carrier_document(client, db, world, headers_a):
    group = _account_group(db, world.a, "COCCOLINO", "coccolino")
    _, policy = _grouped_policy(
        db, world, group, n=1, number="POL-NODOC-1", end_date=date(2027, 1, 1)
    )
    db.commit()

    batch_key = client.post(
        f"{API}/endorsements/batch", headers=headers_a, json=_batch_body([policy.id])
    ).json()["batch_key"]

    blocked = client.post(
        f"{API}/endorsements/batch/{batch_key}/issue", headers=headers_a, json={}
    )
    assert blocked.status_code == 422
    assert "issued_document_id is required" in blocked.json()["detail"]

    db.expire_all()
    db.refresh(policy)
    assert policy.end_date == date(2027, 1, 1)


# --- Warranties ---------------------------------------------------------------------

def test_warranty_tracker_round_trip(client, headers_a, insurer_policy):
    created = client.post(
        f"{API}/policies/{insurer_policy.id}/warranties",
        headers=headers_a,
        json={
            "code": "R-1",
            "title": "Red húmeda operativa",
            "source": "inspection_recommendation",
            "category": "A",
            "is_suspensive": True,
            "deadline_days": 30,
        },
    )
    assert created.status_code == 201, created.text
    warranty_id = created.json()["id"]

    listed = client.get(
        f"{API}/policies/{insurer_policy.id}/warranties", headers=headers_a
    ).json()
    assert [item["code"] for item in listed] == ["R-1"]

    met = client.patch(
        f"{API}/warranties/{warranty_id}",
        headers=headers_a,
        json={"status": "met_on_time", "completed_on": "2026-05-01"},
    )
    assert met.status_code == 200
    assert met.json()["status"] == "met_on_time"


# --- Mirror-diff ----------------------------------------------------------------------

def _extraction(db, world, case, category, payload, document):
    row = Extraction(
        broker_id=world.a.broker_id,
        document_id=document.id,
        case_file_id=case.id,
        kind=ExtractionKind.CASE_DOCUMENT,
        category=category,
        model="test-model",
        prompt_version="test-v1",
        parsed=payload,
        status=ExtractionStatus.SUCCEEDED,
    )
    db.add(row)
    return row


def test_mirror_diff_finds_the_real_discrepancies(client, world, db, headers_a):
    """The Southbridge case: three genuine differences between the 07 and the 08."""
    case = make_case_file(db, world.a)
    db.flush()
    policy = make_policy(db, world.a, world.insurer, case_file_id=case.id)

    _extraction(
        db, world, case, DocumentCategory.ISSUANCE_PROPOSAL,
        {
            "total_insured_amount_uf": 17920,
            "net_taxable": 100,
            "net_exempt": 20,
            "vat_uf": 19,
            "gross_uf": 139,
            "period_start": "2026-01-01",
            "cover_mode": "Todo Riesgo",
            "deductibles_to_issue": [
                {"peril": "incendio", "offered": "5% del siniestro, mínimo UF 25"},
                {"peril": "sismo", "offered": "1% del monto asegurado"},
            ],
            "coverages_to_issue": [{"n": 1, "name": "Incendio", "limit": "UF 17.920"}],
        },
        world.a.document,
    )
    _extraction(
        db, world, case, DocumentCategory.POLICY,
        {
            "total_insured_amount_uf": 17920,
            "net_taxable": 100,
            "net_exempt": 20,
            "vat_uf": 19,
            "gross_uf": 139,
            "period_start_at": "2026-01-01",
            # Discrepancy 1: the carrier wrote a different modality.
            "coverage_modality": "Riesgos Nombrados",
            "deductibles": [
                {"peril": "incendio", "offered": "5% del siniestro, mínimo UF 25"},
                # Discrepancy 2: 6 months where the mirror says 12.
                {"peril": "sismo", "offered": "2% del monto asegurado"},
            ],
            # Discrepancy 3: a coverage that never made it into the policy.
            "coverages": [],
        },
        world.a.document,
    )
    db.commit()

    body = client.get(f"{API}/policies/{policy.id}/mirror-diff", headers=headers_a).json()
    paths = {row["path"] for row in body["rows"]}
    assert "cover_mode" in paths
    assert "deductibles[sismo]" in paths
    assert "coverages[1]" in paths
    assert body["missing_sources"] == []

    queued = client.post(
        f"{API}/policies/{policy.id}/mirror-diff/queue",
        headers=headers_a,
        json={"paths": ["deductibles[sismo]"]},
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["queued"] == 1

    endorsements = client.get(
        f"{API}/endorsements?policy_id={policy.id}", headers=headers_a
    ).json()
    assert endorsements["total"] == 1
    only = endorsements["items"][0]
    assert only["status"] == "draft"
    assert only["kind"] == "deductible_reduction"
    assert only["is_confirmed"] is False


def test_mirror_diff_without_sources_reports_them_missing(client, headers_a, insurer_policy):
    body = client.get(f"{API}/policies/{insurer_policy.id}/mirror-diff", headers=headers_a).json()
    assert set(body["missing_sources"]) == {"issuance_proposal", "policy"}
    assert body["rows"] == []


def test_diff_payloads_is_pure():
    rows = mirror.diff_payloads(
        {"gross_uf": 139, "cover_mode": "Todo Riesgo"},
        {"gross_uf": 150, "cover_mode": "Todo Riesgo"},
    )
    assert [row.path for row in rows] == ["premium.gross_uf"]
    assert rows[0].severity == "high"


# --- Claims ---------------------------------------------------------------------------

def test_claim_items_are_summed_and_the_close_needs_a_ruling(
    client, world, headers_a, insurer_policy, headers_a_tech
):
    created = client.post(
        f"{API}/claims",
        headers=headers_a,
        json={
            "policy_id": insurer_policy.id,
            "claim_number": "SIN-2026-0417",
            "occurred_at": "2026-03-04T04:20:00Z",
            "items": [
                {"kind": "material_damage", "item": "Edificio", "notified_uf": "1000",
                 "determined_uf": "900", "damage_uf": "900", "deductible_uf": "45",
                 "indemnity_uf": "855"},
                {"kind": "business_interruption", "item": "Paralización",
                 "notified_uf": "500", "determined_uf": "400", "damage_uf": "400",
                 "deductible_uf": "0", "indemnity_uf": "400"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    claim_id = created.json()["id"]
    assert created.json()["event_date"] == "2026-03-04"

    items = client.get(f"{API}/claims/{claim_id}/items", headers=headers_a).json()
    assert Decimal(items["totals"]["indemnity_uf"]) == Decimal("1255")
    assert Decimal(items["totals"]["deductible_uf"]) == Decimal("45")

    pending = client.post(
        f"{API}/claims/{claim_id}/close",
        headers=headers_a,
        json={"coverage_ruling": "pending"},
    )
    assert pending.status_code == 422

    closed = client.post(
        f"{API}/claims/{claim_id}/close",
        headers=headers_a,
        json={"coverage_ruling": "covered", "paid_amount_uf": "1255"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert Decimal(closed.json()["settled_amount_uf"]) == Decimal("1255")


def test_claim_close_needs_approve(client, headers_a_exec, insurer_policy, headers_a):
    created = client.post(
        f"{API}/claims",
        headers=headers_a,
        json={"policy_id": insurer_policy.id, "claim_number": "SIN-1"},
    ).json()
    response = client.post(
        f"{API}/claims/{created['id']}/close",
        headers=headers_a_exec,
        json={"coverage_ruling": "covered"},
    )
    assert response.status_code == 403
