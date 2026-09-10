"""The uniform filter vocabulary, the group-by buckets and the XLSX/PDF exports.

Three things are proven here, in this order of importance:

1. **No leak.** A foreign broker's ``account_group_id`` yields an EMPTY page on
   every list — never another tenant's rows, never a 403 that would confirm the
   id exists (non-negotiable 2). The export path is checked the same way.
2. **The filter actually narrows.** Feeding the grupo / grupo-cuenta really does
   drop the rows outside it, on every one of the thirteen entities.
3. **The contract holds.** ``group_by`` returns the documented bucket shape, an
   unsupported dimension is a 422 with ``code == "unsupported_group_by"``, XLSX
   is a real openable workbook with the right row count, PDF starts with
   ``%PDF``, and the row cap answers 422 instead of timing out.

The PDF pipeline is monkeypatched to a bytes stub exactly as the other PDF
suites do — the test suite never launches Chromium.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account_group import AccountGroup
from app.models.asset import Asset
from app.models.client import Client
from app.models.collection import CollectionPlan
from app.models.endorsement import Endorsement
from app.models.inspection import Inspection, InspectionStatus
from app.models.insured import Insured
from app.models.offering import Offering, OfferingStatus
from app.models.placement import Placement, PlacementStatus
from app.models.sales_lead import SalesLead
from app.models.document import Document, DocumentCategory
from app.models.enums import (
    CollectionPlanStatus,
    EndorsementKind,
    EndorsementStatus,
    EntityType,
    LeadStatus,
    PaymentMode,
)
from app.models.policy import Claim, ClaimStatus
from app.models.proposal import Proposal
from app.models.quote import QuoteRequest, QuoteRequestStatus
from tests.conftest import API, Tenant, World, make_case_file, make_policy

# --- Wire the exports router for the suite ----------------------------------
# ``app/main.py`` is owned by another pass; the router only exposes a
# module-level ``router``. Including it here is idempotent and stays correct
# once main.py wires it for real.
from app.api.routers import exports as exports_router  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

if not any(
    getattr(route, "path", "").startswith(f"{API}/exports") for route in fastapi_app.routes
):
    fastapi_app.include_router(exports_router.router, prefix=API)


# --- Fixtures ----------------------------------------------------------------

@pytest.fixture()
def stub_render(monkeypatch):
    """No Chromium in the suite: the HTML→PDF call returns a stub PDF."""
    from app.services import pdf as pdf_module

    async def _fake_render(html: str) -> bytes:
        assert "<table" in html
        return b"%PDF-1.4\n% stub\n"

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)
    return _fake_render


@pytest.fixture()
def local_media(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))


def _group(db: Session, tenant: Tenant, name: str) -> AccountGroup:
    group = AccountGroup(
        broker_id=tenant.broker_id, name=name, slug=name.lower().replace(" ", "-")
    )
    db.add(group)
    db.flush()
    return group


@pytest.fixture()
def scoped_world(db: Session, world: World):
    """Broker A: one grupo with one grupo-cuenta carrying rows of every entity,
    plus a second, UNGROUPED case file whose rows must fall outside the filter.

    Broker B gets its own grupo so "a foreign group id" is a real id, not a
    made-up one — the leak test is only meaningful if the row exists somewhere.
    """
    a, b = world.a, world.b
    group_a = _group(db, a, "Grupo Alfa")
    group_b = _group(db, b, "Grupo Beta")

    a.client.account_group_id = group_a.id
    b.client.account_group_id = group_b.id

    case_in = make_case_file(
        db, a, n=1, account_group_id=group_a.id, title="Cuenta dentro del grupo"
    )
    case_out = make_case_file(db, a, n=2, title="Cuenta sin grupo")

    a.placement.case_file_id = case_in.id
    a.quote.case_file_id = case_in.id

    # A second quote OUTSIDE the group.
    quote_out = QuoteRequest(
        broker_id=a.broker_id,
        placement_id=a.placement.id,
        case_file_id=case_out.id,
        insured_object="Fuera del grupo",
        status=QuoteRequestStatus.DRAFT,
    )
    db.add(quote_out)
    db.flush()

    proposal_in = Proposal(
        broker_id=a.broker_id,
        quote_request_id=a.quote.id,
        insurer_id=world.insurer.id,
        source_document_id=a.document.id,
        case_file_id=case_in.id,
        total_premium_uf=Decimal("120.0000"),
    )
    proposal_out = Proposal(
        broker_id=a.broker_id,
        quote_request_id=quote_out.id,
        insurer_id=world.insurer.id,
        source_document_id=a.document.id,
        case_file_id=case_out.id,
        total_premium_uf=Decimal("77.0000"),
    )
    db.add_all([proposal_in, proposal_out])

    policy_in = make_policy(
        db, a, world.insurer, policy_number="POL-IN", case_file_id=case_in.id,
        start_date=date(2026, 3, 1), end_date=date(2027, 3, 1),
    )
    # Outside: no case file AND a client that is in the group — so it still
    # resolves through ``client.account_group_id``. Use broker B's world for the
    # genuinely-foreign row instead.
    doc_in = Document(
        broker_id=a.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case_in.id,
        case_file_id=case_in.id,
        s3_key=f"documents/case_file/{case_in.id}/dentro.pdf",
        bucket="radal-test-bucket",
        original_name="dentro.pdf",
        mime_type="application/pdf",
        category=DocumentCategory.OTHER,
    )
    doc_out = Document(
        broker_id=a.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case_out.id,
        case_file_id=case_out.id,
        s3_key=f"documents/case_file/{case_out.id}/fuera.pdf",
        bucket="radal-test-bucket",
        original_name="fuera.pdf",
        mime_type="application/pdf",
        category=DocumentCategory.OTHER,
    )
    db.add_all([doc_in, doc_out])

    # --- The "outside" half: a second RUT of broker A that belongs to NO group.
    insured_out = Insured(rut="77999999-9", legal_name="Asegurado Fuera S.A.")
    db.add(insured_out)
    db.flush()
    client_out = Client(broker_id=a.broker_id, insured_id=insured_out.id)
    db.add(client_out)
    db.flush()
    asset_out = Asset(
        broker_id=a.broker_id,
        client_id=client_out.id,
        asset_type="property",
        name="Bodega fuera",
    )
    db.add(asset_out)
    db.flush()
    placement_out = Placement(
        broker_id=a.broker_id,
        client_id=client_out.id,
        asset_id=asset_out.id,
        insurance_line_id=a.line.id,
        status=PlacementStatus.NEGOTIATING,
    )
    db.add(placement_out)
    db.flush()
    policy_out = make_policy(
        db,
        a,
        world.insurer,
        policy_number="POL-OUT",
        client_id=client_out.id,
        placement_id=placement_out.id,
        start_date=date(2028, 3, 1),
        end_date=date(2029, 3, 1),
    )

    claim_in = Claim(
        broker_id=a.broker_id,
        client_id=a.client.id,
        policy_id=policy_in.id,
        case_file_id=case_in.id,
        claim_number="SIN-1",
        status=ClaimStatus.REPORTED,
        event_date=date(2026, 5, 10),
        estimated_amount_uf=Decimal("500.0000"),
    )
    claim_out = Claim(
        broker_id=a.broker_id,
        client_id=client_out.id,
        policy_id=policy_out.id,
        claim_number="SIN-2",
        status=ClaimStatus.REPORTED,
        event_date=date(2026, 8, 10),
        estimated_amount_uf=Decimal("60.0000"),
    )
    db.add_all([claim_in, claim_out])

    db.add_all(
        [
            Endorsement(
                broker_id=a.broker_id,
                policy_id=policy_in.id,
                case_file_id=case_in.id,
                sequence_no=1,
                kind=EndorsementKind.ADDITIONAL_INSURED,
                status=EndorsementStatus.ISSUED,
                effective_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                total_premium_delta_uf=Decimal("10.0000"),
            ),
            Endorsement(
                broker_id=a.broker_id,
                policy_id=policy_out.id,
                sequence_no=1,
                kind=EndorsementKind.ADDITIONAL_INSURED,
                status=EndorsementStatus.ISSUED,
                effective_at=datetime(2028, 4, 1, tzinfo=timezone.utc),
                total_premium_delta_uf=Decimal("5.0000"),
            ),
        ]
    )
    db.add_all(
        [
            CollectionPlan(
                broker_id=a.broker_id,
                policy_id=policy_in.id,
                case_file_id=case_in.id,
                payment_mode=PaymentMode.COUPON_BOOK,
                status=CollectionPlanStatus.CURRENT,
                total_premium_uf=Decimal("139.0000"),
            ),
            CollectionPlan(
                broker_id=a.broker_id,
                policy_id=policy_out.id,
                payment_mode=PaymentMode.TRANSFER,
                status=CollectionPlanStatus.PENDING,
                total_premium_uf=Decimal("139.0000"),
            ),
        ]
    )
    db.add_all(
        [
            Inspection(
                broker_id=a.broker_id,
                asset_id=a.asset.id,
                case_file_id=case_in.id,
                version=1,
                status=InspectionStatus.ISSUED,
                visit_date=date(2026, 2, 1),
            ),
            Inspection(
                broker_id=a.broker_id,
                asset_id=asset_out.id,
                version=1,
                status=InspectionStatus.DRAFT,
                visit_date=date(2028, 2, 1),
            ),
        ]
    )
    db.add_all(
        [
            Offering(
                broker_id=a.broker_id,
                quote_request_id=a.quote.id,
                share_token="tok-dentro",
                status=OfferingStatus.SENT,
            ),
            Offering(
                broker_id=a.broker_id,
                quote_request_id=quote_out.id,
                share_token="tok-fuera",
                status=OfferingStatus.DRAFT,
            ),
        ]
    )
    db.add_all(
        [
            SalesLead(
                broker_id=a.broker_id,
                name="Prospecto del grupo",
                status=LeadStatus.NEW,
                account_group_id=group_a.id,
                converted_case_file_id=case_in.id,
                estimated_premium_uf=Decimal("40.0000"),
            ),
            SalesLead(
                broker_id=a.broker_id,
                name="Prospecto suelto",
                status=LeadStatus.NEW,
                estimated_premium_uf=Decimal("15.0000"),
            ),
        ]
    )

    db.commit()
    return {
        "group_a": group_a,
        "group_b": group_b,
        "case_in": case_in,
        "case_out": case_out,
        "quote_out": quote_out,
        "policy_in": policy_in,
        "policy_out": policy_out,
        "client_out": client_out,
    }


# --- 1. The group filter narrows every list ----------------------------------

_LISTS = [
    ("case-files", "items"),
    ("quotes", "items"),
    ("proposals", "items"),
    ("policies", "items"),
    ("endorsements", "items"),
    ("collections", "items"),
    ("claims", "items"),
    ("documents", "items"),
    ("placements", "items"),
    ("inspections", "items"),
    ("offerings", "items"),
    ("leads", "items"),
    ("clients", "items"),
]


class TestGroupFilterNarrows:
    @pytest.mark.parametrize("path,key", _LISTS)
    def test_every_list_accepts_the_scope_vocabulary(
        self, client, scoped_world, headers_a, path, key
    ):
        """All four params are accepted (never a 422 "unknown query param")."""
        response = client.get(
            f"{API}/{path}",
            params={
                "account_group_id": scoped_world["group_a"].id,
                "case_file_id": scoped_world["case_in"].id,
                "date_from": "2020-01-01",
                "date_to": "2030-12-31",
            },
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        assert key in response.json()

    # (path, rows without the filter, rows inside grupo Alfa). Each entity has
    # exactly one row inside the grupo and one outside it, so this proves the
    # filter NARROWS rather than merely being accepted.
    @pytest.mark.parametrize(
        "path,expected_all,expected_in_group",
        [
            ("case-files", 2, 1),
            ("quotes", 2, 1),
            ("proposals", 2, 1),
            ("policies", 2, 1),
            ("endorsements", 2, 1),
            ("collections", 2, 1),
            ("claims", 2, 1),
            # +1: the world fixture files one proposal PDF on the quote request.
            ("documents", 3, 1),
            ("placements", 2, 1),
            ("inspections", 2, 1),
            ("offerings", 2, 1),
            ("leads", 2, 1),
            ("clients", 2, 1),
        ],
    )
    def test_account_group_id_narrows_every_list(
        self, client, scoped_world, headers_a, path, expected_all, expected_in_group
    ):
        everything = client.get(f"{API}/{path}", headers=headers_a).json()
        narrowed = client.get(
            f"{API}/{path}",
            params={"account_group_id": scoped_world["group_a"].id},
            headers=headers_a,
        ).json()
        assert everything["total"] == expected_all, path
        assert narrowed["total"] == expected_in_group, path
        assert len(narrowed["items"]) == expected_in_group, path

    @pytest.mark.parametrize(
        "path,expected_in_case",
        [
            ("case-files", 1),
            ("quotes", 1),
            ("proposals", 1),
            ("policies", 1),
            ("endorsements", 1),
            ("collections", 1),
            ("claims", 1),
            ("documents", 1),
            ("placements", 1),
            ("inspections", 1),
            ("offerings", 1),
            ("leads", 1),
            ("clients", 1),
        ],
    )
    def test_case_file_id_narrows_every_list(
        self, client, scoped_world, headers_a, path, expected_in_case
    ):
        narrowed = client.get(
            f"{API}/{path}",
            params={"case_file_id": scoped_world["case_in"].id},
            headers=headers_a,
        ).json()
        assert narrowed["total"] == expected_in_case, path

    def test_group_narrows_quotes(self, client, scoped_world, headers_a):
        everything = client.get(f"{API}/quotes", headers=headers_a).json()
        narrowed = client.get(
            f"{API}/quotes",
            params={"account_group_id": scoped_world["group_a"].id},
            headers=headers_a,
        ).json()
        assert everything["total"] == 2
        assert narrowed["total"] == 1
        assert narrowed["items"][0]["insured_object"] != "Fuera del grupo"

    def test_group_narrows_proposals(self, client, scoped_world, headers_a):
        everything = client.get(f"{API}/proposals", headers=headers_a).json()
        narrowed = client.get(
            f"{API}/proposals",
            params={"account_group_id": scoped_world["group_a"].id},
            headers=headers_a,
        ).json()
        assert everything["total"] == 2
        assert narrowed["total"] == 1

    def test_group_narrows_documents(self, client, scoped_world, headers_a):
        narrowed = client.get(
            f"{API}/documents",
            params={"account_group_id": scoped_world["group_a"].id},
            headers=headers_a,
        ).json()
        names = {item["original_name"] for item in narrowed["items"]}
        assert names == {"dentro.pdf"}

    def test_case_file_id_narrows_case_files_to_one(
        self, client, scoped_world, headers_a
    ):
        narrowed = client.get(
            f"{API}/case-files",
            params={"case_file_id": scoped_world["case_in"].id},
            headers=headers_a,
        ).json()
        assert narrowed["total"] == 1
        assert narrowed["items"][0]["id"] == scoped_world["case_in"].id

    def test_date_window_excludes_out_of_range_policies(
        self, client, scoped_world, headers_a
    ):
        """The policy's window is its vigencia start (documented per entity)."""
        inside = client.get(
            f"{API}/policies",
            params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
            headers=headers_a,
        ).json()
        outside = client.get(
            f"{API}/policies",
            params={"date_from": "2027-06-01", "date_to": "2027-12-31"},
            headers=headers_a,
        ).json()
        assert inside["total"] >= 1
        assert outside["total"] == 0

    def test_date_window_on_claims_uses_the_event_date(
        self, client, scoped_world, headers_a
    ):
        hit = client.get(
            f"{API}/claims",
            params={"date_from": "2026-05-01", "date_to": "2026-05-31"},
            headers=headers_a,
        ).json()
        miss = client.get(
            f"{API}/claims",
            params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
            headers=headers_a,
        ).json()
        assert hit["total"] == 1
        assert miss["total"] == 0


# --- 2. A foreign group is an empty page, never a leak -----------------------

class TestForeignGroupIsEmptyNotALeak:
    @pytest.mark.parametrize("path,key", _LISTS)
    def test_foreign_group_yields_an_empty_page(
        self, client, scoped_world, headers_a, path, key
    ):
        """Broker B's REAL group id, asked for by broker A -> zero rows, 200."""
        response = client.get(
            f"{API}/{path}",
            params={"account_group_id": scoped_world["group_b"].id},
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body[key] == [], f"{path} leaked rows for a foreign group"
        assert body.get("total", 0) == 0

    def test_foreign_case_file_yields_an_empty_page(
        self, client, db, scoped_world, world, headers_a
    ):
        foreign_case = make_case_file(db, world.b, n=9)
        db.commit()
        body = client.get(
            f"{API}/quotes",
            params={"case_file_id": foreign_case.id},
            headers=headers_a,
        ).json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_foreign_group_export_is_an_empty_workbook(
        self, client, scoped_world, headers_a
    ):
        response = client.post(
            f"{API}/exports/policies",
            json={
                "format": "xlsx",
                "filters": {"account_group_id": scoped_world["group_b"].id},
            },
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-radal-row-count"] == "0"


# --- 3. group_by ---------------------------------------------------------------

class TestGroupBy:
    def test_bucket_shape_is_the_documented_one(
        self, client, scoped_world, headers_a
    ):
        body = client.get(
            f"{API}/policies/summary", params={"group_by": "status"}, headers=headers_a
        ).json()
        grouped = body["grouped"]
        assert grouped["group_by"] == "status"
        assert "month" in grouped["dimensions"]
        assert grouped["money_field"] == "total_premium_uf"
        assert grouped["total"] >= 1
        bucket = grouped["buckets"][0]
        assert set(bucket) == {"key", "label", "count", "total_uf"}
        assert isinstance(bucket["key"], str)
        assert isinstance(bucket["count"], int)

    def test_group_by_account_group_labels_with_the_group_name(
        self, client, scoped_world, headers_a
    ):
        body = client.get(
            f"{API}/quotes/summary",
            params={"group_by": "account_group"},
            headers=headers_a,
        ).json()
        labels = {b["label"] for b in body["grouped"]["buckets"]}
        assert "Grupo Alfa" in labels

    def test_group_by_month_buckets_by_calendar_month(
        self, client, scoped_world, headers_a
    ):
        body = client.get(
            f"{API}/policies/summary", params={"group_by": "month"}, headers=headers_a
        ).json()
        keys = [b["key"] for b in body["grouped"]["buckets"]]
        assert "2026-03" in keys

    def test_group_by_insurer_labels_with_the_insurer_name(
        self, client, scoped_world, world, headers_a
    ):
        body = client.get(
            f"{API}/proposals/summary",
            params={"group_by": "insurer"},
            headers=headers_a,
        ).json()
        labels = {b["label"] for b in body["grouped"]["buckets"]}
        assert world.insurer.legal_name in labels

    def test_no_group_by_leaves_grouped_null(self, client, scoped_world, headers_a):
        body = client.get(f"{API}/policies/summary", headers=headers_a).json()
        assert body["grouped"] is None

    @pytest.mark.parametrize(
        "path",
        [
            "case-files/summary",
            "quotes/summary",
            "proposals/summary",
            "policies/summary",
            "placements/summary",
            "clients/summary",
            "leads/summary",
            "endorsements/summary",
            "collections/summary",
            "claims/summary",
            "documents/summary",
            "inspections/summary",
            "offerings/summary",
        ],
    )
    def test_unsupported_group_by_is_422_everywhere(
        self, client, scoped_world, headers_a, path
    ):
        response = client.get(
            f"{API}/{path}", params={"group_by": "no_such_dimension"}, headers=headers_a
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "unsupported_group_by"
        assert detail["supported"], detail
        assert "month" in detail["supported"]

    def test_summary_honours_the_group_filter(self, client, scoped_world, headers_a):
        everything = client.get(f"{API}/proposals/summary", headers=headers_a).json()
        narrowed = client.get(
            f"{API}/proposals/summary",
            params={"account_group_id": scoped_world["group_a"].id},
            headers=headers_a,
        ).json()
        assert everything["total"] == 2
        assert narrowed["total"] == 1

    def test_summary_with_a_foreign_group_is_all_zeros(
        self, client, scoped_world, headers_a
    ):
        body = client.get(
            f"{API}/proposals/summary",
            params={"account_group_id": scoped_world["group_b"].id},
            headers=headers_a,
        ).json()
        assert body["total"] == 0


# --- 4. Exports ----------------------------------------------------------------

def _load_workbook(payload: bytes):
    from openpyxl import load_workbook

    return load_workbook(io.BytesIO(payload))


class TestExports:
    def test_catalog_advertises_every_entity(self, client, headers_a):
        body = client.get(f"{API}/exports/entities", headers=headers_a).json()
        assert body["formats"] == ["xlsx", "pdf"]
        assert body["row_cap"] == 10_000
        entities = {item["entity"] for item in body["entities"]}
        assert entities == {
            "case_files", "quotes", "proposals", "policies", "endorsements",
            "collections", "claims", "documents", "placements", "inspections",
            "offerings", "leads", "clients",
        }

    def test_xlsx_is_a_real_workbook_with_the_right_row_count(
        self, client, scoped_world, headers_a
    ):
        response = client.post(
            f"{API}/exports/quotes", json={"format": "xlsx"}, headers=headers_a
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats"
        )
        assert 'attachment; filename="quotes-completo-' in (
            response.headers["content-disposition"]
        )
        workbook = _load_workbook(response.content)
        sheet = workbook.active
        assert sheet.freeze_panes == "A2"
        # 1 header + 2 quote requests.
        assert sheet.max_row == 3
        assert sheet.cell(row=1, column=1).value == "ID"

    def test_xlsx_types_dates_and_uf(self, client, scoped_world, headers_a):
        response = client.post(
            f"{API}/exports/policies",
            json={
                "format": "xlsx",
                "columns": ["policy_number", "start_date", "total_premium_uf"],
            },
            headers=headers_a,
        )
        sheet = _load_workbook(response.content).active
        assert [sheet.cell(row=1, column=c).value for c in (1, 2, 3)] == [
            "N° póliza",
            "Inicio vigencia",
            "Prima total (UF)",
        ]
        assert isinstance(sheet.cell(row=2, column=2).value, (date, datetime))
        # A UF cell is a NUMBER (openpyxl reads a whole float back as int), with
        # the 4-decimal format the column carries in the database.
        assert isinstance(sheet.cell(row=2, column=3).value, (int, float))
        assert not isinstance(sheet.cell(row=2, column=3).value, str)
        assert sheet.cell(row=2, column=3).number_format == "#,##0.0000"

    def test_export_honours_the_group_filter(self, client, scoped_world, headers_a):
        response = client.post(
            f"{API}/exports/proposals",
            json={
                "format": "xlsx",
                "filters": {"account_group_id": scoped_world["group_a"].id},
            },
            headers=headers_a,
        )
        assert response.headers["x-radal-row-count"] == "1"
        assert _load_workbook(response.content).active.max_row == 2

    def test_grouped_export_carries_the_buckets(
        self, client, scoped_world, headers_a
    ):
        response = client.post(
            f"{API}/exports/policies",
            json={"format": "xlsx", "group_by": "status"},
            headers=headers_a,
        )
        sheet = _load_workbook(response.content).active
        assert [sheet.cell(row=1, column=c).value for c in (1, 2, 3, 4)] == [
            "Clave",
            "Etiqueta",
            "Cantidad",
            "Total UF (total_premium_uf)",
        ]

    def test_pdf_starts_with_the_magic_bytes(
        self, client, scoped_world, headers_a, stub_render, local_media
    ):
        response = client.post(
            f"{API}/exports/policies",
            json={
                "format": "pdf",
                "filters": {"account_group_id": scoped_world["group_a"].id},
            },
            headers=headers_a,
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF")
        assert "filename=" in response.headers["content-disposition"]

    def test_pdf_prints_the_active_filters_in_the_header(
        self, client, scoped_world, headers_a, monkeypatch, local_media
    ):
        captured: dict[str, str] = {}
        from app.services import pdf as pdf_module

        async def _capture(html: str) -> bytes:
            captured["html"] = html
            return b"%PDF-1.4\n"

        monkeypatch.setattr(pdf_module, "render_html_to_pdf", _capture)
        client.post(
            f"{API}/exports/policies",
            json={
                "format": "pdf",
                "filters": {
                    "account_group_id": scoped_world["group_a"].id,
                    "date_from": "2026-01-01",
                },
            },
            headers=headers_a,
        )
        html = captured["html"]
        assert "Grupo Alfa" in html          # the filter, resolved to its name
        assert "2026-01-01" in html
        assert "fila(s) exportada(s)" in html  # the row count in the footer
        assert "landscape" in html
        assert "s3" not in html.lower().split("data:")[0]  # never an S3 key

    def test_row_cap_is_a_422_not_a_timeout(
        self, client, scoped_world, headers_a, monkeypatch
    ):
        monkeypatch.setattr(exports_router, "EXPORT_ROW_CAP", 1)
        response = client.post(
            f"{API}/exports/quotes", json={"format": "xlsx"}, headers=headers_a
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "export_too_large"
        assert detail["row_count"] == 2
        assert detail["row_cap"] == 1

    def test_unknown_entity_is_404(self, client, headers_a):
        response = client.post(
            f"{API}/exports/unicorns", json={"format": "xlsx"}, headers=headers_a
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "unknown_entity"

    def test_unknown_column_is_422(self, client, scoped_world, headers_a):
        response = client.post(
            f"{API}/exports/policies",
            json={"format": "xlsx", "columns": ["nope"]},
            headers=headers_a,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unknown_columns"

    def test_unsupported_group_by_on_export_is_422(
        self, client, scoped_world, headers_a
    ):
        response = client.post(
            f"{API}/exports/policies",
            json={"format": "xlsx", "group_by": "insured_object"},
            headers=headers_a,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unsupported_group_by"

    def test_export_is_gated_on_the_entity_view_grant(
        self, client, scoped_world, headers_a_inspector
    ):
        """An inspector may see Inspections but not Quotes -> 403 on that export."""
        headers = headers_a_inspector
        allowed = client.post(
            f"{API}/exports/inspections", json={"format": "xlsx"}, headers=headers
        )
        denied = client.post(
            f"{API}/exports/quotes", json={"format": "xlsx"}, headers=headers
        )
        assert allowed.status_code == 200, allowed.text
        assert denied.status_code == 403

    def test_export_never_returns_an_s3_key(self, client, scoped_world, headers_a):
        """Rule 8: the ``document`` table is the only place an S3 key lives."""
        response = client.post(
            f"{API}/exports/documents", json={"format": "xlsx"}, headers=headers_a
        )
        sheet = _load_workbook(response.content).active
        headers_row = [
            sheet.cell(row=1, column=c).value for c in range(1, sheet.max_column + 1)
        ]
        assert not any("s3" in str(h).lower() for h in headers_row)
        for row in sheet.iter_rows(min_row=2, values_only=True):
            assert not any("documents/case_file" in str(v) for v in row if v)

    def test_export_is_tenant_scoped(self, client, scoped_world, headers_b):
        """Broker B exporting policies never sees broker A's rows."""
        response = client.post(
            f"{API}/exports/policies", json={"format": "xlsx"}, headers=headers_b
        )
        assert response.headers["x-radal-row-count"] == "0"
