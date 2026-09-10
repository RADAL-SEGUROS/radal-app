"""v8 AI / extraction core: dynamic budget-proposal, alignment, policy core.

Hermetic throughout — the chat model is faked and, for alignment, the provider
is simply blanked (``conftest`` points ``AI_BASE_URL`` at an unroutable port), so
the deterministic slug-match fallback runs. Nothing here reaches DeepInfra.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.ai import Extraction, ExtractionStatus
from app.models.comparison import Comparison, ComparisonSource, ComparisonStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import EntityType
from app.models.proposal import Proposal, ProposalStatus
from app.schemas.extraction.budget_proposal import BudgetProposalExtraction
from app.services import ai as ai_service
from app.services.policy_validation import validate_policy_core
from tests.test_ai_streaming import _FakeChatModel


# --- 1. validate_policy_core -------------------------------------------------


def _valid_payload() -> dict:
    return {
        "corredor": {"name": "Corredora XY Ltda."},
        "coverage_start": "2026-01-01",
        "coverage_end": "2027-01-01",
        "asegurado": {"razon_social": "Viña Santa Alicia S.A.", "rut": "76.123.456-7"},
        "taxable_premium_uf": 100,
        "exempt_premium_uf": 0,
        "net_premium_uf": 100,
        "vat_uf": 19,
        "total_premium_uf": 119,
    }


def test_validate_policy_core_valid():
    verdict = validate_policy_core(_valid_payload())
    assert verdict["is_core_valid"] is True
    assert verdict["missing"] == []
    assert verdict["detail"]["prima_reconciles"] is True
    assert verdict["detail"]["asegurado_required"] is True


def test_validate_policy_core_missing_core():
    # No corredor, no vigencia, contradictory premium (vat wrong).
    payload = {
        "asegurado": {"rut": "76.123.456-7"},
        "taxable_premium_uf": 100,
        "net_premium_uf": 100,
        "vat_uf": 50,  # should be 19
        "total_premium_uf": 150,
    }
    verdict = validate_policy_core(payload)
    assert verdict["is_core_valid"] is False
    assert "corredor" in verdict["missing"]
    assert "vigencia" in verdict["missing"]
    assert "prima" in verdict["missing"]
    assert verdict["detail"]["money_errors"]


def test_validate_policy_core_skips_asegurado_when_account_has_identity():
    payload = {
        "corredor": "Corredora XY Ltda.",
        "coverage_start": "2026-01-01",
        "coverage_end": "2027-01-01",
        "taxable_premium_uf": 100,
        "net_premium_uf": 100,
        "vat_uf": 19,
        "total_premium_uf": 119,
        # no asegurado block at all
    }
    # Without identity the asegurado block is required and missing.
    strict = validate_policy_core(payload, {"has_account_identity": False})
    assert "asegurado" in strict["missing"]

    # With a complete account identity the check is skipped and the core passes.
    lenient = validate_policy_core(payload, {"has_account_identity": True})
    assert lenient["is_core_valid"] is True
    assert lenient["detail"]["asegurado_skipped"] is True
    assert "asegurado" not in lenient["missing"]


# --- 2. budget_proposal facet coercion ---------------------------------------


def test_budget_proposal_coercion_drops_bad_field_preserves_partial_read():
    """A bad strict field (``document_type``) is dropped; money + facets survive."""
    parsed = {
        "document_type": "totally-invalid-verdict",  # not a Literal member
        "document_type_confidence": 90,
        "total_premium_uf": "UF 1.234,56",
        "facets": [
            {"key": "sismo", "group": "deductible", "label": "Sismo", "present": True},
            # an unknown group is folded to "other", never dropping the facet
            {"key": "raro", "group": "banana", "label": "Raro", "present": True},
        ],
    }
    payload, warnings = ai_service._coerce_payload(BudgetProposalExtraction, parsed)
    assert payload is not None
    # The bad discriminator was dropped, so the default applies.
    assert payload.document_type == "budget_proposal"
    assert any("document_type" in w for w in warnings)
    # The partial read is preserved: money core + both facets survived.
    assert str(payload.total_premium_uf) == "1234.56"
    assert len(payload.facets) == 2
    assert payload.facets[1].group == "other"  # lenient leaf


# --- 3. extract_budget_proposal writes exactly one row on a wrong file --------


def _readable_document(db, world, tmp_path, *, text: str, name: str = "archivo.txt"):
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
        category=DocumentCategory.BUDGET_PROPOSAL,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def test_extract_budget_proposal_wrong_file_writes_one_succeeded_row(
    world, db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    document = _readable_document(
        db, world, tmp_path, text="Este es un correo cualquiera, no una oferta de seguros. " * 4
    )
    verdict = {
        "document_type": "not_a_proposal",
        "document_type_confidence": 88,
        "rejection_reason": "Es un correo, no una cotización con prima ni coberturas.",
        "facets": [],
    }

    # The tool-calling read is faked at the ``tool_call`` seam — no live provider.
    def _fake_tool_call(**kwargs):
        assert kwargs["schema"] is BudgetProposalExtraction
        payload, warnings = ai_service._coerce_payload(
            BudgetProposalExtraction, dict(verdict)
        )
        return ai_service.ToolCallResult(
            parsed=dict(verdict),
            payload=payload,
            raw={"content": json.dumps(verdict, ensure_ascii=False), "parsed": verdict},
            usage={"prompt_tokens": 11, "completion_tokens": 7},
            reasoning="…razonamiento del modelo…",
            warnings=warnings,
            model="fake/model-1",
        )

    monkeypatch.setattr(ai_service, "tool_call", _fake_tool_call)

    result = ai_service.extract_budget_proposal(
        db, document_id=document.id, broker_id=world.a.broker_id
    )

    assert result.document_type == "not_a_proposal"
    assert result.is_wrong_file is True
    assert "correo" in (result.rejection_reason or "").lower()
    assert result.extraction_id == result.extraction.id

    rows = db.scalars(
        select(Extraction).where(Extraction.document_id == document.id)
    ).all()
    assert len(rows) == 1, "exactly one extraction row per attempt (rule 6)"
    # A wrong file is a SUCCEEDED-but-flagged read, never a failure.
    assert rows[0].status is ExtractionStatus.SUCCEEDED
    assert rows[0].category is DocumentCategory.BUDGET_PROPOSAL
    assert rows[0].prompt_version == ai_service.BUDGET_PROPOSAL_PROMPT_VERSION


# --- 4. align_comparison deterministic fallback (no provider) -----------------


def _comparison_with_sources(db, world):
    """A comparison plus two cached sources — one document + extraction for audit."""
    document = Document(
        broker_id=world.a.broker_id,
        entity_type=EntityType.QUOTE_REQUEST,
        entity_id=world.a.quote.id,
        s3_key=f"cmp-{world.a.broker_id}-{world.a.quote.id}.txt",
        bucket="radal-test-bucket",
        original_name="oferta.txt",
        mime_type="text/plain",
        category=DocumentCategory.BUDGET_PROPOSAL,
    )
    db.add(document)
    db.flush()
    src_extraction = Extraction(
        broker_id=world.a.broker_id,
        document_id=document.id,
        model="fake/model",
        prompt_version="budget-proposal-v1",
        status=ExtractionStatus.SUCCEEDED,
    )
    db.add(src_extraction)
    db.flush()

    comparison = Comparison(broker_id=world.a.broker_id, case_file_id=None)
    db.add(comparison)
    db.flush()

    s1 = ComparisonSource(
        broker_id=world.a.broker_id,
        source_extraction_id=src_extraction.id,
        facets=[
            {"key": "sismo", "group": "deductible", "label": "Sismo", "present": True},
            {"key": "incendio", "group": "coverage", "label": "Incendio", "present": True},
        ],
    )
    s2 = ComparisonSource(
        broker_id=world.a.broker_id,
        facets=[
            {"key": "SISMO", "group": "deductible", "label": "Sismo", "present": True},
            {"key": "robo", "group": "coverage", "label": "Robo", "present": True},
        ],
    )
    db.add_all([s1, s2])
    db.commit()
    db.refresh(comparison)
    return comparison, [s1, s2]


def _submit_comparison_tool_double(dimensions, recommendation=None, *, monkeypatch):
    """Fake ``tool_call`` for the holistic submit_comparison path (hermetic)."""
    def _call(*, tool_schema=None, schema=None, **kwargs):
        name = (tool_schema or {}).get("name")
        parsed = (
            {"dimensions": dimensions, "recommendation": recommendation}
            if name == "submit_comparison"
            else {"recommendation": recommendation}
        )
        return ai_service.ToolCallResult(
            parsed=parsed,
            payload=None,
            raw={"content": json.dumps(parsed, ensure_ascii=False), "parsed": parsed},
            usage={"prompt_tokens": 10, "completion_tokens": 6},
            reasoning=None,
            warnings=[],
            model="fake/model-cmp",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)


def test_submit_comparison_snapshots_table_and_recommendation(world, db, monkeypatch):
    """The holistic comparison standardizes ALL readings into one table + a rec."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    comparison, sources = _comparison_with_sources(db, world)

    dimensions = [
        {"key": "sismo", "group": "deductible", "label": "Sismo", "scope": "common", "cells": [
            {"comparison_source_id": sources[0].id, "present": True, "value": "5%", "verbatim": "5% de la pérdida"},
            {"comparison_source_id": sources[1].id, "present": True, "value": "5%"},
        ]},
        {"key": "incendio", "group": "coverage", "label": "Incendio", "scope": "extra", "cells": [
            {"comparison_source_id": sources[0].id, "present": True},
        ]},
    ]
    recommendation = {
        "recommended_comparison_source_id": sources[0].id,
        "rationale": "Mejor deducible de sismo por prima equivalente.",
        "caveats": ["Revisar el mínimo UF del sismo."],
    }
    _submit_comparison_tool_double(dimensions, recommendation, monkeypatch=monkeypatch)

    result = ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )

    assert result.used_ai is True
    assert result.batched is False
    assert result.recommendation["recommended_comparison_source_id"] == sources[0].id
    keys = {entry["key"] for entry in result.dictionary}
    assert keys == {"sismo", "incendio"}
    assert result.canonical_version == 1
    assert result.table["dimensions"] == dimensions

    # One audit row was written and snapshotted onto the comparison.
    assert result.extraction is not None
    db.refresh(comparison)
    assert comparison.status is ComparisonStatus.ALIGNED
    assert comparison.source_extraction_id == result.extraction.id
    assert comparison.aligned_matrix["recommendation"]["rationale"]


def test_submit_comparison_dictionary_grows_monotonically(world, db, monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    comparison, sources = _comparison_with_sources(db, world)

    _submit_comparison_tool_double(
        [{"key": "sismo", "group": "deductible", "label": "Sismo", "cells": []},
         {"key": "incendio", "group": "coverage", "label": "Incendio", "cells": []}],
        monkeypatch=monkeypatch,
    )
    ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )
    db.refresh(comparison)

    # A re-run that introduces a brand-new dimension bumps the version and appends.
    _submit_comparison_tool_double(
        [{"key": "sismo", "group": "deductible", "label": "Sismo", "cells": []},
         {"key": "lucro_cesante", "group": "coverage", "label": "Lucro cesante", "cells": []}],
        monkeypatch=monkeypatch,
    )
    result = ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )
    keys = {entry["key"] for entry in result.dictionary}
    assert "lucro_cesante" in keys
    assert {"sismo", "incendio"} <= keys  # nothing dropped
    assert result.canonical_version == 2


def test_submit_comparison_blocks_on_provider_down(world, db, monkeypatch):
    """BLOCK-ON-DOWN: a raised AIError PROPAGATES (no deterministic fallback)."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    comparison, sources = _comparison_with_sources(db, world)

    def _down(**kwargs):
        raise ai_service.AITimeout("provider timed out")

    monkeypatch.setattr(ai_service, "tool_call", _down)

    with pytest.raises(ai_service.AITimeout):
        ai_service.submit_comparison(
            db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
        )
    db.rollback()
    db.refresh(comparison)
    assert comparison.status is not ComparisonStatus.ALIGNED


def test_submit_comparison_batches_over_threshold_and_merges(world, db, monkeypatch):
    """Above AI_COMPARE_MAX_INPUT_CHARS the readings run in batches then merge."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    # Force batching: the two readings cannot share a batch.
    monkeypatch.setattr(settings, "AI_COMPARE_MAX_INPUT_CHARS", 200)
    comparison, sources = _comparison_with_sources(db, world)

    calls: list[str] = []

    def _call(*, tool_schema=None, schema=None, **kwargs):
        name = (tool_schema or {}).get("name")
        calls.append(name)
        if name == "submit_comparison":
            # Each batch reports one distinct dimension.
            idx = sum(1 for c in calls if c == "submit_comparison")
            parsed = {"dimensions": [
                {"key": f"dim_{idx}", "group": "coverage", "label": f"Dim {idx}", "cells": []}
            ]}
        else:  # recommend_comparison over the merged table
            parsed = {"recommendation": {"recommended_comparison_source_id": sources[0].id,
                                          "rationale": "merged", "caveats": []}}
        return ai_service.ToolCallResult(
            parsed=parsed, payload=None,
            raw={"content": json.dumps(parsed), "parsed": parsed},
            usage={"prompt_tokens": 3, "completion_tokens": 2}, reasoning=None,
            warnings=[], model="fake/model-batch",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )

    assert result.batched is True
    assert result.batch_count == 2
    assert calls.count("submit_comparison") == 2
    assert calls.count("recommend_comparison") == 1  # one rec over the merged table
    # The merged table unions both batches' dimensions.
    keys = {d["key"] for d in result.table["dimensions"]}
    assert keys == {"dim_1", "dim_2"}
    assert result.recommendation["rationale"] == "merged"
    assert any("batches" in w for w in result.warnings)


def test_submit_comparison_blocks_on_empty_dimensions(world, db, monkeypatch):
    """HARD GUARD: zero dimensions (a truncated/empty answer) blocks the step —
    NEVER a 200 committing an empty table."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    comparison, sources = _comparison_with_sources(db, world)

    def _empty(*, tool_schema=None, **kwargs):
        parsed = {"dimensions": [], "recommendation": None}
        return ai_service.ToolCallResult(
            parsed=parsed, payload=None, raw={"content": "{}", "parsed": parsed},
            usage=None, reasoning=None, warnings=[], model="fake/empty",
        )

    monkeypatch.setattr(ai_service, "tool_call", _empty)

    with pytest.raises(ai_service.AIProviderError):
        ai_service.submit_comparison(
            db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
        )
    db.rollback()
    db.refresh(comparison)
    assert comparison.status is not ComparisonStatus.ALIGNED


def test_submit_comparison_runs_recommend_fallback_when_no_pick(world, db, monkeypatch):
    """When the holistic answer names NO pick, the dedicated recommend call runs
    so a normal run always yields a recommended_comparison_source_id."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    comparison, sources = _comparison_with_sources(db, world)

    calls: list[str] = []

    def _call(*, tool_schema=None, schema=None, **kwargs):
        name = (tool_schema or {}).get("name")
        calls.append(name)
        if name == "submit_comparison":
            parsed = {
                "dimensions": [{"key": "sismo", "group": "deductible", "label": "Sismo", "cells": []}],
                "recommendation": {"recommended_comparison_source_id": None, "rationale": None, "caveats": []},
            }
        else:  # recommend_comparison
            parsed = {"recommendation": {"recommended_comparison_source_id": sources[0].id,
                                          "rationale": "Mejor prima.", "caveats": []}}
        return ai_service.ToolCallResult(
            parsed=parsed, payload=None, raw={"content": json.dumps(parsed), "parsed": parsed},
            usage=None, reasoning=None, warnings=[], model="fake/rec",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )
    assert calls == ["submit_comparison", "recommend_comparison"]
    assert result.recommendation["recommended_comparison_source_id"] == sources[0].id
    assert result.recommendation["rationale"] == "Mejor prima."


def test_dedicated_recommend_schema_requires_pick_holistic_stays_soft():
    """The DEDICATED recommend tool MUST return a pick + rationale (model-steering);
    the nested recommendation inside submit_comparison stays NON-required."""
    dedicated = ai_service._recommend_tool_schema()
    rec = dedicated["parameters"]["properties"]["recommendation"]
    assert rec["required"] == ["recommended_comparison_source_id", "rationale", "caveats"]

    holistic = ai_service._submit_comparison_tool_schema()
    nested = holistic["parameters"]["properties"]["recommendation"]
    assert "required" not in nested, "holistic path must not over-constrain the rec"


def test_dedicated_recommend_lists_valid_ids_in_prompt(world, db):
    """The recommend prompt names the valid comparison_source_ids so the model can
    only pick from the real columns."""
    comparison, sources = _comparison_with_sources(db, world)
    readings = [{"comparison_source_id": s.id, "money_core": {}} for s in sources]
    messages = ai_service._recommend_messages([], readings)
    system = messages[0]["content"]
    assert str(sources[0].id) in system and str(sources[1].id) in system
    assert "null" in system  # explicit "never null" steering


def test_submit_comparison_pickless_recommend_degrades_to_warning(world, db, monkeypatch):
    """SOFT GUARD: if the dedicated recommend call STILL names no pick, the step
    degrades to a warning — the table commits, no 500."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    comparison, sources = _comparison_with_sources(db, world)

    def _call(*, tool_schema=None, schema=None, **kwargs):
        name = (tool_schema or {}).get("name")
        if name == "submit_comparison":
            parsed = {
                "dimensions": [{"key": "sismo", "group": "deductible", "label": "Sismo", "cells": []}],
                "recommendation": {"recommended_comparison_source_id": None, "rationale": None, "caveats": []},
            }
        else:  # recommend_comparison still names no pick
            parsed = {"recommendation": {"recommended_comparison_source_id": None,
                                          "rationale": None, "caveats": ["Ofertas equivalentes."]}}
        return ai_service.ToolCallResult(
            parsed=parsed, payload=None, raw={"content": json.dumps(parsed), "parsed": parsed},
            usage=None, reasoning=None, warnings=[], model="fake/pickless",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_comparison(
        db, comparison=comparison, sources=sources, broker_id=world.a.broker_id
    )
    # No 500: the table still committed, and the pick-less rec is preserved + flagged.
    assert result.recommendation is not None
    assert result.recommendation["recommended_comparison_source_id"] is None
    assert any("no pick" in w for w in result.warnings)
    db.refresh(comparison)
    assert comparison.status is ComparisonStatus.ALIGNED


# --- 5. submit_propuesta: validated minimum core + free tail -----------------


def _winner_proposal(db, world):
    """A committed winning proposal with a reconciling premium core."""
    proposal = Proposal(
        broker_id=world.a.broker_id,
        quote_request_id=world.a.quote.id,
        insurer_id=world.insurer.id,
        source_document_id=world.a.document.id,
        status=ProposalStatus.DRAFT,
        taxable_premium_uf=Decimal("100"),
        exempt_premium_uf=Decimal("0"),
        net_premium_uf=Decimal("100"),
        vat_uf=Decimal("19"),
        total_premium_uf=Decimal("119"),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def _aligned_comparison(db, world):
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=None,
        status=ComparisonStatus.ALIGNED,
        aligned_matrix={"dimensions": [{"key": "sismo", "group": "deductible"}]},
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return comparison


def test_submit_propuesta_builds_core_and_tail(world, db, monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    winner = _winner_proposal(db, world)
    comparison = _aligned_comparison(db, world)

    tool_out = {
        "core": {"insured_name": "Viña Journey S.A.", "coverage_start": "2026-01-01"},
        "additional": [
            {"group": "coverage", "label": "Incendio", "verbatim": "Cobertura de incendio…"},
        ],
    }

    def _call(**kwargs):
        return ai_service.ToolCallResult(
            parsed=tool_out, payload=None,
            raw={"content": json.dumps(tool_out), "parsed": tool_out},
            usage={"prompt_tokens": 4, "completion_tokens": 3}, reasoning=None,
            warnings=[], model="fake/model-prop",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_propuesta(
        db, comparison=comparison, winner=winner, broker_id=world.a.broker_id
    )

    assert result.is_core_valid is True
    assert result.money_errors == []
    # The authoritative premium comes from the winner; the tool adds insured/dates.
    assert result.core["insurer_id"] == world.insurer.id
    assert result.core["total_premium_uf"] == "119.0000"
    assert result.core["insured_name"] == "Viña Journey S.A."
    assert result.additional and result.additional[0]["label"] == "Incendio"


def test_submit_propuesta_populates_core_from_account_antecedentes(world, db, monkeypatch):
    """The account KNOWS the insured identity + vigencia from its registered
    antecedentes; the mint must lift them into the core when the winning
    cotización omits them, while the premium core still HARD-validates."""
    from app.models.enums import RecordExpedienteStatus
    from app.models.record_expediente import RecordExpediente
    from tests.conftest import make_case_file

    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")

    case = make_case_file(db, world.a)
    db.commit()
    db.refresh(case)

    # A REGISTERED, human-validated antecedentes payload (sectioned, broker-shaped).
    db.add(
        RecordExpediente(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            status=RecordExpedienteStatus.REGISTERED,
            payload={
                "asegurado": {
                    "razon_social": "Viña Antecedente S.A.",
                    "rut": "76123456-7",
                },
                "vigencia": {
                    "vigencia_inicio": "2026-03-01",
                    "vigencia_fin": "2027-03-01",
                },
            },
        )
    )
    db.commit()

    winner = _winner_proposal(db, world)  # premium reconciles; no coverage dates
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=case.id,
        status=ComparisonStatus.ALIGNED,
        aligned_matrix={"dimensions": []},
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    # The cotización states nothing beyond premium: an EMPTY tool core.
    tool_out = {"core": {}, "additional": []}

    seen: dict = {}

    def _call(**kwargs):
        seen["messages"] = kwargs.get("messages")
        return ai_service.ToolCallResult(
            parsed=tool_out, payload=None,
            raw={"content": json.dumps(tool_out), "parsed": tool_out},
            usage=None, reasoning=None, warnings=[], model="fake/model-prop",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_propuesta(
        db, comparison=comparison, winner=winner, broker_id=world.a.broker_id
    )

    # The premium core is still HARD-validated and authoritative from the winner.
    assert result.is_core_valid is True
    assert result.money_errors == []
    assert result.core["insurer_id"] == world.insurer.id
    assert result.core["total_premium_uf"] == "119.0000"

    # Insured identity + vigencia were POPULATED from the account antecedentes.
    assert result.core["insured_name"] == "Viña Antecedente S.A."
    assert result.core["insured_rut"] == "76123456-7"
    assert result.core["coverage_start"] == "2026-03-01"
    assert result.core["coverage_end"] == "2027-03-01"

    # Those populated minimums no longer show up as missing-field warnings.
    assert not any(
        w.startswith("Missing minimum propuesta field: insured_name")
        or w.startswith("Missing minimum propuesta field: coverage_start")
        or w.startswith("Missing minimum propuesta field: coverage_end")
        for w in result.warnings
    )

    # The account context was fed to the model as a system-block prepend.
    assert seen["messages"][0]["role"] == "system"
    assert "CONTEXTO DE LA CUENTA" in seen["messages"][0]["content"]


def test_submit_propuesta_cotizacion_wins_over_account_context(world, db, monkeypatch):
    """Precedence: a value stated by the winning cotización beats the account
    antecedentes; the account only FILLS what the cotización omits."""
    from app.models.enums import RecordExpedienteStatus
    from app.models.record_expediente import RecordExpediente
    from tests.conftest import make_case_file

    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")

    case = make_case_file(db, world.a)
    db.commit()
    db.refresh(case)
    db.add(
        RecordExpediente(
            broker_id=world.a.broker_id,
            case_file_id=case.id,
            status=RecordExpedienteStatus.REGISTERED,
            payload={"asegurado": {"razon_social": "Nombre Antecedentes S.A."}},
        )
    )
    db.commit()

    winner = _winner_proposal(db, world)
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=case.id,
        status=ComparisonStatus.ALIGNED,
        aligned_matrix={"dimensions": []},
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    # The cotización DOES state the insured name — it must win.
    tool_out = {"core": {"insured_name": "Nombre Cotización S.A."}, "additional": []}

    def _call(**kwargs):
        return ai_service.ToolCallResult(
            parsed=tool_out, payload=None,
            raw={"content": json.dumps(tool_out), "parsed": tool_out},
            usage=None, reasoning=None, warnings=[], model="fake/model-prop",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_propuesta(
        db, comparison=comparison, winner=winner, broker_id=world.a.broker_id
    )
    assert result.core["insured_name"] == "Nombre Cotización S.A."


def test_submit_propuesta_hard_fails_a_broken_premium(world, db, monkeypatch):
    """A tool core that breaks the PREMIUM arithmetic is invalid (mint will 422)."""
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    winner = _winner_proposal(db, world)
    comparison = _aligned_comparison(db, world)

    # vat=99 contradicts 0.19 * taxable(100) = 19 — a hard premium error.
    tool_out = {"core": {"taxable_premium_uf": 100, "vat_uf": 99}, "additional": []}

    def _call(**kwargs):
        return ai_service.ToolCallResult(
            parsed=tool_out, payload=None,
            raw={"content": json.dumps(tool_out), "parsed": tool_out},
            usage=None, reasoning=None, warnings=[], model="fake/model-prop",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)

    result = ai_service.submit_propuesta(
        db, comparison=comparison, winner=winner, broker_id=world.a.broker_id
    )
    assert result.is_core_valid is False
    assert any(e["field"] == "vat_uf" for e in result.money_errors)


def test_submit_propuesta_blocks_on_provider_down(world, db, monkeypatch):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    winner = _winner_proposal(db, world)
    comparison = _aligned_comparison(db, world)

    def _down(**kwargs):
        raise ai_service.AIProviderError("provider down")

    monkeypatch.setattr(ai_service, "tool_call", _down)
    with pytest.raises(ai_service.AIProviderError):
        ai_service.submit_propuesta(
            db, comparison=comparison, winner=winner, broker_id=world.a.broker_id
        )
