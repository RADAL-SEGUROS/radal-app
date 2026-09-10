"""End-to-end walk of the FULL broker creation journey, hermetic to the AI
provider. This is the "execute the AI readers even without real AI" coverage:
the chat model is FAKED (a single mutable double whose JSON is swapped per
phase), so every reader — antecedentes consolidation, budget-proposal extraction
and policy extraction — runs its real service path, writes its real
``extraction`` row and returns its real review/verdict payload, without ever
reaching DeepInfra (the suite blanks ``AI_API_KEY``; we set a fake one and
monkeypatch ``ai_service._chat_model``).

The journey, on ONE account, in order:

    1. create an ``account_group`` WITH an emoji icon;
    2. attach a client RUT (group membership) → an account ``case_file`` mints
       the ``account_client`` rows;
    3. create the account ``case_file(kind=account)`` and assign a GLOBAL ramo
       template via ``POST /case-files/{id}/line``;
    4. run the antecedentes reader (faked) — asserts an ``extraction`` row and a
       ``review`` payload (warn-not-block: 200, never a 409);
    5. build a comparison: create → add a column via faked ``extract_budget_proposal``
       → align (deterministic slug-match fallback) → promote a column to a real
       inbound ``proposal``;
    6. mint a ``broker_proposal`` from the aligned comparison + winner, then ratify;
    7. upload a policy via ``POST /policies/upload`` — a missing-core file 422s
       ``not_a_policy``; a core-valid file commits and persists ``policy.payload``
       (including a dynamic remainder the typed columns never carry);
    8. assert ``GET /case-files/{id}/pending-actions`` reflects the progression.

Everything is broker-A scoped throughout.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.account_client import AccountClient
from app.models.ai import Extraction, ExtractionStatus
from app.models.broker_proposal import BrokerProposalStatus
from app.models.comparison import ComparisonStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseSection, EntityType
from app.models.line_record_schema import LineRecordSchema
from app.models.policy import Policy
from app.models.proposal import Proposal
from app.services import ai as ai_service
from tests.conftest import API


# --- A permissive, mutable fake chat model -----------------------------------
#
# It must serve THREE different readers in one test, so a single instance is
# monkeypatched in and its ``content`` is swapped between phases. Structured
# output reads the content LIVE (through ``_owner``), and — unlike the strict
# double in ``test_ai_streaming`` — it asserts nothing about ``method`` /
# ``include_raw``, so it works for antecedentes, budgets AND policies alike.

class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.response_metadata = {
            "model_name": "fake/model-e2e",
            "token_usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        self.usage_metadata = {"input_tokens": 5, "output_tokens": 7}


class _FakeStructured:
    def __init__(self, owner: "_FakeChatModel"):
        self._owner = owner

    def invoke(self, messages):
        return {"raw": _FakeMessage(self._owner.content), "parsed": None, "parsing_error": None}


class _FakeChatModel:
    def __init__(self, content: str = "{}"):
        self.content = content

    def with_structured_output(self, schema, method=None, include_raw=False):
        return _FakeStructured(self)

    def invoke(self, messages):
        return _FakeMessage(self.content)

    async def astream(self, messages):
        yield _FakeMessage(self.content)


# The three JSON payloads the faked reader returns, one per phase. ------------

_ANTECEDENTES = {
    "identification": {"razon_social": "Viña Journey S.A.", "giro": "Viñedos"},
    "values": {
        "total_uf": "UF 17.920",
        "partidas": [{"item": "Edificio", "monto_uf": "UF 10.000"}],
    },
    "confidence": 80,
}

_BUDGET_OFFER = {
    "document_type": "budget_proposal",
    "document_type_confidence": 95,
    "insurer_name": "HDI Seguros S.A.",
    "insurer_rut": "99301000-6",
    "insurer_cmf_code": "CMF-HDI-001",
    "taxable_premium_uf": 100,
    "exempt_premium_uf": 0,
    "net_premium_uf": 100,
    "vat_uf": 19,
    "total_premium_uf": 119,
    "facets": [
        {"key": "sismo", "group": "deductible", "label": "Sismo", "present": True},
        {"key": "incendio", "group": "coverage", "label": "Incendio", "present": True},
    ],
}

_POLICY_MISSING_CORE = {
    "policy_number": "POL-JOURNEY-BAD",
    "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"},
}

_POLICY_VALID = {
    "policy_number": "POL-JOURNEY-OK",
    "insurer": {"legal_name": "HDI Seguros S.A.", "rut": "99301000-6", "cmf_code": "CMF-HDI-001"},
    "broker": {"name": "Corredora Alfa SpA"},
    "period_start_at": "2026-01-01T12:00:00Z",
    "period_end_at": "2027-01-01T12:00:00Z",
    "total_insured_amount_uf": 17920,
    "premium": {"taxable_premium_uf": 100, "exempt_premium_uf": 20},
    # A dynamic remainder the typed money core never carries — proves the full
    # parse lands on ``policy.payload``, not just the promoted columns.
    "vehicles": [{"patente": "ABCD12", "marca": "Volvo"}],
}


# --- Small builders on an EXISTING (API-created) account case ----------------

def _add_antecedentes_docs(db, tenant, case_id, tmp_path):
    """Two antecedentes documents with real bytes, filed on ``case_id``."""
    for name, section, category, text in (
        ("solicitud.txt", CaseSection.ROOT_PROSPECT, DocumentCategory.PROSPECT_REQUEST,
         "Solicitud de seguro de la Viña Journey S.A., giro viñedos. " * 5),
        ("slip.txt", CaseSection.SUBMISSION, DocumentCategory.SUBMISSION_LETTER,
         "Slip: monto total asegurado UF 17.920, partida edificio UF 10.000. " * 5),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        db.add(
            Document(
                broker_id=tenant.broker_id,
                entity_type=EntityType.CASE_FILE,
                entity_id=case_id,
                case_file_id=case_id,
                section=section,
                s3_key=str(path),
                bucket="radal-test-bucket",
                original_name=name,
                mime_type="text/plain",
                category=category,
            )
        )
    db.commit()


def _readable_doc(db, tenant, case_id, tmp_path, *, name, category, text):
    """A readable document filed on the account case, returned refreshed."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    document = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case_id,
        case_file_id=case_id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=category,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


# --- The full journey --------------------------------------------------------

def test_full_broker_journey_hermetic(client, world, headers_a, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(_ANTECEDENTES, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)

    tenant = world.a

    # ---- 1. Group WITH an emoji icon; attach the client (membership) --------
    resp = client.post(
        f"{API}/account-groups",
        json={
            "name": "Grupo Journey",
            "icon": {"kind": "emoji", "value": "🍇"},
            "client_ids": [tenant.client.id],
        },
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    assert group["icon"]["kind"] == "emoji"
    assert group["icon"]["value"] == "🍇"
    group_id = group["id"]

    # ---- 3. The account case (kind=account) over the tenant's placement -----
    resp = client.post(
        f"{API}/case-files",
        json={
            "kind": "account",
            "placement_id": tenant.placement.id,
            "account_group_id": group_id,
        },
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()
    case_id = case["id"]
    assert case["kind"] == "account"

    # ---- 2 (cont.). account_client membership was minted for the contratante -
    members = db.scalars(
        select(AccountClient).where(AccountClient.case_file_id == case_id)
    ).all()
    assert any(m.client_id == tenant.client.id and m.is_primary for m in members)

    # Baseline pending-actions: no antecedentes record yet.
    before = client.get(
        f"{API}/case-files/{case_id}/pending-actions", headers=headers_a
    ).json()
    assert "antecedentes_missing" in {a["code"] for a in before["actions"]}

    # ---- 3 (cont.). Assign a GLOBAL ramo template via POST /line ------------
    template = LineRecordSchema(
        broker_id=None,
        insurance_line_id=tenant.line.id,
        name="Plantilla Global Journey",
        version=1,
        is_active=True,
        is_template=True,
        definition={
            "sections": [
                {
                    "key": "identification",
                    "label": "Identificación",
                    "fields": [
                        {"key": "razon_social", "label": "Razón social", "type": "text",
                         "required": True},
                        {"key": "giro", "label": "Giro", "type": "text"},
                    ],
                },
                {
                    "key": "values",
                    "label": "Montos",
                    "fields": [
                        {"key": "total_uf", "label": "Monto total", "type": "money_uf",
                         "required": True},
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
        },
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    resp = client.post(
        f"{API}/case-files/{case_id}/line",
        json={"line_record_schema_id": template.id},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["schema_id"] == template.id

    # ---- 4. The antecedentes reader (faked): review payload, NOT a 409 ------
    _add_antecedentes_docs(db, tenant, case_id, tmp_path)
    resp = client.post(
        f"{API}/case-files/{case_id}/antecedentes/process", headers=headers_a
    )
    assert resp.status_code == 200, resp.text  # warn-not-block, never 409
    ant = resp.json()
    assert ant["status"] == "review"
    assert ant["payload"]["identification"]["razon_social"] == "Viña Journey S.A."
    assert ant["extraction_id"]

    rows = db.scalars(
        select(Extraction).where(Extraction.case_file_id == case_id)
    ).all()
    assert len(rows) == 1, "exactly one extraction row per reader attempt"
    assert rows[0].status is ExtractionStatus.SUCCEEDED

    # ---- 5. Comparison: create → column → align → promote -------------------
    fake.content = json.dumps(_BUDGET_OFFER, ensure_ascii=False)

    # The budget read, the holistic comparison and the outbound propuesta all use
    # tool-calling; fake by tool name. The comparison MUST return non-empty
    # dimensions or the (v9) hard guard blocks the step.
    def _fake_tool_call(*, schema=None, tool_schema=None, **kwargs):
        name = (tool_schema or {}).get("name")
        if name == "submit_comparison":
            parsed = {
                "dimensions": [
                    {"key": "sismo", "group": "deductible", "label": "Sismo", "cells": []},
                    {"key": "incendio", "group": "coverage", "label": "Incendio", "cells": []},
                ],
                "recommendation": {"recommended_comparison_source_id": None,
                                   "rationale": "Mejor cobertura de sismo.", "caveats": []},
            }
        elif name == "recommend_comparison":
            parsed = {"recommendation": {"recommended_comparison_source_id": None,
                                          "rationale": "Mejor cobertura de sismo.", "caveats": []}}
        elif name == "submit_propuesta":
            parsed = {"core": {}, "additional": []}
        else:
            parsed = json.loads(fake.content)
        payload, warnings = (None, [])
        if schema is not None:
            payload, warnings = ai_service._coerce_payload(schema, dict(parsed))
        return ai_service.ToolCallResult(
            parsed=parsed,
            payload=payload,
            raw={"content": json.dumps(parsed, ensure_ascii=False), "parsed": parsed},
            usage={"prompt_tokens": 5, "completion_tokens": 7},
            reasoning=None,
            warnings=warnings,
            model="fake/model-e2e",
        )

    monkeypatch.setattr(ai_service, "tool_call", _fake_tool_call)

    resp = client.post(
        f"{API}/comparisons", json={"case_file_id": case_id}, headers=headers_a
    )
    assert resp.status_code == 201, resp.text
    comparison_id = resp.json()["id"]

    offer_doc = _readable_doc(
        db, tenant, case_id, tmp_path,
        name="oferta-hdi.txt", category=DocumentCategory.BUDGET_PROPOSAL,
        text="Cotización HDI: prima total UF 119, incendio y sismo. " * 6,
    )
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": offer_doc.id},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["is_wrong_file"] is False
    assert entry["document_type"] == "budget_proposal"
    entry_id = entry["entry"]["id"]

    # Align — deterministic slug-match fallback (no provider reached).
    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["comparison"]["status"] == ComparisonStatus.ALIGNED.value

    # Promote the column into a real inbound proposal.
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote",
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    proposal_id = resp.json()["proposal_id"]
    proposal = db.get(Proposal, proposal_id)
    assert proposal is not None
    assert str(proposal.total_premium_uf) == "119.0000"
    assert proposal.source_document_id == offer_doc.id

    # ---- 6. Mint the broker_proposal, then ratify ---------------------------
    resp = client.post(
        f"{API}/broker-proposals",
        json={
            "comparison_id": comparison_id,
            "winning_proposal_id": proposal_id,
            "winner_note": "Mejor cobertura de sismo por prima.",
        },
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    bp = resp.json()
    assert bp["status"] == BrokerProposalStatus.DRAFT.value
    bp_id = bp["id"]

    resp = client.post(
        f"{API}/broker-proposals/{bp_id}/ratify",
        json={"note": "Ratificado por el cliente."},
        headers=headers_a,
    )
    assert resp.status_code == 200, resp.text
    ratified = resp.json()
    assert ratified["is_ratified"] is True
    assert ratified["status"] == BrokerProposalStatus.RATIFIED.value

    # ---- 7. Policy upload: validate-then-dynamic ----------------------------
    # (a) A missing-core file is a visible 422 ``not_a_policy`` — nothing writes.
    fake.content = json.dumps(_POLICY_MISSING_CORE, ensure_ascii=False)
    bad_doc = _readable_doc(
        db, tenant, case_id, tmp_path,
        name="no-es-poliza.txt", category=DocumentCategory.POLICY,
        text="Correo de la aseguradora sin vigencia ni prima. " * 6,
    )
    resp = client.post(
        f"{API}/policies/upload",
        json={"document_id": bad_doc.id, "case_file_id": case_id},
        headers=headers_a,
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "not_a_policy"
    assert "vigencia" in detail["missing"] and "prima" in detail["missing"]
    assert db.scalars(
        select(Policy).where(Policy.policy_number == "POL-JOURNEY-BAD")
    ).first() is None

    # (b) A core-valid file commits and persists the FULL payload.
    fake.content = json.dumps(_POLICY_VALID, ensure_ascii=False)
    good_doc = _readable_doc(
        db, tenant, case_id, tmp_path,
        name="poliza.txt", category=DocumentCategory.POLICY,
        text="Póliza emitida por HDI, vigencia 2026-2027, prima afecta UF 100. " * 6,
    )
    resp = client.post(
        f"{API}/policies/upload",
        json={"document_id": good_doc.id, "case_file_id": case_id},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_core_valid"] is True
    assert body["overridden"] is False
    policy = body["policy"]
    assert policy["policy_number"] == "POL-JOURNEY-OK"
    assert policy["source_document_id"] == good_doc.id
    assert Decimal(policy["net_premium_uf"]) == Decimal("120.0000")
    assert Decimal(policy["vat_uf"]) == Decimal("19.0000")

    db.expire_all()
    row = db.get(Policy, policy["id"])
    assert row.is_core_valid is True
    assert row.payload["vehicles"][0]["marca"] == "Volvo"  # dynamic remainder persisted

    # ---- 8. Pending-actions reflect the progression -------------------------
    after = client.get(
        f"{API}/case-files/{case_id}/pending-actions", headers=headers_a
    ).json()
    assert after["case_file_id"] == case_id
    codes = {a["code"] for a in after["actions"]}
    # The antecedentes reader ran (no longer "missing"), and the comparison is
    # aligned (no longer "unaligned") — the journey moved forward.
    assert "antecedentes_missing" not in codes
    assert "comparison_unaligned" not in codes
    for action in after["actions"]:
        assert action["severity"] in {"info", "warning", "blocker"}
        assert action["tab"] and action["reason"]
