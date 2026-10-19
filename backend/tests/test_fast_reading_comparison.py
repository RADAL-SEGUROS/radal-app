"""Fast reading — the COMPARISON path (docs/v12/11-fast-reading.md).

Hermetic: every provider call is faked at the ``ai.tool_call`` seam. Covers the grouped,
parallel cotización read (split / merge / one row per attempt), the parallel
standardization batches (concurrency + deterministic batch-order merge + facet
references), the quick-retry policy and the honest ``ai_timeout`` error body.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.ai import Extraction, ExtractionStatus
from app.models.comparison import Comparison
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseStage, EntityType
from app.schemas.extraction.budget_proposal import _FACET_GROUPS, BudgetProposalExtraction
from app.schemas.extraction.registry import spec_for
from app.services import ai as ai_service
from app.services import budget_read
from app.services import comparison_standardize as standardize
from app.services.ai_error_copy import AI_TIMEOUT_ES
from tests.conftest import API, make_case_file

_CORE = {
    "document_type": "budget_proposal",
    "document_type_confidence": 93,
    "insurer_name": "HDI Seguros S.A.",
    "insurer_rut": "99301000-6",
    "taxable_premium_uf": 100,
    "exempt_premium_uf": 50,
    "net_premium_uf": 150,
    "vat_uf": 19,
    "total_premium_uf": 169,
    "commission_pct": 12.5,
    "validity_business_days": 30,
}

#: What each facet group "finds" in the fake document.
_FACETS_BY_GROUP = {
    "sublimit": [
        {"group": "sublimit", "label": "Remoción de escombros", "verbatim": "Hasta UF 5.000", "value": 5000},
        {"group": "sublimit", "label": "Gastos de extinción", "verbatim": "Hasta UF 1.000", "value": 1000},
    ],
    "coverage": [
        {"group": "coverage", "label": "Incendio", "verbatim": "Incendio y adicionales", "present": True},
        # Also appears (same label) in the exclusions call — a CROSS-call duplicate.
        {"group": "coverage", "label": "Terrorismo", "verbatim": "No cubre terrorismo", "present": False},
    ],
    "deductible": [
        {"group": "deductible", "label": "Sismo", "verbatim": "2% del monto asegurado, mínimo UF 25"},
        # Same label twice WITHIN one call (two items) — both kept.
        {"group": "deductible", "label": "Sismo", "verbatim": "3% del monto asegurado, mínimo UF 50"},
    ],
    "warranty": [{"group": "warranty", "label": "Rociadores", "verbatim": "Mantener rociadores operativos"}],
    "exclusion": [
        {"group": "exclusion", "label": "Terrorismo", "verbatim": "Se excluye terrorismo"},
        {"group": "exclusion", "label": "Guerra", "verbatim": "Se excluye guerra"},
    ],
    "clause": [{"group": "clause", "label": "Cláusula 1", "verbatim": "Cláusula de 72 horas"}],
    "other": [],
}


def _result(parsed, *, schema=None, model="fake/model-fast"):
    payload, warnings = (None, [])
    if schema is not None:
        payload, warnings = ai_service._coerce_payload(schema, dict(parsed))
    return ai_service.ToolCallResult(
        parsed=parsed,
        payload=payload,
        raw={"content": json.dumps(parsed, ensure_ascii=False), "parsed": parsed},
        usage={"prompt_tokens": 100, "completion_tokens": 10},
        reasoning=None,
        warnings=warnings,
        model=model,
        finish_reason="tool_calls",
    )


def _grouped_read_double(calls: list[dict], *, barrier: threading.Barrier | None = None, fail: dict | None = None):
    """A ``tool_call`` double that answers each group call from what it was ASKED for.

    Every call also returns a conflicting core value, to prove the merge only trusts
    ``money_core`` for the core and only the facet calls for facets.
    """
    lock = threading.Lock()

    def _call(*, tool_schema=None, schema=None, **kwargs):
        with lock:
            calls.append({"tool": tool_schema, "schema": schema, **kwargs})
        if barrier is not None:
            barrier.wait()  # raises BrokenBarrierError unless the calls overlap
        name = tool_schema["name"]
        if fail and name in fail:
            raise fail[name]
        if name == "record_budget_core":
            return _result({**_CORE, "facets": [{"group": "coverage", "label": "NO"}]}, schema=schema)
        groups = tool_schema["parameters"]["properties"]["facets"]["items"]["properties"]["group"]["enum"]
        facets = [f for g in groups for f in _FACETS_BY_GROUP[g]]
        return _result({"total_premium_uf": 999999, "facets": facets}, schema=schema)

    return _call


def _document(db, world, tmp_path, *, chars: int, name: str = "cotizacion.txt") -> Document:
    path = tmp_path / name
    path.write_text(("Cotización Todo Riesgo. " * (chars // 24 + 1))[:chars], encoding="utf-8")
    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
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


# --- 1. The plan --------------------------------------------------------------


def test_short_document_reads_core_plus_one_facet_call():
    plans = budget_read.plan_groups("x" * 500)
    assert [p.name for p in plans] == ["money_core", "facets"]
    facets = plans[1].tool_schema["parameters"]["properties"]["facets"]
    assert facets["items"]["properties"]["group"]["enum"] == list(_FACET_GROUPS)


def test_long_document_splits_facets_into_disjoint_groups_covering_the_vocabulary():
    plans = budget_read.plan_groups("x" * budget_read.FACET_SPLIT_MIN_CHARS)
    assert plans[0].name == "money_core"
    facet_plans = plans[1:]
    assert len(facet_plans) == len(budget_read.FACET_GROUP_SPLIT) >= 2
    enums = [
        p.tool_schema["parameters"]["properties"]["facets"]["items"]["properties"]["group"]["enum"]
        for p in facet_plans
    ]
    flat = [g for e in enums for g in e]
    assert sorted(flat) == sorted(_FACET_GROUPS), "every facet group is read exactly once"
    assert len(flat) == len(set(flat))
    # One call per document never exceeds the pool (no queued, serialised group).
    from app.services.ai_parallel import MAX_PARALLEL_CALLS

    assert len(plans) <= MAX_PARALLEL_CALLS


def test_core_tool_has_no_facets_and_facet_tool_has_no_core():
    core = budget_read.money_core_tool_schema()["parameters"]["properties"]
    assert "facets" not in core
    assert {"document_type", "total_premium_uf", "insurer_rut", "commission_pct"} <= set(core)
    facets = budget_read.facets_tool_schema(("exclusion",))["parameters"]["properties"]
    assert set(facets) == {"facets"}


def test_guidance_no_longer_asks_for_facet_description():
    spec = spec_for(DocumentCategory.BUDGET_PROPOSAL)
    assert "`description`" not in spec.guidance
    assert spec.prompt_version == ai_service.BUDGET_PROPOSAL_PROMPT_VERSION == "budget-proposal-v2"


# --- 2. The merge -------------------------------------------------------------


def test_merge_facets_drops_cross_call_duplicates_keeps_within_call_ones():
    first = [{"label": "Sismo", "group": "deductible"}, {"label": "Sismo", "group": "deductible"}]
    second = [{"label": "SISMO", "group": "exclusion"}, {"label": "Guerra", "group": "exclusion"}]
    merged = budget_read.merge_facets([first, second])
    assert [f["label"] for f in merged] == ["Sismo", "Sismo", "Guerra"]


def test_extract_budget_proposal_runs_groups_in_parallel_and_merges(world, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    document = _document(db, world, tmp_path, chars=budget_read.FACET_SPLIT_MIN_CHARS + 100)
    calls: list[dict] = []
    n_calls = len(budget_read.plan_groups("x" * budget_read.FACET_SPLIT_MIN_CHARS))
    barrier = threading.Barrier(n_calls, timeout=5)
    monkeypatch.setattr(ai_service, "tool_call", _grouped_read_double(calls, barrier=barrier))

    result = ai_service.extract_budget_proposal(
        db, document_id=document.id, broker_id=world.a.broker_id
    )

    assert len(calls) == n_calls  # all overlapped at the barrier: truly concurrent
    for call in calls:
        assert call["timeout"] == min(settings.AI_TIMEOUT_SECONDS, budget_read.FAST_READ_TIMEOUT_SECONDS)
        assert call["max_retries"] == 0
        assert call["schema"] is BudgetProposalExtraction
    # Same document prefix for every group (provider prefix cache); only the tail differs.
    prefixes = {c["messages"][1]["content"].split("--- FIN DEL DOCUMENTO ---")[0] for c in calls}
    assert len(prefixes) == 1

    payload = result.payload
    assert payload.total_premium_uf == 169  # the core came ONLY from money_core
    labels = [f.label for f in payload.facets]
    assert "NO" not in labels  # the core call's stray facet was ignored
    # Plan order, within-call duplicates kept, the cross-call "Terrorismo" dropped once.
    seen, deduped = set(), []
    for groups in budget_read.FACET_GROUP_SPLIT.values():
        batch = [f["label"].lower() for g in groups for f in _FACETS_BY_GROUP[g]]
        deduped += [label for label in batch if label not in seen]
        seen.update(batch)
    assert [label.lower() for label in labels] == deduped
    assert labels.count("Sismo") == 2
    assert labels.count("Terrorismo") == 1

    rows = db.scalars(select(Extraction).where(Extraction.document_id == document.id)).all()
    assert len(rows) == 1, "exactly one extraction row per attempt (rule 6)"
    row = rows[0]
    assert row.status is ExtractionStatus.SUCCEEDED
    assert row.prompt_version == "budget-proposal-v2"
    assert row.prompt_tokens == 100 * n_calls and row.completion_tokens == 10 * n_calls
    groups = row.raw_output["groups"]
    assert list(groups) == [p.name for p in budget_read.plan_groups("x" * budget_read.FACET_SPLIT_MIN_CHARS)]
    for stat in groups.values():
        assert {"seconds", "prompt_tokens", "completion_tokens", "finish_reason"} <= set(stat)
    assert row.raw_output["parallel"] is True
    assert row.parsed["facets"] and float(row.parsed["total_premium_uf"]) == 169


def test_a_failed_group_fails_the_read_with_one_failed_row(world, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    document = _document(db, world, tmp_path, chars=500)
    calls: list[dict] = []
    monkeypatch.setattr(
        ai_service,
        "tool_call",
        _grouped_read_double(calls, fail={"record_budget_facets": ai_service.AITimeout("slow")}),
    )

    with pytest.raises(ai_service.AITimeout):
        ai_service.extract_budget_proposal(db, document_id=document.id, broker_id=world.a.broker_id)

    rows = db.scalars(select(Extraction).where(Extraction.document_id == document.id)).all()
    assert len(rows) == 1
    assert rows[0].status is ExtractionStatus.FAILED
    assert "AITimeout" in rows[0].error
    groups = rows[0].raw_output["groups"]
    assert "error" in groups["facets"] and "seconds" in groups["money_core"]
    # A timeout is NEVER retried (it would push the request past the edge).
    assert sum(1 for c in calls if c["tool"]["name"] == "record_budget_facets") == 1


# --- 3. Retry policy / timeout mapping -------------------------------------------


def test_quick_failure_is_retried_once_timeout_never():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise ai_service.AIProviderError("The AI provider returned 503")
        return "ok"

    assert budget_read.call_with_quick_retry(flaky) == "ok"
    assert len(attempts) == 2

    timeouts = []

    def slow():
        timeouts.append(1)
        raise ai_service.AITimeout("t")

    with pytest.raises(ai_service.AITimeout):
        budget_read.call_with_quick_retry(slow)
    assert len(timeouts) == 1


def test_provider_gateway_timeout_is_an_ai_timeout():
    def gateway():
        raise ai_service.AIProviderError("The AI provider returned 504")

    with pytest.raises(ai_service.AITimeout):
        budget_read.call_with_quick_retry(gateway)


# --- 4. The honest error body on the comparison endpoints --------------------------


@pytest.fixture()
def comparison_id(client, headers_a, db, world):
    case = make_case_file(db, world.a, stage=CaseStage.COMPARISON)
    db.commit()
    resp = client.post(f"{API}/comparisons", json={"case_file_id": case.id}, headers=headers_a)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_entry_timeout_is_504_ai_timeout_with_spanish_copy(
    client, headers_a, world, db, tmp_path, monkeypatch, comparison_id
):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    document = _document(db, world, tmp_path, chars=500)
    monkeypatch.setattr(
        ai_service,
        "tool_call",
        _grouped_read_double([], fail={"record_budget_facets": ai_service.AITimeout("slow")}),
    )
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": document.id},
        headers=headers_a,
    )
    assert resp.status_code == 504, resp.text
    assert resp.headers["X-Radal-AI-Error"] == "ai_timeout"
    assert resp.json() == {"detail": {"code": "ai_timeout", "message": AI_TIMEOUT_ES}}
    assert AI_TIMEOUT_ES == "La lectura tardó más de lo esperado. Reintenta; si persiste, avísanos."


def test_entry_provider_down_is_ai_unavailable_not_timeout(
    client, headers_a, world, db, tmp_path, monkeypatch, comparison_id
):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    document = _document(db, world, tmp_path, chars=500)
    monkeypatch.setattr(
        ai_service,
        "tool_call",
        _grouped_read_double(
            [], fail={"record_budget_core": ai_service.AIProviderError("The AI provider returned 400")}
        ),
    )
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries",
        json={"document_id": document.id},
        headers=headers_a,
    )
    assert resp.status_code == 502, resp.text
    assert resp.headers["X-Radal-AI-Error"] == "ai_provider"
    assert resp.json()["detail"]["code"] == "ai_unavailable"


# --- 5. Standardization: parallel batches, batch-order merge, facet references -------


def _reading(source_id: int, labels: list[str]) -> dict:
    return ai_service.build_reading(
        comparison_source_id=source_id,
        proposal_id=None,
        is_wrong_file=False,
        wrong_file_reason=None,
        money_core={"total_premium_uf": 100 + source_id},
        facets=[
            {"group": "coverage", "label": label, "verbatim": f"{label} literal {source_id}", "value": source_id}
            for label in labels
        ],
    )


def test_standardization_first_failing_batch_in_order_blocks(monkeypatch):
    readings = [_reading(1, ["A"]), _reading(2, ["B"])]
    monkeypatch.setattr(settings, "AI_COMPARE_MAX_INPUT_CHARS", 10)

    def _call(*, tool_schema=None, messages=None, **kwargs):
        sid = json.loads(messages[1]["content"])["readings"][0]["comparison_source_id"]
        if sid == 1:
            time.sleep(0.1)
            raise ai_service.AITimeout("batch 1 slow")
        raise ai_service.AIParseError("batch 2 bad")

    monkeypatch.setattr(ai_service, "tool_call", _call)
    with pytest.raises(ai_service.AITimeout):
        standardize.standardize_readings(readings, {"dictionary": []})


def test_fill_cells_falls_back_to_the_dimension_slug_without_a_reference():
    readings = [_reading(7, ["Remoción de escombros"])]
    dims = [{"key": "remocion-de-escombros", "label": "Remoción de Escombros",
             "cells": [{"comparison_source_id": 7}]}]
    filled = standardize.fill_cells(dims, readings)
    assert filled[0]["cells"][0]["verbatim"] == "Remoción de escombros literal 7"


# --- 5. Orchestrator follow-ups (live probe 2026-10-18) ------------------------------


def test_rate_limited_call_backs_off_and_retries_up_to_twice(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(budget_read.time, "sleep", lambda s: sleeps.append(s))
    attempts = []

    def limited():
        attempts.append(1)
        if len(attempts) < 3:
            raise ai_service.AIProviderError("The AI provider returned 429")
        return "ok"

    assert budget_read.call_with_quick_retry(limited) == "ok"
    assert len(attempts) == 3 and len(sleeps) == 2 and sleeps[0] < sleeps[1]

    attempts.clear()

    def always_limited():
        attempts.append(1)
        raise ai_service.AIProviderError("The AI provider returned 429")

    with pytest.raises(ai_service.AIProviderError):
        budget_read.call_with_quick_retry(always_limited)
    assert len(attempts) == 1 + budget_read.RATE_LIMIT_RETRIES


def test_split_facet_calls_carry_a_definition_and_cap_verbatim():
    plans = {p.name: p for p in budget_read.plan_groups("x" * 20_000)}
    cover = plans["facets_cover"]
    assert cover.facet_groups == ("coverage", "deductible")
    assert "sublímite" in cover.instruction.lower() and "300 caracteres" in cover.instruction
    verbatim = cover.tool_schema["parameters"]["properties"]["facets"]["items"]["properties"]["verbatim"]
    assert "300 caracteres" in verbatim["description"]


def test_split_call_facets_of_foreign_groups_are_dropped(world, db, tmp_path, monkeypatch):
    """A split call that re-emits another call's groups must not duplicate them."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    document = _document(db, world, tmp_path, chars=20_000)

    def _call(*, tool_schema=None, messages=None, **kwargs):
        if tool_schema["name"] != "record_budget_facets":
            return _result({"document_type": "budget_proposal", "total_premium_uf": 10})
        owned = tool_schema["parameters"]["properties"]["facets"]["items"]["properties"]["group"]["enum"]
        # Every call answers its own group AND a stray "sublimit" (the live failure mode).
        facets = [{"group": g, "label": f"{g} propia", "verbatim": "v"} for g in owned]
        facets.append({"group": "sublimit", "label": "Extensión colada", "verbatim": "v"})
        return _result({"facets": facets})

    monkeypatch.setattr(ai_service, "tool_call", _call)
    result = ai_service.extract_budget_proposal(db, document_id=document.id, broker_id=world.a.broker_id)
    labels = [f["label"] for f in (result.parsed or {}).get("facets", [])] if hasattr(result, "parsed") else None
    if labels is None:  # the result type carries the payload instead
        labels = [f.label for f in result.payload.facets]
    assert labels.count("Extensión colada") == 1  # only the sublimits call's own copy survives
    assert "sublimit propia" in labels


def _grouped_reading(source_id: int) -> dict:
    return ai_service.build_reading(
        comparison_source_id=source_id,
        proposal_id=None,
        is_wrong_file=False,
        wrong_file_reason=None,
        money_core={"total_premium_uf": 100 + source_id},
        facets=[
            {"group": group, "label": f"{group} {i}", "verbatim": f"{group} {i} lit {source_id}", "value": i}
            for group in ("sublimit", "coverage", "exclusion", "warranty")
            for i in range(5)
        ],
    )


# --- Review round 2 follow-up: pre-match + compact rows of references ---------------


def _class_reading(source_id: int, extra: list[dict]) -> dict:
    """11 shared facets (matched in code) + this reading's own, differently worded ones."""
    shared = [
        {"group": "sublimit", "label": f"Sublímite {i}", "verbatim": f"S{i} lit {source_id}", "value": 1000 * i}
        for i in range(1, 6)
    ] + [
        {"group": "exclusion", "label": f"Exclusión {i}", "verbatim": f"E{i} lit {source_id}"}
        for i in range(1, 7)
    ]
    return ai_service.build_reading(
        comparison_source_id=source_id, proposal_id=None, is_wrong_file=False,
        wrong_file_reason=None, money_core={"total_premium_uf": 100 + source_id},
        facets=shared + extra, insurer_label=f"Cía {source_id}",
    )


def test_alignment_calls_run_per_class_in_parallel_with_the_recommend_call(monkeypatch):
    readings = [
        _class_reading(1, [{"group": "coverage", "label": "Rehabilitación automática", "verbatim": "Rehab 1"},
                           {"group": "deductible", "label": "Deducible incendio", "verbatim": "10 %"}]),
        _class_reading(2, [{"group": "clause", "label": "Cláusula de reposición de suma", "verbatim": "Rehab 2"},
                           {"group": "deductible", "label": "Incendio y adicionales", "verbatim": "UF 25"}]),
        _class_reading(3, [{"group": "other", "label": "Forma de pago", "verbatim": "10 cuotas"}]),
    ]  # 36 facets > GROUP_SPLIT_MIN_FACETS
    barrier = threading.Barrier(3, timeout=5)  # cover + deductible + recommend together
    seen: dict[str, dict] = {}

    def _call(*, tool_schema=None, messages=None, **kwargs):
        name = tool_schema["name"]
        barrier.wait()
        blob = json.loads(messages[1]["content"])
        if name == "recommend_comparison":
            # The provisional table: every facet already a row (shared + own).
            assert len(blob["dimensions"]) == 5 + 6 + 5
            assert {m["insurer"] for m in blob["money"]} == {"Cía 1", "Cía 2", "Cía 3"}
            return _result({"recommendation": {"recommended_comparison_source_id": 2,
                                               "rationale": "La columna 2 es la mejor.", "caveats": []}})
        groups = {f["group"] for r in blob["readings"] for f in r["facets"]}
        bucket = "deductible" if groups == {"deductible"} else "cover"
        seen[bucket] = blob
        # Only the unmatched facets travel, numbered as in the full reading.
        labels = [f["label"] for r in blob["readings"] for f in r["facets"]]
        assert not any(label.startswith(("Sublímite", "Exclusión")) for label in labels)
        props = tool_schema["parameters"]["properties"]["rows"]["items"]["properties"]
        assert set(props) == {"fixed", "refs"}  # no wording, no labels, no keys
        time.sleep(0.2 if bucket == "cover" else 0.0)  # the first bucket answers last
        if bucket == "cover":
            return _result({"rows": [{"refs": [[1, 12], [2, 12]]}]})  # rehab 1 == rehab 2
        return _result({"rows": [{"refs": [[1, 13], [2, 13]]}]})

    monkeypatch.setattr(ai_service, "tool_call", _call)
    result = standardize.standardize_readings(readings, {"dictionary": []})

    assert set(seen) == {"cover", "deductible"}  # every exclusion matched in code: no call
    assert result.batched is True and result.batch_count == 2
    assert list(result.call_stats) == ["prematch", "cover", "deductible", "recommend"]
    assert result.call_stats["prematch"]["matched_facets"] == 33
    groups = [d["group"] for d in result.dimensions]
    # Class order: cover rows first (sublimits matched in code), deductibles, exclusions.
    assert groups[:5] == ["sublimit"] * 5 and groups[-6:] == ["exclusion"] * 6
    rehab = next(d for d in result.dimensions if d["label"] == "Rehabilitación automática")
    assert [(c["comparison_source_id"], c["verbatim"]) for c in rehab["cells"]] == [(1, "Rehab 1"), (2, "Rehab 2")]
    assert rehab["scope"] == "extra"
    pago = next(d for d in result.dimensions if d["label"] == "Forma de pago")
    assert [c["comparison_source_id"] for c in pago["cells"]] == [3]  # unmatched -> its own row
    sub1 = result.dimensions[0]
    assert sub1["scope"] == "common" and [c["value"] for c in sub1["cells"]] == [1000, 1000, 1000]
    assert result.recommendation["rationale"] == "Cía 2 es la mejor."


def test_submit_comparison_records_parallel_call_stats(world, db, monkeypatch):
    """The audit row of a split alignment keeps per-call seconds/tokens + the pre-match."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    monkeypatch.setattr(standardize, "GROUP_SPLIT_MIN_FACETS", 1)
    from tests.test_v8_ai_core import _comparison_with_sources

    comparison, sources = _comparison_with_sources(db, world)

    def _call(*, tool_schema=None, **kwargs):
        if tool_schema["name"] == "submit_comparison":
            return _result({"rows": []})
        return _result({"recommendation": {"recommended_comparison_source_id": sources[0].id,
                                           "rationale": "r", "caveats": []}})

    monkeypatch.setattr(ai_service, "tool_call", _call)
    result = ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )
    raw = result.extraction.raw_output
    assert {"prematch", "cover", "recommend"} <= set(raw["calls"])
    assert raw["calls"]["prematch"]["rows"] == 1  # "Sismo" in both readings
    assert result.extraction.prompt_version == "comparison-submit-v2"
    from app.models.comparison import ComparisonStatus

    assert db.get(Comparison, comparison.id).status is ComparisonStatus.ALIGNED


def test_prematch_folds_identical_labels_and_keeps_prior_keys():
    readings = standardize._indexed([_grouped_reading(1), _grouped_reading(2)])
    prior = {"dictionary": [{"key": "sub-cero", "group": "sublimit", "label": "sublimit 0"}]}
    pre = standardize.prematch(readings, prior)
    assert len(pre.rows) == 20 and not any(pre.residual.values())
    first = pre.rows[0]
    assert first["key"] == "sub-cero"  # carry-forward through the prior dictionary's slug
    assert first["cells"] == [{"comparison_source_id": 1, "facet_n": 1}, {"comparison_source_id": 2, "facet_n": 1}]
    # Two facets of ONE reading with the same label (two items) are left to the model.
    twice = standardize._indexed([
        ai_service.build_reading(comparison_source_id=1, proposal_id=None, is_wrong_file=False,
                                 wrong_file_reason=None, money_core={},
                                 facets=[{"group": "deductible", "label": "Sismo"}, {"group": "deductible", "label": "Sismo"}]),
        ai_service.build_reading(comparison_source_id=2, proposal_id=None, is_wrong_file=False,
                                 wrong_file_reason=None, money_core={},
                                 facets=[{"group": "deductible", "label": "Sismo"}]),
    ])
    pre = standardize.prematch(twice, {"dictionary": []})
    assert pre.rows == [] and sum(len(v) for v in pre.residual["deductible"].values()) == 3


def test_a_timed_out_alignment_call_keeps_a_complete_table(monkeypatch):
    readings = [
        _class_reading(1, [{"group": "coverage", "label": "Rehabilitación automática", "verbatim": "Rehab 1"}]),
        _class_reading(2, [{"group": "clause", "label": "Cláusula de reposición de suma", "verbatim": "Rehab 2"}]),
        _class_reading(3, []),
    ]

    def _call(*, tool_schema=None, timeout=None, **kwargs):
        assert timeout <= standardize.ALIGN_CALL_TIMEOUT_SECONDS
        if tool_schema["name"] == "submit_comparison":
            raise ai_service.AITimeout("slow")
        return _result({"recommendation": {"recommended_comparison_source_id": 1, "rationale": "r", "caveats": []}})

    monkeypatch.setattr(ai_service, "tool_call", _call)
    result = standardize.standardize_readings(readings, {"dictionary": []})
    labels = [d["label"] for d in result.dimensions]
    assert "Rehabilitación automática" in labels and "Cláusula de reposición de suma" in labels
    assert any("Realinea" in w for w in result.warnings)
    assert result.call_stats["cover"]["error"] == "timeout"


def test_a_timed_out_alignment_still_blocks_when_nothing_answers(monkeypatch):
    readings = [_class_reading(1, [{"group": "coverage", "label": "A"}]),
                _class_reading(2, [{"group": "coverage", "label": "B"}])]

    def _down(**kwargs):
        raise ai_service.AITimeout("down")

    monkeypatch.setattr(ai_service, "tool_call", _down)
    with pytest.raises(ai_service.AITimeout):
        standardize.standardize_readings(readings, {"dictionary": []})


def test_a_rejected_provider_option_is_retried_without_it(monkeypatch):
    seen: list = []

    def _call(*, tool_schema=None, extra_body=None, **kwargs):
        seen.append(extra_body)
        if extra_body:
            raise ai_service.AIProviderError("The AI provider returned 400")
        return _result({"rows": []})

    monkeypatch.setattr(ai_service, "tool_call", _call)
    standardize._call({"fixed_rows": [], "readings": []}, "fake/model")
    assert seen[0] == standardize.STANDARDIZE_EXTRA_BODY and seen[-1] is None
