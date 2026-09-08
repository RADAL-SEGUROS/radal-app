"""The three Analytics aggregates: /quotes/summary, /proposals/summary, /policies/summary.

Each endpoint mirrors ``GET /case-files/summary``: module View permission at the
gate, every count scoped to the caller's ``broker_id``, statuses zero-filled so
the dashboard never KeyErrors on an empty tenant. The suites below cover the
happy-path arithmetic, a tenant-isolation case and an RBAC-denied case each.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.proposal import Proposal
from app.models.quote import QuoteRequest, QuoteRequestStatus
from tests.conftest import API, Tenant, auth_headers, ensure_role_user, make_policy


# --- Local builders -----------------------------------------------------------

def make_quote(db: Session, tenant: Tenant, **overrides) -> QuoteRequest:
    """One extra quote request on the tenant's placement (ORM, no validation)."""
    fields = {
        "broker_id": tenant.broker_id,
        "placement_id": tenant.placement.id,
        "insured_object": f"Bodega {tenant.broker.trade_name}",
        "status": QuoteRequestStatus.DRAFT,
    }
    fields.update(overrides)
    quote = QuoteRequest(**fields)
    db.add(quote)
    db.flush()
    return quote


def make_proposal(db: Session, tenant: Tenant, insurer, **overrides) -> Proposal:
    """One proposal against the tenant's world quote (ORM, no validation)."""
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


# --- GET /quotes/summary ------------------------------------------------------

class TestQuotesSummary:
    def test_counts_statuses_and_the_two_clocks(self, client, world, db, headers_a):
        now = datetime.now(timezone.utc)
        # Open and past due -> overdue.
        make_quote(
            db, world.a, status=QuoteRequestStatus.DRAFT, due_at=now - timedelta(days=2)
        )
        # Sent five days ago -> inside the 30-day window.
        make_quote(
            db, world.a, status=QuoteRequestStatus.SENT, sent_at=now - timedelta(days=5)
        )
        # Terminal with a past due date -> NOT overdue; sent 45 days ago -> NOT recent.
        make_quote(
            db,
            world.a,
            status=QuoteRequestStatus.CLOSED,
            sent_at=now - timedelta(days=45),
            due_at=now - timedelta(days=10),
        )
        db.commit()

        body = client.get(f"{API}/quotes/summary", headers=headers_a).json()
        # The world fixture already holds one RECEIVING quote per tenant.
        assert body["total"] == 4
        assert body["by_status"] == {
            "draft": 1,
            "sent": 1,
            "receiving": 1,
            "closed": 1,
            "cancelled": 0,
        }
        assert body["sent_last_30d"] == 1
        assert body["overdue"] == 1

    def test_is_scoped_to_the_callers_broker(self, client, world, db, headers_b):
        now = datetime.now(timezone.utc)
        make_quote(
            db, world.a, status=QuoteRequestStatus.SENT, sent_at=now, due_at=now - timedelta(days=1)
        )
        db.commit()

        body = client.get(f"{API}/quotes/summary", headers=headers_b).json()
        assert body["total"] == 1  # only broker B's own world quote
        assert body["by_status"]["sent"] == 0
        assert body["sent_last_30d"] == 0
        assert body["overdue"] == 0

    def test_denied_without_quotes_view(self, client, world, headers_a_inspector):
        response = client.get(f"{API}/quotes/summary", headers=headers_a_inspector)
        assert response.status_code == 403, response.text


# --- GET /proposals/summary ---------------------------------------------------

class TestProposalsSummary:
    def test_counts_money_and_the_insurer_mix(self, client, world, db, headers_a):
        make_proposal(
            db, world.a, world.insurer,
            status="submitted",
            total_premium_uf=Decimal("100.0000"),
            comprehensive_rate_permille=Decimal("1.5000"),
        )
        make_proposal(
            db, world.a, world.insurer,
            status="accepted",
            total_premium_uf=Decimal("200.0000"),
            comprehensive_rate_permille=Decimal("2.5000"),
            is_confirmed=True,
        )
        # Out of the running: excluded from the money aggregates, still counted.
        make_proposal(
            db, world.a, world.insurer_no_cmf,
            status="rejected",
            total_premium_uf=Decimal("999.0000"),
            comprehensive_rate_permille=Decimal("99.0000"),
        )
        db.commit()

        body = client.get(f"{API}/proposals/summary", headers=headers_a).json()
        assert body["total"] == 3
        assert body["by_status"] == {
            "draft": 0,
            "submitted": 1,
            "accepted": 1,
            "rejected": 1,
            "withdrawn": 0,
            "expired": 0,
        }
        assert body["confirmed_count"] == 1
        # Decimals travel as strings, quantized to the column's 4 decimals.
        assert body["total_premium_uf"] == "300.0000"
        assert body["avg_rate_permille"] == "2.0000"
        assert body["by_insurer"] == [
            {
                "insurer_id": world.insurer.id,
                "insurer_name": world.insurer.legal_name,
                "count": 2,
            },
            {
                "insurer_id": world.insurer_no_cmf.id,
                "insurer_name": world.insurer_no_cmf.legal_name,
                "count": 1,
            },
        ]

    def test_empty_tenant_is_all_zeroes(self, client, world, db, headers_b):
        make_proposal(db, world.a, world.insurer, status="submitted")
        db.commit()

        body = client.get(f"{API}/proposals/summary", headers=headers_b).json()
        assert body["total"] == 0
        assert body["by_insurer"] == []
        assert body["confirmed_count"] == 0
        assert body["total_premium_uf"] == "0"
        assert body["avg_rate_permille"] is None

    def test_denied_without_proposals_view(self, client, world, headers_a_inspector):
        response = client.get(f"{API}/proposals/summary", headers=headers_a_inspector)
        assert response.status_code == 403, response.text


# --- GET /policies/summary ----------------------------------------------------

class TestPoliciesSummary:
    def test_counts_active_premium_and_the_renewal_clock(
        self, client, world, db, headers_a
    ):
        today = date.today()
        # Active, expiring inside the 60-day window.
        make_policy(
            db, world.a, world.insurer,
            policy_number="POL-SUM-1",
            end_date=today + timedelta(days=30),
        )
        # Active, expiring far outside the window.
        make_policy(
            db, world.a, world.insurer,
            policy_number="POL-SUM-2",
            end_date=today + timedelta(days=120),
            total_premium_uf=Decimal("100.0000"),
        )
        # Cancelled with a near end date -> neither premium nor expiring.
        make_policy(
            db, world.a, world.insurer,
            policy_number="POL-SUM-3",
            status="cancelled",
            end_date=today + timedelta(days=10),
            total_premium_uf=Decimal("999.0000"),
        )
        db.commit()

        body = client.get(f"{API}/policies/summary", headers=headers_a).json()
        assert body["total"] == 3
        assert body["by_status"] == {
            "draft": 0,
            "active": 2,
            "expired": 0,
            "cancelled": 1,
            "renewed": 0,
        }
        assert body["active_count"] == 2
        # 139 (conftest default) + 100, active only, as a string.
        assert body["total_premium_uf"] == "239.0000"
        assert body["expiring_within_60_days"] == 1

    def test_is_scoped_to_the_callers_broker(self, client, world, db, headers_b):
        make_policy(
            db, world.a, world.insurer,
            policy_number="POL-SUM-A",
            end_date=date.today() + timedelta(days=15),
        )
        db.commit()

        body = client.get(f"{API}/policies/summary", headers=headers_b).json()
        assert body["total"] == 0
        assert body["active_count"] == 0
        assert body["total_premium_uf"] == "0"
        assert body["expiring_within_60_days"] == 0

    def test_denied_without_policies_view(self, client, db, world):
        """An unknown role is denied everywhere — including the aggregate."""
        user = ensure_role_user(db, world.a, "no_such_role", "outsider")
        headers = auth_headers(client, user.email)
        response = client.get(f"{API}/policies/summary", headers=headers)
        assert response.status_code == 403, response.text

    def test_inspector_view_grant_passes_the_gate(
        self, client, world, headers_a_inspector
    ):
        """Policies.View = yes for the inspector, so the summary answers 200."""
        response = client.get(f"{API}/policies/summary", headers=headers_a_inspector)
        assert response.status_code == 200, response.text
