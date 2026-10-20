"""Review round 3, R2–R5 — comparisons and the Propuesta (hermetic).

* R2 a deductible / composite cell keeps its condition text ("10% de la pérdida,
  mínimo UF 10"), never a bare "10"; "No contratado" is not "UF 0".
* R3 the recommendation reads the FINAL table, after the alignment (covered in
  ``test_fast_reading_comparison``); a recommend timeout keeps the table.
* R4 the tasa integral is what the cotización states — never afecta + exenta; when
  absent it is prima neta / monto asegurado (Southbridge: 2,310 ‰, not 4,420 ‰).
* R5 *Reintentar* on a column that read without its core; the recommendation never
  computes a prima total it did not read.
"""
from __future__ import annotations

import json
from decimal import Decimal

from app.core.config import settings
from app.models.ai import Extraction, ExtractionKind, ExtractionStatus
from app.models.comparison import ComparisonSource
from app.models.document import DocumentCategory
from app.models.proposal import Proposal
from app.services import ai as ai_service
from app.services import comparison_standardize as standardize
from tests.conftest import API
from tests.test_ai_streaming import _FakeChatModel, tool_call_double
from tests.test_comparison_flow import _comparison_with_column, account_case  # noqa: F401
from tests.test_v8_ai_core import _aligned_comparison

#: The money core of "03 Cotizacion Southbridge - Vina Indomita.pdf" (§6 Tarificación):
#: bienes físicos 40 % afecta + 60 % exenta, BOTH at 2,21 ‰; terrorismo 0,100 ‰ over
#: UF 475 256; no single integral rate printed.
SOUTHBRIDGE = {
    "document_type": "budget_proposal",
    "document_type_confidence": 97,
    "insurer_name": "Southbridge Compañía de Seguros Generales S.A.",
    "insurer_rut": "99301000-6",
    "insurer_cmf_code": "CMF-HDI-001",
    "insured_amount_uf": 475256,
    "taxable_premium_uf": 439.14,
    "exempt_premium_uf": 658.70,
    "net_premium_uf": 1097.84,
    "vat_uf": 83.44,
    "total_premium_uf": 1181.28,
    "taxable_rate_permille": 2.21,
    "exempt_rate_permille": 2.21,
    "comprehensive_rate_permille": None,
    "commission_pct": 10,
    "facets": [],
}


def _fake_read(monkeypatch, offer: dict) -> None:
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    fake = _FakeChatModel(content=json.dumps(offer, ensure_ascii=False))
    monkeypatch.setattr(ai_service, "_chat_model", lambda **kwargs: fake)
    monkeypatch.setattr(ai_service, "tool_call", tool_call_double(fake))


# --- R4 · the tasa integral ---------------------------------------------------------


def test_southbridge_promote_stores_2_310_not_the_4_420_sum(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch  # noqa: F811
):
    _fake_read(monkeypatch, SOUTHBRIDGE)
    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="03-southbridge.txt"
    )
    entry = next(
        e for e in client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"]
        if e["id"] == entry_id
    )
    assert Decimal(str(entry["premium"]["comprehensive_rate_permille"])) == Decimal("2.3100")
    assert entry["premium"]["comprehensive_rate_derived"] is True
    assert Decimal(str(entry["premium"]["insured_amount_uf"])) == Decimal("475256")

    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote", headers=headers_a
    )
    assert resp.status_code == 201, resp.text
    proposal = db.get(Proposal, resp.json()["proposal_id"])
    assert proposal.comprehensive_rate_permille == Decimal("2.3100")
    assert proposal.taxable_rate_permille == Decimal("2.2100")
    assert proposal.exempt_rate_permille == Decimal("2.2100")
    assert resp.json()["warnings"] == []


def test_without_the_insured_amount_no_rate_is_made_up(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch  # noqa: F811
):
    _fake_read(monkeypatch, {**SOUTHBRIDGE, "insured_amount_uf": None})
    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="03-southbridge-b.txt"
    )
    resp = client.post(
        f"{API}/comparisons/{comparison_id}/entries/{entry_id}/promote", headers=headers_a
    )
    assert resp.status_code == 201, resp.text
    assert db.get(Proposal, resp.json()["proposal_id"]).comprehensive_rate_permille is None


def _legacy_winner(db, world, *, parsed: dict) -> Proposal:
    """A proposal promoted BEFORE R4: the sum 4,420 ‰ stored, the read had no rate."""
    extraction = Extraction(
        broker_id=world.a.broker_id,
        document_id=world.a.document.id,
        kind=ExtractionKind.PROPOSAL,
        category=DocumentCategory.BUDGET_PROPOSAL,
        model="fake/read",
        prompt_version="budget-proposal-v2",
        parsed=parsed,
        status=ExtractionStatus.SUCCEEDED,
    )
    db.add(extraction)
    db.flush()
    proposal = Proposal(
        broker_id=world.a.broker_id,
        quote_request_id=world.a.quote.id,
        insurer_id=world.insurer.id,
        source_document_id=world.a.document.id,
        extraction_id=extraction.id,
        taxable_premium_uf=Decimal("439.14"),
        exempt_premium_uf=Decimal("658.70"),
        net_premium_uf=Decimal("1097.84"),
        vat_uf=Decimal("83.44"),
        total_premium_uf=Decimal("1181.28"),
        taxable_rate_permille=Decimal("2.21"),
        exempt_rate_permille=Decimal("2.21"),
        comprehensive_rate_permille=Decimal("4.42"),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def _propuesta(db, world, monkeypatch, winner, tool_core: dict):
    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    out = {"core": tool_core, "additional": []}

    def _call(**kwargs):
        return ai_service.ToolCallResult(
            parsed=out, payload=None, raw={"content": json.dumps(out), "parsed": out},
            usage=None, reasoning=None, warnings=[], model="fake/prop",
        )

    monkeypatch.setattr(ai_service, "tool_call", _call)
    return ai_service.submit_propuesta(
        db, comparison=_aligned_comparison(db, world), winner=winner, broker_id=world.a.broker_id
    )


def test_propuesta_corrects_a_pre_fix_summed_rate_and_ignores_the_models_rates(
    db, world, monkeypatch
):
    parsed = {k: v for k, v in SOUTHBRIDGE.items() if k != "facets"}
    winner = _legacy_winner(db, world, parsed=parsed)
    # The model "helpfully" sums the rates again: ignored, rates are the cotización's.
    result = _propuesta(
        db, world, monkeypatch, winner,
        {"insured_name": "Viña Indómita SpA", "comprehensive_rate_permille": 4.42,
         "taxable_rate_permille": 9.9},
    )
    assert result.is_core_valid
    assert Decimal(str(result.core["comprehensive_rate_permille"])) == Decimal("2.3100")
    assert Decimal(str(result.core["taxable_rate_permille"])) == Decimal("2.2100")
    assert not any("additivity" in w for w in result.warnings)


def test_propuesta_drops_a_summed_rate_it_cannot_recompute(db, world, monkeypatch):
    parsed = {k: v for k, v in SOUTHBRIDGE.items() if k not in ("facets", "insured_amount_uf")}
    winner = _legacy_winner(db, world, parsed=parsed)
    result = _propuesta(db, world, monkeypatch, winner, {"insured_name": "Viña Indómita SpA"})
    assert "comprehensive_rate_permille" not in result.core


def test_a_stated_rate_equal_to_the_sum_is_kept(db, world, monkeypatch):
    """Old-style offers state incendio + sismo rates over the SAME base and an integral
    equal to their sum: a READ integral rate is never touched."""
    parsed = {**{k: v for k, v in SOUTHBRIDGE.items() if k != "facets"},
              "comprehensive_rate_permille": 4.42}
    winner = _legacy_winner(db, world, parsed=parsed)
    result = _propuesta(db, world, monkeypatch, winner, {"insured_name": "Viña Indómita SpA"})
    assert Decimal(str(result.core["comprehensive_rate_permille"])) == Decimal("4.4200")


# --- R2 · condition text in deductible / composite cells ---------------------------


def _row(group: str, label: str, cells: list[dict]) -> dict:
    return {"key": label.lower(), "group": group, "label": label, "scope": "common",
            "cells": [{**c, "_facet_group": group} for c in cells]}


def test_deductibles_keep_their_minimum_and_not_contracted_is_not_uf_0():
    readings = [{"comparison_source_id": i} for i in (1, 2, 3)]
    dims = standardize.finalize_dimensions(
        [
            _row("deductible", "Filtración de vino", [
                {"comparison_source_id": 1, "value": "10", "verbatim": "10% de la pérdida, mínimo UF 10"},
                {"comparison_source_id": 2, "value": 10, "verbatim": "10% de la pérdida, mínimo UF 25"},
                {"comparison_source_id": 3, "value": "10% de la pérdida, mínimo UF 30",
                 "verbatim": "10% de la pérdida, mínimo UF 30"},
            ]),
            _row("sublimit", "Rotura de cristales", [
                {"comparison_source_id": 1, "value": 5, "verbatim": "UF 5 por cristal plano; otros UF 3"},
                {"comparison_source_id": 2, "value": "UF 5.000", "verbatim": "Rotura de cristales UF 5.000"},
            ]),
            _row("coverage", "Perjuicio por paralización", [
                {"comparison_source_id": 1, "value": 90000,
                 "verbatim": "Perjuicio por paralización no contratado. La compañía deja constancia de "
                             "haber ofrecido la cobertura por UF 90.000 a seis meses"},
                {"comparison_source_id": 2, "value": 90000, "verbatim": "Perjuicio por paralización UF 90.000"},
                {"comparison_source_id": 3, "value": 0, "verbatim": "No cotizado"},
            ]),
        ],
        readings,
    )
    deductible, glass, pxp = dims
    assert [c["value"] for c in deductible["cells"]] == [
        "10% de la pérdida, mínimo UF 10",
        "10% de la pérdida, mínimo UF 25",
        "10% de la pérdida, mínimo UF 30",
    ]
    assert [c.get("numeric_value") for c in deductible["cells"]] == [10, 10, None]
    assert all("unit" not in c for c in deductible["cells"])
    assert glass["cells"][0]["value"] == "UF 5 por cristal plano; otros UF 3"
    assert (glass["cells"][1]["value"], glass["cells"][1]["unit"]) == (5000, "uf")
    assert [(c.get("value"), c.get("present")) for c in pxp["cells"]] == [
        (None, False), (90000, None), (None, False)
    ]
    # What the recommendation reads: Southbridge does NOT have PxP.
    rec = standardize.recommend_dimensions(dims, {1: "Southbridge", 2: "HDI", 3: "Mapfre"})
    pxp_rec = rec[2]["cells"]
    assert pxp_rec["Southbridge"].get("no") is True and pxp_rec["Mapfre"].get("no") is True
    assert pxp_rec["HDI"] == {"v": 90000, "u": "uf"}
    assert rec[0]["cells"]["HDI"]["v"] == "10% de la pérdida, mínimo UF 25"


# --- R3 · the recommendation after the alignment ------------------------------------


def test_a_timed_out_recommendation_keeps_the_aligned_table(monkeypatch):
    readings = [
        {"comparison_source_id": 1, "insurer": "A", "money_core": {"total_premium_uf": 10},
         "facets": [{"group": "coverage", "label": "Incendio", "verbatim": "UF 100"}]},
        {"comparison_source_id": 2, "insurer": "B", "money_core": {"total_premium_uf": 12},
         "facets": [{"group": "coverage", "label": "Incendio", "verbatim": "UF 200"}]},
    ]
    calls: list[str] = []

    def _call(*, tool_schema=None, **kwargs):
        calls.append(tool_schema["name"])
        raise ai_service.AITimeout("slow")

    monkeypatch.setattr(ai_service, "tool_call", _call)
    result = standardize.standardize_readings(readings, {"dictionary": []})
    assert calls == ["recommend_comparison"]  # everything matched in code
    assert len(result.dimensions) == 1 and result.recommendation is None
    assert any("La recomendación tardó más de lo esperado" in w for w in result.warnings)


# --- R5 · Reintentar + no invented totals --------------------------------------------


def test_an_ok_read_without_its_core_offers_retry(
    client, headers_a, world, db, account_case, tmp_path, monkeypatch  # noqa: F811
):
    partial = {"document_type": "budget_proposal", "exempt_premium_uf": 658.70,
               "exempt_rate_permille": 2.21, "commission_pct": 10,
               "facets": [{"group": "coverage", "label": "Incendio", "verbatim": "Incendio"}]}
    _fake_read(monkeypatch, partial)
    comparison_id, entry_id, _doc = _comparison_with_column(
        client, headers_a, db, world, tmp_path, account_case, name="r2-old.txt"
    )
    entry = next(
        e for e in client.get(f"{API}/comparisons/{comparison_id}", headers=headers_a).json()["entries"]
        if e["id"] == entry_id
    )
    assert entry["status"] == "ok"
    assert set(entry["missing_core"]) == {"insurer_name", "total_premium_uf", "net_premium_uf"}
    assert entry["can_retry"] is True

    # The retry re-reads into the same column; a complete read no longer offers it.
    _fake_read(monkeypatch, SOUTHBRIDGE)
    resp = client.post(f"{API}/comparisons/{comparison_id}/entries/{entry_id}/retry", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["entry"]["can_retry"] is False and resp.json()["entry"]["missing_core"] == []


def test_the_recommendation_never_gets_premiums_to_compute_a_missing_total():
    readings = [
        {"comparison_source_id": 1, "insurer": "Southbridge",
         "money_core": {"exempt_premium_uf": 658.70, "exempt_rate_permille": 2.21}},
        {"comparison_source_id": 2, "insurer": "HDI",
         "money_core": {"total_premium_uf": 1541.24, "net_premium_uf": 1441.91}},
    ]
    messages = ai_service._recommend_messages([], readings)
    money = json.loads(messages[1]["content"])["money"]
    assert money[0]["total_premium_read"] is False
    assert "exempt_premium_uf" not in money[0]["money_core"]
    assert money[0]["money_core"] == {"exempt_rate_permille": 2.21}
    assert money[1]["total_premium_read"] is True and money[1]["money_core"]["net_premium_uf"] == 1441.91
    assert "NUNCA la calcules" in messages[0]["content"]

    rec = standardize._note_missing_totals(
        {"recommended_comparison_source_id": 2, "rationale": "HDI.", "caveats": ["Otra."]}, readings
    )
    assert rec["caveats"][0].startswith("No se leyó la prima total de Southbridge")
    assert rec["caveats"][1] == "Otra."


def test_source_unused_import_guard():
    """Keeps the ComparisonSource import honest for readers of this file."""
    assert ComparisonSource.__tablename__ == "comparison_source"
