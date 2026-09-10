"""v8 broker-journey backend: validate-then-dynamic POLICY upload, ANTECEDENTES
warn-not-block + advisory ramos, the public INSURED-DECISION surface, and GROUP
ICONS. Every path stays hermetic to the AI provider (the fake chat model).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.document import Document, DocumentCategory
from app.models.enums import EntityType
from app.models.policy import Policy
from app.services import ai as ai_service
from tests.conftest import API, make_case_file


# --- Fake provider (mirrors tests/test_ai_streaming.py, kept local) ----------

class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.response_metadata = {"token_usage": {"prompt_tokens": 5, "completion_tokens": 7}}
        self.usage_metadata = {"input_tokens": 5, "output_tokens": 7}


class _FakeStructured:
    def __init__(self, content: str, parsed=None):
        self._content = content
        self._parsed = parsed

    def invoke(self, messages):
        return {"raw": _FakeMessage(self._content), "parsed": self._parsed, "parsing_error": None}


class _FakeChatModel:
    def __init__(self, *, content: str = "{}"):
        self._content = content

    def with_structured_output(self, schema, method=None, include_raw=False):
        return _FakeStructured(self._content)

    def invoke(self, messages):
        return _FakeMessage(self._content)


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


def _policy_document(db, world, tmp_path, case_id):
    """A readable POLICY document attached to an account case file."""
    path = tmp_path / "poliza.txt"
    path.write_text("Póliza emitida por la aseguradora. " * 20, encoding="utf-8")
    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case_id,
        case_file_id=case_id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name="poliza.txt",
        mime_type="text/plain",
        category=DocumentCategory.POLICY,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _fake(monkeypatch, payload: dict):
    monkeypatch.setattr(
        ai_service, "_chat_model", lambda **kwargs: _FakeChatModel(content=json.dumps(payload))
    )


_VALID_INSURER = {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"}


# =============================================================================
# 1. Validate-then-dynamic POLICY upload
# =============================================================================

def test_policy_upload_valid_core_commits_and_persists_payload(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    case = make_case_file(db, world.a)
    db.commit()
    document = _policy_document(db, world, tmp_path, case.id)

    _fake(monkeypatch, {
        "policy_number": "POL-UP-VALID",
        "insurer": _VALID_INSURER,
        "broker": {"name": "Corredora Alfa SpA"},
        "period_start_at": "2026-01-01T12:00:00Z",
        "period_end_at": "2027-01-01T12:00:00Z",
        "total_insured_amount_uf": 17920,
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 20},
        # A "dynamic" remainder the typed columns never carry — proves the full
        # payload is persisted, not just the promoted core.
        "vehicles": [{"patente": "ABCD12", "marca": "Volvo"}],
    })

    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "case_file_id": case.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_core_valid"] is True
    assert body["overridden"] is False
    policy = body["policy"]
    assert policy["policy_number"] == "POL-UP-VALID"
    assert policy["source_document_id"] == document.id
    assert policy["extraction_id"] is not None
    # The FULL parse is on payload; the typed money core is promoted to columns.
    assert policy["payload"]["vehicles"][0]["patente"] == "ABCD12"
    assert Decimal(policy["net_premium_uf"]) == Decimal("120.0000")
    assert Decimal(policy["vat_uf"]) == Decimal("19.0000")

    db.expire_all()
    row = db.get(Policy, policy["id"])
    assert row.is_core_valid is True
    assert row.payload["vehicles"][0]["marca"] == "Volvo"
    assert row.extraction_id is not None


def test_policy_upload_missing_core_is_422_not_a_policy(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    case = make_case_file(db, world.a)
    db.commit()
    document = _policy_document(db, world, tmp_path, case.id)

    # No vigencia, no prima — this does not look like a policy.
    _fake(monkeypatch, {"policy_number": "POL-BAD", "insurer": _VALID_INSURER})

    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "case_file_id": case.id},
        headers=headers_a,
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "not_a_policy"
    assert "vigencia" in detail["missing"] and "prima" in detail["missing"]
    assert "póliza" in detail["reason"].lower()
    # Nothing was committed.
    assert db.scalars(select(Policy).where(Policy.policy_number == "POL-BAD")).first() is None


def test_policy_upload_override_commits_anyway(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    case = make_case_file(db, world.a)
    db.commit()
    document = _policy_document(db, world, tmp_path, case.id)

    _fake(monkeypatch, {"policy_number": "POL-OVERRIDE", "insurer": _VALID_INSURER})

    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "case_file_id": case.id, "override": True},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_core_valid"] is False
    assert body["overridden"] is True
    assert body["warnings"], "the override records what was still missing"

    db.expire_all()
    row = db.get(Policy, body["policy"]["id"])
    assert row.policy_number == "POL-OVERRIDE"
    assert row.is_core_valid is False
    assert "vigencia" in row.core_validation["missing"]


def test_policy_upload_resolves_insurer_from_account_when_pdf_names_no_rut(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """Real policy PDFs name the insurer only by NAME. When the payload has no
    rut/cmf, the insurer is resolved from the ACCOUNT's accepted proposal (never
    by name), so the commit SUCCEEDS instead of a 422."""
    from app.models.proposal import Proposal, ProposalStatus

    case = make_case_file(db, world.a)
    db.commit()
    # The account already accepted an offer from a known (native) insurer.
    accepted = Proposal(
        broker_id=world.a.broker_id,
        quote_request_id=world.a.quote.id,
        insurer_id=world.insurer.id,
        source_document_id=world.a.document.id,
        status=ProposalStatus.ACCEPTED,
        taxable_premium_uf=Decimal("100"),
        net_premium_uf=Decimal("100"),
        vat_uf=Decimal("19"),
        total_premium_uf=Decimal("119"),
    )
    db.add(accepted)
    db.commit()

    document = _policy_document(db, world, tmp_path, case.id)
    _fake(monkeypatch, {
        "policy_number": "POL-NAME-ONLY",
        # NO rut, NO cmf — only a name, as the corpus policies carry.
        "insurer": {"legal_name": "Southbridge Compañía de Seguros Generales S.A."},
        "broker": {"name": "Corredora Alfa SpA"},
        "period_start_at": "2026-01-01T12:00:00Z",
        "period_end_at": "2027-01-01T12:00:00Z",
        "total_insured_amount_uf": 17920,
        "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 20},
    })

    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id, "case_file_id": case.id},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_core_valid"] is True
    # Resolved from the account's accepted proposal — never by name.
    assert body["policy"]["insurer_id"] == world.insurer.id
    assert any("account" in w.lower() for w in body["warnings"])


def test_policy_upload_of_another_tenants_document_is_404(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    case = make_case_file(db, world.b)
    db.commit()
    document = _policy_document(db, world, tmp_path, case.id)
    # Reassign the document to broker B so broker A cannot see it.
    document.broker_id = world.b.broker_id
    db.commit()

    _fake(monkeypatch, {"policy_number": "POL-X", "insurer": _VALID_INSURER})
    response = client.post(
        f"{API}/policies/upload",
        json={"document_id": document.id},
        headers=headers_a,
    )
    assert response.status_code == 404, response.text


# =============================================================================
# 2. ANTECEDENTES: advisory ramos (cross-ramo template allowed)
# =============================================================================

def _seed_line(db, *, broker_id, line_id, name):
    from app.models.line_record_schema import LineRecordSchema

    row = LineRecordSchema(
        broker_id=broker_id,
        insurance_line_id=line_id,
        name=name,
        version=1,
        is_active=True,
        is_template=False,
        definition={"sections": [
            {"key": "identification", "label": "Identificación",
             "fields": [{"key": "razon_social", "label": "Razón social", "type": "text"}]}
        ]},
    )
    db.add(row)
    db.flush()
    return row


def test_assign_cross_ramo_template_is_allowed_with_a_warning(
    client, world, headers_a, db, tmp_path
):
    """v8: a template of another ramo no longer 422s — it is a soft warning."""
    from app.models.insurance_line import InsuranceLine

    other_line = InsuranceLine(broker_id=world.a.broker_id, name="Otro Ramo Alfa")
    db.add(other_line)
    db.flush()
    foreign_ramo = _seed_line(
        db, broker_id=world.a.broker_id, line_id=other_line.id, name="Plantilla Otro Ramo"
    )
    case = make_case_file(db, world.a)  # its insurance_line is world.a.line
    db.commit()

    response = client.post(
        f"{API}/case-files/{case.id}/line",
        json={"line_record_schema_id": foreign_ramo.id},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_id"] == foreign_ramo.id
    assert any("otro ramo" in w.lower() for w in body["warnings"])


def test_process_without_a_line_surfaces_the_generic_schema(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """The generic-antecedentes fallback populates the response ``schema``."""
    from tests.test_antecedentes import _account_with_docs, _fake_model

    case = _account_with_docs(db, world.a, tmp_path)  # no line seeded
    _fake_model(monkeypatch)

    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] is not None
    keys = {s["key"] for s in body["schema"]["sections"]}
    assert {"asegurado", "materias", "siniestralidad"} <= keys


# =============================================================================
# 3. Public INSURED-DECISION surface
# =============================================================================

def _make_proposal(client, headers, quote_id, doc_id, insurer_id, *, taxable="100"):
    response = client.post(
        f"{API}/proposals",
        json={
            "quote_request_id": quote_id,
            "insurer_id": insurer_id,
            "source_document_id": doc_id,
            "taxable_premium_uf": taxable,
            "exempt_premium_uf": "50",
            "status": "submitted",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _make_extra_insurer(client, headers, rut, cmf, name):
    response = client.post(
        f"{API}/insurers/match",
        json={"rut": rut, "cmf_code": cmf, "legal_name": name},
        headers=headers,
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    return body["insurer"]["id"] if "insurer" in body else body["id"]


def _sent_offering(client, world, headers):
    """An offering with two open proposals, moved out of DRAFT so it is public."""
    p1 = _make_proposal(client, headers, world.a.quote.id, world.a.document.id, world.insurer.id)
    other = _make_extra_insurer(client, headers, "5000032-K", "CMF-CHUBB-2", "Chubb Seguros Chile S.A.")
    p2 = _make_proposal(
        client, headers, world.a.quote.id, world.a.document.id, other, taxable="120"
    )
    created = client.post(
        f"{API}/offerings",
        json={"quote_request_id": world.a.quote.id, "selected_proposal_id": p1},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    offering = created.json()
    sent = client.post(
        f"{API}/offerings/{offering['id']}/send",
        json={"channel": "link"},
        headers=headers,
    )
    assert sent.status_code == 200, sent.text
    return offering, p1, p2


def test_public_offering_lists_all_open_proposals(client, world, headers_a):
    offering, p1, p2 = _sent_offering(client, world, headers_a)
    public = client.get(f"{API}/public/offerings/{offering['share_token']}")
    assert public.status_code == 200, public.text
    body = public.json()
    ids = {p["id"] for p in body["proposals"]}
    assert ids == {p1, p2}
    # No broker-internal leak: commission never appears in the projection.
    assert "commission_pct" not in json.dumps(body)
    assert any(p["is_recommended"] for p in body["proposals"])


def test_public_offering_decision_records_and_is_idempotent(client, world, headers_a):
    offering, p1, p2 = _sent_offering(client, world, headers_a)
    token = offering["share_token"]

    decided = client.post(
        f"{API}/public/offerings/{token}/decision",
        json={"proposal_id": p2, "note": "Prefiero la cobertura de Chubb"},
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["decided_proposal_id"] == p2
    assert body["status"] == "accepted"

    # Idempotent: the same choice again is a 200 no-op.
    again = client.post(
        f"{API}/public/offerings/{token}/decision",
        json={"proposal_id": p2, "note": "Prefiero la cobertura de Chubb"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["decided_proposal_id"] == p2

    # The broker sees the insured's choice vs its own recommendation.
    broker_view = client.get(f"{API}/offerings/{offering['id']}", headers=headers_a).json()
    assert broker_view["decided_proposal_id"] == p2
    assert broker_view["selected_proposal_id"] == p1
    assert broker_view["decided_note"] == "Prefiero la cobertura de Chubb"


def test_public_offering_decision_foreign_proposal_is_422(client, world, headers_a):
    offering, _p1, _p2 = _sent_offering(client, world, headers_a)
    response = client.post(
        f"{API}/public/offerings/{offering['share_token']}/decision",
        json={"proposal_id": 999999},
    )
    assert response.status_code == 422, response.text


def test_public_offering_decision_on_a_draft_token_is_404(client, world, headers_a):
    # A DRAFT offering is "not yet shared" — its token stays 404 on decision too.
    p1 = _make_proposal(client, headers_a, world.a.quote.id, world.a.document.id, world.insurer.id)
    created = client.post(
        f"{API}/offerings",
        json={"quote_request_id": world.a.quote.id, "selected_proposal_id": p1},
        headers=headers_a,
    ).json()
    response = client.post(
        f"{API}/public/offerings/{created['share_token']}/decision",
        json={"proposal_id": p1},
    )
    assert response.status_code == 404, response.text


# =============================================================================
# 4. GROUP ICONS
# =============================================================================

def test_group_create_with_emoji_icon(client, world, headers_a):
    response = client.post(
        f"{API}/account-groups",
        json={"name": "JO Pastelería", "icon": {"kind": "emoji", "value": "🍰"}},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    icon = response.json()["icon"]
    assert icon["kind"] == "emoji"
    assert icon["value"] == "🍰"
    assert icon["url"] is None


def test_group_create_with_glyph_icon(client, world, headers_a):
    response = client.post(
        f"{API}/account-groups",
        json={"name": "Grupo Glifo", "icon": {"kind": "glyph", "value": "factory"}},
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    icon = response.json()["icon"]
    assert icon["kind"] == "glyph"
    assert icon["value"] == "factory"


def test_group_create_with_image_icon(client, world, headers_a):
    response = client.post(
        f"{API}/account-groups",
        json={
            "name": "Grupo Imagen",
            "icon": {"kind": "image", "document_id": world.a.document.id},
        },
        headers=headers_a,
    )
    assert response.status_code == 201, response.text
    icon = response.json()["icon"]
    assert icon["kind"] == "image"
    assert icon["url"], "an image icon resolves a scoped URL"


def test_group_image_icon_foreign_document_is_404(client, world, headers_a):
    response = client.post(
        f"{API}/account-groups",
        json={
            "name": "Grupo Ajeno",
            "icon": {"kind": "image", "document_id": world.b.document.id},
        },
        headers=headers_a,
    )
    assert response.status_code == 404, response.text


def test_group_update_can_set_an_icon(client, world, headers_a):
    created = client.post(
        f"{API}/account-groups", json={"name": "Sin Icono"}, headers=headers_a
    ).json()
    assert created["icon"] is None
    updated = client.patch(
        f"{API}/account-groups/{created['id']}",
        json={"icon": {"kind": "emoji", "value": "🏭"}},
        headers=headers_a,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["icon"]["value"] == "🏭"
