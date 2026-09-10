"""AI robustness: SSE streaming, the new loaders, and "never a 500".

The acceptance rule this file encodes (spec §12.8): an LLM outage — the simplest
being an unset ``AI_API_KEY`` — yields **503 with an AIError code** on every AI
endpoint and a **persisted failed extraction row**, never a 500 and never a
half-written entity.

The provider is faked throughout. These tests never reach DeepInfra.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.ai import AgentMessage, Extraction, ExtractionKind, ExtractionStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import EntityType
from app.services import ai as ai_service
from tests.conftest import API


# --- Provider doubles --------------------------------------------------------


class _FakeMessage:
    """What a LangChain chat model returns, reduced to what the service reads."""

    def __init__(self, content: str):
        self.content = content
        self.response_metadata = {"model_name": "fake/model-1", "token_usage": {}}
        self.usage_metadata = {"input_tokens": 11, "output_tokens": 7}


class _FakeStructured:
    def __init__(self, content: str, parsed=None, parsing_error=None):
        self._content = content
        self._parsed = parsed
        self._parsing_error = parsing_error

    def invoke(self, messages):
        assert isinstance(messages, list) and messages, "the prompt must not be empty"
        return {
            "raw": _FakeMessage(self._content),
            "parsed": self._parsed,
            "parsing_error": self._parsing_error,
        }


class _FakeChatModel:
    def __init__(self, *, content: str = "{}", parsed=None, chunks=None, fail_with=None):
        self._content = content
        self._parsed = parsed
        self._chunks = chunks or []
        self._fail_with = fail_with
        self.structured_calls: list[dict] = []

    def with_structured_output(self, schema, method=None, include_raw=False):
        self.structured_calls.append({"schema": schema, "method": method, "raw": include_raw})
        assert method == "json_mode", "DeepInfra has JSON mode, not strict tool-calling"
        assert include_raw is True, "the audit row needs the raw answer"
        return _FakeStructured(self._content, self._parsed)

    def invoke(self, messages):
        if self._fail_with is not None:
            raise self._fail_with
        return _FakeMessage(self._content)

    async def astream(self, messages):
        for chunk in self._chunks:
            yield _FakeMessage(chunk)
        if self._fail_with is not None:
            raise self._fail_with


# A fixed, non-empty standardized table the faked comparison returns, so the
# align step snapshots a real dictionary (v9: no deterministic fallback exists).
_FAKE_COMPARISON_DIMENSIONS = [
    {"key": "sismo", "group": "deductible", "label": "Sismo", "scope": "common", "cells": []},
    {"key": "incendio", "group": "coverage", "label": "Incendio", "scope": "common", "cells": []},
]
_FAKE_RECOMMENDATION = {
    "recommended_comparison_source_id": None,
    "recommended_proposal_id": None,
    "rationale": "Mejor cobertura de sismo por prima.",
    "caveats": [],
}


def tool_call_double(fake: "_FakeChatModel"):
    """A ``ai.tool_call`` double that dispatches by tool name (v9).

    One hermetic double serves every tool-calling path a comparison flow touches:
    the budget-proposal read (Phase 1), the holistic ``submit_comparison`` /
    ``recommend_comparison`` (Phase 2) and the outbound ``submit_propuesta``. It
    reads the offer JSON a ``_FakeChatModel`` carries for the budget path and
    returns fixed, well-formed shapes for the others — no live provider.
    """

    def _result(parsed, *, schema=None):
        payload = None
        warnings: list = []
        if schema is not None:
            payload, warnings = ai_service._coerce_payload(schema, dict(parsed))
        return ai_service.ToolCallResult(
            parsed=parsed,
            payload=payload,
            raw={"content": json.dumps(parsed, ensure_ascii=False), "parsed": parsed},
            usage={"prompt_tokens": 11, "completion_tokens": 7},
            reasoning=None,
            warnings=warnings,
            model="fake/model-1",
        )

    def _call(*, schema=None, tool_schema=None, **kwargs):
        name = (tool_schema or {}).get("name")
        if name == "submit_comparison":
            return _result(
                {"dimensions": _FAKE_COMPARISON_DIMENSIONS, "recommendation": _FAKE_RECOMMENDATION}
            )
        if name == "recommend_comparison":
            return _result({"recommendation": _FAKE_RECOMMENDATION})
        if name == "submit_propuesta":
            # Empty core => submit_propuesta falls back to the winner's authoritative
            # (already-reconciled) columns; a small free tail rides along.
            return _result(
                {"core": {}, "additional": [{"group": "coverage", "label": "Incendio"}]}
            )
        # Default: the budget-proposal read, from the fake's live content.
        return _result(json.loads(fake._content), schema=schema)

    return _call


@pytest.fixture()
def no_ai_key(monkeypatch):
    """The outage case: the feature is configured off."""
    monkeypatch.setattr(settings, "AI_API_KEY", "")


@pytest.fixture()
def ai_key(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")


def _readable_document(db, world, tmp_path, *, text: str, name: str = "cotizacion.txt"):
    """A document whose bytes really exist on disk, so the loader succeeds."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=DocumentCategory.INSURER_QUOTATION,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


# --- 1. An outage is a 503 with a code, plus an audit row --------------------


def test_document_extract_without_a_key_is_503_and_persists_a_failed_row(
    client, world, headers_a, db, tmp_path, no_ai_key
):
    document = _readable_document(
        db, world, tmp_path, text="Cotización de prueba con texto suficiente para el lector. " * 3
    )

    response = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": document.id},
        headers=headers_a,
    )

    assert response.status_code == 503
    assert response.headers["X-Radal-AI-Error"] == "ai_not_configured"

    db.expire_all()
    rows = db.scalars(select(Extraction).where(Extraction.document_id == document.id)).all()
    assert len(rows) == 1, "exactly one extraction row in EVERY outcome"
    assert rows[0].status == ExtractionStatus.FAILED
    assert "AINotConfigured" in (rows[0].error or "")
    assert rows[0].category is DocumentCategory.INSURER_QUOTATION
    assert rows[0].prompt_version == ai_service.PROMPT_VERSION


def test_legacy_proposal_extract_still_behaves_identically(
    client, world, headers_a, db, tmp_path, no_ai_key
):
    """/ai/proposals/extract is a thin wrapper — same status, same audit row."""
    document = _readable_document(
        db, world, tmp_path, text="Propuesta de seguro con texto suficiente para el lector. " * 3
    )

    response = client.post(
        f"{API}/ai/proposals/extract", json={"document_id": document.id}, headers=headers_a
    )

    assert response.status_code == 503
    db.expire_all()
    row = db.scalars(select(Extraction).where(Extraction.document_id == document.id)).one()
    assert row.status == ExtractionStatus.FAILED
    assert row.kind is ExtractionKind.PROPOSAL
    assert row.prompt_version == ai_service.PROMPT_VERSION


def test_proposal_summary_without_a_key_is_503_with_an_audit_row(
    client, world, headers_a, db, tmp_path, no_ai_key
):
    from app.models.proposal import Proposal, ProposalOrigin, ProposalStatus

    document = _readable_document(db, world, tmp_path, text="x" * 200, name="fuente.txt")
    proposal = Proposal(
        broker_id=world.a.broker_id,
        quote_request_id=world.a.quote.id,
        insurer_id=world.insurer.id,
        origin=ProposalOrigin.NATIVE,
        source_document_id=document.id,
        status=ProposalStatus.SUBMITTED,
        taxable_premium_uf=Decimal("100.0000"),
        exempt_premium_uf=Decimal("0.0000"),
        net_premium_uf=Decimal("100.0000"),
        vat_uf=Decimal("19.0000"),
        total_premium_uf=Decimal("119.0000"),
        is_confirmed=True,
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    response = client.post(f"{API}/ai/proposals/{proposal.id}/summary", headers=headers_a)

    assert response.status_code == 503
    assert response.headers["X-Radal-AI-Error"] == "ai_not_configured"
    db.expire_all()
    row = db.scalars(
        select(Extraction).where(Extraction.kind == ExtractionKind.SUMMARY)
    ).one()
    assert row.status == ExtractionStatus.FAILED
    assert db.get(type(proposal), proposal.id).ai_summary is None


def test_stream_without_a_key_is_503_before_the_stream_opens(
    client, world, headers_a, no_ai_key
):
    created = client.post(f"{API}/ai/threads", json={"scope": "general"}, headers=headers_a)
    thread_id = created.json()["id"]

    response = client.post(
        f"{API}/ai/threads/{thread_id}/stream", json={"content": "hola"}, headers=headers_a
    )

    assert response.status_code == 503
    assert response.headers["X-Radal-AI-Error"] == "ai_not_configured"


def test_an_unreadable_document_is_422_and_writes_no_row(
    client, world, headers_a, db, ai_key
):
    """The fixture document's bytes do not exist — no OCR this pass, no row."""
    response = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": world.a.document.id},
        headers=headers_a,
    )
    assert response.status_code == 422
    assert response.headers["X-Radal-AI-Error"] == "document_unavailable"
    assert db.scalars(select(Extraction)).all() == []


def test_another_tenants_document_is_404(client, world, headers_a, ai_key):
    response = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": world.b.document.id},
        headers=headers_a,
    )
    assert response.status_code == 404


def test_an_unsupported_category_is_422(client, world, headers_a, db, tmp_path, ai_key):
    document = _readable_document(db, world, tmp_path, text="texto suficiente " * 10)
    response = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": document.id, "category": "submission_pack"},
        headers=headers_a,
    )
    assert response.status_code == 422
    assert response.headers["X-Radal-AI-Error"] == "unsupported_category"


# --- 2. A successful registry-driven extraction ------------------------------


def test_extract_document_persists_one_row_and_suggests(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    answer = json.dumps(
        {
            "quotation_number": "COT-9",
            "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6",
                        "cmf_code": "CMF-HDI-001"},
            "net_premium_taxable_uf": "UF 100",
            "net_premium_exempt_uf": "UF 20",
            "gross_premium_uf": "UF 139",
            "deductibles": [{"peril": "fire", "basis": "loss", "pct": 5}],
            "coverages": [{"number": "1", "name": "Incendio"}],
            "confidence": 82,
        },
        ensure_ascii=False,
    )
    fake = _FakeChatModel(content=f"```json\n{answer}\n```")  # fences on purpose
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)

    document = _readable_document(db, world, tmp_path, text="Cotización HDI. " * 20)
    response = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": document.id, "category": "insurer_quotation"},
        headers=headers_a,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["canonical_category"] == "insurer_quotation"
    assert body["schema_name"] == "InsurerQuotationExtraction"
    assert body["payload"]["quotation_number"] == "COT-9"
    assert body["payload"]["taxable_premium_uf"] == "100"
    # The legacy contract survives for this category.
    assert body["suggestion"]["insurer"]["cmf_code"] == "CMF-HDI-001"
    assert body["extraction"]["status"] == "succeeded"
    assert body["extraction"]["confidence"] == "82.000"

    db.expire_all()
    rows = db.scalars(select(Extraction)).all()
    assert len(rows) == 1
    assert rows[0].parsed["insurer"]["rut"] == "99301000-6"
    # SUGGEST never commits: no proposal was created.
    from app.models.proposal import Proposal

    assert db.scalars(select(Proposal)).all() == []


def test_a_provider_exception_never_becomes_a_500(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    class _Boom(Exception):
        pass

    class _Exploding(_FakeChatModel):
        def with_structured_output(self, schema, method=None, include_raw=False):
            raise _Boom("upstream on fire")

    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: _Exploding())
    document = _readable_document(db, world, tmp_path, text="Cotización HDI. " * 20)

    response = client.post(
        f"{API}/ai/documents/extract", json={"document_id": document.id}, headers=headers_a
    )

    assert response.status_code == 502
    assert response.headers["X-Radal-AI-Error"] == "ai_provider"
    db.expire_all()
    assert db.scalars(select(Extraction)).one().status == ExtractionStatus.FAILED


def test_unparsable_prose_is_422_with_the_raw_answer_kept(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    fake = _FakeChatModel(content="Lo siento, no puedo leer este documento.")
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    document = _readable_document(db, world, tmp_path, text="Cotización HDI. " * 20)

    response = client.post(
        f"{API}/ai/documents/extract", json={"document_id": document.id}, headers=headers_a
    )

    assert response.status_code == 422
    assert response.headers["X-Radal-AI-Error"] == "ai_parse"
    db.expire_all()
    row = db.scalars(select(Extraction)).one()
    assert row.status == ExtractionStatus.FAILED


def test_confirming_a_policy_payload_commits_the_policy(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """Spec §6.6: confirm COMMITS the payload to its prefill target.

    A ``policy`` (08) extraction, once human-confirmed, writes the policy row —
    idempotently (a second confirm updates, never duplicates).
    """
    from tests.conftest import make_case_file
    from app.models.policy import Policy

    case = make_case_file(db, world.a)
    db.commit()

    fake = _FakeChatModel(content=json.dumps({"policy_number": "0020119904"}))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    document = _readable_document(db, world, tmp_path, text="Póliza emitida. " * 20)
    document.case_file_id = case.id
    db.commit()

    extracted = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": document.id, "category": "policy"},
        headers=headers_a,
    ).json()
    extraction_id = extracted["extraction"]["id"]

    reviewed = {
        "policy_number": "0020119904-CORREGIDO",
        "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6",
                    "cmf_code": "CMF-HDI-001"},
        "total_insured_amount_uf": "UF 17.920",
        # v8: the POLICY commit now runs the fixed-core gate — a vigencia is part
        # of the minimal core, so the confirmed payload must carry it.
        "period_start_at": "2026-01-01T12:00:00Z",
        "period_end_at": "2027-01-01T12:00:00Z",
        "premium": {"taxable_premium_uf": "UF 100", "exempt_premium_uf": "UF 20"},
    }
    response = client.post(
        f"{API}/ai/documents/{extraction_id}/confirm",
        json={"payload": reviewed},
        headers=headers_a,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is True  # the dispatcher committed the target entity
    assert body["informational"] is False
    assert body["commit"]["entity_type"] == "policy"
    assert body["payload"]["policy_number"] == "0020119904-CORREGIDO"
    assert body["payload"]["total_insured_amount_uf"] == "17920"
    assert "policy" in body["prefill_target"]

    db.expire_all()
    row = db.get(Extraction, extraction_id)
    assert row.parsed["policy_number"] == "0020119904-CORREGIDO"
    assert row.raw_output["confirmation"]["applied"] is True

    policy = db.get(Policy, body["commit"]["entity_id"])
    assert policy.policy_number == "0020119904-CORREGIDO"
    assert policy.source_document_id == document.id
    # net = taxable + exempt; vat = 0.19 x taxable; total = net + vat
    assert policy.net_premium_uf == Decimal("120.0000")
    assert policy.vat_uf == Decimal("19.0000")
    assert policy.total_premium_uf == Decimal("139.0000")

    # Idempotency: confirming again UPDATES the same policy, never a second one.
    again = client.post(
        f"{API}/ai/documents/{extraction_id}/confirm",
        json={"payload": reviewed},
        headers=headers_a,
    )
    assert again.status_code == 200, again.text
    assert again.json()["commit"]["entity_id"] == policy.id
    db.expire_all()
    count = db.scalars(
        select(Policy).where(Policy.policy_number == "0020119904-CORREGIDO")
    ).all()
    assert len(count) == 1


def test_confirming_a_quotation_payload_creates_the_proposal(
    client, world, headers_a, db, tmp_path, ai_key, monkeypatch
):
    """The one category whose confirm really does commit an entity."""
    answer = json.dumps(
        {
            "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6",
                        "cmf_code": "CMF-HDI-001"},
            "net_premium_taxable_uf": 100,
        }
    )
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: _FakeChatModel(content=answer))
    document = _readable_document(db, world, tmp_path, text="Cotización HDI. " * 20)

    extraction_id = client.post(
        f"{API}/ai/documents/extract",
        json={"document_id": document.id, "category": "insurer_quotation"},
        headers=headers_a,
    ).json()["extraction"]["id"]

    reviewed = {
        "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6",
                     "cmf_code": "CMF-HDI-001"},
        "net_premium_taxable_uf": "UF 100",
        "net_premium_exempt_uf": "UF 20",
        "deductibles": [{"peril": "fire", "basis": "loss", "pct": 5}],
    }
    response = client.post(
        f"{API}/ai/documents/{extraction_id}/confirm",
        json={"payload": reviewed, "quote_request_id": world.a.quote.id},
        headers=headers_a,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is True
    proposal = body["proposal"]
    assert proposal["insurer_cmf_code"] == "CMF-HDI-001"
    # net = taxable + exempt; vat = 0.19 x taxable; total = net + vat
    assert Decimal(proposal["net_premium_uf"]) == Decimal("120.0000")
    assert Decimal(proposal["vat_uf"]) == Decimal("19.0000")
    assert Decimal(proposal["total_premium_uf"]) == Decimal("139.0000")
    assert proposal["deductibles"]["fire"]["pct"] == "5"

    # A second confirm of the same extraction is a conflict, not a duplicate.
    again = client.post(
        f"{API}/ai/documents/{extraction_id}/confirm",
        json={"payload": reviewed, "quote_request_id": world.a.quote.id},
        headers=headers_a,
    )
    assert again.status_code == 409


# --- 3. Streaming ------------------------------------------------------------


def _open_thread(client, headers) -> int:
    created = client.post(f"{API}/ai/threads", json={"scope": "general"}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _frames(body: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event is not None:
            parsed.append((event, data or {}))
    return parsed


def test_stream_emits_start_tokens_done_and_persists_both_messages(
    client, world, headers_a, db, ai_key, monkeypatch
):
    fake = _FakeChatModel(chunks=["El deducible ", "de incendio ", "es 5%."])
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    thread_id = _open_thread(client, headers_a)

    response = client.post(
        f"{API}/ai/threads/{thread_id}/stream",
        json={"content": "¿cuál es el deducible?"},
        headers=headers_a,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    frames = _frames(response.text)
    assert frames[0][0] == "start"
    assert frames[0][1]["thread_id"] == thread_id
    tokens = [data["delta"] for event, data in frames if event == "token"]
    assert "".join(tokens) == "El deducible de incendio es 5%."
    assert frames[-1][0] == "done"
    assert frames[-1][1]["message_id"]

    db.expire_all()
    messages = db.scalars(
        select(AgentMessage).where(AgentMessage.thread_id == thread_id).order_by(AgentMessage.id)
    ).all()
    assert [m.role.value for m in messages] == ["user", "assistant"]
    assert messages[1].content == "El deducible de incendio es 5%."


def test_a_mid_stream_failure_is_an_error_frame_and_writes_nothing(
    client, world, headers_a, db, ai_key, monkeypatch
):
    fake = _FakeChatModel(chunks=["El deducible "], fail_with=RuntimeError("socket reset"))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    thread_id = _open_thread(client, headers_a)

    response = client.post(
        f"{API}/ai/threads/{thread_id}/stream", json={"content": "hola"}, headers=headers_a
    )

    # The 200 is already committed by the time the provider dies: an SSE error
    # frame is the only correct answer, never an HTTP 500.
    assert response.status_code == 200
    frames = _frames(response.text)
    events = [event for event, _ in frames]
    assert events[0] == "start" and events[-1] == "error"
    assert frames[-1][1]["code"] == "ai_provider"

    db.expire_all()
    assert (
        db.scalars(select(AgentMessage).where(AgentMessage.thread_id == thread_id)).all() == []
    )


def test_stream_refuses_another_users_thread(client, world, headers_a, headers_b, ai_key):
    thread_id = _open_thread(client, headers_a)
    response = client.post(
        f"{API}/ai/threads/{thread_id}/stream", json={"content": "hola"}, headers=headers_b
    )
    assert response.status_code == 404  # another tenant never learns it exists


# --- 4. Document loaders -----------------------------------------------------


def test_docx_loader_flattens_headings_and_tables(db, world, tmp_path):
    from docx import Document as DocxDocument

    docx = DocxDocument()
    docx.add_heading("Bases Técnicas", level=1)
    docx.add_paragraph("Vigencia desde las 12:00 horas del 01-01-2026.")
    table = docx.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Partida"
    table.cell(0, 1).text = "Monto UF"
    table.cell(1, 0).text = "Edificio"
    table.cell(1, 1).text = "17.920"
    path = tmp_path / "bases.docx"
    docx.save(path)

    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name="bases.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        category=DocumentCategory.TECHNICAL_BRIEF,
    )

    text = ai_service.load_document_text(document)
    assert "## Bases Técnicas" in text
    assert "Partida\tMonto UF" in text
    assert "Edificio\t17.920" in text


def test_xlsx_loader_emits_sheet_headers_and_tsv(db, world, tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Montos"
    sheet.append(["Partida", "Monto UF"])
    sheet.append([])  # a blank spacer row, collapsed by the loader
    sheet.append(["Edificio", 17920])
    path = tmp_path / "montos.xlsx"
    workbook.save(path)

    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name="montos.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        category=DocumentCategory.INSURED_VALUES_SCHEDULE,
    )

    text = ai_service.load_document_text(document)
    assert "### Montos" in text
    assert "Partida\tMonto UF" in text
    assert "Edificio\t17920" in text


def test_a_scanned_file_is_an_explicit_failure(db, world, tmp_path):
    """No OCR this pass: an image-only file fails visibly instead of silently."""
    path = tmp_path / "escaneo.txt"
    path.write_text("   ", encoding="utf-8")
    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name="escaneo.txt",
        mime_type="text/plain",
        category=DocumentCategory.POLICY,
    )
    with pytest.raises(ai_service.DocumentUnavailable) as excinfo:
        ai_service.load_document_text(document)
    assert "OCR" in str(excinfo.value)
