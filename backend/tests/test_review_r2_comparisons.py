"""Review round 2 — Comparación → Propuesta (docs/v12/12-review-r2-fixes.md §A).

Hermetic: every provider call is faked at the ``ai.tool_call`` seam (conftest blanks the
key and points the provider at an unroutable port). Covers:

* A1 — the money core comes back COMPLETE (every key required + nullable; one re-ask of
  a sparse core) and an alignment that raced a new column never claims ``aligned``;
* A2 — a failed read is a VISIBLE failed column with *Reintentar*, skipped by ``align``;
* A3 — the recommendation speaks in insurer names and prima total, never "columna N";
* A4 — exclusion presence, numeric values + unit, one row per concept, no deductible
  inside a coverage row;
* A5 — promote creates the placement on demand and takes the catalogue ``insurer_id``;
  every error is structured Spanish;
* A6 — a read cotización moves the account to *Cotizaciones recibidas*, an alignment to
  *Comparación*, and the comparison guard counts the comparator's columns.
"""
from __future__ import annotations

import json
import threading

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.ai import Extraction
from app.models.case_file import CaseFile, CaseFileStageEvent
from app.models.comparison import Comparison, ComparisonEntry, ComparisonSource, ComparisonStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseStage, EntityType
from app.models.insurer import Insurer
from app.models.placement import Placement, PlacementStatus
from app.models.proposal import Proposal
from app.services import ai as ai_service
from app.services import budget_read
from app.services import case_files as machine
from app.services import comparison_standardize as standardize
from tests.conftest import API, make_case_file

# --- Fakes ------------------------------------------------------------------------

_FULL_CORE = {
    "document_type": "budget_proposal",
    "document_type_confidence": 98,
    "insurer_name": "HDI Seguros S.A.",
    "insurer_rut": None,
    "insurer_cmf_code": None,
    "taxable_premium_uf": 100,
    "exempt_premium_uf": 50,
    "net_premium_uf": 150,
    "vat_uf": 19,
    "total_premium_uf": 169,
    "commission_pct": 10,
}

#: What GLM-5.3-Flash really returned on 2026-10-19 for all three corpus offers.
_SPARSE_CORE = {
    "document_type": "budget_proposal",
    "document_type_confidence": 98,
    "exempt_premium_uf": 658.7,
    "exempt_rate_permille": 2.21,
    "commission_pct": 10,
}

_FACETS = [
    {"group": "coverage", "label": "Incendio", "verbatim": "Incendio y adicionales", "present": True},
    {"group": "exclusion", "label": "Terrorismo", "verbatim": "Se excluye terrorismo", "present": False},
]


def _result(parsed, *, schema=None):
    payload, warnings = (None, [])
    if schema is not None:
        payload, warnings = ai_service._coerce_payload(schema, dict(parsed))
    return ai_service.ToolCallResult(
        parsed=parsed,
        payload=payload,
        raw={"content": json.dumps(parsed, ensure_ascii=False), "parsed": parsed},
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        reasoning=None,
        warnings=warnings,
        model="fake/model",
        finish_reason="tool_calls",
    )


class _Provider:
    """One fake provider for the read + standardization; records every call."""

    def __init__(self, cores=None, *, fail_core=None, on_submit=None):
        self.cores = list(cores or [_FULL_CORE])
        self.fail_core = fail_core
        self.on_submit = on_submit
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, *, tool_schema=None, schema=None, messages=None, **kwargs):
        name = tool_schema["name"]
        with self._lock:
            self.calls.append({"name": name, "messages": messages, "tool_schema": tool_schema})
            core_index = sum(1 for c in self.calls if c["name"] == "record_budget_core") - 1
        if name == "record_budget_core":
            if self.fail_core is not None:
                raise self.fail_core
            return _result(dict(self.cores[min(core_index, len(self.cores) - 1)]), schema=schema)
        if name == "record_budget_facets":
            return _result({"facets": [dict(f) for f in _FACETS]}, schema=schema)
        if name == "submit_comparison":
            if self.on_submit is not None:
                self.on_submit()
            blob = json.loads(messages[1]["content"])
            dims = []
            for reading in blob["readings"]:
                for facet in reading["facets"]:
                    dims.append(
                        {
                            "key": facet["label"].lower(),
                            "group": facet["group"],
                            "label": facet["label"],
                            "cells": [{"comparison_source_id": reading["comparison_source_id"], "facet_n": facet["n"]}],
                        }
                    )
            return _result({"dimensions": dims})
        if name == "recommend_comparison":
            blob = json.loads(messages[1]["content"])
            first = blob["money"][0]["comparison_source_id"]
            return _result(
                {
                    "recommendation": {
                        "recommended_comparison_source_id": first,
                        "rationale": f"Recomiendo la columna {first}: menor prima total.",
                        "caveats": [f"La columna {first} exige inspección."],
                    }
                }
            )
        raise AssertionError(f"unexpected tool {name}")

    def count(self, name):
        return sum(1 for c in self.calls if c["name"] == name)


@pytest.fixture()
def provider(monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _Provider()
    monkeypatch.setattr(ai_service, "tool_call", fake)
    return fake


def _document(db, world, tmp_path, name="cotizacion.txt", broker=None):
    tenant = broker or world.a
    path = tmp_path / name
    path.write_text("Cotización Todo Riesgo. Prima bruta total UF 169. " * 10, encoding="utf-8")
    document = Document(
        broker_id=tenant.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=tenant.quote.id,
        s3_key=str(path),
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="text/plain",
        category=DocumentCategory.BUDGET_PROPOSAL,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def _open(client, headers, case_id):
    resp = client.post(f"{API}/comparisons", json={"case_file_id": case_id}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add(client, headers, comparison_id, document_id):
    return client.post(
        f"{API}/comparisons/{comparison_id}/entries", json={"document_id": document_id}, headers=headers
    )


@pytest.fixture()
def account_case(db, world):
    case = make_case_file(db, world.a, stage=CaseStage.COMPARISON)
    db.commit()
    db.refresh(case)
    return case


# --- A1 · the money core ------------------------------------------------------------


def test_money_core_tool_requires_every_key_and_allows_null():
    schema = budget_read.money_core_tool_schema()
    props = schema["parameters"]["properties"]
    assert set(schema["parameters"]["required"]) == set(props)
    assert "insurer_name" in props and "total_premium_uf" in props
    assert props["document_type"]["type"] == "string"  # the verdict is never null
    for key, prop in props.items():
        if key != "document_type":
            assert "null" in prop["type"], key
    assert "null" in budget_read._CORE_INSTRUCTION  # "the value, or null"


def test_core_is_incomplete_rule():
    assert budget_read.core_is_incomplete(_SPARSE_CORE)
    assert not budget_read.core_is_incomplete(_FULL_CORE)
    assert not budget_read.core_is_incomplete({"document_type": "not_a_proposal"})
    assert budget_read.core_is_incomplete({**_FULL_CORE, "insurer_name": None})
    assert not budget_read.core_is_incomplete({**_FULL_CORE, "total_premium_uf": None})  # net is enough


def test_sparse_core_is_reasked_once_and_the_column_shows_insurer_and_total(
    client, headers_a, world, db, account_case, tmp_path, provider
):
    provider.cores = [_SPARSE_CORE, _FULL_CORE]
    comparison_id = _open(client, headers_a, account_case.id)
    resp = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id)
    assert resp.status_code == 201, resp.text
    assert provider.count("record_budget_core") == 2
    repair = [c for c in provider.calls if c["name"] == "record_budget_core"][1]
    assert "omitió" in repair["messages"][1]["content"]

    entry = resp.json()["entry"]
    assert entry["status"] == "ok"
    assert float(entry["premium"]["total_premium_uf"]) == 169
    assert entry["insurer_name"] == "HDI"  # short display name
    assert entry["insurer_read_name"] == "HDI Seguros S.A."
    assert entry["missing_core"] == []
    extraction = db.get(Extraction, resp.json()["extraction_id"])
    assert extraction.raw_output["groups"]["money_core"]["repaired"] is True
    assert extraction.parsed["total_premium_uf"] is not None


def test_complete_core_is_not_reasked(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).status_code == 201
    assert provider.count("record_budget_core") == 1


def test_an_alignment_that_raced_a_new_column_stays_draft(
    client, headers_a, world, db, account_case, tmp_path, provider
):
    """Dev /comparisons/26: Mapfre's column was committed while the HDI-triggered align
    was still waiting on the provider; the align then marked the comparison ``aligned``
    over two columns and Mapfre read "No indicada" everywhere."""
    comparison_id = _open(client, headers_a, account_case.id)
    assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path, "a.txt").id).status_code == 201
    late_doc = _document(db, world, tmp_path, "late.txt")
    late_extraction = db.scalars(select(Extraction).order_by(Extraction.id.desc())).first()

    def _concurrent_upload():
        other = SessionLocal()
        try:
            source = ComparisonSource(
                broker_id=world.a.broker_id,
                source_extraction_id=late_extraction.id,
                facets=[dict(f) for f in _FACETS],
            )
            other.add(source)
            other.flush()
            other.add(ComparisonEntry(comparison_id=comparison_id, comparison_source_id=source.id, sort_order=9))
            other.commit()
        finally:
            other.close()

    provider.on_submit = _concurrent_upload
    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["comparison"]["status"] == ComparisonStatus.DRAFT.value
    assert any("Realinea" in w for w in body["warnings"])
    entries = body["comparison"]["entries"]
    assert [e["aligned"] for e in entries] == [True, False]
    assert late_doc.id  # the late document exists; its column waits for a re-align


def test_entries_get_increasing_sort_order(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    for name in ("a.txt", "b.txt", "c.txt"):
        assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path, name).id).status_code == 201
    entries = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"]
    assert [e["sort_order"] for e in entries] == [0, 1, 2]


# --- A2 · a failed read is a visible failed column ----------------------------------


def test_failed_read_is_a_visible_failed_column_and_retry_fills_it(
    client, headers_a, world, db, account_case, tmp_path, provider
):
    comparison_id = _open(client, headers_a, account_case.id)
    document = _document(db, world, tmp_path, "mapfre.txt")
    provider.fail_core = ai_service.AITimeout("The AI provider timed out after 90s")
    resp = _add(client, headers_a, comparison_id, document.id)
    assert resp.status_code == 504
    assert resp.json()["detail"]["code"] == "ai_timeout"

    comparison = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()
    [entry] = comparison["entries"]
    assert entry["status"] == "failed"
    assert entry["error_code"] == "ai_timeout"
    assert entry["error_message"].startswith("La lectura tardó")
    assert entry["document_id"] == document.id
    assert entry["premium"] is None

    # Nothing usable to align: a Spanish structured 422, no provider call.
    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "no_columns"
    assert resp.json()["detail"]["reason"] == resp.json()["detail"]["message"]
    assert provider.count("submit_comparison") == 0

    # Reintentar re-reads INTO THE SAME column.
    provider.fail_core = None
    resp = client.post(f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/retry", headers=headers_a)
    assert resp.status_code == 200, resp.text
    retried = resp.json()["entry"]
    assert retried["id"] == entry["id"] and retried["status"] == "ok"
    assert float(retried["premium"]["total_premium_uf"]) == 169
    entries = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"]
    assert len(entries) == 1


def test_reposting_the_failed_document_retries_its_column(
    client, headers_a, world, db, account_case, tmp_path, provider
):
    comparison_id = _open(client, headers_a, account_case.id)
    document = _document(db, world, tmp_path)
    provider.fail_core = ai_service.AIProviderError("The AI provider returned 503")
    assert _add(client, headers_a, comparison_id, document.id).status_code == 502
    provider.fail_core = None
    resp = _add(client, headers_a, comparison_id, document.id)
    assert resp.status_code == 201
    entries = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"]
    assert len(entries) == 1 and entries[0]["status"] == "ok"


def test_align_skips_failed_columns(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path, "ok.txt").id).status_code == 201
    provider.fail_core = ai_service.AITimeout("slow")
    assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path, "ko.txt").id).status_code == 504
    provider.fail_core = None

    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    submit = next(c for c in provider.calls if c["name"] == "submit_comparison")
    readings = json.loads(submit["messages"][1]["content"])["readings"]
    assert len(readings) == 1
    columns = resp.json()["comparison"]["aligned_matrix"]["columns"]
    assert len(columns) == 1
    statuses = [e["status"] for e in resp.json()["comparison"]["entries"]]
    assert statuses == ["ok", "failed"]


def test_retry_is_tenant_scoped(client, headers_a, headers_b, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    entry_id = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).json()["entry"]["id"]
    resp = client.post(f"{API}/comparisons/{comparison_id}/entries/{entry_id}/retry", headers=headers_b)
    assert resp.status_code == 404


# --- A3 · the recommendation speaks in insurer names --------------------------------


def test_recommend_prompt_names_insurers_and_compares_total_premium():
    readings = [
        {"comparison_source_id": 4, "insurer": "Southbridge", "money_core": {"total_premium_uf": 1181.28}},
        {"comparison_source_id": 6, "insurer": None, "money_core": {"insurer_name": "MAPFRE COMPAÑÍA DE SEGUROS GENERALES DE CHILE S.A.", "total_premium_uf": 1077.68}},
    ]
    messages = ai_service._recommend_messages([], readings)
    system = messages[0]["content"]
    assert "4 = Southbridge" in system and "6 = Mapfre" in system
    assert "PRIMA TOTAL" in system and "columna" in system  # forbids "columna"
    money = json.loads(messages[1]["content"])["money"]
    assert money[1]["insurer"] == "Mapfre" and money[1]["total_premium_uf"] == 1077.68
    submit_system = ai_service._submit_comparison_messages(readings, {"dictionary": []})[0]["content"]
    assert "PRIMA TOTAL" in submit_system and "COMPAÑÍA" in submit_system


def test_column_references_become_insurer_names():
    rec = standardize.humanize_recommendation(
        {
            "recommended_comparison_source_id": 4,
            "rationale": "Recomiendo la columna 4: su prima es menor que la de la columna 5. Folio N° 2025000418877.",
            "caveats": ["La columna 4 exige inspección.", "comparison_source_id 6 no informa IVA."],
        },
        {4: "Southbridge", 5: "HDI", 6: "Mapfre"},
    )
    assert rec["rationale"] == (
        "Recomiendo Southbridge: su prima es menor que la de HDI. Folio N° 2025000418877."
    )
    assert rec["caveats"] == ["Southbridge exige inspección.", "Mapfre no informa IVA."]


def test_insurer_short_names():
    assert standardize.insurer_short_name("Southbridge Compañía de Seguros Generales S.A.") == "Southbridge"
    assert standardize.insurer_short_name("HDI Seguros S.A.") == "HDI"
    assert standardize.insurer_short_name("MAPFRE COMPAÑÍA DE SEGUROS GENERALES DE CHILE S.A.") == "Mapfre"
    assert standardize.insurer_short_name(None) is None


def test_aligned_recommendation_names_the_insurer(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).status_code == 201
    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    rec = resp.json()["recommendation"]
    assert rec["rationale"] == "Recomiendo HDI: menor prima total."
    assert rec["caveats"] == ["HDI exige inspección."]


# --- A4 · standardization quality ---------------------------------------------------


def _reading(sid, facets):
    return ai_service.build_reading(
        comparison_source_id=sid, proposal_id=None, is_wrong_file=False, wrong_file_reason=None,
        money_core={}, facets=facets,
    )


def test_exclusion_cells_mean_the_offer_contains_the_exclusion():
    readings = [
        _reading(1, [{"group": "exclusion", "label": "Terrorismo", "verbatim": "Se excluye", "present": False}]),
        _reading(2, [{"group": "exclusion", "label": "Terrorismo", "verbatim": "Excluido", "present": True}]),
    ]
    dims = [{"key": "terrorismo", "group": "exclusion", "label": "Terrorismo",
             "cells": [{"comparison_source_id": 1, "facet_n": 1}, {"comparison_source_id": 2, "facet_n": 1}]}]
    out = standardize.finalize_dimensions(standardize.fill_cells(dims, readings), readings)
    assert [c["present"] for c in out[0]["cells"]] == [True, True]
    assert all("_facet_group" not in c for c in out[0]["cells"])
    # ... and the read normalises it at the source too.
    assert budget_read.normalize_exclusion_presence(
        [{"group": "exclusion", "present": False}, {"group": "coverage", "present": False}]
    ) == [{"group": "exclusion", "present": True}, {"group": "coverage", "present": False}]


@pytest.mark.parametrize(
    ("value", "verbatim", "group", "expected_value", "expected_unit"),
    [
        ("475256", "Límite", "sublimit", 475256, "uf"),
        ("UF 15.000", "x", "coverage", 15000, "uf"),
        ("25%", "x", "clause", 25, "pct"),
        ("57,03", "Prima UF 57,03", "other", 57.03, "uf"),
        # Review r3 (R2): a composite verbatim ("10% … máx UF 20.000") is the display value;
        # the bare number would drop the cap.
        (20000, "10% máx UF 20.000", "sublimit", "10% máx UF 20.000", None),
        (10, "10% máx UF 20.000", "coverage", "10% máx UF 20.000", None),
        # A deductible always shows its condition.
        ("7 días", "Carencia 7 días", "deductible", "Carencia 7 días", None),
        ("10% máx UF 20.000", "x", "sublimit", "10% máx UF 20.000", None),  # composite stays text
        (15, "Validez: 15 días corridos", "other", 15, "days"),
    ],
)
def test_cell_values_are_numbers_with_a_unit(value, verbatim, group, expected_value, expected_unit):
    out = standardize.normalize_cell_value({"value": value, "verbatim": verbatim}, group)
    assert out["value"] == expected_value
    assert out.get("unit") == expected_unit


def test_one_concept_filed_in_two_groups_is_one_row():
    dims = [
        {"key": "rehabilitacion-automatica", "group": "coverage", "label": "Rehabilitación automática",
         "scope": "extra", "cells": [{"comparison_source_id": 1, "verbatim": "a"}]},
        {"key": "clausula-de-rehabilitacion-automatica-de-suma-asegurada", "group": "clause",
         "label": "Cláusula de rehabilitación automática de suma asegurada", "scope": "extra",
         "cells": [{"comparison_source_id": 2, "verbatim": "b"}]},
        # Same concept but BOTH columns already present -> two real items, kept apart.
        {"key": "sismo", "group": "coverage", "label": "Sismo", "cells": [{"comparison_source_id": 1}, {"comparison_source_id": 2}]},
        {"key": "sismo-2", "group": "coverage", "label": "Sismo", "cells": [{"comparison_source_id": 1}]},
        # A deductible never folds into a coverage row.
        {"key": "rehabilitacion-automatica-deducible", "group": "deductible",
         "label": "Rehabilitación automática", "cells": [{"comparison_source_id": 3}]},
    ]
    out = standardize.dedupe_dimensions(dims, column_count=2)
    keys = [d["key"] for d in out]
    assert keys == ["rehabilitacion-automatica", "sismo", "sismo-2", "rehabilitacion-automatica-deducible"]
    assert [c["comparison_source_id"] for c in out[0]["cells"]] == [1, 2]
    assert out[0]["scope"] == "common"


def test_a_deductible_cell_inside_a_coverage_row_moves_to_its_own_row():
    readings = [
        _reading(1, [{"group": "coverage", "label": "Sismo", "verbatim": "Terremoto y maremoto"}]),
        _reading(2, [{"group": "deductible", "label": "Sismo", "verbatim": "2% mínimo UF 50"}]),
    ]
    dims = [{"key": "sismo", "group": "coverage", "label": "Sismo",
             "cells": [{"comparison_source_id": 1, "facet_n": 1}, {"comparison_source_id": 2, "facet_n": 1}]}]
    out = standardize.finalize_dimensions(standardize.fill_cells(dims, readings), readings)
    assert [(d["key"], d["group"]) for d in out] == [("sismo", "coverage"), ("sismo-deducible", "deductible")]
    assert [c["comparison_source_id"] for c in out[1]["cells"]] == [2]
    assert out[1]["label"] == "Sismo (deducible)"


# --- A5 · promote: placement on demand + catalogue insurer --------------------------


@pytest.fixture()
def bare_account(db, world):
    """A v12 account opened from the UI: no placement yet."""
    case = make_case_file(db, world.a, placement_id=None, stage=CaseStage.INTAKE, n=7)
    db.commit()
    db.refresh(case)
    return case


def test_entry_exposes_catalogue_suggestions_for_the_read_name(
    client, headers_a, world, db, bare_account, tmp_path, provider
):
    comparison_id = _open(client, headers_a, bare_account.id)
    entry = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).json()["entry"]
    assert entry["insurer_id"] is None  # a name is never a resolution (rule 7)
    assert entry["insurer_suggestions"][0] == {
        "id": world.insurer.id, "name": "HDI Seguros S.A.", "rut": world.insurer.rut,
        "cmf_code": world.insurer.cmf_code,
    }


def test_promote_without_placement_creates_it_and_takes_the_catalogue_insurer(
    client, headers_a, world, db, bare_account, tmp_path, provider
):
    comparison_id = _open(client, headers_a, bare_account.id)
    entry = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).json()["entry"]

    # No identity at all: a structured Spanish 422 carrying the suggestions.
    resp = client.post(f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/promote", headers=headers_a)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "insurer_identity_incomplete"
    assert detail["reason"].startswith("Elige la compañía")
    assert detail["insurer_suggestions"][0]["id"] == world.insurer.id

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/promote",
        json={"insurer_id": world.insurer.id},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["insurer_id"] == world.insurer.id
    assert body["placement_created"] is True
    db.expire_all()
    case = db.get(CaseFile, bare_account.id)
    placement = db.get(Placement, body["placement_id"])
    assert case.placement_id == placement.id and placement.case_file_id == case.id
    assert placement.client_id == case.client_id and placement.insurance_line_id == case.insurance_line_id
    # The account is at quotes_received (A6) -> the placement is quoting.
    assert placement.status == PlacementStatus.QUOTING
    assert db.get(Comparison, comparison_id).placement_id == placement.id
    proposal = db.get(Proposal, body["proposal_id"])
    assert proposal.insurer_id == world.insurer.id and float(proposal.total_premium_uf) == 169
    assert body["entry"]["insurer_id"] == world.insurer.id


def test_promote_refuses_an_insurer_the_broker_cannot_see(
    client, headers_a, world, db, account_case, tmp_path, provider
):
    private = Insurer(
        legal_name="Privada de B S.A.", rut="76086428-5", cmf_code="CMF-PRIV-B",
        is_native=False, created_by_broker_id=world.b.broker_id,
    )
    db.add(private)
    db.commit()
    comparison_id = _open(client, headers_a, account_case.id)
    entry = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).json()["entry"]
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/promote",
        json={"insurer_id": private.id},
        headers=headers_a,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "insurer_not_found"


def test_promote_errors_are_spanish(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    entry = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).json()["entry"]
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/promote",
        json={"insurer_rut": "11.111.111-1"},  # a valid RUT nobody owns, no CMF to create one
        headers=headers_a,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "insurer_unresolved"
    assert "RUT" in detail["reason"] and "placement" not in detail["reason"].lower()


def test_promote_of_a_failed_column_is_refused(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    provider.fail_core = ai_service.AITimeout("slow")
    _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id)
    entry = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"][0]
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/promote",
        json={"insurer_id": world.insurer.id},
        headers=headers_a,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "read_failed"


# --- A6 · the journey follows the comparator ----------------------------------------


def test_cotizaciones_and_alignment_advance_the_account(
    client, headers_a, world, db, bare_account, tmp_path, provider
):
    comparison_id = _open(client, headers_a, bare_account.id)
    resp = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id)
    assert resp.status_code == 201
    db.expire_all()
    case = db.get(CaseFile, bare_account.id)
    assert case.stage == CaseStage.QUOTES_RECEIVED
    events = db.scalars(
        select(CaseFileStageEvent).where(CaseFileStageEvent.case_file_id == case.id)
    ).all()
    skipped = {e.to_stage for e in events if e.is_skipped}
    assert {CaseStage.TECHNICAL_BASIS, CaseStage.MARKET_SUBMISSION} <= skipped

    # The comparison guard counts the comparator's read columns (no proposal yet).
    assert machine.guard_reason(db, case, CaseStage.COMPARISON) is None

    resp = client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a)
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert db.get(CaseFile, bare_account.id).stage == CaseStage.COMPARISON


def test_a_wrong_file_does_not_advance_the_account(
    client, headers_a, world, db, bare_account, tmp_path, provider
):
    provider.cores = [{"document_type": "not_a_proposal", "rejection_reason": "Es un correo."}]
    comparison_id = _open(client, headers_a, bare_account.id)
    resp = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id)
    assert resp.status_code == 201 and resp.json()["entry"]["status"] == "wrong_file"
    db.expire_all()
    case = db.get(CaseFile, bare_account.id)
    assert case.stage == CaseStage.INTAKE
    assert machine.guard_reason(db, case, CaseStage.COMPARISON) is not None


def test_the_comparator_never_moves_an_account_back(
    client, headers_a, world, db, tmp_path, provider
):
    case = make_case_file(db, world.a, stage=CaseStage.INSURED_DECISION, n=8)
    db.commit()
    comparison_id = _open(client, headers_a, case.id)
    assert _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).status_code == 201
    assert client.post(f"{API}/comparisons/{comparison_id}/align", headers=headers_a).status_code == 200
    db.expire_all()
    assert db.get(CaseFile, case.id).stage == CaseStage.INSURED_DECISION


def test_a_failed_reread_keeps_a_good_column(client, headers_a, world, db, account_case, tmp_path, provider):
    comparison_id = _open(client, headers_a, account_case.id)
    entry = _add(client, headers_a, comparison_id, _document(db, world, tmp_path).id).json()["entry"]
    provider.fail_core = ai_service.AITimeout("slow")
    resp = client.post(f"{API}/comparisons/{comparison_id}/entries/{entry['id']}/retry", headers=headers_a)
    assert resp.status_code == 504
    again = client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"][0]
    assert again["status"] == "ok"
    assert again["source_extraction_id"] == entry["source_extraction_id"]
