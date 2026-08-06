"""Insurer find-or-create dedups on normalized rut / cmf_code — NEVER on name.

OCR of insurer PDFs yields "HDI Seguros S.A.", "HDI SEGUROS SA", "Hdi seguros
s a." for one company. Matching on the name would either fragment one insurer
into many rows or merge two genuinely different companies. The identity is
``rut`` + ``cmf_code`` and nothing else.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.api.routers.insurers import (
    InsurerIdentityConflict,
    InsurerIdentityError,
    find_or_create_insurer,
)
from app.models.insurer import Insurer
from tests.conftest import API

# One company spelled the many ways a document might render it.
NAME_VARIANTS = [
    "HDI Seguros S.A.",
    "HDI SEGUROS SA",
    "Hdi Seguros S. A.",
    "  hdi seguros s.a.  ",
    "HDI SEGUROS CHILE S.A.",
]


class TestDedupOnRut:
    def test_same_rut_different_names_resolve_to_one_insurer(self, db, world) -> None:
        before = db.scalar(select(func.count(Insurer.id)))
        resolved = []
        for name in NAME_VARIANTS:
            insurer, created, matched_on = find_or_create_insurer(
                db, rut=world.insurer.rut, name=name
            )
            resolved.append(insurer.id)
            assert created is False
            assert matched_on == "rut"

        assert len(set(resolved)) == 1
        assert resolved[0] == world.insurer.id
        assert db.scalar(select(func.count(Insurer.id))) == before

    def test_rut_spellings_are_normalized_before_matching(self, db, world) -> None:
        # 99301000-6 dotted, spaced, hyphen-less and zero-padded is ONE rut.
        for spelling in ["99.301.000-6", " 99301000-6 ", "993010006", "099301000-6"]:
            insurer, created, matched_on = find_or_create_insurer(
                db, rut=spelling, name="Whatever S.A."
            )
            assert created is False
            assert matched_on == "rut"
            assert insurer.id == world.insurer.id


class TestDedupOnCmfCode:
    def test_same_cmf_code_different_names_resolve_to_one_insurer(
        self, db, world
    ) -> None:
        for name in NAME_VARIANTS:
            insurer, created, matched_on = find_or_create_insurer(
                db, cmf_code=world.insurer.cmf_code, name=name
            )
            assert created is False
            assert matched_on == "cmf_code"
            assert insurer.id == world.insurer.id

    def test_cmf_code_case_is_normalized_before_matching(self, db, world) -> None:
        insurer, created, matched_on = find_or_create_insurer(
            db, cmf_code=" cmf-hdi-001 ", name="HDI SEGUROS SA"
        )
        assert created is False
        assert matched_on == "cmf_code"
        assert insurer.id == world.insurer.id


class TestNameIsNeverAnIdentity:
    def test_identical_name_with_a_different_rut_creates_a_second_insurer(
        self, db, world
    ) -> None:
        # Same trading name, genuinely different company -> must NOT be merged.
        insurer, created, matched_on = find_or_create_insurer(
            db,
            rut="5000015-K",            # a different, valid rut
            cmf_code="CMF-OTHER-77",
            name="HDI Seguros S.A.",    # byte-identical to world.insurer's name
        )
        assert created is True
        assert matched_on is None
        assert insurer.id != world.insurer.id

    def test_matching_by_name_alone_is_impossible(self, db, world) -> None:
        # With neither rut nor cmf_code there is no identity to match on.
        with pytest.raises(InsurerIdentityError):
            find_or_create_insurer(db, name="HDI Seguros S.A.")


class TestCreationAndAmbiguity:
    def test_unknown_identity_creates_an_external_insurer(self, db, world) -> None:
        insurer, created, matched_on = find_or_create_insurer(
            db, rut="5000029-K", cmf_code="CMF-NEW-42", name="Nueva Aseguradora SpA"
        )
        assert created is True
        assert matched_on is None
        assert insurer.is_native is False
        assert insurer.rut == "5000029-K"

    def test_conflicting_rut_and_cmf_code_is_ambiguous(self, db, world) -> None:
        # rut points at insurer X, cmf_code at insurer Y -> refuse to guess.
        other = Insurer(
            legal_name="Otra Compañía S.A.", rut="5000046-K", cmf_code="CMF-OTRA-5"
        )
        db.add(other)
        db.commit()

        with pytest.raises(InsurerIdentityConflict):
            find_or_create_insurer(
                db, rut=world.insurer.rut, cmf_code=other.cmf_code, name="X"
            )


class TestMatchEndpoint:
    def test_match_endpoint_dedups_across_name_variants(
        self, client, world, headers_a
    ) -> None:
        ids = set()
        for name in NAME_VARIANTS:
            response = client.post(
                f"{API}/insurers/match",
                json={"rut": world.insurer.rut, "legal_name": name},
                headers=headers_a,
            )
            assert response.status_code in (200, 201), response.text
            body = response.json()
            ids.add(body["insurer"]["id"] if "insurer" in body else body["id"])

        assert ids == {world.insurer.id}

    def test_match_endpoint_does_not_rename_the_canonical_insurer(
        self, client, world, headers_a, db
    ) -> None:
        client.post(
            f"{API}/insurers/match",
            json={"rut": world.insurer.rut, "legal_name": "HDI SEGUROS SA"},
            headers=headers_a,
        )
        db.expire_all()
        refreshed = db.get(Insurer, world.insurer.id)
        assert refreshed.legal_name == "HDI Seguros S.A."


class TestSchemaGuarantees:
    def test_rut_and_cmf_code_are_unique_and_not_null(self) -> None:
        columns = Insurer.__table__.c
        assert columns.rut.unique is True
        assert columns.rut.nullable is False
        assert columns.cmf_code.unique is True
        assert columns.cmf_code.nullable is False

    def test_legal_name_is_not_unique(self) -> None:
        # Two different companies may legitimately share a trading name.
        assert Insurer.__table__.c.legal_name.unique is not True
