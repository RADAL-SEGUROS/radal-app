"""PDF rendering for the COMPARISON and PROPUESTA expedients.

Two layers, both hermetic — never Chromium:

* the HTML builders (``render_comparison_html`` / ``render_propuesta_html``) are
  PURE string builders, unit-tested here with plain dicts (no DB, no browser);
* the two ``/pdf`` endpoints render through the Playwright pipeline, which is
  monkeypatched to a bytes stub EXACTLY as the antecedentes PDF tests do, so the
  suite never launches a browser. They assert the download payload + that
  ``pdf_document_id`` is linked back onto the row, plus the not-aligned 422 and
  the tenant-404.
"""
from __future__ import annotations

import re

import pytest

from app.core.config import settings
from app.models.broker_proposal import BrokerProposal, BrokerProposalStatus
from app.models.comparison import Comparison, ComparisonStatus
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseStage
from app.services.pdf_templates import (
    MISSING_OPTIONAL,
    render_comparison_html,
    render_propuesta_html,
)
from tests.conftest import API, make_case_file


_BRANDING = {
    "radal_wordmark": "Radal.",
    "broker_logo_datauri": None,
    "broker_name": "Ossa Covarrubias Corredores de Seguros",
    "broker_rut": "76543210-K",
    "cmf_code": "R-123",
    "cover_meta": [
        {"label": "Razón social", "value": "Viña Santa Alicia S.A.", "required": True},
    ],
    "running_title": "Comparación",
    "eyebrow": "Comparación de ofertas",
}


# --- Pure builder: comparison ------------------------------------------------

def _comparison_data() -> dict:
    return {
        "columns": [
            {"label": "HDI Seguros", "recommended": True, "wrong_file": False},
            {"label": "Consorcio", "recommended": False, "wrong_file": False},
        ],
        "premium_rows": [
            {"label": "Prima total (UF)", "values": ["119", "131"]},
            {"label": "Prima neta (UF)", "values": ["100", "110"]},
        ],
        "common_dimensions": [
            {
                "label": "Incendio",
                "group": "coverage",
                "cells": [
                    {"present": True, "value": "10.000", "verbatim": "Ampara incendio hasta UF 10.000"},
                    {"present": True, "value": "9.500", "verbatim": None},
                ],
            },
            {
                "label": "Sismo",
                "group": "deductible",
                "cells": [
                    {"present": True, "value": "2%", "verbatim": "Deducible 2% del valor"},
                    {"present": False, "value": None, "verbatim": None},
                ],
            },
        ],
        "extra_dimensions": [
            {
                "label": "Robo",
                "group": "coverage",
                "cells": [
                    {"present": True, "value": "500", "verbatim": None},
                    None,  # this insurer never states it
                ],
            },
        ],
        "recommendation": {
            "pick_label": "HDI Seguros",
            "rationale": "Mejor cobertura de sismo por prima.",
            "caveats": ["Verificar deducible de robo."],
        },
    }


def test_comparison_html_is_wellformed_and_carries_key_data():
    html = render_comparison_html(
        title="Comparación — Viña Santa Alicia", branding=_BRANDING, data=_comparison_data()
    )
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    # Cover + running frame reused from the shared shell.
    assert "cover-word" in html
    assert "Ossa Covarrubias Corredores de Seguros" in html
    # The insurer columns, premium highlight and dimension values are all present.
    assert "HDI Seguros" in html
    assert "Consorcio" in html
    assert "Prima total (UF)" in html
    assert "119" in html and "131" in html
    assert "Incendio" in html and "Sismo" in html and "Robo" in html
    # A verbatim wording rides compactly under the value.
    assert "Ampara incendio hasta UF 10.000" in html
    assert "verb" in html
    # An excluded dimension renders the excluido marker; an absent cell a dash.
    assert "excluido" in html
    assert MISSING_OPTIONAL in html
    # The AI recommendation block is prominent.
    assert 'class="rec"' in html
    assert "Recomendación" in html
    assert "Mejor cobertura de sismo por prima." in html
    assert "Verificar deducible de robo." in html


def test_comparison_html_emits_no_http_img_src():
    html = render_comparison_html(title="T", branding=_BRANDING, data=_comparison_data())
    for src in re.findall(r'<img[^>]*\ssrc="([^"]*)"', html, flags=re.IGNORECASE):
        assert src.startswith("data:")
    assert not re.search(r'src="https?://', html, flags=re.IGNORECASE)


# --- Pure builder: propuesta -------------------------------------------------

def _propuesta_data() -> dict:
    return {
        "identity_rows": [
            {"label": "Aseguradora", "value": "HDI Seguros", "required": True},
            {"label": "Asegurado", "value": "Viña Santa Alicia S.A.", "required": True},
            {"label": "Vigencia", "value": "2026-01-01 — 2027-01-01", "required": True},
        ],
        "money_rows": [
            {"label": "Prima neta", "value": "100"},
            {"label": "IVA", "value": "19"},
            {"label": "Prima total", "value": "119"},
        ],
        "winning_rows": [
            {"label": "Prima total (UF)", "value": "119"},
        ],
        "additional_groups": [
            {
                "group": "coverage",
                "items": [
                    {"label": "Incendio", "value": "10.000", "verbatim": "Ampara incendio"},
                ],
            },
        ],
        "content_hash": "abc123def456",
        "ratified": True,
    }


def test_propuesta_html_is_wellformed_and_carries_core():
    html = render_propuesta_html(
        title="Propuesta — Viña Santa Alicia", branding=_BRANDING, data=_propuesta_data()
    )
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    # Core identity + money table + winning quote + additional tail all present.
    assert "HDI Seguros" in html
    assert "Viña Santa Alicia S.A." in html
    assert "Prima total" in html and "119" in html
    assert "Incendio" in html and "Ampara incendio" in html
    # The content hash and ratified state are rendered in the state callout.
    assert "abc123def456" in html
    assert "Ratificada" in html


def test_propuesta_html_borrador_state_when_not_ratified():
    data = _propuesta_data()
    data["ratified"] = False
    html = render_propuesta_html(title="T", branding=_BRANDING, data=data)
    assert "Borrador" in html


def test_propuesta_html_emits_no_http_img_src():
    html = render_propuesta_html(title="T", branding=_BRANDING, data=_propuesta_data())
    assert not re.search(r'src="https?://', html, flags=re.IGNORECASE)


# --- Endpoint tests (render stubbed, no Chromium) ----------------------------

@pytest.fixture()
def local_media(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))


@pytest.fixture()
def stub_render(monkeypatch):
    """Replace the async Playwright render with a bytes stub (never Chromium)."""
    async def _fake_render(html: str) -> bytes:
        assert html.startswith("<!doctype html>")
        return b"%PDF-1.4 stub"

    import app.services.pdf as pdf_module

    monkeypatch.setattr(pdf_module, "render_html_to_pdf", _fake_render)


@pytest.fixture()
def account_case(db, world):
    case = make_case_file(db, world.a, stage=CaseStage.COMPARISON)
    db.commit()
    db.refresh(case)
    return case


def _aligned_comparison(db, world, account_case) -> Comparison:
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        status=ComparisonStatus.ALIGNED,
        canonical_version=1,
        dictionary=[{"key": "incendio", "group": "coverage", "label": "Incendio"}],
        aligned_matrix={
            "columns": [
                {"comparison_source_id": 1, "proposal_id": None, "is_wrong_file": False},
                {"comparison_source_id": 2, "proposal_id": None, "is_wrong_file": False},
            ],
            "dimensions": [
                {
                    "key": "incendio",
                    "group": "coverage",
                    "label": "Incendio",
                    "scope": "common",
                    "cells": [
                        {"comparison_source_id": 1, "present": True, "value": "10.000", "verbatim": "Ampara incendio"},
                        {"comparison_source_id": 2, "present": False, "value": None, "verbatim": None},
                    ],
                },
            ],
            "recommendation": {
                "recommended_comparison_source_id": 1,
                "rationale": "La mejor cobertura.",
                "caveats": [],
            },
        },
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return comparison


def test_comparison_pdf_generates_and_links_document(
    client, headers_a, world, db, account_case, local_media, stub_render
):
    comparison = _aligned_comparison(db, world, account_case)
    resp = client.get(f"{API}/comparisons/{comparison.id}/pdf", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mime_type"] == "application/pdf"
    assert body["url"]
    assert body["s3_key"]

    db.refresh(comparison)
    assert comparison.pdf_document_id is not None
    doc = db.get(Document, comparison.pdf_document_id)
    assert doc is not None
    assert doc.category == DocumentCategory.COMPARISON_PACK
    assert doc.broker_id == world.a.broker_id

    # Regenerating reuses the same fixed-route document (overwrite, not a dupe).
    again = client.get(f"{API}/comparisons/{comparison.id}/pdf", headers=headers_a)
    assert again.status_code == 200
    db.refresh(comparison)
    assert comparison.pdf_document_id == doc.id


def test_comparison_pdf_refuses_unaligned(
    client, headers_a, world, db, account_case, local_media, stub_render
):
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        status=ComparisonStatus.DRAFT,
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)

    resp = client.get(f"{API}/comparisons/{comparison.id}/pdf", headers=headers_a)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "comparison_not_aligned"


def test_comparison_pdf_foreign_is_404(
    client, headers_b, world, db, account_case, local_media, stub_render
):
    comparison = _aligned_comparison(db, world, account_case)
    resp = client.get(f"{API}/comparisons/{comparison.id}/pdf", headers=headers_b)
    assert resp.status_code == 404


def _minted_broker_proposal(db, world, account_case) -> BrokerProposal:
    comparison = Comparison(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        status=ComparisonStatus.ALIGNED,
    )
    db.add(comparison)
    db.flush()
    bp = BrokerProposal(
        broker_id=world.a.broker_id,
        case_file_id=account_case.id,
        comparison_id=comparison.id,
        status=BrokerProposalStatus.DRAFT,
        content_hash="deadbeefcafe0001",
        payload={
            "core": {
                "insurer_id": world.insurer.id,
                "insured_name": "Viña Santa Alicia S.A.",
                "insured_rut": "96789012-3",
                "coverage_start": "2026-01-01",
                "coverage_end": "2027-01-01",
                "net_premium_uf": "100",
                "vat_uf": "19",
                "total_premium_uf": "119",
            },
            "additional": [
                {"group": "coverage", "label": "Incendio", "value": "10.000", "verbatim": "Ampara incendio"},
            ],
            "comparison_snapshot": {
                "winning_proposal": {"id": 1, "total_premium_uf": "119", "net_premium_uf": "100"},
            },
        },
    )
    db.add(bp)
    db.commit()
    db.refresh(bp)
    return bp


def test_propuesta_pdf_generates_and_links_document(
    client, headers_a, world, db, account_case, local_media, stub_render
):
    bp = _minted_broker_proposal(db, world, account_case)
    resp = client.get(f"{API}/broker-proposals/{bp.id}/pdf", headers=headers_a)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mime_type"] == "application/pdf"
    assert body["url"]

    db.refresh(bp)
    assert bp.pdf_document_id is not None
    doc = db.get(Document, bp.pdf_document_id)
    assert doc is not None
    assert doc.category == DocumentCategory.PROPOSAL_PACK
    assert doc.broker_id == world.a.broker_id


def test_propuesta_pdf_foreign_is_404(
    client, headers_b, world, db, account_case, local_media, stub_render
):
    bp = _minted_broker_proposal(db, world, account_case)
    resp = client.get(f"{API}/broker-proposals/{bp.id}/pdf", headers=headers_b)
    assert resp.status_code == 404
