"""The two hard preconditions for a proposal to exist at all.

Rule 3 (docs/v2-architecture.md §4.3):
  * a proposal CANNOT be created without ``source_document_id`` — the file is
    mandatory context for the AI and for the audit trail;
  * its insurer must carry BOTH ``rut`` and ``cmf_code``.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.proposal import Proposal
from tests.conftest import API


def _body(world, **overrides) -> dict:
    body = {
        "quote_request_id": world.a.quote.id,
        "insurer_id": world.insurer.id,
        "source_document_id": world.a.document.id,
    }
    body.update(overrides)
    return body


class TestSourceDocumentIsMandatory:
    def test_baseline_create_with_a_document_succeeds(
        self, client, world, headers_a
    ) -> None:
        response = client.post(f"{API}/proposals", json=_body(world), headers=headers_a)
        assert response.status_code == 201, response.text
        assert response.json()["source_document_id"] == world.a.document.id

    def test_omitting_source_document_id_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        body = _body(world)
        del body["source_document_id"]
        response = client.post(f"{API}/proposals", json=body, headers=headers_a)
        assert response.status_code == 422, response.text

    def test_null_source_document_id_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        response = client.post(
            f"{API}/proposals", json=_body(world, source_document_id=None), headers=headers_a
        )
        assert response.status_code == 422, response.text

    def test_unknown_source_document_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        response = client.post(
            f"{API}/proposals", json=_body(world, source_document_id=999999),
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "source_document_not_found"

    def test_another_brokers_document_cannot_be_used(
        self, client, world, headers_a
    ) -> None:
        # Broker B's file is invisible to broker A, so it is "not found", never borrowed.
        response = client.post(
            f"{API}/proposals",
            json=_body(world, source_document_id=world.b.document.id),
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "source_document_not_found"

    def test_patch_cannot_null_out_the_source_document(
        self, client, world, headers_a
    ) -> None:
        proposal_id = client.post(
            f"{API}/proposals", json=_body(world), headers=headers_a
        ).json()["id"]

        response = client.patch(
            f"{API}/proposals/{proposal_id}",
            json={"source_document_id": None},
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "source_document_required"

    def test_the_column_itself_is_not_nullable(self) -> None:
        # Belt and braces: the DB refuses it too, not just the router.
        assert Proposal.__table__.c.source_document_id.nullable is False


class TestInsurerIdentityIsMandatory:
    def test_insurer_missing_cmf_code_is_rejected_422(
        self, client, world, headers_a
    ) -> None:
        response = client.post(
            f"{API}/proposals",
            json=_body(world, insurer_id=world.insurer_no_cmf.id),
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "insurer_identity_incomplete"
        assert detail["missing"] == ["cmf_code"]

    def test_insurer_missing_rut_is_rejected_422(self, client, world, headers_a) -> None:
        response = client.post(
            f"{API}/proposals",
            json=_body(world, insurer_id=world.insurer_no_rut.id),
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "insurer_identity_incomplete"
        assert detail["missing"] == ["rut"]

    def test_unknown_insurer_is_rejected_404(self, client, world, headers_a) -> None:
        response = client.post(
            f"{API}/proposals", json=_body(world, insurer_id=999999), headers=headers_a
        )
        assert response.status_code == 404, response.text

    def test_patching_to_an_incomplete_insurer_is_rejected(
        self, client, world, headers_a
    ) -> None:
        proposal_id = client.post(
            f"{API}/proposals", json=_body(world), headers=headers_a
        ).json()["id"]

        response = client.patch(
            f"{API}/proposals/{proposal_id}",
            json={"insurer_id": world.insurer_no_cmf.id},
            headers=headers_a,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "insurer_identity_incomplete"

    def test_no_proposal_row_survives_a_refused_create(
        self, client, world, headers_a, db
    ) -> None:
        for bad in (
            _body(world, insurer_id=world.insurer_no_cmf.id),
            _body(world, insurer_id=world.insurer_no_rut.id),
            _body(world, source_document_id=999999),
        ):
            client.post(f"{API}/proposals", json=bad, headers=headers_a)
        db.expire_all()
        assert db.scalar(select(func.count(Proposal.id))) == 0


class TestOriginDerivation:
    def test_origin_defaults_from_the_insurers_native_flag(
        self, client, world, headers_a
    ) -> None:
        response = client.post(f"{API}/proposals", json=_body(world), headers=headers_a)
        assert response.status_code == 201, response.text
        # world.insurer is native -> origin native, without the caller saying so.
        assert response.json()["origin"] == "native"

    def test_explicit_origin_wins(self, client, world, headers_a) -> None:
        response = client.post(
            f"{API}/proposals", json=_body(world, origin="external"), headers=headers_a
        )
        assert response.status_code == 201, response.text
        assert response.json()["origin"] == "external"
