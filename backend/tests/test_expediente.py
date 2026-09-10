"""The EXPEDIENTE COMPLETO — ``GET /case-files/{id}/expediente`` (+ its PDF).

Four things are asserted, in the order they matter:

* a FRESH account aggregates cleanly with everything pending and a **Spanish**
  reason on every absent section ("Pronto, al cerrar X");
* a MID-JOURNEY account reports ``complete`` milestones whose ``completed_at``
  is the REAL ``case_file_stage_event`` timestamp (never inferred, never "now");
* another broker's case id is a **404**, by this path as by every other;
* the PDF endpoint streams bytes starting with ``%PDF`` — the Playwright render
  is monkeypatched to a bytes stub exactly as the other PDF suites do, so the
  suite never launches Chromium and nothing is persisted.

The router is mounted here rather than in ``app/main.py`` (the module is wired
by its owner): the fixture is a no-op once the include line lands.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.config import settings
from app.models.case_file import CaseFileStageEvent
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseSection, CaseStage
from tests.conftest import API, make_case_file


# --- The router is wired by its owner; mount it here if it is not yet ---------

@pytest.fixture(scope="module", autouse=True)
def _mounted() -> None:
    from app.api.routers import expediente as expediente_router
    from app.main import app as fastapi_app

    path = f"{settings.API_V1_PREFIX}/case-files/{{case_id}}/expediente"
    if not any(getattr(route, "path", None) == path for route in fastapi_app.routes):
        fastapi_app.include_router(
            expediente_router.router, prefix=settings.API_V1_PREFIX
        )


@pytest.fixture()
def stub_render(monkeypatch):
    """Replace the async Playwright render with a bytes stub (never Chromium)."""

    async def _fake_render(html: str) -> bytes:
        assert html.startswith("<!doctype html>")
        assert "Expediente completo" in html
        return b"%PDF-1.4 stub"

    import app.services.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)


def _account(db, tenant, **overrides):
    case = make_case_file(
        db,
        tenant,
        period_start=date(2026, 1, 1),
        period_end=date(2027, 1, 1),
        period_label="2026-2027",
        **overrides,
    )
    db.commit()
    db.refresh(case)
    return case


def _ramo(db, tenant, name: str = "Incendio y Sismo (TRBF)"):
    from app.models.line_record_schema import LineRecordSchema

    row = LineRecordSchema(
        broker_id=tenant.broker_id,
        insurance_line_id=tenant.line.id,
        name=name,
        version=1,
        is_active=True,
        recommended_files=[
            {
                "key": "loss_history",
                "label": "Siniestralidad",
                "category": "loss_history",
                "format": "pdf",
                "required": True,
            },
            {
                "key": "insured_values_schedule",
                "label": "Montos asegurados",
                "category": "insured_values_schedule",
                "format": "pdf/word",
                "required": False,
            },
        ],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _intake_document(db, tenant, case, category: DocumentCategory, name: str):
    doc = Document(
        broker_id=tenant.broker_id,
        entity_type="case_file",
        entity_id=case.id,
        case_file_id=case.id,
        section=CaseSection.ROOT_PROSPECT,
        s3_key=f"documents/case_file/{case.id}/{name}",
        bucket="radal-test-bucket",
        original_name=name,
        mime_type="application/pdf",
        size_bytes=4096,
        category=category,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _stage_event(db, case, *, to_stage, from_stage, occurred_at):
    event = CaseFileStageEvent(
        broker_id=case.broker_id,
        case_file_id=case.id,
        from_stage=from_stage,
        to_stage=to_stage,
        occurred_at=occurred_at,
    )
    db.add(event)
    db.commit()
    return event


# --- A fresh account: everything pending, every reason in Spanish ------------

def test_fresh_account_aggregates_with_spanish_pending_reasons(
    client, headers_a, world, db
):
    _ramo(db, world.a)
    case = _account(db, world.a)

    resp = client.get(f"{API}/case-files/{case.id}/expediente", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["case_file"]["id"] == case.id
    assert body["case_file"]["stage"] == "intake"
    assert body["case_file"]["period_label"] == "2026-2027"
    # Rule 1 of the group layer: a folder that never moved is still editable.
    assert body["case_file"]["period_locked"] is False
    assert body["case_file"]["ramo_name"] == "Incendio y Sismo (TRBF)"
    assert body["generated_at"]

    # The contratante is the first member and is flagged as such.
    assert body["clients"], body["clients"]
    assert body["clients"][0]["id"] == world.a.client.id
    assert body["clients"][0]["is_contratante"] is True
    assert body["clients"][0]["rut"] == "77111111-4"

    # The five milestones, in order, none complete.
    keys = [step["key"] for step in body["journey"]]
    assert keys == [
        "antecedentes",
        "technical_basis",
        "comparison",
        "proposal",
        "policies",
    ]
    assert [step["label"] for step in body["journey"]][:2] == [
        "Antecedentes",
        "Bases Técnicas",
    ]
    assert body["journey"][0]["status"] == "in_progress"
    assert all(step["completed_at"] is None for step in body["journey"])
    assert all(step["pending_reason"] for step in body["journey"])
    # A pending milestone explains itself with the "Pronto, al cerrar X" line.
    assert body["journey"][4]["pending_reason"].startswith("Pronto, al cerrar")
    # Every milestone carries a factual Spanish summary line.
    assert body["journey"][0]["summary"] == "0 de 2 archivos recomendados"

    # The recommended-file slots are surfaced empty, with the required one named.
    antecedentes = body["antecedentes"]
    assert antecedentes["recommended_total"] == 2
    assert antecedentes["recommended_filled"] == 0
    assert antecedentes["complete"] is False
    assert antecedentes["missing_required"] == ["Siniestralidad"]
    assert [slot["filled"] for slot in antecedentes["slots"]] == [False, False]

    # Nothing downstream exists yet — and each says so in Spanish.
    assert body["comparison"] is None
    assert body["comparison_pending_reason"] == "Pronto, al cerrar Bases Técnicas"
    assert body["proposal"] is None
    assert body["proposal_pending_reason"] == "Pronto, al cerrar Comparación"
    assert body["policies"] == []
    assert body["policies_pending_reason"] == "Pronto, al cerrar Propuesta"
    assert body["money"] is None
    assert body["money_pending_reason"] == "Pronto, al cerrar Comparación"
    assert body["documents"] == []
    assert body["documents_count"] == 0


def test_antecedentes_slots_fill_and_never_expose_the_s3_key(
    client, headers_a, world, db
):
    _ramo(db, world.a)
    case = _account(db, world.a)
    _intake_document(db, world.a, case, DocumentCategory.LOSS_HISTORY, "siniestralidad.pdf")
    # A cotización belongs to Comparación, never to Antecedentes (v9 error #4).
    _intake_document(
        db, world.a, case, DocumentCategory.INSURER_QUOTATION, "hdi-cotizacion.pdf"
    )

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    antecedentes = body["antecedentes"]
    assert antecedentes["recommended_filled"] == 1
    assert antecedentes["documents_count"] == 1  # the cotización is scoped out
    assert antecedentes["free_uploads_count"] == 0
    assert antecedentes["missing_required"] == []
    filled = [slot for slot in antecedentes["slots"] if slot["filled"]]
    assert filled[0]["label"] == "Siniestralidad"
    assert filled[0]["documents_count"] == 1

    # The document index lists BOTH files (it is the folder index) — but rule 8
    # holds: no S3 key, no bucket, ever.
    assert body["documents_count"] == 2
    names = {doc["filename"] for doc in body["documents"]}
    assert names == {"siniestralidad.pdf", "hdi-cotizacion.pdf"}
    for doc in body["documents"]:
        assert "s3_key" not in doc and "bucket" not in doc
        assert doc["size_bytes"] == 4096
        assert doc["category_label"]


# --- Mid-journey: completion dates are REAL stage events ---------------------

def test_mid_journey_completion_dates_come_from_stage_events(
    client, headers_a, world, db
):
    _ramo(db, world.a)
    case = _account(db, world.a)

    first = datetime(2026, 2, 3, 12, 0, tzinfo=timezone.utc)
    second = first + timedelta(days=7)
    _stage_event(
        db, case, from_stage=CaseStage.INTAKE, to_stage=CaseStage.TECHNICAL_BASIS,
        occurred_at=first,
    )
    _stage_event(
        db, case, from_stage=CaseStage.TECHNICAL_BASIS,
        to_stage=CaseStage.MARKET_SUBMISSION, occurred_at=second,
    )
    case.stage = CaseStage.MARKET_SUBMISSION
    db.commit()

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    steps = {step["key"]: step for step in body["journey"]}
    assert steps["antecedentes"]["status"] == "complete"
    assert steps["antecedentes"]["completed_at"].startswith("2026-02-03")
    assert steps["antecedentes"]["pending_reason"] is None
    assert steps["technical_basis"]["status"] == "complete"
    assert steps["technical_basis"]["completed_at"].startswith("2026-02-10")
    assert steps["comparison"]["status"] == "in_progress"
    assert steps["comparison"]["completed_at"] is None
    assert steps["proposal"]["status"] == "pending"
    assert steps["policies"]["status"] == "pending"

    # A folder that has moved past intake has its vigencia frozen (rule 1).
    assert body["case_file"]["period_locked"] is True

    # The milestone in progress reports the machine's OWN Spanish guard reason,
    # not a re-invented one; the ones still out of reach say "Pronto, al cerrar X".
    from app.services import case_files as machine

    expected = machine.guard_reason(db, case, CaseStage.COMPARISON)
    assert expected and steps["comparison"]["pending_reason"] == expected
    assert steps["proposal"]["pending_reason"] == "Pronto, al cerrar Comparación"
    assert steps["policies"]["pending_reason"] == "Pronto, al cerrar Propuesta"

    # The bitácora carries the two stage moves, newest first.
    kinds = [entry["kind"] for entry in body["activity"]]
    assert kinds.count("stage") == 2
    assert body["activity"][0]["to_stage"] == "market_submission"


def test_policies_close_the_journey_and_drive_the_money_core(
    client, headers_a, world, db
):
    from tests.conftest import make_policy

    case = _account(db, world.a, stage=CaseStage.ACTIVE)
    policy = make_policy(db, world.a, world.insurer, case_file_id=case.id)
    policy.taxable_premium_uf = Decimal("100.0000")
    policy.exempt_premium_uf = Decimal("0.0000")
    policy.net_premium_uf = Decimal("100.0000")
    policy.vat_uf = Decimal("19.0000")
    policy.total_premium_uf = Decimal("119.0000")
    db.commit()

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    steps = {step["key"]: step for step in body["journey"]}
    assert steps["policies"]["status"] == "complete"
    assert steps["policies"]["summary"] == "1 póliza(s) emitida(s)"
    assert len(body["policies"]) == 1
    assert body["policies"][0]["insurer_name"] == "HDI Seguros S.A."

    money = body["money"]
    assert money["source"] == "policy"
    assert money["currency"] == "UF"
    # vat = 0.19 x taxable (never on net); total = net + vat.
    assert Decimal(money["vat_uf"]) == Decimal("19.0000")
    assert Decimal(money["total_premium_uf"]) == Decimal("119.0000")
    assert money["warnings"] == []
    assert body["money_pending_reason"] is None


# --- Tenant isolation + shape guards -----------------------------------------

def test_foreign_case_is_404_not_403(client, headers_b, world, db):
    case = _account(db, world.a)
    resp = client.get(f"{API}/case-files/{case.id}/expediente", headers=headers_b)
    assert resp.status_code == 404, resp.text


def test_post_sale_case_is_422_not_an_account(client, headers_a, world, db):
    from app.models.enums import CaseFileKind

    case = _account(
        db, world.a, kind=CaseFileKind.CLAIM, stage=CaseStage.CLAIM_REPORTED, n=2
    )
    resp = client.get(f"{API}/case-files/{case.id}/expediente", headers=headers_a)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "not_an_account"


# --- The PDF: streamed bytes, nothing persisted -------------------------------

def test_expediente_pdf_streams_bytes(client, headers_a, world, db, stub_render):
    _ramo(db, world.a)
    case = _account(db, world.a)

    before = db.query(Document).count()
    resp = client.get(f"{API}/case-files/{case.id}/expediente/pdf", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert "attachment" in resp.headers["content-disposition"]
    # Rendered on demand: no pack row, no document row, nothing to go stale.
    assert db.query(Document).count() == before


def test_expediente_pdf_foreign_is_404(client, headers_b, world, db, stub_render):
    case = _account(db, world.a)
    resp = client.get(f"{API}/case-files/{case.id}/expediente/pdf", headers=headers_b)
    assert resp.status_code == 404, resp.text


# --- Comparación + propuesta: the middle of the journey, fully shaped ---------

def _aligned_comparison(db, tenant, case, source_id: int):
    from app.models.comparison import (
        Comparison,
        ComparisonEntry,
        ComparisonSource,
        ComparisonStatus,
    )

    source = ComparisonSource(id=source_id, broker_id=tenant.broker_id, facets=[])
    db.add(source)
    db.flush()
    comparison = Comparison(
        broker_id=tenant.broker_id,
        case_file_id=case.id,
        status=ComparisonStatus.ALIGNED,
        canonical_version=2,
        dictionary=[{"key": "incendio", "group": "coverage", "label": "Incendio"}],
        aligned_matrix={
            "columns": [
                {
                    "comparison_source_id": source.id,
                    "proposal_id": None,
                    "is_wrong_file": False,
                }
            ],
            "dimensions": [
                {
                    "key": "incendio",
                    "group": "coverage",
                    "label": "Incendio",
                    "scope": "common",
                    "cells": [
                        {
                            "comparison_source_id": source.id,
                            "present": True,
                            "value": "10.000",
                            "verbatim": "Ampara incendio",
                        }
                    ],
                }
            ],
            "recommendation": {
                "recommended_comparison_source_id": source.id,
                "rationale": "Mejor cobertura por prima.",
                "caveats": ["Deducible mayor"],
            },
        },
    )
    comparison.entries.append(
        ComparisonEntry(comparison_source_id=source.id, is_recommended=True, sort_order=0)
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return comparison


def _broker_proposal(db, tenant, case, insurer):
    from app.models.broker_proposal import BrokerProposal, BrokerProposalStatus

    bp = BrokerProposal(
        broker_id=tenant.broker_id,
        case_file_id=case.id,
        status=BrokerProposalStatus.RATIFIED,
        is_ratified=True,
        content_hash="a" * 64,
        payload={
            "core": {
                "insurer_id": insurer.id,
                "insured_name": "Asegurado Alfa S.A.",
                "insured_rut": "77111111-4",
                "coverage_start": "2026-01-01",
                "coverage_end": "2027-01-01",
                "taxable_premium_uf": "100",
                "exempt_premium_uf": "0",
                "net_premium_uf": "100",
                "vat_uf": "19",
                "total_premium_uf": "119",
                "commission_pct": "15",
            }
        },
    )
    db.add(bp)
    db.commit()
    db.refresh(bp)
    return bp


def test_comparison_and_propuesta_are_summarised(client, headers_a, world, db):
    case = _account(db, world.a, stage=CaseStage.PROPOSAL_ISSUED)
    _aligned_comparison(db, world.a, case, source_id=901)
    _broker_proposal(db, world.a, case, world.insurer)

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    comparison = body["comparison"]
    assert comparison["status"] == "aligned"
    assert comparison["entry_count"] == 1
    assert comparison["canonical_version"] == 2
    assert comparison["columns"][0]["recommended"] is True
    assert comparison["recommendation"]["pick_label"] == comparison["columns"][0]["label"]
    assert comparison["recommendation"]["rationale"] == "Mejor cobertura por prima."
    assert comparison["recommendation"]["caveats"] == ["Deducible mayor"]
    assert body["comparison_pending_reason"] is None

    proposal = body["proposal"]
    assert proposal["is_ratified"] is True
    assert proposal["insurer_name"] == "HDI Seguros S.A."
    assert proposal["insured_rut"] == "77111111-4"
    assert Decimal(proposal["total_premium_uf"]) == Decimal("119")
    assert body["proposal_pending_reason"] is None

    # With no póliza yet, the money core falls back to the propuesta and the
    # invariants still hold (vat = 0.19 x taxable, total = net + vat).
    money = body["money"]
    assert money["source"] == "broker_proposal"
    assert Decimal(money["vat_uf"]) == Decimal("19")
    assert money["warnings"] == []

    steps = {step["key"]: step for step in body["journey"]}
    assert steps["comparison"]["status"] == "complete"
    # Complete without a stage event: the date is a FACT or it is null.
    assert steps["comparison"]["completed_at"] is None
    assert steps["proposal"]["summary"].startswith("Propuesta ratificada · HDI")


def test_expediente_pdf_prints_the_comparison_matrix(
    client, headers_a, world, db, monkeypatch
):
    case = _account(db, world.a, stage=CaseStage.PROPOSAL_ISSUED)
    _aligned_comparison(db, world.a, case, source_id=902)
    _broker_proposal(db, world.a, case, world.insurer)

    captured: dict[str, str] = {}

    async def _fake_render(html: str) -> bytes:
        captured["html"] = html
        return b"%PDF-1.4 stub"

    import app.services.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)

    resp = client.get(f"{API}/case-files/{case.id}/expediente/pdf", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF")

    html = captured["html"]
    for heading in (
        "Identificación de la cuenta",
        "Empresas de la cuenta",
        "Journey del expediente",
        "Antecedentes",
        "Comparación",
        "Propuesta",
        "Pólizas",
        "Índice de documentos",
        "Resumen económico",
    ):
        assert heading in html, heading
    assert "Recomendación" in html
    assert "Mejor cobertura por prima." in html
    # A section with nothing in it prints the Spanish reason, never a blank.
    assert "Pronto, al cerrar Propuesta" in html


# --- The legacy folder: complete journey, no v9 artifacts --------------------

def test_completed_legacy_folder_never_contradicts_its_own_journey(
    client, headers_a, world, db
):
    """A folder that ran its whole course before the v9 comparison/propuesta
    tables existed closes every milestone yet owns neither artifact.

    The block reason must then say the artifact was never REGISTERED — never a
    prerequisite ("Pronto, al cerrar Bases Técnicas") that the journey printed
    one card above already shows as complete. Both cannot be true at once.
    """
    case = _account(db, world.a, stage=CaseStage.ACTIVE)
    _stage_event(
        db, case, from_stage=CaseStage.INTAKE, to_stage=CaseStage.TECHNICAL_BASIS,
        occurred_at=datetime(2025, 9, 19, 10, 0, tzinfo=timezone.utc),
    )
    _stage_event(
        db, case, from_stage=CaseStage.TECHNICAL_BASIS, to_stage=CaseStage.COMPARISON,
        occurred_at=datetime(2025, 10, 8, 10, 0, tzinfo=timezone.utc),
    )
    _stage_event(
        db, case, from_stage=CaseStage.POLICY_ISSUED, to_stage=CaseStage.ACTIVE,
        occurred_at=datetime(2025, 10, 20, 10, 0, tzinfo=timezone.utc),
    )

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    steps = {step["key"]: step for step in body["journey"]}
    assert all(step["status"] == "complete" for step in body["journey"])
    assert steps["antecedentes"]["completed_at"].startswith("2025-09-19")
    assert steps["policies"]["completed_at"].startswith("2025-10-20")

    # No comparison / propuesta / póliza row exists on this folder …
    assert body["comparison"] is None
    assert body["proposal"] is None
    assert body["policies"] == []
    assert body["money"] is None

    # … and NOT ONE block explains itself with a prerequisite the journey above
    # already shows as closed.
    reasons = {
        key: body[f"{key}_pending_reason"]
        for key in ("antecedentes", "comparison", "proposal", "policies", "money")
    }
    for key, reason in reasons.items():
        assert reason, key
        assert not reason.startswith("Pronto, al cerrar"), (key, reason)

    assert reasons["comparison"] == "La cuenta avanzó sin registrar una comparación en Radal."
    assert reasons["proposal"] == "La cuenta avanzó sin registrar una propuesta en Radal."
    assert reasons["policies"] == "La cuenta avanzó sin registrar pólizas en Radal."
    assert reasons["money"] == "La cuenta avanzó sin registrar el desglose de prima en Radal."
    assert reasons["antecedentes"] == "La cuenta avanzó sin registrar antecedentes en Radal."


def test_unreached_milestones_still_say_pronto_al_cerrar(client, headers_a, world, db):
    """The other half of the rule: a folder that has NOT reached the milestone
    keeps the prerequisite line — the fix must not flatten both cases into one."""
    case = _account(db, world.a, stage=CaseStage.INTAKE)
    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    assert body["comparison_pending_reason"] == "Pronto, al cerrar Bases Técnicas"
    assert body["proposal_pending_reason"] == "Pronto, al cerrar Comparación"
    assert body["policies_pending_reason"] == "Pronto, al cerrar Propuesta"
    assert body["money_pending_reason"] == "Pronto, al cerrar Comparación"
    assert body["antecedentes_pending_reason"] == "Aún no se ha cargado ningún antecedente"


# --- The other direction: artifacts that RUN AHEAD of the stage machine -------

def test_artifacts_ahead_of_the_stage_never_contradict_their_own_summary(
    client, headers_a, world, db
):
    """The mirror of the legacy folder: the comparison / propuesta / póliza rows
    exist while the folder's STAGE is still back at Bases Técnicas.

    The step's ``summary`` then counts real work, so its ``pending_reason`` must
    not read "Pronto, al cerrar Bases Técnicas" — printed together the two lines
    say the work both exists and has not started.
    """
    from tests.conftest import make_policy

    case = _account(db, world.a, stage=CaseStage.TECHNICAL_BASIS)
    _aligned_comparison(db, world.a, case, source_id=911)
    _broker_proposal(db, world.a, case, world.insurer)
    make_policy(db, world.a, world.insurer, case_file_id=case.id)
    db.commit()

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    steps = {step["key"]: step for step in body["journey"]}
    assert steps["comparison"]["status"] == "pending"
    assert steps["proposal"]["status"] == "pending"
    assert steps["policies"]["status"] == "pending"

    for key in ("comparison", "proposal", "policies"):
        summary = steps[key]["summary"]
        reason = steps[key]["pending_reason"]
        assert summary and reason, key
        # The summary counts real work, so the reason may not deny it exists.
        assert not reason.startswith("Pronto, al cerrar"), (key, reason)
        assert "ya está" in reason or "ya están" in reason, (key, reason)
        assert reason.endswith("avance desde Bases técnicas.")

    assert steps["comparison"]["pending_reason"] == (
        "La comparación ya está cargada; la etapa se cierra cuando el "
        "expediente avance desde Bases técnicas."
    )
    assert steps["proposal"]["pending_reason"] == (
        "La propuesta ya está registrada; la etapa se cierra cuando el "
        "expediente avance desde Bases técnicas."
    )
    assert steps["policies"]["pending_reason"] == (
        "La póliza ya está emitida; la etapa se cierra cuando el "
        "expediente avance desde Bases técnicas."
    )


def test_journey_summary_formats_money_the_spanish_way(client, headers_a, world, db):
    """``UF 1120.1300`` is a raw Decimal, not copy: the journey summary must use
    the same Chilean grouping every other money string on the page uses."""
    case = _account(db, world.a, stage=CaseStage.PROPOSAL_ISSUED)
    bp = _broker_proposal(db, world.a, case, world.insurer)
    payload = dict(bp.payload)
    payload["core"] = {**payload["core"], "total_premium_uf": "1120.1300"}
    bp.payload = payload
    db.commit()

    body = client.get(
        f"{API}/case-files/{case.id}/expediente", headers=headers_a
    ).json()

    steps = {step["key"]: step for step in body["journey"]}
    assert "UF 1.120,13" in steps["proposal"]["summary"]
    assert "1120.1300" not in steps["proposal"]["summary"]
