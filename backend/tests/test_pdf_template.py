"""Unit tests for the expediente HTML composer.

Pure string-builder tests — they run WITHOUT Chromium/Playwright (the real
render is covered separately behind a marker). They assert the human-validate
markers (`—` / "falta"), the broker identity, that fonts are embedded (no CDN
dependency when the bundled woff2 files are present), and — load-bearing — that
NO http(s) img src ever leaks into the HTML (an external img src hangs
``networkidle`` when the render host is offline).
"""
from __future__ import annotations

import re

from app.services.pdf_templates import (
    MISSING_OPTIONAL,
    MISSING_REQUIRED,
    _fonts_available,
    render_expediente_html,
)

# A 1x1 transparent PNG data URI (valid `data:` logo).
_LOGO_DATAURI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_BRANDING = {
    "radal_wordmark": "Radal.",
    "broker_logo_datauri": _LOGO_DATAURI,
    "broker_name": "Ossa Covarrubias Corredores de Seguros",
    "broker_rut": "76543210-K",
    "cmf_code": "R-123",
    "cover_meta": [
        {"label": "Razón social", "value": "Viña Santa Alicia S.A.", "required": True},
        {"label": "RUT", "value": "96789012-3", "required": True},
        {"label": "Ramo", "value": "Incendio y Sismo", "required": True},
        {"label": "Vigencia", "value": "2026-2027", "required": True},
    ],
}


def _sample_sections() -> list[dict]:
    return [
        {
            "kind": "fields",
            "heading": "Identidad del asegurado",
            "fields": [
                {"label": "Nombre", "value": "Viña Santa Alicia S.A.", "required": True},
                {"label": "Giro", "value": "", "required": True},  # -> falta
                {"label": "Sitio web", "value": None, "required": False},  # -> —
                {"label": "Empleados", "value": 42, "hint": "Dato declarado"},
                {"label": "Opera 24/7", "value": True},
            ],
        },
        {
            "kind": "table",
            "heading": "Bienes asegurados",
            "columns": [
                {"key": "item", "label": "Ítem", "required": True},
                {"key": "value_uf", "label": "Valor UF", "required": True},
            ],
            "rows": [
                {"item": "Edificio", "value_uf": "12.000"},
                {"item": "Maquinaria", "value_uf": ""},  # -> falta in a cell
            ],
        },
        {
            "kind": "table",
            "heading": "Siniestros (vacío)",
            "columns": [{"key": "year", "label": "Año"}],
            "rows": [],
            "required": True,  # empty required table -> falta marker
        },
        {"kind": "stats", "heading": "Resumen", "items": [
            {"label": "Suma asegurada", "value": "12.000 UF"},
            {"label": "Deducible", "value": "", "required": False},
        ]},
        {"kind": "callout", "heading": "Nota", "text": "Pendiente validación humana."},
    ]


def _render() -> str:
    return render_expediente_html(
        title="Expediente de Antecedentes — Viña Santa Alicia",
        branding=_BRANDING,
        sections=_sample_sections(),
    )


def test_renders_missing_markers():
    html = _render()
    # Optional-empty renders a muted em-dash; required-empty renders a "falta" chip.
    assert MISSING_OPTIONAL in html
    assert MISSING_REQUIRED in html
    assert "falta" in html
    assert "—" in html


def test_required_empty_field_shows_falta():
    html = _render()
    # The "Giro" required-empty field must carry the falta badge somewhere after it.
    idx = html.find("Giro")
    assert idx != -1
    assert "falta" in html[idx : idx + 400]


def test_optional_empty_field_shows_dash_not_falta():
    # A lone optional-empty field renders the muted dash, never a falta chip.
    html = render_expediente_html(
        title="T",
        branding=_BRANDING,
        sections=[{"kind": "fields", "heading": "S", "fields": [
            {"label": "Sitio web", "value": None, "required": False},
        ]}],
    )
    idx = html.find("Sitio web")
    segment = html[idx : idx + 200]
    assert MISSING_OPTIONAL in segment
    assert "falta" not in segment


def test_broker_identity_present():
    html = _render()
    assert "Ossa Covarrubias Corredores de Seguros" in html
    assert "76543210-K" in html
    assert "R-123" in html
    # Radal logo lockup on the cover: isotype + "Radal." with accented dot.
    assert "cover-word" in html
    assert 'class="dot"' in html
    assert "cover-iso" in html  # the Radal isotype is embedded on the cover


def test_cover_meta_present():
    html = _render()
    assert "Viña Santa Alicia S.A." in html
    assert "Razón social" in html
    assert "2026-2027" in html


def test_no_http_img_src_leaks():
    html = _render()
    # No <img> may point at an http(s) URL — only data: URIs are allowed.
    img_srcs = re.findall(r'<img[^>]*\ssrc="([^"]*)"', html, flags=re.IGNORECASE)
    assert img_srcs, "expected the broker logo <img> to be emitted"
    for src in img_srcs:
        assert src.startswith("data:"), f"non-data img src leaked: {src[:40]}"
    # Belt-and-suspenders: no bare http(s):// image-ish reference in any src.
    assert not re.search(r'src="https?://', html, flags=re.IGNORECASE)


def test_boolean_and_list_values_render():
    html = _render()
    assert "Sí" in html  # boolean True -> "Sí"


def test_broker_logo_fallback_when_no_datauri():
    branding = dict(_BRANDING)
    branding["broker_logo_datauri"] = None
    html = render_expediente_html(title="T", branding=branding, sections=[])
    # The Radal marks are always embedded (cover + header isotype), but NO broker
    # logo img is emitted; the broker name renders in the personalised font instead.
    assert 'class="rh-broker-logo"' not in html
    assert "rh-broker-word" in html
    assert "Ossa Covarrubias Corredores de Seguros" in html


def test_non_data_logo_uri_is_rejected():
    # A stray http logo URI must be dropped, never emitted as an img src.
    branding = dict(_BRANDING)
    branding["broker_logo_datauri"] = "https://evil.example/logo.png"
    html = render_expediente_html(title="T", branding=branding, sections=[])
    assert "evil.example" not in html
    assert 'class="rh-broker-logo"' not in html  # the stray http logo is dropped


def test_fonts_embedded_no_cdn_when_available():
    html = _render()
    if _fonts_available():
        # Bundled woff2 -> embedded as data: URIs, no Google Fonts @import.
        assert "data:font/woff2;base64," in html
        assert "fonts.googleapis.com" not in html
    else:  # documented offline-unsafe fallback
        assert "fonts.googleapis.com" in html


def test_page_css_verbatim():
    html = _render()
    assert "size:A4" in html
    assert "297mm" in html  # the cover sheet is a full A4 page
    assert "print-color-adjust:exact" in html


def test_sections_are_numbered():
    html = _render()
    # First numbered (non-callout) section gets seclabel "01".
    assert 'class="seclabel">01<' in html


def _matrix_section() -> dict:
    """A ubicaciones × materias matrix table with money columns + a totals row."""
    return {
        "kind": "table",
        "heading": "Ubicaciones y materias aseguradas",
        "totals": True,
        "columns": [
            {"key": "location", "label": "Ubicación"},
            {"key": "building_uf", "label": "Edificio", "money": True},
            {"key": "stock_uf", "label": "Existencias", "money": True},
        ],
        "rows": [
            {"location": "Casa matriz", "building_uf": "UF 10.000", "stock_uf": "5.000"},
            {"location": "Bodega", "building_uf": "2.920", "stock_uf": ""},
        ],
    }


def test_totals_row_sums_money_columns():
    html = render_expediente_html(
        title="T", branding=_BRANDING, sections=[_matrix_section()]
    )
    # A totals row is emitted with the "Total" label.
    assert 'class="total"' in html
    assert "Total" in html
    # Edificio: 10.000 + 2.920 = 12.920 (Chilean thousands grouping).
    assert "12.920" in html
    # Existencias: 5.000 + (empty) = 5.000.
    assert "5.000" in html


def test_money_columns_right_align():
    html = render_expediente_html(
        title="T", branding=_BRANDING, sections=[_matrix_section()]
    )
    # Money header + cells carry the right-align class.
    assert 'class="num"' in html


def test_no_totals_row_without_flag():
    section = _matrix_section()
    section["totals"] = False
    html = render_expediente_html(title="T", branding=_BRANDING, sections=[section])
    assert 'class="total"' not in html
