"""Accepting a proposal: one transaction, four effects.

The winner becomes ``accepted``, every still-open sibling becomes ``rejected``,
the quote request closes and the placement moves to ``awarded``. A broker must
never be left with two live winners, or a closed quote with no winner.
"""
from __future__ import annotations

from app.models.placement import PlacementStatus
from app.models.proposal import ProposalStatus
from app.models.quote import QuoteRequestStatus
from tests.conftest import API


def _make_insurer(client, headers, rut: str, cmf: str, name: str) -> int:
    response = client.post(
        f"{API}/insurers/match", json={"rut": rut, "cmf_code": cmf, "legal_name": name},
        headers=headers,
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    return body["insurer"]["id"] if "insurer" in body else body["id"]


def _competing_proposals(client, world, headers) -> list[int]:
    """Three proposals from three different insurers on one quote request."""
    insurer_ids = [
        world.insurer.id,
        _make_insurer(client, headers, "5000032-K", "CMF-CHUBB-2", "Chubb Seguros Chile S.A."),
        _make_insurer(client, headers, "5000001-K", "CMF-MAPFRE-3", "Mapfre Seguros S.A."),
    ]
    proposal_ids = []
    for index, insurer_id in enumerate(insurer_ids):
        response = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": world.a.quote.id,
                "insurer_id": insurer_id,
                "source_document_id": world.a.document.id,
                "taxable_premium_uf": str(100 + index * 10),
                "exempt_premium_uf": "50",
                "status": "submitted",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        proposal_ids.append(response.json()["id"])
    return proposal_ids


class TestAccept:
    def test_accept_rejects_siblings_and_awards_the_placement(
        self, client, world, headers_a
    ) -> None:
        winner, *losers = _competing_proposals(client, world, headers_a)

        response = client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)
        assert response.status_code == 200, response.text
        result = response.json()

        assert result["proposal_status"] == ProposalStatus.ACCEPTED.value
        assert sorted(result["rejected_proposal_ids"]) == sorted(losers)
        assert result["quote_request_status"] == QuoteRequestStatus.CLOSED.value
        assert result["placement_status"] == PlacementStatus.AWARDED.value

    def test_the_effects_are_actually_persisted(self, client, world, headers_a) -> None:
        winner, *losers = _competing_proposals(client, world, headers_a)
        client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)

        accepted = client.get(f"{API}/proposals/{winner}", headers=headers_a).json()
        assert accepted["status"] == ProposalStatus.ACCEPTED.value

        for loser in losers:
            body = client.get(f"{API}/proposals/{loser}", headers=headers_a).json()
            assert body["status"] == ProposalStatus.REJECTED.value

        quote = client.get(f"{API}/quotes/{world.a.quote.id}", headers=headers_a).json()
        assert quote["status"] == QuoteRequestStatus.CLOSED.value

    def test_exactly_one_proposal_is_accepted_afterwards(
        self, client, world, headers_a
    ) -> None:
        winner, *_ = _competing_proposals(client, world, headers_a)
        client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)

        listing = client.get(
            f"{API}/proposals?quote_request_id={world.a.quote.id}", headers=headers_a
        ).json()
        accepted = [
            item for item in listing["items"]
            if item["status"] == ProposalStatus.ACCEPTED.value
        ]
        assert len(accepted) == 1
        assert accepted[0]["id"] == winner

    def test_a_sibling_of_another_quote_is_untouched(
        self, client, world, headers_a
    ) -> None:
        winner, *_ = _competing_proposals(client, world, headers_a)

        # A second quote on the same placement, with its own proposal.
        other_quote = client.post(
            f"{API}/quotes", json={"placement_id": world.a.placement.id},
            headers=headers_a,
        ).json()["id"]
        bystander = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": other_quote,
                "insurer_id": world.insurer.id,
                "source_document_id": world.a.document.id,
                "status": "submitted",
            },
            headers=headers_a,
        ).json()["id"]

        client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)

        body = client.get(f"{API}/proposals/{bystander}", headers=headers_a).json()
        assert body["status"] == "submitted"

    def test_an_already_rejected_proposal_cannot_be_accepted(
        self, client, world, headers_a
    ) -> None:
        winner, loser, _ = _competing_proposals(client, world, headers_a)
        client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)

        response = client.post(f"{API}/proposals/{loser}/accept", headers=headers_a)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "proposal_not_acceptable"

    def test_an_accepted_proposal_cannot_be_deleted(
        self, client, world, headers_a
    ) -> None:
        winner, *_ = _competing_proposals(client, world, headers_a)
        client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)

        response = client.delete(f"{API}/proposals/{winner}", headers=headers_a)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "proposal_accepted"


class TestRejectTheWinner:
    def test_rejecting_the_awarded_proposal_reopens_the_quote(
        self, client, world, headers_a
    ) -> None:
        winner, *_ = _competing_proposals(client, world, headers_a)
        client.post(f"{API}/proposals/{winner}/accept", headers=headers_a)

        response = client.post(f"{API}/proposals/{winner}/reject", headers=headers_a)
        assert response.status_code == 200, response.text
        result = response.json()

        # Never leave a closed quote with no winner.
        assert result["proposal_status"] == ProposalStatus.REJECTED.value
        assert result["quote_request_status"] == QuoteRequestStatus.RECEIVING.value
        assert result["placement_status"] == PlacementStatus.NEGOTIATING.value


class TestConfirmationGate:
    def test_an_unconfirmed_ai_prefilled_proposal_cannot_be_accepted(
        self, client, world, headers_a, db
    ) -> None:
        from app.models.ai import Extraction
        from app.models.proposal import Proposal

        proposal_id = client.post(
            f"{API}/proposals",
            json={
                "quote_request_id": world.a.quote.id,
                "insurer_id": world.insurer.id,
                "source_document_id": world.a.document.id,
                "status": "submitted",
            },
            headers=headers_a,
        ).json()["id"]

        extraction = Extraction(
            broker_id=world.a.broker_id,
            document_id=world.a.document.id,
            model="test-model",
            prompt_version="v1",
        )
        db.add(extraction)
        db.flush()
        db.get(Proposal, proposal_id).extraction_id = extraction.id
        db.commit()

        blocked = client.post(f"{API}/proposals/{proposal_id}/accept", headers=headers_a)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "proposal_not_confirmed"

        confirmed = client.post(
            f"{API}/proposals/{proposal_id}/confirm", json={"is_confirmed": True},
            headers=headers_a,
        )
        assert confirmed.status_code == 200, confirmed.text

        accepted = client.post(f"{API}/proposals/{proposal_id}/accept", headers=headers_a)
        assert accepted.status_code == 200, accepted.text
