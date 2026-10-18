"""``POST /policies/upload`` — the broker's explicit insurer choice (v12, Pólizas list).

Real policy PDFs print the issuing insurer only by NAME (no rut/cmf), and a
colocación created on demand has no accepted proposal to infer it from. Rule 7
stays: an insurer is never auto-matched by name — the 422 ``insurer_unresolved``
carries name-based SUGGESTIONS and the broker re-posts the same document with
``insurer_id``. The retry re-uses the core read: the PDF is read ONCE.

Hermetic: the provider is a fake over ``ai_service._chat_model`` (conftest blanks
the key; ``ai_key`` only flips the "configured" switch).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.ai import Extraction
from app.models.case_file import CaseFile
from app.models.client import Client
from app.models.document import Document, DocumentCategory
from app.models.enums import EntityType
from app.models.catalog import InsuranceSubtype
from app.models.insured import Insured
from app.models.insurer import Insurer
from app.models.policy import Policy
from app.schemas.extraction.policy_tiers import CORE_GROUPS
from app.services import ai as ai_service
from app.services import policy_reading
from app.services.extraction_commit import insurer_name_suggestions
from tests.conftest import API


# =============================================================================
# Provider double — answers per core GROUP, counts every call
# =============================================================================


class _Msg:
    def __init__(self, content: str):
        self.content = content
        self.additional_kwargs: dict = {}
        self.response_metadata = {
            "model_name": "fake/tiered",
            "finish_reason": "stop",
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        self.usage_metadata = {"input_tokens": 100, "output_tokens": 50}


class _Fake:
    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        return _Model(self)


class _Model:
    def __init__(self, owner: _Fake):
        self.owner = owner

    def with_structured_output(self, schema, method=None, include_raw=False):
        return _Structured(self.owner, schema)


class _Structured:
    def __init__(self, owner: _Fake, schema):
        self.owner, self.schema = owner, schema

    def invoke(self, messages):
        with self.owner.lock:
            self.owner.calls.append(self.schema.__name__)
        answer = self.owner.answers.get(self.schema.__name__, {})
        return {"raw": _Msg(json.dumps(answer, ensure_ascii=False)), "parsed": None,
                "parsing_error": None}


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


_SOUTHBRIDGE_NAME = "Southbridge Compañía de Seguros Generales S.A."
_NEW_INSURED_RUT = "76086428-5"


def _identity(insurer: dict, number: str = "SB-001") -> dict:
    return {
        "policy_number": number,
        "insurer": insurer,
        "insured": [{"legal_name": "Empresa Nueva SpA", "rut": "76.086.428-5"}],
        "broker": {"legal_name": "Corredora Alfa SpA"},
        "period_start_at": "2026-01-01T12:00",
        "period_end_at": "2027-01-01T12:00",
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 39, "vat_uf": 19,
                    "total_premium_uf": 158},
        "confidence": 90,
    }


def _install(monkeypatch, insurer: dict, number: str = "SB-001") -> _Fake:
    fake = _Fake({"CoreIdentityMoney": _identity(insurer, number), "CorePlan": {}})
    monkeypatch.setattr(ai_service, "_chat_model", fake)
    return fake


def _doc(db, tenant, tmp_path, name="poliza-southbridge.txt") -> Document:
    path = tmp_path / name
    path.write_text("Póliza emitida por Southbridge. " * 30, encoding="utf-8")
    document = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CLIENT,
        entity_id=tenant.client.id,
        case_file_id=None,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=DocumentCategory.POLICY,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@pytest.fixture()
def insurers(db, world):
    """Southbridge (generales + vida), an unrelated company, and an external
    'Southbridge' created by broker B — invisible to broker A."""
    rows = {
        "southbridge": Insurer(legal_name=_SOUTHBRIDGE_NAME, rut="99288000-7",
                               cmf_code="CMF-SB-01", is_native=True),
        "southbridge_vida": Insurer(legal_name="Southbridge Seguros de Vida S.A.",
                                    rut="96655860-1", cmf_code="CMF-SB-02", is_native=False),
        "mapfre": Insurer(legal_name="Mapfre Compañía de Seguros Generales de Chile S.A.",
                          rut="96508210-7", cmf_code="CMF-MAP-01", is_native=True),
        "foreign": Insurer(legal_name="Southbridge Externa Ltda.", rut="76543210-3",
                           cmf_code="CMF-EXT-B", is_native=False,
                           created_by_broker_id=world.b.broker_id),
    }
    db.add_all(rows.values())
    db.commit()
    for row in rows.values():
        db.refresh(row)
    return rows


def _sub(db) -> InsuranceSubtype:
    return db.scalars(select(InsuranceSubtype).where(InsuranceSubtype.code == "PRO-02")).one()


def _clients_with_rut(db, broker_id: int, rut: str) -> int:
    return db.scalar(
        select(func.count(Client.id))
        .join(Insured, Insured.id == Client.insured_id)
        .where(Client.broker_id == broker_id, Insured.rut == rut)
    )


def _core_rows(db, document_id: int) -> list[Extraction]:
    return db.scalars(
        select(Extraction).where(
            Extraction.document_id == document_id,
            Extraction.prompt_version == policy_reading.CORE_PROMPT_VERSION,
        )
    ).all()


# =============================================================================
# The flow: insured_unresolved -> insurer_unresolved -> 201, ONE read
# =============================================================================


def test_name_only_insurer_is_chosen_by_the_broker_and_the_pdf_is_read_once(
    client, world, db, catalog, insurers, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db)
    document = _doc(db, world.a, tmp_path)
    fake = _install(monkeypatch, {"legal_name": _SOUTHBRIDGE_NAME})
    body = {"document_id": document.id, "insurance_subtype_id": sub.id}
    cases_before = db.scalar(select(func.count(CaseFile.id)))

    # 1. Unknown insured RUT -> 422 insured_unresolved (the one and only read).
    first = client.post(f"{API}/policies/upload", json=body, headers=headers_a)
    assert first.status_code == 422, first.text
    assert first.json()["detail"]["code"] == "insured_unresolved"
    assert len(fake.calls) == len(CORE_GROUPS)

    # 2. "Crear este asegurado y seguir" -> 422 insurer_unresolved + suggestions.
    second = client.post(
        f"{API}/policies/upload", json={**body, "create_client": True}, headers=headers_a
    )
    assert second.status_code == 422, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "insurer_unresolved"
    assert detail["field"] == "insurer"
    assert detail["read_name"] == _SOUTHBRIDGE_NAME
    assert "RUT" in detail["reason"] and "Elige la compañía" in detail["reason"]
    ids = [s["id"] for s in detail["suggestions"]]
    assert ids[0] == insurers["southbridge"].id  # best match first
    assert insurers["southbridge_vida"].id in ids
    assert insurers["mapfre"].id not in ids  # shares only the legal noise
    assert insurers["foreign"].id not in ids  # another broker's external company
    assert len(ids) <= 3
    assert set(detail["suggestions"][0]) == {"id", "name", "rut", "cmf_code"}
    assert detail["suggestions"][0]["cmf_code"] == "CMF-SB-01"
    assert detail["extraction_id"] == first.json()["detail"]["extraction_id"]
    # The 422 rolled everything back: no client, no colocación, no policy.
    db.expire_all()
    assert _clients_with_rut(db, world.a.broker_id, _NEW_INSURED_RUT) == 0
    assert db.scalar(select(func.count(CaseFile.id))) == cases_before
    assert db.scalars(select(Policy).where(Policy.policy_number == "SB-001")).first() is None
    assert len(fake.calls) == len(CORE_GROUPS)  # re-used, not re-read

    # 3. "Usar esta compañía" — the dialog re-posts every prior choice.
    third = client.post(
        f"{API}/policies/upload",
        json={**body, "create_client": True, "insurer_id": insurers["southbridge"].id},
        headers=headers_a,
    )
    assert third.status_code == 201, third.text
    result = third.json()
    policy = db.get(Policy, result["policy"]["id"])
    assert policy.insurer_id == insurers["southbridge"].id
    assert "Compañía elegida por el corredor (el PDF no trae su RUT/CMF)." in result["warnings"]
    assert policy_reading.REUSED_READ_ES in result["warnings"]
    assert _clients_with_rut(db, world.a.broker_id, _NEW_INSURED_RUT) == 1  # exactly ONE

    # The provider was called ONCE across the three requests; one core row.
    assert len(fake.calls) == len(CORE_GROUPS)
    rows = _core_rows(db, document.id)
    assert len(rows) == 1 and rows[0].id == result["extraction_id"]
    # A confirmed read is never replayed.
    db.refresh(document)
    assert policy_reading.reusable_core_read(
        db, document=document, broker_id=world.a.broker_id
    ) is None


def test_unknown_or_invisible_insurer_id_is_insurer_not_found(
    client, world, db, catalog, insurers, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db)
    document = _doc(db, world.a, tmp_path)
    _install(monkeypatch, {"legal_name": _SOUTHBRIDGE_NAME})
    base = {"document_id": document.id, "insurance_subtype_id": sub.id,
            "client_id": world.a.client.id}

    for bad in (999_999, insurers["foreign"].id):
        response = client.post(
            f"{API}/policies/upload", json={**base, "insurer_id": bad}, headers=headers_a
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "insurer_not_found"
        assert detail["insurer_id"] == bad
        assert "compañía elegida" in detail["reason"]
    assert db.scalars(select(Policy).where(Policy.policy_number == "SB-001")).first() is None


def test_a_payload_with_rut_cmf_ignores_the_chosen_insurer(
    client, world, db, catalog, insurers, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db)
    document = _doc(db, world.a, tmp_path)
    _install(
        monkeypatch,
        {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"},
        number="HDI-777",
    )
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "insurance_subtype_id": sub.id,
              "client_id": world.a.client.id, "insurer_id": insurers["southbridge"].id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert db.get(Policy, body["policy"]["id"]).insurer_id == world.insurer.id
    assert any("no coincide con el RUT/CMF" in w for w in body["warnings"])
    assert "Compañía elegida por el corredor (el PDF no trae su RUT/CMF)." not in body["warnings"]


# =============================================================================
# The re-use window and the name suggestions (pure)
# =============================================================================


def test_a_stale_core_read_is_not_reused(
    client, world, db, catalog, insurers, tmp_path, ai_key, monkeypatch, headers_a
):
    sub = _sub(db)
    document = _doc(db, world.a, tmp_path)
    fake = _install(monkeypatch, {"legal_name": _SOUTHBRIDGE_NAME})
    body = {"document_id": document.id, "insurance_subtype_id": sub.id,
            "client_id": world.a.client.id}
    assert client.post(f"{API}/policies/upload", json=body, headers=headers_a).status_code == 422
    assert len(fake.calls) == len(CORE_GROUPS)

    row = _core_rows(db, document.id)[0]
    assert policy_reading.reusable_core_read(
        db, document=document, broker_id=world.a.broker_id
    ).id == row.id
    later = datetime.now(timezone.utc) + policy_reading.CORE_REUSE_WINDOW + timedelta(minutes=1)
    assert policy_reading.reusable_core_read(
        db, document=document, broker_id=world.a.broker_id, now=later
    ) is None
    # Another broker never re-uses it either.
    assert policy_reading.reusable_core_read(
        db, document=document, broker_id=world.b.broker_id
    ) is None


def test_name_suggestions_use_only_the_distinctive_tokens(db, world, insurers):
    broker_id = world.a.broker_id
    hits = insurer_name_suggestions(db, broker_id, "SOUTHBRIDGE SEGUROS")
    assert hits[0]["id"] in {insurers["southbridge"].id, insurers["southbridge_vida"].id}
    assert insurers["foreign"].id not in [h["id"] for h in hits]
    # Accents / case / legal suffixes never make a match on their own.
    assert insurer_name_suggestions(db, broker_id, "Compañía de Seguros Generales S.A.") == []
    assert insurer_name_suggestions(db, broker_id, None) == []
    assert [h["id"] for h in insurer_name_suggestions(db, broker_id, "MAPFRE chile")] == [
        insurers["mapfre"].id
    ]
    # Broker B sees its own external company.
    assert insurers["foreign"].id in [
        h["id"] for h in insurer_name_suggestions(db, world.b.broker_id, "Southbridge")
    ]

