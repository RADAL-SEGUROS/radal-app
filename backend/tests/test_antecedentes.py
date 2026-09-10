"""The v6 antecedentes expediente: consolidate → validate → register → PDF.

Hermetic throughout: the chat model is faked (``tests.test_ai_streaming._FakeChatModel``)
and the Chromium PDF render is monkeypatched to a bytes stub, so the suite never
reaches DeepInfra and never needs a browser. The acceptance invariants:

* SUGGEST writes exactly one ``extraction`` row and stages a ``review`` expediente;
  nothing is committed until ``/register`` (CLAUDE.md rule 6).
* every query filters ``broker_id``; a foreign account is a 404, never a 403.
* an AI outage is a 503 with a failed audit row — never a 500.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.ai import Extraction, ExtractionKind, ExtractionStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseSection, EntityType, RecordExpedienteStatus
from app.models.line_record_schema import LineRecordSchema
from app.models.record_expediente import RecordExpediente
from app.services import ai as ai_service
from tests.conftest import API, make_case_file
from tests.test_ai_streaming import _FakeChatModel


# --- Fixtures / builders -----------------------------------------------------

_DEFINITION = {
    "sections": [
        {
            "key": "identification",
            "label": "Identificación",
            "fields": [
                {"key": "razon_social", "label": "Razón social", "type": "text", "required": True},
                {"key": "giro", "label": "Giro", "type": "text"},
            ],
        },
        {
            "key": "values",
            "label": "Montos",
            "fields": [
                {"key": "total_uf", "label": "Monto total", "type": "money_uf", "required": True},
                {
                    "key": "partidas",
                    "label": "Partidas",
                    "type": "list",
                    "fields": [
                        {"key": "item", "label": "Ítem", "type": "text"},
                        {"key": "monto_uf", "label": "Monto UF", "type": "money_uf"},
                    ],
                },
            ],
        },
    ]
}

_SUGGESTED = {
    "identification": {"razon_social": "Viña Santa Alicia S.A.", "giro": "Viñedos"},
    "values": {
        "total_uf": "UF 17.920",
        "partidas": [{"item": "Edificio", "monto_uf": "UF 10.000"}],
    },
    "confidence": 77,
}


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


@pytest.fixture()
def local_media(monkeypatch, tmp_path):
    """Force local file storage so the generated PDF never touches S3."""
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))


def _make_schema(db, *, broker_id, line_id, active=True, version=1, name="Antecedentes Property"):
    row = LineRecordSchema(
        broker_id=broker_id,
        insurance_line_id=line_id,
        name=name,
        version=version,
        is_active=active,
        definition=_DEFINITION,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _account_with_docs(db, tenant, tmp_path):
    """An account case_file plus two antecedentes documents with real bytes."""
    case = make_case_file(db, tenant)
    db.commit()
    for name, section, category, text in (
        ("solicitud.txt", CaseSection.ROOT_PROSPECT, DocumentCategory.PROSPECT_REQUEST,
         "Solicitud de seguro de la Viña Santa Alicia S.A., giro viñedos. " * 5),
        ("slip.txt", CaseSection.SUBMISSION, DocumentCategory.SUBMISSION_LETTER,
         "Slip: monto total asegurado UF 17.920, partida edificio UF 10.000. " * 5),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        db.add(
            Document(
                broker_id=tenant.broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=case.id,
                case_file_id=case.id,
                section=section,
                s3_key=str(path),
                bucket="radal-test-bucket",
                original_name=name,
                mime_type="text/plain",
                category=category,
            )
        )
    db.commit()
    return case


def _fake_model(monkeypatch, *, content=None):
    fake = _FakeChatModel(content=content or json.dumps(_SUGGESTED, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    return fake


# --- 1. Consolidation SUGGEST ------------------------------------------------


def test_process_consolidates_and_stages_a_review(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)

    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "review"
    assert body["schema"]["sections"][0]["key"] == "identification"
    assert body["payload"]["identification"]["razon_social"] == "Viña Santa Alicia S.A."
    # Chilean number parsing normalised the UF amount.
    assert body["payload"]["values"]["total_uf"] == "17920"
    assert body["extraction_id"]
    # The full extraction is embedded so the review UI can render provenance.
    assert body["extraction"]["id"] == body["extraction_id"]
    assert body["extraction"]["status"] == "succeeded"
    assert body["extraction"]["prompt_version"]

    db.expire_all()
    rows = db.scalars(select(Extraction).where(Extraction.case_file_id == case.id)).all()
    assert len(rows) == 1, "exactly one extraction row per attempt"
    assert rows[0].status is ExtractionStatus.SUCCEEDED
    assert rows[0].kind is ExtractionKind.CASE_DOCUMENT
    assert rows[0].prompt_version == ai_service.ANTECEDENTES_PROMPT_VERSION

    exp = db.scalars(
        select(RecordExpediente).where(RecordExpediente.case_file_id == case.id)
    ).one()
    assert exp.status is RecordExpedienteStatus.REVIEW
    assert exp.payload["values"]["partidas"][0]["item"] == "Edificio"
    # SUGGEST never registers.
    assert exp.registered_at is None


def test_get_returns_the_schema_even_before_processing(
    client, world, headers_a, db, tmp_path
):
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)

    response = client.get(
        f"{API}/case-files/{case.id}/antecedentes", headers=headers_a
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft"
    assert body["payload"] is None
    assert body["schema"]["sections"][1]["fields"][1]["type"] == "list"


def test_process_without_a_schema_falls_back_to_generic(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """A ramo with no configured template no longer 422s: it consolidates against
    the built-in GENERIC definition so a free upload always yields an extraction."""
    case = _account_with_docs(db, world.a, tmp_path)  # no schema seeded
    _fake_model(monkeypatch)

    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "review"

    # Exactly one extraction row, and it succeeded (rule 6).
    rows = db.scalars(select(Extraction).where(Extraction.case_file_id == case.id)).all()
    assert len(rows) == 1
    assert rows[0].status is ExtractionStatus.SUCCEEDED
    assert rows[0].prompt_version == ai_service.ANTECEDENTES_PROMPT_VERSION

    # The service resolved no template, so the consolidation used the generic shape.
    result = ai_service.consolidate_antecedentes(
        db, case_file_id=case.id, broker_id=world.a.broker_id
    )
    assert result.line_record_schema is None
    assert {s["key"] for s in ai_service._GENERIC_ANTECEDENTES_DEFINITION["sections"]} == {
        "asegurado",
        "materias",
        "siniestralidad",
        "observaciones",
    }


# --- 2. Register (human confirm) ---------------------------------------------


def test_register_validates_and_persists_the_human_payload(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)
    client.post(f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a)

    edited = {
        "identification": {"razon_social": "Viña Santa Alicia LIMITADA", "giro": "Viñedos"},
        "values": {"total_uf": "UF 18.500", "partidas": [{"item": "Bodega", "monto_uf": "UF 5.000"}]},
    }
    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/register",
        json={"payload": edited},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "registered"
    assert body["payload"]["identification"]["razon_social"] == "Viña Santa Alicia LIMITADA"
    assert body["payload"]["values"]["total_uf"] == "18500"

    # Reopening shows the persisted, validated values.
    reopened = client.get(
        f"{API}/case-files/{case.id}/antecedentes", headers=headers_a
    ).json()
    assert reopened["status"] == "registered"
    assert reopened["payload"]["values"]["partidas"][0]["item"] == "Bodega"

    db.expire_all()
    exp = db.scalars(
        select(RecordExpediente).where(RecordExpediente.case_file_id == case.id)
    ).one()
    assert exp.status is RecordExpedienteStatus.REGISTERED
    assert exp.registered_by_id == world.a.admin.id


# --- 3. Branded PDF ----------------------------------------------------------


def test_pdf_generates_and_files_a_document(
    client, world, headers_a, db, tmp_path, ai_key, local_media, monkeypatch
):
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)
    client.post(f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a)
    client.post(
        f"{API}/case-files/{case.id}/antecedentes/register",
        json={"payload": _SUGGESTED},
        headers=headers_a,
    )

    async def _fake_render(html: str) -> bytes:
        assert "http://" not in html and "https://" not in html, "no remote assets"
        return b"%PDF-1.4 fake antecedentes"

    import app.services.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)

    response = client.get(
        f"{API}/case-files/{case.id}/antecedentes/pdf", headers=headers_a
    )
    assert response.status_code == 200, response.text
    assert response.json()["url"]

    db.expire_all()
    exp = db.scalars(
        select(RecordExpediente).where(RecordExpediente.case_file_id == case.id)
    ).one()
    assert exp.pdf_document_id is not None
    doc = db.get(Document, exp.pdf_document_id)
    assert doc.category is DocumentCategory.ANTECEDENTES_PACK
    assert doc.entity_type is EntityType.CASE_FILE
    assert doc.broker_id == world.a.broker_id


def test_pdf_before_register_renders_incomplete(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """v8 warn-not-block: an unregistered (REVIEW) expediente still renders the
    Bases Técnicas, but flagged ``incomplete`` — never the old 409."""
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)
    client.post(f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a)

    async def _fake_render(html: str) -> bytes:
        return b"%PDF-1.4 borrador"

    import app.services.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)

    response = client.get(
        f"{API}/case-files/{case.id}/antecedentes/pdf", headers=headers_a
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"]
    assert body["incomplete"] is True


# --- 4. Tenancy --------------------------------------------------------------


def test_another_tenant_gets_404_on_the_account(
    client, world, headers_b, db, tmp_path, ai_key, monkeypatch
):
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)

    for method, suffix in (
        ("get", ""),
        ("post", "/process"),
        ("get", "/pdf"),
    ):
        call = getattr(client, method)
        response = call(
            f"{API}/case-files/{case.id}/antecedentes{suffix}", headers=headers_b
        )
        assert response.status_code == 404, (method, suffix, response.text)

    reg = client.post(
        f"{API}/case-files/{case.id}/antecedentes/register",
        json={"payload": {}},
        headers=headers_b,
    )
    assert reg.status_code == 404


# --- 5. "Never a 500" --------------------------------------------------------


def test_process_without_a_key_is_503_and_persists_a_failed_row(
    client, world, headers_a, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AI_API_KEY", "")  # the outage
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)

    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a
    )
    assert response.status_code == 503
    assert response.headers["X-Radal-AI-Error"] == "ai_not_configured"

    db.expire_all()
    rows = db.scalars(select(Extraction).where(Extraction.case_file_id == case.id)).all()
    assert len(rows) == 1
    assert rows[0].status is ExtractionStatus.FAILED
    # SUGGEST never staged a review.
    exp = db.scalars(
        select(RecordExpediente).where(RecordExpediente.case_file_id == case.id)
    ).first()
    assert exp is None


# --- 6. Ramo-schema maintainer CRUD ------------------------------------------


def test_ramo_schema_crud_roundtrip(client, world, headers_a, db):
    created = client.post(
        f"{API}/line-record-schemas",
        json={
            "insurance_line_id": world.a.line.id,
            "name": "Antecedentes Property",
            "definition": _DEFINITION,
        },
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    schema_id = created.json()["id"]
    assert created.json()["is_global"] is False
    assert created.json()["definition"]["sections"][0]["key"] == "identification"

    listed = client.get(f"{API}/line-record-schemas", headers=headers_a)
    assert listed.status_code == 200
    assert any(row["id"] == schema_id for row in listed.json())

    resolved = client.get(
        f"{API}/line-record-schemas/{world.a.line.id}", headers=headers_a
    )
    assert resolved.status_code == 200
    assert resolved.json()["id"] == schema_id

    updated = client.put(
        f"{API}/line-record-schemas/{schema_id}",
        json={"name": "Antecedentes Property v2", "is_active": False},
        headers=headers_a,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Antecedentes Property v2"
    assert updated.json()["is_active"] is False

    # Deleting a non-existent (or foreign / global) schema is a 404, never a 403.
    missing = client.delete(
        f"{API}/line-record-schemas/{schema_id + 9999}", headers=headers_a
    )
    assert missing.status_code == 404, missing.text

    deleted = client.delete(
        f"{API}/line-record-schemas/{schema_id}", headers=headers_a
    )
    assert deleted.status_code == 204, deleted.text
    gone = client.delete(
        f"{API}/line-record-schemas/{schema_id}", headers=headers_a
    )
    assert gone.status_code == 404


def test_ramo_schema_crud_requires_settings_manage(
    client, world, headers_a_exec, db
):
    """The maintainer tab is broker_admin-only (Settings.Manage)."""
    listed = client.get(f"{API}/line-record-schemas", headers=headers_a_exec)
    assert listed.status_code == 403
    created = client.post(
        f"{API}/line-record-schemas",
        json={"insurance_line_id": world.a.line.id, "name": "X", "definition": _DEFINITION},
        headers=headers_a_exec,
    )
    assert created.status_code == 403


# --- 7. v7 lines: templates, usage, assignment, completeness -----------------


def test_list_includes_is_template_and_usage(
    client, world, headers_a, db, tmp_path
):
    """The lines list flags templates and reports per-line usage (accounts/groups)."""
    # A global template (broker_id NULL, is_template=True) and the broker's own line.
    template = LineRecordSchema(
        broker_id=None,
        insurance_line_id=world.a.line.id,
        name="Plantilla Property",
        version=1,
        is_active=True,
        is_template=True,
        definition=_DEFINITION,
    )
    db.add(template)
    own = _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id, name="Mi línea")
    # An account on that ramo resolves to the broker's own line → usage 1 account.
    case = _account_with_docs(db, world.a, tmp_path)

    listed = client.get(f"{API}/line-record-schemas", headers=headers_a)
    assert listed.status_code == 200, listed.text
    rows = {row["id"]: row for row in listed.json()}

    assert rows[template.id]["is_template"] is True
    assert rows[template.id]["is_global"] is True
    assert rows[own.id]["is_template"] is False
    # The account with no explicit assignment resolves to the broker's own line.
    assert rows[own.id]["usage"]["accounts"] == 1
    assert rows[template.id]["usage"]["accounts"] == 0
    assert case.id  # (silence linters)


def test_create_from_template_clones_definition(client, world, headers_a, db):
    template = LineRecordSchema(
        broker_id=None,
        insurance_line_id=world.a.line.id,
        name="Plantilla RC",
        version=1,
        is_active=True,
        is_template=True,
        definition=_DEFINITION,
    )
    db.add(template)
    db.commit()

    created = client.post(
        f"{API}/line-record-schemas",
        json={"name": "Línea desde plantilla", "from_template_id": template.id},
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["is_template"] is False
    assert body["broker_id"] == world.a.broker_id
    assert body["insurance_line_id"] == world.a.line.id
    assert body["definition"]["sections"][0]["key"] == "identification"


def test_delete_blocked_when_line_in_use(client, world, headers_a, db, tmp_path):
    own = _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id, name="En uso")
    case = _account_with_docs(db, world.a, tmp_path)
    case.line_record_schema_id = own.id
    db.commit()

    blocked = client.delete(f"{API}/line-record-schemas/{own.id}", headers=headers_a)
    assert blocked.status_code == 409, blocked.text
    assert "uso" in blocked.json()["detail"].lower()


def test_assign_line_to_account(client, world, headers_a, db, tmp_path):
    line_a = _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id, name="Línea A")
    line_b = _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id, name="Línea B")
    case = _account_with_docs(db, world.a, tmp_path)

    response = client.post(
        f"{API}/case-files/{case.id}/line",
        json={"line_record_schema_id": line_b.id},
        headers=headers_a,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_id"] == line_b.id
    assert body["line_name"] == "Línea B"

    db.expire_all()
    reread = db.get(type(case), case.id)
    assert reread.line_record_schema_id == line_b.id
    assert line_a.id != line_b.id


def test_assign_foreign_line_is_404(client, world, headers_a, headers_b, db, tmp_path):
    """A line owned by another broker cannot be assigned (404, never 403)."""
    foreign = _make_schema(db, broker_id=world.b.broker_id, line_id=world.b.line.id, name="Ajena")
    case = _account_with_docs(db, world.a, tmp_path)
    response = client.post(
        f"{API}/case-files/{case.id}/line",
        json={"line_record_schema_id": foreign.id},
        headers=headers_a,
    )
    assert response.status_code == 404


def test_read_reports_required_and_missing_and_complete(
    client, world, headers_a, db, tmp_path
):
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)

    body = client.get(
        f"{API}/case-files/{case.id}/antecedentes", headers=headers_a
    ).json()
    # Two mandatory fields (razon_social, total_uf); nothing filled yet.
    labels = {(f["section"], f["field"]) for f in body["required_fields"]}
    assert ("identification", "razon_social") in labels
    assert ("values", "total_uf") in labels
    assert body["complete"] is False
    missing = {(f["section"], f["field"]) for f in body["missing_required"]}
    assert missing == labels


def test_pdf_when_incomplete_renders_with_a_warning(
    client, world, headers_a, db, tmp_path, ai_key, local_media, monkeypatch
):
    """v8 warn-not-block: a registered-but-incomplete expediente still exports the
    Bases Técnicas — flagged ``incomplete`` with the missing fields — never a 409."""
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)
    client.post(f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a)
    # Register a deliberately incomplete payload (the dynamic model is lenient).
    reg = client.post(
        f"{API}/case-files/{case.id}/antecedentes/register",
        json={"payload": {"identification": {"giro": "Viñedos"}}},
        headers=headers_a,
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["complete"] is False

    async def _fake_render(html: str) -> bytes:
        return b"%PDF-1.4 borrador"

    import app.services.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)

    pdf = client.get(f"{API}/case-files/{case.id}/antecedentes/pdf", headers=headers_a)
    assert pdf.status_code == 200, pdf.text
    body = pdf.json()
    assert body["url"]
    assert body["incomplete"] is True
    assert body["missing_required"], "the missing mandatory fields are surfaced"


# --- 8. v9 corrected model: ramo = recommended files + context --------------


def test_seeded_ramos_carry_the_exact_recommended_files(db):
    """The two global ramos are seeded as name + recommended-files + context.

    A ramo is NO LONGER a field schema — ``recommended_files`` is its first-class
    content, each entry ``{key, label, doc_type, category, format, required,
    description}``."""
    from app.db.import_fixtures import seed_line_templates

    # Broker 999999 is absent → the Fuenzalida clone/assign steps are a no-op; only
    # the two global ramos (broker_id NULL) are seeded.
    seed_line_templates(db, broker_id=999999, assign_case_files=(), commit=True)

    rows = db.scalars(
        select(LineRecordSchema).where(LineRecordSchema.broker_id.is_(None))
    ).all()
    by_name = {row.name: row for row in rows}
    assert "Incendio y Sismo (TRBF)" in by_name
    assert "RC / Ingeniería / Transporte" in by_name

    incendio = by_name["Incendio y Sismo (TRBF)"]
    inc_files = incendio.recommended_files
    # Exactly the four Property recommended files, in order.
    assert [(f["label"], f["required"]) for f in inc_files] == [
        ("Slip de términos y condiciones", True),
        ("Desglose de montos por ubicación", True),
        ("Siniestralidad", True),
        ("Informe de inspección", False),
    ]
    slip = inc_files[0]
    # The slip is the bases técnicas itself, in Word.
    assert slip["doc_type"] == "slip de términos y condiciones"
    assert slip["format"] == "word"
    # Every entry carries the corrected shape.
    for f in inc_files:
        assert {"key", "label", "doc_type", "category", "format", "required", "description"} <= set(f)

    rc = by_name["RC / Ingeniería / Transporte"]
    assert [(f["label"], f["required"]) for f in rc.recommended_files] == [
        ("Slip de términos y condiciones", True),
        ("Cuestionario / formulario de riesgo", True),
        ("Siniestralidad", True),
    ]


def test_create_ramo_with_only_recommended_files(client, world, headers_a, db):
    """A ramo is valid with just a name + recommended files — the deprecated
    sectioned ``definition`` is never required (v9 corrected model)."""
    created = client.post(
        f"{API}/line-record-schemas",
        json={
            "name": "Transporte marítimo",
            "recommended_files": [
                {
                    "key": "terms_slip",
                    "label": "Slip de términos y condiciones",
                    "doc_type": "slip de términos y condiciones",
                    "format": "word",
                    "required": True,
                    "description": "Las bases técnicas del riesgo.",
                }
            ],
            "explanation": "Ramo de transporte marítimo.",
        },
        headers=headers_a,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["insurance_line_id"] is None
    assert body["recommended_files"][0]["label"] == "Slip de términos y condiciones"
    assert body["recommended_files"][0]["format"] == "word"
    assert body["explanation"] == "Ramo de transporte marítimo."
    # The deprecated definition is empty — a ramo carries no field schema now.
    assert body["definition"]["sections"] == []


# --- 9. Antecedentes scoping: EXCLUDE the cotización / insurer-quote docs -----


def test_antecedentes_excludes_cotizacion_documents(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """The antecedentes set is only what the INSURED communicated. Every insurer
    cotización (03 Southbridge/HDI/Mapfre) is excluded — it belongs to the
    comparison — even when misfiled under a submission section."""
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)

    # Two insurer quotations: one correctly under insurer_quotes, one MISFILED
    # under the submission section. Neither may enter the antecedentes set.
    for name, section in (
        ("03-southbridge.txt", CaseSection.INSURER_QUOTES),
        ("03-hdi.txt", CaseSection.SUBMISSION),
    ):
        path = tmp_path / name
        path.write_text("Cotización de la aseguradora, prima total UF 900. " * 5, encoding="utf-8")
        db.add(
            Document(
                broker_id=world.a.broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=case.id,
                case_file_id=case.id,
                section=section,
                s3_key=str(path),
                bucket="radal-test-bucket",
                original_name=name,
                mime_type="text/plain",
                category=DocumentCategory.INSURER_QUOTATION,
            )
        )
    db.commit()

    docs = ai_service._antecedentes_documents(db, case, world.a.broker_id)
    names = {d.original_name for d in docs}
    assert names == {"solicitud.txt", "slip.txt"}
    assert "03-southbridge.txt" not in names
    assert "03-hdi.txt" not in names

    # And the consolidation only reads the antecedentes documents.
    _fake_model(monkeypatch)
    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a
    )
    assert response.status_code == 200, response.text
    per_doc_names = {d["document_name"] for d in response.json()["document_extractions"]}
    assert per_doc_names == {"solicitud.txt", "slip.txt"}


def test_process_exposes_per_document_extractions(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """SUGGEST carries per-document extraction cards keyed by document, plus the
    consolidated payload — the "Extracción" subtab reads the former."""
    _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id)
    case = _account_with_docs(db, world.a, tmp_path)
    _fake_model(monkeypatch)

    response = client.post(
        f"{API}/case-files/{case.id}/antecedentes/process", headers=headers_a
    )
    assert response.status_code == 200, response.text
    body = response.json()
    per = body["document_extractions"]
    assert len(per) == 2  # one card per source document
    assert all(item["document_id"] for item in per)
    assert all(item["payload"] is not None for item in per)
    # The consolidated payload is derived from the per-document reads.
    assert body["payload"]["identification"]["razon_social"] == "Viña Santa Alicia S.A."

    # Re-reading persists the per-document cards (they live on the source extraction).
    reread = client.get(
        f"{API}/case-files/{case.id}/antecedentes", headers=headers_a
    ).json()
    assert {d["document_name"] for d in reread["document_extractions"]} == {
        "solicitud.txt",
        "slip.txt",
    }


# --- 10. Upload format policy: PDF/Word pass, Excel rejected ------------------


def test_antecedentes_upload_rejects_excel_accepts_pdf_and_word(
    client, world, headers_a, db, tmp_path, local_media
):
    """The antecedentes upload FORMAT must be PDF or Word. Excel is a 415 with an
    actionable message; PDF and Word pass."""
    case = make_case_file(db, world.a)
    db.commit()

    def _upload(filename, mime):
        return client.post(
            f"{API}/documents",
            files={"file": (filename, b"%PDF-1.4 or docx bytes, non-empty", mime)},
            data={
                "entity_type": EntityType.CASE_FILE.value,
                "entity_id": str(case.id),
                "category": DocumentCategory.INSURED_VALUES_SCHEDULE.value,
                "case_file_id": str(case.id),
                "section": CaseSection.SUBMISSION.value,
            },
            headers=headers_a,
        )

    excel = _upload(
        "montos.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert excel.status_code == 415, excel.text
    assert "Excel" in excel.json()["detail"]

    pdf = _upload("montos.pdf", "application/pdf")
    assert pdf.status_code == 201, pdf.text

    word = _upload(
        "slip.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert word.status_code == 201, word.text


def test_broker_schema_wins_over_global_seed(client, world, headers_a, db, tmp_path, ai_key, monkeypatch):
    # A global seed (broker_id NULL) and the broker's own row for the same ramo.
    _make_schema(db, broker_id=None, line_id=world.a.line.id, name="Seed", version=1)
    own = _make_schema(db, broker_id=world.a.broker_id, line_id=world.a.line.id, name="Own", version=1)

    resolved = ai_service.resolve_line_record_schema(
        db, insurance_line_id=world.a.line.id, broker_id=world.a.broker_id
    )
    assert resolved.id == own.id
    assert resolved.broker_id == world.a.broker_id
