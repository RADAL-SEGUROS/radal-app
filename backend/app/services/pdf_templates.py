"""Generic branded-expediente HTML composer for the HTML→PDF pipeline.

:func:`render_expediente_html` returns a single self-contained HTML string —
fonts (Inter body, Space Grotesk for the Radal wordmark, Sora for a broker-name
wordmark fallback) and logos embedded as ``data:`` URIs, a trimmed Radal
"Signal" CSS inlined in the head, **no runtime network dependency** — that
:func:`app.services.pdf.render_html_to_pdf` renders to A4 PDF bytes. It is generic
on purpose: antecedentes is the first caller (cover + insured identity + a
per-ramo schema), and later expedientes reuse the same composer.

Branding (the rule the user set 2026-09-07):

- The **cover** carries the **Radal logo** (isotype + "Radal." wordmark lockup)
  — Radal owns the cover.
- Every **content page header** carries the **broker on the left** (its uploaded
  logo, or — when none — the broker company name set in a personalised display
  font) and the **Radal isotype on the right**. Co-branding on the running frame,
  not on the cover.
- A broker without an uploaded logo therefore never shows a monogram avatar; it
  shows its name as a typographic wordmark.

Pagination: content **flows** across as many A4 pages as needed (no fixed-height
clipping). The running header repeats on every content page via ``position:fixed``
inside the ``@page`` top margin; the footer (broker legal name + page n/N) is a
``@page`` margin box. The cover is page one, full-bleed, and paints over the
running frame (``@page:first`` clears its margins).

Every empty value renders a marker: a muted em-dash ``—`` when optional, a
``.badge.warn`` "falta" chip when required — so the human-validate step is visible.
Logos/marks are ``data:`` URIs only; an http(s) img src is never emitted (it would
hang ``networkidle`` when the render host is offline).
"""
from __future__ import annotations

import base64
import html
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.schemas.extraction.common import parse_uf

__all__ = [
    "render_expediente_html",
    "render_expediente_completo_html",
    "render_comparison_html",
    "render_propuesta_html",
    "MISSING_OPTIONAL",
    "MISSING_REQUIRED",
]

_ASSETS_DIR = Path(__file__).with_name("pdf_assets")

# Rendered markers (module-level so tests can assert against them).
MISSING_OPTIONAL = '<span class="muted">—</span>'
MISSING_REQUIRED = '<span class="badge warn">falta</span>'


# ---------------------------------------------------------------------------
# Fonts + marks — embedded as data: URIs (offline-safe).
# ---------------------------------------------------------------------------

_FONT_FILES = [
    # (family, weight, filename)
    ("Inter", "100 900", "inter-latin.woff2"),
    ("Inter", "100 900", "inter-latin-ext.woff2"),
    ("Space Grotesk", "700", "space-grotesk-700.woff2"),
    ("Sora", "700", "sora-600.woff2"),
]


def _font_face_block() -> str:
    faces: list[str] = []
    for family, weight, fname in _FONT_FILES:
        try:
            raw = (_ASSETS_DIR / fname).read_bytes()
        except OSError:
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        faces.append(
            f"@font-face{{font-family:'{family}';font-style:normal;"
            f"font-weight:{weight};font-display:block;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
    if faces:
        return "\n".join(faces)
    # Documented offline-unsafe fallback: only when the bundled woff2 are absent.
    return (
        "@import url('https://fonts.googleapis.com/css2?"
        "family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@700"
        "&family=Sora:wght@700&display=block');"
    )


def _fonts_available() -> bool:
    """True when the bundled Inter woff2 are present (so fonts embed, no CDN)."""
    return (_ASSETS_DIR / "inter-latin.woff2").exists()


def _mark_datauri(name: str) -> str | None:
    """A bundled Radal mark SVG (``radal-mark-teal.svg`` …) as a data: URI."""
    try:
        raw = (_ASSETS_DIR / name).read_bytes()
    except OSError:
        return None
    return f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"


# ---------------------------------------------------------------------------
# CSS — trimmed Radal "Signal" subset, inlined. Hairline borders, one accent.
# ---------------------------------------------------------------------------

def _css_str(value: str) -> str:
    """Escape a Python string for use inside a CSS ``content:"…"`` value."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _stylesheet(footer_left: str) -> str:
    foot = _css_str(footer_left)
    return (
        _font_face_block()
        + """
:root{
  --accent:#0e7c70; --accent-2:#e6f4f1; --accent-ink:#0a5c53;
  --paper:#ffffff; --ground:#f7f8f8;
  --ink:#1a2422; --ink-2:#41504c; --muted:#8a968f;
  --line:#e5e7eb; --line-2:#eef1f0;
  --warn:#b45309; --warn-bg:#fef3e2;
  --font-body:"Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-word:"Space Grotesk", var(--font-body);
  --font-broker:"Sora", var(--font-body);
}
*{box-sizing:border-box;margin:0;padding:0;}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
body{font-family:var(--font-body);color:var(--ink);background:var(--paper);
  font-size:11px;line-height:1.5;font-feature-settings:"tnum" 1,"cv05" 1;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;}

/* Content pages reserve a top band for the fixed running header and a bottom
   band for the margin-box footer. The cover (first page) is full-bleed. */
@page{
  size:A4; margin:13mm 15mm 15mm;
  @bottom-left{content:\"""" + foot + """\";font-family:"Inter",sans-serif;
    font-size:8px;color:#8a968f;}
  @bottom-right{content:"Página " counter(page) " / " counter(pages);
    font-family:"Inter",sans-serif;font-size:8px;color:#8a968f;}
}
@page:first{ margin:0; @bottom-left{content:none;} @bottom-right{content:none;} }

/* Running header — a <thead> inside the content <table>. Chromium repeats a
   table-header-group at the top of every page the table spans, which is the
   reliable way to get a per-page header with images (position:fixed is not
   dependable in headless print). The cover is a separate block BEFORE the table,
   so the header only appears on the content pages, never on the cover. */
.report{width:100%;border-collapse:collapse;}
.report thead{display:table-header-group;}
.report>thead>tr>td,.report>tbody>tr>td{padding:0;}
.runhead{display:flex;align-items:flex-end;justify-content:space-between;
  height:15mm;padding-bottom:3.5mm;margin-bottom:6mm;
  border-bottom:1px solid var(--line);}
.rh-broker-logo{max-height:8.5mm;max-width:52mm;object-fit:contain;object-position:left bottom;display:block;}
.rh-broker-word{font-family:var(--font-broker);font-weight:700;font-size:12.5px;
  letter-spacing:0.005em;color:var(--ink);line-height:1;}
.rh-right{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:8.5px;
  letter-spacing:0.12em;text-transform:uppercase;}
.rh-iso{width:6.5mm;height:6.5mm;display:block;}

/* Cover — page one, full-bleed, paints over the running frame. */
.cover{position:relative;z-index:10;background:var(--paper);
  height:297mm;overflow:hidden;break-after:page;
  display:flex;flex-direction:column;padding:30mm 22mm 26mm;}
.cover-logo{display:flex;align-items:center;gap:13px;}
.cover-iso{width:16mm;height:16mm;display:block;}
.cover-word{font-family:var(--font-word);font-weight:700;font-size:40px;
  letter-spacing:-0.01em;color:var(--ink);line-height:1;}
.cover-word .dot{color:var(--accent);}
.cover-tag{margin-top:6px;font-size:10px;color:var(--muted);letter-spacing:0.02em;}
.cover-body{margin-top:auto;}
.eyebrow{display:inline-block;font-size:10px;font-weight:600;letter-spacing:0.14em;
  text-transform:uppercase;color:var(--accent-ink);background:var(--accent-2);
  padding:5px 11px;border-radius:6px;}
.display{margin-top:16px;font-size:36px;line-height:1.1;font-weight:700;
  letter-spacing:-0.02em;color:var(--ink);max-width:150mm;}
.cover-meta{margin-top:30px;display:grid;grid-template-columns:1fr 1fr;
  gap:0;border-top:1px solid var(--line);}
.cover-meta .cm{padding:13px 14px 13px 0;border-bottom:1px solid var(--line);}
.cover-meta .cm:nth-child(odd){padding-left:0;}
.cover-meta .cm:nth-child(even){padding-left:18px;border-left:1px solid var(--line);}
.cover-meta .cm .k{font-size:9px;letter-spacing:0.1em;text-transform:uppercase;
  color:var(--muted);font-weight:600;}
.cover-meta .cm .v{margin-top:3px;font-size:13px;color:var(--ink);font-weight:500;}
.cover-emisor{margin-top:26px;font-size:10px;color:var(--ink-2);}
.cover-emisor b{color:var(--ink);font-weight:600;}

/* Flowing content. */
.content{padding-top:4mm;}

/* Sections — flow across pages; the head stays with its content, rows never
   split, table headers repeat on break. */
.section{margin-bottom:18px;break-inside:avoid;}
.section.tbl{break-inside:auto;}
.sec-head{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;
  padding-bottom:7px;border-bottom:1px solid var(--line);break-after:avoid;}
.seclabel{font-size:10px;font-weight:700;color:var(--accent);
  letter-spacing:0.04em;min-width:20px;}
.sec-title{font-size:14px;font-weight:600;color:var(--ink);letter-spacing:-0.01em;}

/* Field grid */
.fields{display:grid;grid-template-columns:1fr 1fr;gap:0;}
.fields.one{grid-template-columns:1fr;}
.field{padding:9px 16px 9px 0;border-bottom:1px solid var(--line-2);break-inside:avoid;}
.field .k{font-size:9px;letter-spacing:0.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;}
.field .v{margin-top:2px;font-size:11.5px;color:var(--ink);}
.field .hint{margin-top:2px;font-size:9px;color:var(--muted);font-style:italic;}

/* Tables */
table.data{width:100%;border-collapse:collapse;font-size:10.5px;margin-top:4px;}
table.data thead{display:table-header-group;}
table.data tr{break-inside:avoid;}
table.data thead th{text-align:left;font-size:9px;letter-spacing:0.06em;
  text-transform:uppercase;color:var(--muted);font-weight:600;
  padding:7px 10px;border-bottom:1px solid var(--line);}
table.data tbody td{padding:8px 10px;border-bottom:1px solid var(--line-2);
  color:var(--ink);vertical-align:top;}
table.data tbody tr:last-child td{border-bottom:1px solid var(--line);}
/* Money / number columns right-align (header + body + total). */
table.data th.num,table.data td.num{text-align:right;
  font-feature-settings:"tnum" 1;white-space:nowrap;}
/* Totals row: a solid top rule, the label + column sums in medium weight. */
table.data tbody tr.total td{border-top:1.5px solid var(--line);
  border-bottom:none;padding-top:9px;font-weight:600;color:var(--ink);}
.table-empty{padding:10px 0;font-size:10.5px;}

/* Stats */
.stats{display:flex;flex-wrap:wrap;gap:0;border:1px solid var(--line);
  border-radius:8px;overflow:hidden;}
.stats .stat{flex:1 1 25%;min-width:120px;padding:12px 14px;
  border-right:1px solid var(--line);}
.stats .stat:last-child{border-right:none;}
.stats .stat .k{font-size:9px;letter-spacing:0.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;}
.stats .stat .v{margin-top:4px;font-size:16px;font-weight:600;color:var(--ink);}

/* Callout */
.callout{border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:6px;padding:12px 14px;background:var(--ground);break-inside:avoid;}
.callout .ch{font-size:11px;font-weight:600;color:var(--ink);margin-bottom:4px;}
.callout .ct{font-size:10.5px;color:var(--ink-2);}

/* Markers */
.muted{color:var(--muted);}
.badge{display:inline-block;font-size:9px;font-weight:600;letter-spacing:0.04em;
  padding:2px 7px;border-radius:999px;text-transform:uppercase;}
.badge.warn{color:var(--warn);background:var(--warn-bg);}
.badge.ok{color:var(--accent-ink);background:var(--accent-2);}
.badge.no{color:var(--warn);background:var(--warn-bg);}

/* Comparison matrix — one dimension per row, one insurer per column. */
table.data th.dimcol,table.data td.dimcol{text-align:left;min-width:38mm;}
table.data th.rec,table.data td.rec-col{color:var(--accent-ink);}
.dim-k{font-size:10.5px;color:var(--ink);font-weight:500;}
.dim-g{font-size:8px;letter-spacing:0.06em;text-transform:uppercase;
  color:var(--muted);margin-top:2px;}
.cell-val{font-size:10.5px;color:var(--ink);}
.verb{margin-top:3px;font-size:8.5px;color:var(--muted);line-height:1.35;font-style:italic;}

/* AI recommendation — the prominent pick block. */
.rec{border:1px solid var(--accent);border-left:3px solid var(--accent);
  border-radius:6px;padding:13px 15px;background:var(--accent-2);
  break-inside:avoid;margin-bottom:18px;}
.rec .rh2{font-size:9px;font-weight:700;letter-spacing:0.12em;
  text-transform:uppercase;color:var(--accent-ink);}
.rec .rpick{margin-top:5px;font-size:15px;font-weight:600;color:var(--ink);}
.rec .rrat{margin-top:6px;font-size:10.5px;color:var(--ink-2);}
.rec .rcav{margin-top:7px;font-size:9.5px;color:var(--warn);}
"""
    )


# ---------------------------------------------------------------------------
# Value rendering
# ---------------------------------------------------------------------------

def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _fmt_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "Sí" if value else "No"
    return str(value)


def _render_value(value: Any, *, required: bool = False) -> str:
    """A validated value, or the appropriate missing marker."""
    if _is_empty(value):
        return MISSING_REQUIRED if required else MISSING_OPTIONAL
    if isinstance(value, (list, tuple)):
        parts = [html.escape(_fmt_scalar(v)) for v in value if not _is_empty(v)]
        return ", ".join(parts) if parts else MISSING_OPTIONAL
    return html.escape(_fmt_scalar(value))


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_fields(section: dict) -> str:
    fields = section.get("fields") or []
    one = bool(section.get("full_width"))
    rows: list[str] = []
    for f in fields:
        hint = f.get("hint") or f.get("description")
        hint_html = f'<div class="hint">{_esc(hint)}</div>' if hint else ""
        rows.append(
            '<div class="field">'
            f'<div class="k">{_esc(f.get("label"))}</div>'
            f'<div class="v">{_render_value(f.get("value"), required=bool(f.get("required")))}</div>'
            f"{hint_html}"
            "</div>"
        )
    cls = "fields one" if one else "fields"
    return f'<div class="{cls}">{"".join(rows)}</div>'


def _sum_money(rows: list, key: str) -> Decimal | None:
    """Lenient sum of one money column across ``rows`` — Chilean or plain numbers
    (``"UF 17.920"`` → 17920). Non-numeric cells are skipped; an all-empty column
    yields None (rendered as a muted dash in the total row)."""
    total: Decimal | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_uf(row.get(key))
        if parsed is None:
            continue
        total = parsed if total is None else total + parsed
    return total


def _fmt_money(value: Decimal) -> str:
    """Chilean thousands grouping, trailing ``.0`` trimmed (17920 → "17.920")."""
    value = value.normalize()
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(value)
    frac = value - whole
    grouped = f"{whole:,}".replace(",", ".")
    if frac != 0:
        dec = format(frac, "f").split(".")[1].rstrip("0")
        if dec:
            grouped = f"{grouped},{dec}"
    return f"{sign}{grouped}"


def _render_table(section: dict) -> str:
    columns = section.get("columns") or []
    data_rows = section.get("rows") or []
    if not data_rows:
        empty = MISSING_REQUIRED if section.get("required") else MISSING_OPTIONAL
        return f'<div class="table-empty">{empty}</div>'
    head = "".join(
        f'<th class="num">{_esc(c.get("label"))}</th>'
        if c.get("money")
        else f"<th>{_esc(c.get('label'))}</th>"
        for c in columns
    )
    body_rows: list[str] = []
    for row in data_rows:
        cells: list[str] = []
        for c in columns:
            key = c.get("key")
            required = bool(c.get("required"))
            cls = ' class="num"' if c.get("money") else ""
            value = row.get(key) if isinstance(row, dict) else None
            cells.append(f"<td{cls}>{_render_value(value, required=required)}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    total_html = ""
    if section.get("totals"):
        cells: list[str] = []
        for i, c in enumerate(columns):
            if i == 0:
                cells.append("<td>Total</td>")
            elif c.get("money"):
                summed = _sum_money(data_rows, c.get("key"))
                inner = _fmt_money(summed) if summed is not None else MISSING_OPTIONAL
                cells.append(f'<td class="num">{inner}</td>')
            else:
                cells.append("<td></td>")
        total_html = f'<tr class="total">{"".join(cells)}</tr>'

    return (
        '<table class="data"><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body_rows)}{total_html}</tbody></table>"
    )


def _render_stats(section: dict) -> str:
    items = section.get("items") or []
    cells = "".join(
        '<div class="stat">'
        f'<div class="k">{_esc(i.get("label"))}</div>'
        f'<div class="v">{_render_value(i.get("value"), required=bool(i.get("required")))}</div>'
        "</div>"
        for i in items
    )
    return f'<div class="stats">{cells}</div>'


def _render_callout(section: dict) -> str:
    heading = section.get("heading")
    text = section.get("text")
    ch = f'<div class="ch">{_esc(heading)}</div>' if heading else ""
    return f'<div class="callout">{ch}<div class="ct">{_esc(text)}</div></div>'


def _render_section(section: dict, *, number: int | None) -> str:
    kind = section.get("kind", "fields")
    if kind == "callout":
        return f'<div class="section">{_render_callout(section)}</div>'
    if kind == "table":
        inner = _render_table(section)
        cls = "section tbl"
    elif kind == "stats":
        inner = _render_stats(section)
        cls = "section"
    else:
        inner = _render_fields(section)
        cls = "section"
    label = f'<span class="seclabel">{number:02d}</span>' if number is not None else ""
    heading = section.get("heading")
    head = (
        f'<div class="sec-head">{label}'
        f'<span class="sec-title">{_esc(heading)}</span></div>'
        if heading or label
        else ""
    )
    return f'<div class="{cls}">{head}{inner}</div>'


# ---------------------------------------------------------------------------
# Cover + running header
# ---------------------------------------------------------------------------

def _cover_logo() -> str:
    """The Radal logo lockup: teal isotype + 'Radal.' wordmark."""
    iso = _mark_datauri("radal-mark-teal.svg")
    iso_html = (
        f'<img class="cover-iso" src="{html.escape(iso, quote=True)}" alt="" />' if iso else ""
    )
    return (
        '<div class="cover-logo">'
        f"{iso_html}"
        '<span class="cover-word">Radal<span class="dot">.</span></span>'
        "</div>"
    )


def _broker_head(branding: dict) -> str:
    """The broker on the running header's left: uploaded logo, or — when none —
    the broker company name in a personalised display font (never a monogram)."""
    logo = branding.get("broker_logo_datauri")
    if logo and isinstance(logo, str) and logo.startswith("data:"):
        return f'<img class="rh-broker-logo" src="{html.escape(logo, quote=True)}" alt="" />'
    name = branding.get("broker_name")
    if name:
        return f'<span class="rh-broker-word">{_esc(name)}</span>'
    # Last resort: the Radal wordmark, so the header is never empty.
    return '<span class="rh-broker-word">Radal.</span>'


def _run_head(branding: dict) -> str:
    right_label = branding.get("running_title") or "Antecedentes"
    iso = _mark_datauri("radal-mark-teal.svg")
    iso_html = f'<img class="rh-iso" src="{html.escape(iso, quote=True)}" alt="" />' if iso else ""
    return (
        '<div class="runhead">'
        f'<div class="rh-left">{_broker_head(branding)}</div>'
        f'<div class="rh-right"><span>{_esc(right_label)}</span>{iso_html}</div>'
        "</div>"
    )


def _cover(title: str, branding: dict) -> str:
    eyebrow = branding.get("eyebrow") or "Expediente · Antecedentes"
    meta = branding.get("cover_meta") or []
    meta_html = "".join(
        '<div class="cm">'
        f'<div class="k">{_esc(m.get("label"))}</div>'
        f'<div class="v">{_render_value(m.get("value"), required=bool(m.get("required")))}</div>'
        "</div>"
        for m in meta
    )
    meta_block = f'<div class="cover-meta">{meta_html}</div>' if meta_html else ""

    emisor_bits: list[str] = []
    if branding.get("broker_name"):
        emisor_bits.append(f'<b>{_esc(branding["broker_name"])}</b>')
    if branding.get("broker_rut"):
        emisor_bits.append(f'RUT {_esc(branding["broker_rut"])}')
    if branding.get("cmf_code"):
        emisor_bits.append(f'CMF {_esc(branding["cmf_code"])}')
    emisor = (
        f'<div class="cover-emisor">Emisor · {" · ".join(emisor_bits)}</div>'
        if emisor_bits
        else ""
    )
    tagline = branding.get("tagline") or "El expediente del riesgo"

    return (
        '<section class="cover">'
        f"{_cover_logo()}"
        f'<div class="cover-tag">{_esc(tagline)}</div>'
        '<div class="cover-body">'
        f'<span class="eyebrow">{_esc(eyebrow)}</span>'
        f'<h1 class="display">{_esc(title)}</h1>'
        f"{meta_block}{emisor}"
        "</div>"
        "</section>"
    )


# ---------------------------------------------------------------------------
# Public composer
# ---------------------------------------------------------------------------

def render_expediente_html(*, title: str, branding: dict, sections: list[dict]) -> str:
    """Compose the full self-contained expediente HTML string.

    Args:
        title: the display title shown on the cover.
        branding: cover + running-frame identity. Keys: ``broker_logo_datauri``
            (``data:`` URI or None → styled broker-name wordmark), ``broker_name``,
            ``broker_rut``, ``cmf_code``; optional ``eyebrow`` (cover eyebrow),
            ``tagline`` (cover sub-line), ``running_title`` (header right label,
            default "Antecedentes") and ``cover_meta`` (list of
            ``{label, value, required?}`` — razón social, RUT, ramo, vigencia).
            ``radal_wordmark`` is accepted for back-compat but the Radal lockup is
            always "Radal.".
        sections: ordered Section dicts. A Section is
            ``{"kind": "fields"|"table"|"callout"|"stats", "heading": str, ...}``.

    Content flows across as many A4 pages as needed; the running header repeats on
    every page and the footer carries the broker legal name + page n/N. Every empty
    value renders a marker (``—`` optional, ``falta`` required). No http(s) img src.
    """
    sections = sections or []

    numbered = 0
    body_parts: list[str] = []
    for sec in sections:
        num: int | None = None
        # A section flagged ``unnumbered`` is a sub-part of the section above it
        # (e.g. the siniestros table under "Siniestralidad") — it keeps its
        # heading but takes no numeral.
        if sec.get("kind", "fields") != "callout" and not sec.get("unnumbered"):
            numbered += 1
            num = numbered
        body_parts.append(_render_section(sec, number=num))

    return _wrap_document(title=title, branding=branding, body_html="".join(body_parts))


def _wrap_document(*, title: str, branding: dict, body_html: str) -> str:
    """The shared self-contained document shell: cover + repeating running header
    + flowing content, with the footer (broker legal name + page n/N) as a
    ``@page`` margin box. Every branded expediente PDF (antecedentes, comparison,
    propuesta) is wrapped by this one composer, so cover/header/footer stay
    identical across artifacts."""
    footer_left = str(branding.get("broker_name") or "Radal.")
    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8"/>'
        f"<style>{_stylesheet(footer_left)}</style>"
        f"<title>{_esc(title)}</title></head><body>"
        f"{_cover(title, branding)}"
        '<table class="report"><thead><tr><td>'
        f"{_run_head(branding)}"
        '</td></tr></thead><tbody><tr><td>'
        f'<main class="content">{body_html}</main>'
        "</td></tr></tbody></table>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Comparison composer (the aligned proposal-comparison matrix)
# ---------------------------------------------------------------------------

def _recommendation_block(rec: dict | None) -> str:
    """The AI recommendation: named pick + rationale + caveats, rendered
    prominently at the head of the comparison."""
    rec = rec or {}
    pick = rec.get("pick_label")
    rationale = rec.get("rationale")
    caveats = rec.get("caveats") or []
    if _is_empty(pick) and _is_empty(rationale) and not caveats:
        return ""
    pick_html = (
        f'<div class="rpick">{_esc(pick)}</div>'
        if not _is_empty(pick)
        else '<div class="rpick">Sin recomendación</div>'
    )
    rat_html = (
        f'<div class="rrat">{_esc(rationale)}</div>' if not _is_empty(rationale) else ""
    )
    cav_html = ""
    if caveats:
        joined = " · ".join(_esc(c) for c in caveats if not _is_empty(c))
        if joined:
            cav_html = f'<div class="rcav">Advertencias · {joined}</div>'
    return (
        '<div class="rec"><div class="rh2">Recomendación</div>'
        f"{pick_html}{rat_html}{cav_html}</div>"
    )


def _comparison_column_headers(columns: list[dict]) -> str:
    ths = ['<th class="dimcol">Dimensión</th>']
    for col in columns:
        label = col.get("label") or "—"
        rec = " rec" if col.get("recommended") else ""
        suffix = " · archivo inválido" if col.get("wrong_file") else ""
        ths.append(f'<th class="num{rec}">{_esc(label)}{_esc(suffix)}</th>')
    return "".join(ths)


def _premium_cell(value: Any) -> str:
    if _is_empty(value):
        return f'<td class="num">{MISSING_OPTIONAL}</td>'
    return f'<td class="num">{_esc(value)}</td>'


def _dimension_cell(cell: dict | None) -> str:
    """One insurer's cell for a standardized dimension: present/excluded/absent
    marker + the value, with the verbatim wording compactly below it."""
    if cell is None:
        return f"<td>{MISSING_OPTIONAL}</td>"
    present = cell.get("present")
    value = cell.get("value")
    verbatim = cell.get("verbatim")
    inner: list[str] = []
    if present is False:
        inner.append('<span class="badge no">excluido</span>')
    elif present is True and _is_empty(value):
        inner.append('<span class="badge ok">incluido</span>')
    if not _is_empty(value):
        inner.append(f'<span class="cell-val">{_esc(value)}</span>')
    if not inner:
        inner.append(MISSING_OPTIONAL)
    body = " ".join(inner)
    if not _is_empty(verbatim):
        body += f'<div class="verb">{_esc(verbatim)}</div>'
    return f"<td>{body}</td>"


def _render_premium_matrix(columns: list[dict], premium_rows: list[dict]) -> str:
    """The premium/rate highlight row-group: one metric per row, one insurer per
    column, values right-aligned."""
    if not premium_rows:
        return f'<div class="table-empty">{MISSING_OPTIONAL}</div>'
    head = _comparison_column_headers(columns)
    body: list[str] = []
    for row in premium_rows:
        cells = [f'<td class="dimcol"><span class="dim-k">{_esc(row.get("label"))}</span></td>']
        for value in row.get("values") or []:
            cells.append(_premium_cell(value))
        body.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<table class="data"><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _render_dimension_matrix(columns: list[dict], dimensions: list[dict]) -> str:
    """A standardized-dimensions matrix: one dimension per row (label + group),
    one insurer per column, each cell value + marker + verbatim."""
    if not dimensions:
        return f'<div class="table-empty">{MISSING_OPTIONAL}</div>'
    head = _comparison_column_headers(columns)
    body: list[str] = []
    for dim in dimensions:
        group = dim.get("group")
        group_html = (
            f'<div class="dim-g">{_esc(group)}</div>' if not _is_empty(group) else ""
        )
        cells = [
            '<td class="dimcol">'
            f'<span class="dim-k">{_esc(dim.get("label") or dim.get("key"))}</span>'
            f"{group_html}</td>"
        ]
        for cell in dim.get("cells") or []:
            cells.append(_dimension_cell(cell))
        body.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<table class="data"><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _matrix_section(heading: str, number: int, inner: str) -> str:
    label = f'<span class="seclabel">{number:02d}</span>'
    head = (
        f'<div class="sec-head">{label}'
        f'<span class="sec-title">{_esc(heading)}</span></div>'
    )
    return f'<div class="section tbl">{head}{inner}</div>'


def render_comparison_html(*, title: str, branding: dict, data: dict) -> str:
    """Compose the aligned comparison PDF HTML string (a PURE string builder).

    Args:
        title: the display title shown on the cover.
        branding: the same cover + running-frame identity dict
            :func:`render_expediente_html` takes.
        data: the rendered comparison, a plain dict (no DB):
            ``columns`` — ordered list of ``{label, recommended?, wrong_file?}``;
            ``premium_rows`` — ordered ``{label, values:[per-column]}`` for the
            premium/rate highlight; ``common_dimensions`` / ``extra_dimensions`` —
            standardized dimension rows ``{label, key?, group?, cells:[per-column
            {present, value, verbatim} | None]}``; ``recommendation`` —
            ``{pick_label, rationale, caveats:[...]}``.

    Content flows across as many A4 pages as needed; no http(s) img src is emitted.
    """
    data = data or {}
    columns = data.get("columns") or []
    parts: list[str] = [_recommendation_block(data.get("recommendation"))]

    number = 0
    number += 1
    parts.append(
        _matrix_section(
            "Primas y tasa", number, _render_premium_matrix(columns, data.get("premium_rows") or [])
        )
    )
    common = data.get("common_dimensions") or []
    if common:
        number += 1
        parts.append(
            _matrix_section("Coberturas comunes", number, _render_dimension_matrix(columns, common))
        )
    extras = data.get("extra_dimensions") or []
    if extras:
        number += 1
        parts.append(
            _matrix_section("Coberturas adicionales", number, _render_dimension_matrix(columns, extras))
        )

    return _wrap_document(title=title, branding=branding, body_html="".join(parts))


# ---------------------------------------------------------------------------
# Propuesta composer (the outbound broker→insurer document)
# ---------------------------------------------------------------------------

def _state_callout(content_hash: str | None, ratified: bool) -> str:
    state = "Ratificada" if ratified else "Borrador"
    hash_html = (
        f'<div class="ct">Hash de contenido · {_esc(content_hash)}</div>'
        if not _is_empty(content_hash)
        else ""
    )
    return (
        f'<div class="callout"><div class="ch">Estado · {state}</div>{hash_html}</div>'
    )


def render_propuesta_html(*, title: str, branding: dict, data: dict) -> str:
    """Compose the outbound propuesta PDF HTML string (a PURE string builder).

    Args:
        title: the display title shown on the cover.
        branding: the same cover + running-frame identity dict.
        data: the rendered propuesta, a plain dict (no DB):
            ``identity_rows`` — ``{label, value, required?}`` (insurer + insured +
            vigencia); ``money_rows`` — the premium core ``{label, value}`` money
            table; ``winning_rows`` — the winning-quote summary ``{label, value}``;
            ``additional_groups`` — ``{group, items:[{label, value, verbatim}]}``;
            ``content_hash`` and ``ratified`` (rendered in the state callout, and
            reflected in the running header via ``branding``).
    """
    data = data or {}
    parts: list[str] = [
        _state_callout(data.get("content_hash"), bool(data.get("ratified")))
    ]

    number = 0
    identity = data.get("identity_rows") or []
    number += 1
    parts.append(
        _render_section(
            {
                "kind": "fields",
                "heading": "Identificación",
                "fields": [
                    {
                        "label": r.get("label"),
                        "value": r.get("value"),
                        "required": bool(r.get("required")),
                    }
                    for r in identity
                ],
            },
            number=number,
        )
    )

    money = data.get("money_rows") or []
    number += 1
    parts.append(
        _render_section(
            {
                "kind": "table",
                "heading": "Núcleo de prima (UF)",
                "columns": [
                    {"key": "label", "label": "Concepto"},
                    {"key": "value", "label": "UF", "money": True},
                ],
                "rows": [
                    {"label": r.get("label"), "value": r.get("value")} for r in money
                ],
            },
            number=number,
        )
    )

    winning = data.get("winning_rows") or []
    if winning:
        number += 1
        parts.append(
            _render_section(
                {
                    "kind": "fields",
                    "heading": "Cotización adjudicada",
                    "fields": [
                        {"label": r.get("label"), "value": r.get("value")}
                        for r in winning
                    ],
                },
                number=number,
            )
        )

    for group in data.get("additional_groups") or []:
        items = group.get("items") or []
        if not items:
            continue
        number += 1
        parts.append(
            _render_section(
                {
                    "kind": "table",
                    "heading": group.get("group") or "Adicional",
                    "columns": [
                        {"key": "label", "label": "Detalle"},
                        {"key": "value", "label": "Valor"},
                        {"key": "verbatim", "label": "Textual"},
                    ],
                    "rows": items,
                },
                number=number,
            )
        )

    return _wrap_document(title=title, branding=branding, body_html="".join(parts))


# ---------------------------------------------------------------------------
# Expediente completo composer (the whole grupo-cuenta on one document)
# ---------------------------------------------------------------------------

def _pending_callout(reason: str | None) -> str:
    """What prints where a section does not exist yet: the Spanish reason
    ("Pronto, al cerrar Comparación"), never a blank. The section heading is
    already above it, so the callout only carries the reason."""
    return _render_callout(
        {
            "heading": "Pendiente",
            "text": reason or "Pronto, en cuanto avance el expediente",
        }
    )


def _journey_table(rows: list[dict]) -> str:
    """The journey with REAL completion dates — the heart of this document."""
    return _render_table(
        {
            "kind": "table",
            "columns": [
                {"key": "milestone", "label": "Hito"},
                {"key": "state", "label": "Estado"},
                {"key": "completed_at", "label": "Completado"},
                {"key": "summary", "label": "Resumen"},
                {"key": "reason", "label": "Pendiente"},
            ],
            "rows": rows,
        }
    )


def render_expediente_completo_html(*, title: str, branding: dict, data: dict) -> str:
    """Compose the EXPEDIENTE COMPLETO PDF HTML string (a PURE string builder).

    The super overview of one grupo-cuenta: cover → identidad → empresas →
    journey (with completion dates) → antecedentes → comparación (+ the AI
    recommendation) → propuesta → pólizas → índice de documentos → resumen de
    dinero. Every absent section prints its Spanish "Pronto, al cerrar X" line
    instead of a blank.

    Args:
        title: the display title shown on the cover.
        branding: the same cover + running-frame identity dict
            :func:`render_expediente_html` takes.
        data: the shaped aggregate, a plain dict (no DB) — see
            ``app.services.expediente.expediente_pdf_data``:
            ``identity_rows`` ``[{label,value,required?}]``; ``clients_rows``
            ``[{legal_name,rut,role}]``; ``journey_rows``
            ``[{milestone,state,completed_at,summary,reason}]``;
            ``antecedentes_rows`` ``[{file,required,state}]`` +
            ``antecedentes_note``; ``comparison`` (the SAME dict
            :func:`render_comparison_html` takes) + ``comparison_pending``;
            ``propuesta_rows`` ``[{label,value}]`` + ``propuesta_pending``;
            ``policies_rows`` ``[{number,insurer,period,premium}]`` +
            ``policies_pending``; ``documents_rows``
            ``[{name,category,section,date}]``; ``money_rows``
            ``[{label,value}]`` + ``money_pending`` + ``money_source``.
    """
    data = data or {}
    parts: list[str] = []
    number = 0

    # 01 Identidad
    number += 1
    parts.append(
        _render_section(
            {
                "kind": "fields",
                "heading": "Identificación de la cuenta",
                "fields": data.get("identity_rows") or [],
            },
            number=number,
        )
    )

    # 02 Empresas de la cuenta
    number += 1
    parts.append(
        _render_section(
            {
                "kind": "table",
                "heading": "Empresas de la cuenta",
                "columns": [
                    {"key": "legal_name", "label": "Razón social", "required": True},
                    {"key": "rut", "label": "RUT", "required": True},
                    {"key": "role", "label": "Rol"},
                ],
                "rows": data.get("clients_rows") or [],
            },
            number=number,
        )
    )

    # 03 Journey
    number += 1
    parts.append(
        _matrix_section(
            "Journey del expediente", number, _journey_table(data.get("journey_rows") or [])
        )
    )

    # 04 Antecedentes
    number += 1
    antecedentes_rows = data.get("antecedentes_rows") or []
    note = data.get("antecedentes_note")
    inner = (
        _render_table(
            {
                "kind": "table",
                "columns": [
                    {"key": "file", "label": "Archivo recomendado"},
                    {"key": "required", "label": "Obligatorio"},
                    {"key": "state", "label": "Estado", "required": True},
                ],
                "rows": antecedentes_rows,
            }
        )
        if antecedentes_rows
        else _pending_callout(note)
    )
    if antecedentes_rows and note:
        inner += _render_callout({"heading": "Resumen", "text": note})
    parts.append(_matrix_section("Antecedentes", number, inner))

    # 05 Comparación
    number += 1
    comparison = data.get("comparison")
    if comparison:
        columns = comparison.get("columns") or []
        inner = _recommendation_block(comparison.get("recommendation"))
        inner += _render_premium_matrix(columns, comparison.get("premium_rows") or [])
        common = comparison.get("common_dimensions") or []
        if common:
            inner += _render_dimension_matrix(columns, common)
        extras = comparison.get("extra_dimensions") or []
        if extras:
            inner += _render_dimension_matrix(columns, extras)
    else:
        inner = _pending_callout(data.get("comparison_pending"))
    parts.append(_matrix_section("Comparación", number, inner))

    # 06 Propuesta
    number += 1
    propuesta_rows = data.get("propuesta_rows") or []
    if propuesta_rows:
        parts.append(
            _render_section(
                {
                    "kind": "fields",
                    "heading": "Propuesta",
                    "fields": [
                        {"label": row.get("label"), "value": row.get("value")}
                        for row in propuesta_rows
                    ],
                },
                number=number,
            )
        )
    else:
        parts.append(
            _matrix_section(
                "Propuesta", number, _pending_callout(data.get("propuesta_pending"))
            )
        )

    # 07 Pólizas
    number += 1
    policies_rows = data.get("policies_rows") or []
    if policies_rows:
        inner = _render_table(
            {
                "kind": "table",
                "columns": [
                    {"key": "number", "label": "N° de póliza", "required": True},
                    {"key": "insurer", "label": "Aseguradora"},
                    {"key": "period", "label": "Vigencia"},
                    {"key": "premium", "label": "Prima total (UF)", "money": True},
                ],
                "rows": policies_rows,
                "totals": True,
            }
        )
    else:
        inner = _pending_callout(data.get("policies_pending"))
    parts.append(_matrix_section("Pólizas", number, inner))

    # 08 Índice de documentos
    number += 1
    parts.append(
        _matrix_section(
            "Índice de documentos",
            number,
            _render_table(
                {
                    "kind": "table",
                    "columns": [
                        {"key": "name", "label": "Documento"},
                        {"key": "category", "label": "Tipo"},
                        {"key": "section", "label": "Sub-expediente"},
                        {"key": "date", "label": "Fecha"},
                    ],
                    "rows": data.get("documents_rows") or [],
                }
            ),
        )
    )

    # 09 Dinero
    number += 1
    money_rows = data.get("money_rows") or []
    if money_rows:
        inner = _render_table(
            {
                "kind": "table",
                "columns": [
                    {"key": "label", "label": "Concepto"},
                    {"key": "value", "label": "UF", "money": True},
                ],
                "rows": money_rows,
            }
        )
    else:
        inner = _pending_callout(data.get("money_pending"))
    parts.append(_matrix_section("Resumen económico (UF)", number, inner))

    return _wrap_document(title=title, branding=branding, body_html="".join(parts))


# ---------------------------------------------------------------------------
# Table report composer (the Datos / Analítica export)
# ---------------------------------------------------------------------------

_REPORT_OVERRIDES = """
/* Landscape, no cover: a data export is a running table, not an expediente.
   These rules come AFTER the shared stylesheet, so they win the cascade. */
@page{ size:A4 landscape; margin:12mm 12mm 14mm; }
@page:first{ margin:12mm 12mm 14mm; }
.rep-filters{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px;}
.rep-chip{display:inline-block;font-size:9px;font-weight:600;letter-spacing:0.02em;
  color:var(--accent-ink);background:var(--accent-2);padding:4px 9px;border-radius:999px;}
.rep-chip .k{color:var(--muted);font-weight:600;text-transform:uppercase;
  letter-spacing:0.08em;margin-right:4px;}
.rep-title{font-size:16px;font-weight:600;letter-spacing:-0.01em;margin-bottom:2px;}
.rep-sub{font-size:9.5px;color:var(--muted);margin-bottom:12px;}
table.data{font-size:8.5px;}
table.data thead th{font-size:7.5px;padding:6px 7px;}
table.data tbody td{padding:5px 7px;}
.rep-foot{margin-top:12px;padding-top:7px;border-top:1px solid var(--line);
  font-size:9px;color:var(--muted);}
"""


def render_table_report_html(
    *,
    title: str,
    branding: dict,
    columns: list[dict],
    rows: list[list],
    filters: list[dict] | None = None,
    subtitle: str | None = None,
    row_count: int | None = None,
    footer_note: str | None = None,
) -> str:
    """Compose a landscape, branded, one-table report for the data exports.

    Deliberately reuses the expediente stylesheet, the running header
    (broker left, Radal isotype right) and the ``@page`` footer, then overrides
    the page box to landscape and drops the cover — an export is a working
    document, not a client-facing expediente, so there is exactly ONE PDF stack
    in this codebase, not two.

    Args:
        title: the report heading (e.g. "Pólizas").
        branding: same dict as :func:`render_expediente_html`.
        columns: ``[{"label": str, "num": bool}]`` — ``num`` right-aligns.
        rows: the already-formatted cell strings, one list per row.
        filters: ``[{"label": str, "value": str}]`` printed as chips in the head.
        row_count: printed in the footer; falls back to ``len(rows)``.
    """
    columns = columns or []
    rows = rows or []
    count = len(rows) if row_count is None else row_count
    footer_left = str(branding.get("broker_name") or "Radal.")

    chips = "".join(
        f'<span class="rep-chip"><span class="k">{_esc(f.get("label"))}</span>'
        f'{_esc(f.get("value"))}</span>'
        for f in (filters or [])
    )
    filters_html = f'<div class="rep-filters">{chips}</div>' if chips else ""

    head_cells = "".join(
        f'<th class="num">{_esc(c.get("label"))}</th>'
        if c.get("num")
        else f"<th>{_esc(c.get('label'))}</th>"
        for c in columns
    )
    body_rows = []
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            num = bool(columns[index].get("num")) if index < len(columns) else False
            rendered = _esc(value) if value not in (None, "") else MISSING_OPTIONAL
            cells.append(f'<td class="num">{rendered}</td>' if num else f"<td>{rendered}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    if not body_rows:
        body_rows.append(
            f'<tr><td colspan="{max(len(columns), 1)}" class="table-empty muted">'
            "Sin resultados para los filtros aplicados.</td></tr>"
        )

    table_html = (
        '<table class="data"><thead><tr>'
        + head_cells
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
    )
    foot = footer_note or f"{count} fila(s) exportada(s)."

    body = (
        f'<div class="rep-title">{_esc(title)}</div>'
        + (f'<div class="rep-sub">{_esc(subtitle)}</div>' if subtitle else "")
        + filters_html
        + table_html
        + f'<div class="rep-foot">{_esc(foot)}</div>'
    )

    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8"/>'
        f"<style>{_stylesheet(footer_left)}{_REPORT_OVERRIDES}</style>"
        f"<title>{_esc(title)}</title></head><body>"
        '<table class="report"><thead><tr><td>'
        f"{_run_head(branding)}"
        "</td></tr></thead><tbody><tr><td>"
        f'<main class="content">{body}</main>'
        "</td></tr></tbody></table>"
        "</body></html>"
    )
