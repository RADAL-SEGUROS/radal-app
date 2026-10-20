"""The comparison STANDARDIZATION (docs/v12/11-fast-reading.md, review round 2 §19.19).

``ai.submit_comparison`` assembles the readings (DB) and persists the snapshot (DB); this
module only talks to the provider, so it never touches a session from a worker thread and
the manual timing probe can run it on in-memory readings.

Pipeline (:func:`standardize_readings`): facets whose label slug repeats across readings
are PRE-MATCHED in code (:func:`prematch` — on the corpus ~85 % of all facets, every
exclusion); the model sees only what is left, one call per group class
(:data:`MODEL_BUCKETS`) in parallel with the recommend call, and answers only the MATCHES
as references (``{"fixed"?, "refs": [[source_id, n], …]}``) — no wording, labels or keys,
which are rebuilt in code (:func:`fill_cells` / :func:`finalize_dimensions`). Every
facet not matched becomes a row of its own, so the table is complete whatever the model
answers. The table shape the grid / PDF / propuesta read is unchanged.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.ai_parallel import run_groups
from app.services.budget_read import call_with_quick_retry, first_error

#: Per ``submit_comparison`` call. Above the reads' 50 s on purpose: a dense batch was
#: measured live at > 50 s even in parallel (2026-10-18), and the step must still FINISH
#: inside the Lambda's 120 s — the SPA re-reads the comparison when the 60 s edge cuts
#: the answer (``pollComparison``), so a completed, committed alignment is recovered.
STANDARDIZE_CALL_TIMEOUT_SECONDS = 70
#: The whole step's provider budget (batches + recommend), under the Lambda's 120 s.
STANDARDIZE_BUDGET_SECONDS = 110
RECOMMEND_TIMEOUT_MIN_SECONDS = 15
RECOMMEND_TIMEOUT_MAX_SECONDS = 50
#: Above this many facets (all readings together) the unmatched facets are aligned with one
#: parallel call per group class; below it, one call aligns. Either way the recommend call
#: runs AFTER, over the final table (review r3, R3).
GROUP_SPLIT_MIN_FACETS = 30
#: Per alignment call of the split path. Live 2026-10-19 at ~20 tok/s: cover call 56.5 s,
#: recommend 43.2 s. Since R3 the recommend follows the alignment: worst case 55 + 50 s,
#: inside the Lambda's 120 s and past the 60 s edge (the SPA polls ``status=aligned``).
ALIGN_CALL_TIMEOUT_SECONDS = 55
#: A TIMED-OUT alignment call does not block the step: the table is already complete
#: (every facet is a pre-matched row or a row of its own); only the folding of
#: differently-worded facets of that class is missing, said in a warning (Realinear
#: retries). Likewise a TIMED-OUT recommendation keeps the finished table with a warning.
#: Any other provider error still blocks (BLOCK-ON-PROVIDER-DOWN).
SOFT_ALIGN_TIMEOUT = True


@dataclass
class Standardized:
    """What the standardization produced (before the dictionary / snapshot step)."""

    dimensions: list[dict[str, Any]]
    recommendation: dict[str, Any] | None
    batched: bool
    batch_count: int
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    warnings: list[str] = field(default_factory=list)
    #: ``{call name: {seconds, prompt_tokens, completion_tokens, finish_reason}}``.
    call_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    wall_seconds: float = 0.0


def _timeout() -> int:
    return min(int(settings.AI_TIMEOUT_SECONDS), STANDARDIZE_CALL_TIMEOUT_SECONDS)


def _recommend_timeout(elapsed: float) -> int:
    """What is left of the step budget, clamped to a useful window."""
    left = int(STANDARDIZE_BUDGET_SECONDS - elapsed)
    window = max(RECOMMEND_TIMEOUT_MIN_SECONDS, min(RECOMMEND_TIMEOUT_MAX_SECONDS, left))
    return min(int(settings.AI_TIMEOUT_SECONDS), window)


def _stats(result: Any, seconds: float) -> dict[str, Any]:
    usage = getattr(result, "usage", None) or {}
    return {
        "seconds": round(seconds, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "finish_reason": getattr(result, "finish_reason", None),
    }


def _indexed(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The readings with every facet numbered ``n`` (1-based, position in the reading).

    The numbering is GLOBAL to the reading (not to a slice), so a ``[source_id, n]``
    reference means the same facet in every call, in :func:`prematch` and in
    :func:`fill_cells`.
    """
    out: list[dict[str, Any]] = []
    for reading in readings:
        facets = [
            {"n": index, **facet}
            for index, facet in enumerate(reading.get("facets") or [], start=1)
            if isinstance(facet, dict)
        ]
        out.append({**reading, "facets": facets})
    return out


# --- Compact answer format (review round 2, follow-up) ------------------------------
#
# Live 2026-10-19 (provider slow): the four group slices answered with one cell OBJECT
# per column per row (~75 tokens a row) and two of them hit the 70 s call timeout — no
# three-offer table ever committed on dev. The model now answers ROWS OF REFERENCES:
# ``{"key", "label", "refs": [[source_id, n], ...]}`` (~25 tokens a row); the cells
# (verbatim / value / present / unit) are rebuilt in code from the referenced facets
# (:func:`fill_cells` + :func:`finalize_dimensions`), ``group`` from the facets, ``scope``
# from the columns a row covers. And most rows never reach the model at all: facets whose
# label slug is identical across readings are PRE-MATCHED in code (:func:`prematch`);
# the model sees only what still needs judgement, plus the pre-matched rows that miss a
# column (so it can attach a differently-worded facet to them).

#: The calls of the standardization, by group CLASS: concepts cross groups inside the
#: cover class (a "rehabilitación automática" is a coverage for one insurer and a clause
#: for another), never into deductibles or exclusions.
MODEL_BUCKETS: dict[str, tuple[str, ...]] = {
    "cover": ("sublimit", "coverage", "clause", "warranty", "other"),
    "deductible": ("deductible",),
    "exclusion": ("exclusion",),
}

#: Provider options of the alignment calls (``ai.tool_call(extra_body=…)``): GLM's thinking
#: off — a matching task, not a judgement one. Live 2026-10-19 on DeepInfra the switch was
#: accepted (``reasoning_effort: none`` timed out, ``chat_template_kwargs.enable_thinking``
#: made it think MORE: 6 k reasoning chars). A provider that rejects it (4xx) is retried
#: once without it, so the option can never block a comparison.
STANDARDIZE_EXTRA_BODY: dict[str, Any] | None = {"thinking": {"type": "disabled"}}
#: A provider 4xx that is about the request (an option it does not take), not the load.
_REJECTED_RE = re.compile(r"returned (?:400|404|422)\b")

#: Same for the recommend call (its prose benefits from thinking; it is one short call).
RECOMMEND_EXTRA_BODY: dict[str, Any] | None = None

#: Wording sent per UNMATCHED facet (the decisive sentence is what tells two labels apart).
RESIDUAL_VERBATIM_CHARS = 220

_SUBMIT_SYSTEM = (
    "Eres el estandarizador de un comparador de ofertas de seguros en Chile. El sistema ya "
    "emparejó las condiciones con el mismo nombre. Recibes, por columna (comparison_source_id + "
    "compañía), las FACETAS AÚN SIN EMPAREJAR de cada cotización, numeradas `n`, y las FILAS FIJAS "
    "que no tienen todas las columnas (`fixed_rows`: id, label, columnas que ya la tienen). "
    "Llama a la función submit_comparison.\n\n"
    "DEVUELVE SOLO las filas que emparejan: "
    "(a) una facet que es el MISMO concepto que una fila fija, para una columna que esa fila no "
    "tiene → {\"fixed\": id de la fila, \"refs\": [[comparison_source_id, n]]}; "
    "(b) dos o más facetas de columnas DISTINTAS que son el mismo concepto (aunque una lo llame "
    "cobertura y otra cláusula o extensión) → {\"refs\": [[id, n], [id, n], …]}. "
    "Las facetas sin par NO se devuelven: el sistema las deja en filas propias. "
    "Nunca dos facetas de la misma columna en una fila; nunca un DEDUCIBLE ni una EXCLUSIÓN con "
    "una cobertura o sublímite; cada facet en una sola fila. No copies textos ni valores: solo "
    "refs (y `fixed`). No inventes facetas."
)

_RECOMMEND_IN_SUBMIT = (
    " Además entrega `recommendation`: la oferta recomendada (comparison_source_id), su "
    "fundamento y salvedades; nombra cada oferta por su COMPAÑÍA (campo `insurer`), nunca "
    "\"columna N\" ni ids, y compara la PRIMA TOTAL (total_premium_uf = neta + IVA)."
)


def submit_tool_schema(with_recommendation: bool = False) -> dict[str, Any]:
    """``submit_comparison`` answering only the MATCHES, as references (no wording, no
    labels, no keys: those are rebuilt in code from the referenced facets)."""
    from app.services import ai

    row = {
        "type": "object",
        "properties": {
            "fixed": {"type": "string", "description": "El id de la fila fija a la que se agrega (F1, F2…), si aplica."},
            "refs": {
                "type": "array",
                "description": "Las facetas de la fila: [comparison_source_id, n].",
                "items": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            },
        },
        "required": ["refs"],
    }
    properties: dict[str, Any] = {"rows": {"type": "array", "items": row}}
    if with_recommendation:
        properties["recommendation"] = ai._recommendation_schema_props()
    return {
        "name": "submit_comparison",
        "description": (
            "Devuelve solo las filas que emparejan facetas entre cotizaciones (o con una fila "
            "fija), como referencias. No copies textos ni valores."
        ),
        "parameters": {"type": "object", "properties": properties, "required": ["rows"]},
    }


def submit_messages(payload: dict[str, Any], *, with_recommendation: bool = False) -> list[dict[str, str]]:
    import json

    system = _SUBMIT_SYSTEM + (_RECOMMEND_IN_SUBMIT if with_recommendation else "")
    user = json.dumps(payload, ensure_ascii=False, default=str)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def fill_cells(
    dimensions: list[dict[str, Any]], readings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve every cell's ``facet_n`` against its reading: copy the verbatim (and the
    value / present the model left out). A cell the model already filled keeps its values.
    Without a usable ``facet_n`` the cell falls back to the reading's facet whose key/label
    slug equals the dimension's key or label (same slug rule as ``ai._canonical_key``);
    no match leaves the cell as the model gave it."""
    from app.schemas.proposal import coverage_key

    def _slug(value: Any) -> str | None:
        return coverage_key(None, str(value)) if value else None

    by_ref: dict[tuple[Any, int], dict[str, Any]] = {}
    by_slug: dict[tuple[Any, str], dict[str, Any]] = {}
    for reading in readings:
        source_id = reading.get("comparison_source_id")
        for index, facet in enumerate(reading.get("facets") or [], start=1):
            if isinstance(facet, dict):
                by_ref[(source_id, index)] = facet
                for value in (facet.get("key"), facet.get("label")):
                    slug = _slug(value)
                    if slug:
                        by_slug.setdefault((source_id, slug), facet)
    for dimension in dimensions:
        dim_slugs = [s for s in (_slug(dimension.get("key")), _slug(dimension.get("label"))) if s]
        cells = []
        for cell in dimension.get("cells") or []:
            cell = dict(cell)
            ref = cell.pop("facet_n", None)
            source_id = cell.get("comparison_source_id")
            try:
                facet = by_ref.get((source_id, int(ref))) if ref is not None else None
            except (TypeError, ValueError):
                facet = None
            if facet is None:
                facet = next(
                    (by_slug[(source_id, s)] for s in dim_slugs if (source_id, s) in by_slug),
                    None,
                )
            if facet is not None:
                for key in ("verbatim", "value", "present"):
                    if cell.get(key) is None and facet.get(key) is not None:
                        cell[key] = facet.get(key)
                if facet.get("group"):
                    # Private, stripped by :func:`finalize_dimensions`: which group the
                    # cell's facet really belongs to (a deductible inside a coverage row).
                    cell[_FACET_GROUP] = str(facet.get("group"))
            cells.append(cell)
        dimension["cells"] = cells
    return dimensions


# --- Post-processing of the standardized table (review round 2, A3/A4) -------------
#
# Deterministic, after the model answered. The live table of 2026-10-19 (dev
# /comparisons/26) showed: an exclusion "Excluida" in one column and "Incluida" in the
# next for the same exclusion; raw "475256" / "15000" strings with no unit; a "Sismo"
# row with a coverage in one column and a deductible text in the next; and one concept
# split over two rows because one slice filed it as a coverage and another as a clause.

_FACET_GROUP = "_facet_group"

#: Groups that must never share a row with another class: a deductible is not a cover
#: amount, and an exclusion reads as the OPPOSITE of a coverage.
_GROUP_CLASS: dict[str, str] = {"deductible": "deductible", "exclusion": "exclusion"}


def _group_class(group: Any) -> str:
    return _GROUP_CLASS.get(str(group or "other"), "cover")


_CONCEPT_STOPWORDS = frozenset(
    {
        "de", "del", "la", "las", "el", "los", "y", "e", "o", "a", "en", "por", "para",
        "con", "sin", "al", "un", "una", "clausula", "clausulas", "cobertura",
        "coberturas", "extension", "extensiones", "adicional", "adicionales", "condicion",
        "condiciones", "particular", "particulares", "garantia", "garantias",
        "sublimite", "sublimites", "otorgada", "otorgado", "uf",
    }
)


def _concept_tokens(value: Any) -> frozenset[str]:
    import re
    import unicodedata

    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(ch)
    ).lower()
    if folded.startswith("text:"):  # a ``coverage_key`` slug of a non-canonical label
        folded = folded[5:]
    return frozenset(
        token for token in re.split(r"[^a-z0-9]+", folded) if token and token not in _CONCEPT_STOPWORDS
    )


def _same_concept(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two rows name the same concept: equal token sets of key or label, or one a
    superset of the other's (>= 2 tokens) — "rehabilitación automática" vs "cláusula de
    rehabilitación automática de suma asegurada"."""
    names_a = [t for t in (_concept_tokens(a.get("key")), _concept_tokens(a.get("label"))) if t]
    names_b = [t for t in (_concept_tokens(b.get("key")), _concept_tokens(b.get("label"))) if t]
    for x in names_a:
        for y in names_b:
            if x == y:
                return True
            small, big = (x, y) if len(x) <= len(y) else (y, x)
            if len(small) >= 2 and small <= big:
                return True
    return False


def _sources(row: dict[str, Any]) -> set[Any]:
    return {c.get("comparison_source_id") for c in row.get("cells") or [] if isinstance(c, dict)}


def split_mixed_groups(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A cell whose facet is of another CLASS than its row (a deductible text inside a
    "Sismo" coverage row) moves to a sibling row of its own group, right after."""
    out: list[dict[str, Any]] = []
    for row in dimensions:
        row_class = _group_class(row.get("group"))
        keep: list[dict[str, Any]] = []
        moved: dict[str, list[dict[str, Any]]] = {}
        for cell in row.get("cells") or []:
            facet_group = cell.get(_FACET_GROUP) if isinstance(cell, dict) else None
            if facet_group and _group_class(facet_group) != row_class:
                moved.setdefault(facet_group, []).append(cell)
            else:
                keep.append(cell)
        out.append({**row, "cells": keep})
        for group, cells in moved.items():
            suffix = {"deductible": "deducible", "exclusion": "exclusion"}.get(_group_class(group), group)
            out.append(
                {
                    **row,
                    "key": f"{row.get('key')}-{suffix}",
                    "group": group,
                    "label": f"{row.get('label') or row.get('key')} ({_GROUP_LABEL_ES.get(group, group)})",
                    "cells": cells,
                }
            )
    return out


_GROUP_LABEL_ES = {
    "coverage": "cobertura",
    "deductible": "deducible",
    "exclusion": "exclusión",
    "sublimit": "sublímite",
    "clause": "cláusula",
    "warranty": "garantía",
    "other": "otra condición",
}


def _same_concept_strict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Equal concept tokens, or one a superset of the other's with >= 3 tokens — the bar
    for folding rows that SHARE a column (the reading filed one concept twice)."""
    names_a = [t for t in (_concept_tokens(a.get("key")), _concept_tokens(a.get("label"))) if t]
    names_b = [t for t in (_concept_tokens(b.get("key")), _concept_tokens(b.get("label"))) if t]
    for x in names_a:
        for y in names_b:
            small, big = (x, y) if len(x) <= len(y) else (y, x)
            if x == y or (len(small) >= 3 and small <= big):
                return True
    return False


def dedupe_dimensions(dimensions: list[dict[str, Any]], column_count: int | None = None) -> list[dict[str, Any]]:
    """Fold rows naming the SAME concept into the first one (A4).

    Only rows of a compatible class (never a deductible or an exclusion into a cover
    row). Rows with DISJOINT columns fold on :func:`_same_concept` — one concept filed as
    a coverage for one insurer and as a clause for another. Rows of DIFFERENT groups that
    share a column fold on the stricter :func:`_same_concept_strict` (live 2026-10-19:
    Southbridge's read listed "Rehabilitación automática de montos" both as a coverage
    and as a clause); the first row keeps its cell for a shared column. Two rows of the
    SAME group sharing a column stay apart (two real items, e.g. two "Sismo" deductibles).
    """
    out: list[dict[str, Any]] = []
    for row in dimensions:
        target = None
        for kept in out:
            if _group_class(kept.get("group")) != _group_class(row.get("group")):
                continue
            shared = _sources(kept) & _sources(row)
            if not shared and _same_concept(kept, row):
                target = kept
                break
            if shared and kept.get("group") != row.get("group") and _same_concept_strict(kept, row):
                target = kept
                break
        if target is None:
            out.append({**row, "cells": list(row.get("cells") or [])})
            continue
        have = _sources(target)
        target["cells"] = target["cells"] + [
            c for c in row.get("cells") or [] if c.get("comparison_source_id") not in have
        ]
        if column_count and target.get("scope") is not None:
            target["scope"] = "common" if len(_sources(target)) >= column_count else "extra"
    return out


import re as _re  # noqa: E402 - kept next to the patterns that use it

_SIMPLE_VALUE_RE = _re.compile(
    r"^\s*(?P<pre>UF|U\.F\.|CLP|USD|US\$|\$)?\s*(?P<num>-?\d[\d.,]*)\s*"
    r"(?P<post>%|‰|por\s*mil|UF|d[ií]as?|horas?|hrs?\.?|CLP|USD)?\s*$",
    _re.IGNORECASE,
)


def _unit_of(marker: str | None) -> str | None:
    if not marker:
        return None
    m = marker.strip().lower().replace(".", "")
    if m in {"uf"}:
        return "uf"
    if m == "%":
        return "pct"
    if m in {"‰", "por mil", "pormil"} or m.startswith("por"):
        return "permille"
    if m.startswith("d"):
        return "days"
    if m.startswith("h"):
        return "hours"
    if m == "clp" or m == "$":
        return "clp"
    if m in {"usd", "us$"}:
        return "usd"
    return None


def _number(value: Any) -> int | float | None:
    from app.schemas.extraction.common import parse_uf

    parsed = parse_uf(value)
    if parsed is None:
        return None
    return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)


def _unit_from_text(text: Any) -> str | None:
    """The single unit a verbatim talks in, when it names exactly one kind."""
    if not isinstance(text, str) or not text:
        return None
    kinds = set()
    if _re.search(r"\bU\.?F\b", text, _re.IGNORECASE):
        kinds.add("uf")
    if "%" in text:
        kinds.add("pct")
    if "‰" in text or _re.search(r"por\s*mil", text, _re.IGNORECASE):
        kinds.add("permille")
    return kinds.pop() if len(kinds) == 1 else None


#: A quantity in a verbatim: "UF 5", "10%", "2,5 ‰", "15.000", "30 días".
_QUANTITY_RE = _re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")
#: A cap / floor / second branch: the part a bare number drops ("mínimo UF 25",
#: "máx UF 20.000", "otros UF 3", "con un tope de …").
_CONDITION_RE = _re.compile(
    r"\b(?:m[aá]x(?:imo)?|m[ií]n(?:imo)?|tope|otros?|otras?|adicional|por\s+evento|"
    r"por\s+ubicaci[oó]n|de\s+la\s+p[eé]rdida|del\s+monto|del\s+siniestro|carencia)\b",
    _re.IGNORECASE,
)
#: "Not contracted" wording behind a 0 the model put in ``value`` (PxP "UF 0").
_NOT_INCLUDED_RE = _re.compile(
    r"\bno\s+(?:contratad|cotizad|incluid|cubiert|otorgad|aplica)", _re.IGNORECASE
)
#: "<n> <word>" units the grid has no symbol for (kept as the phrase).
_COUNT_WORD_RE = r"\s*(d[ií]as?|horas?|hrs?\.?|años?|meses|mes|cuotas?|semanas?)\b"
#: A display value this long is shortened only in the recommend input.
RECOMMEND_VALUE_CHARS = 120


def _is_plain(value: Any) -> bool:
    """A bare quantity (number, "10", "UF 15.000", "25%") or nothing."""
    if value is None or isinstance(value, bool):
        return value is None
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and (not value.strip() or _SIMPLE_VALUE_RE.match(value) is not None)


def is_composite_condition(text: Any) -> bool:
    """True when a verbatim states more than one bare number can carry: a cap or floor,
    a base ("de la pérdida"), a second amount ("…UF 5; otros UF 3")."""
    if not isinstance(text, str) or not text.strip():
        return False
    return len(_QUANTITY_RE.findall(text)) >= 2 or (
        _CONDITION_RE.search(text) is not None and _QUANTITY_RE.search(text) is not None
    )


def _as_condition(cell: dict[str, Any], group: Any) -> dict[str, Any]:
    """The cell shown as its VERBATIM condition (review r3, R2): ``value`` = the condition
    text; the parsed number survives as ``numeric_value`` (+ ``numeric_unit``)."""
    verbatim = " ".join(str(cell.get("verbatim") or "").split())
    parsed = normalize_cell_value({k: v for k, v in cell.items() if k != "unit"}, group, _plain_only=True)
    out = {k: v for k, v in cell.items() if k != "unit"}
    out["value"] = verbatim
    number = parsed.get("value")
    if isinstance(number, (int, float)) and not isinstance(number, bool):
        out["numeric_value"] = number
        if parsed.get("unit"):
            out["numeric_unit"] = parsed["unit"]
    return out


def normalize_cell_value(
    cell: dict[str, Any], group: Any, *, _plain_only: bool = False
) -> dict[str, Any]:
    """``value`` as a NUMBER with its ``unit`` when it is a plain quantity (A4), and the
    condition text when a number alone would lie (review r3, R2).

    ``"475256"`` -> ``475256``; ``"UF 15.000"`` -> ``15000`` + ``uf``; ``"25%"`` -> ``25``
    + ``pct``. A composite text ("10% del monto asegurado, máx UF 25.000") stays text.
    A bare number takes the unit its verbatim names when it names exactly one; a bare
    sublimit / coverage amount >= 100 is UF (the corpus' tables are in UF).

    R2: a **deductible** with a bare number ("10") and a verbatim ("10% de la pérdida,
    mínimo UF 10") shows the verbatim — the minimum is what tells two offers apart; the
    same for any cell whose verbatim is a composite condition ("UF 5 cristal plano;
    otros UF 3", "10% … máx UF 20.000"). A cover cell read as ``0`` whose verbatim says
    "No contratado" reads ``present=false`` (not "UF 0"). "15" with a verbatim "15 días"
    gets ``unit=days``; "10" with "10 años" reads "10 años".
    """
    group_name = str(group or "")
    value = cell.get("value")
    verbatim = cell.get("verbatim")
    has_verbatim = isinstance(verbatim, str) and bool(verbatim.strip())
    if not _plain_only and group_name != "exclusion" and has_verbatim and _is_plain(value):
        if group_name != "deductible" and _NOT_INCLUDED_RE.search(verbatim) and (
            value is None or _number(value) == 0 or _first_sentence_not_included(verbatim)
        ):
            # "Perjuicio por paralización: No contratado" read as UF 0 / nothing — or
            # Southbridge's "Perjuicio por paralización no contratado. … ofrecido por
            # UF 90.000" read as 90 000: the offer does NOT include it. Never "UF 0",
            # "Incluida" or the offered amount as if it were cover (the round-3
            # recommendation credited Southbridge with HDI's PxP from that cell).
            out = {k: v for k, v in cell.items() if k != "unit"}
            number = _number(value) if value is not None else None
            if number:
                out["numeric_value"] = int(number) if number == int(number) else float(number)
            out["value"] = None
            out["present"] = False
            return out
        if group_name == "deductible" or is_composite_condition(verbatim):
            return _as_condition(cell, group)
    unit: str | None = None
    if isinstance(value, str):
        match = _SIMPLE_VALUE_RE.match(value)
        if match is None:
            return cell
        number = _number(match.group("num"))
        if number is None:
            return cell
        unit = _unit_of(match.group("pre")) or _unit_of(match.group("post"))
        value = number
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        return cell
    if unit is None:
        unit = _unit_from_text(verbatim)
    if unit is None and has_verbatim and not _plain_only:
        word = _re.search(
            rf"(?<![\d.,]){_re.escape(_plain_number(value))}{_COUNT_WORD_RE}", verbatim, _re.IGNORECASE
        )
        if word is not None:
            token = word.group(1).lower()
            if token.startswith("d"):
                unit = "days"
            elif token.startswith("h"):
                unit = "hours"
            else:
                return {**cell, "value": f"{_plain_number(value)} {word.group(1)}", "numeric_value": value}
    if unit is None and group_name in ("sublimit", "coverage") and abs(value) >= 100:
        unit = "uf"
    out = {**cell, "value": value}
    if unit is not None:
        out["unit"] = unit
    return out


def _first_sentence_not_included(text: str) -> bool:
    """"No contratado" / "no cotizado" stated in the verbatim's FIRST sentence."""
    first = _re.split(r"[.;]\s", text.strip(), maxsplit=1)[0]
    return _NOT_INCLUDED_RE.search(first) is not None


def _plain_number(value: int | float) -> str:
    """How a number appears in Spanish text: ``10`` / ``2,5``."""
    if isinstance(value, float) and not value.is_integer():
        return f"{value}".replace(".", ",")
    return str(int(value))


def finalize_dimensions(
    dimensions: list[dict[str, Any]], readings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Split mixed rows, fold duplicates, force exclusion presence, number the values,
    and drop the private facet-group marker. Pure; runs on every standardization path."""
    columns = len([r for r in readings if not r.get("is_wrong_file")]) or None
    rows = dedupe_dimensions(split_mixed_groups(dimensions), columns)
    out: list[dict[str, Any]] = []
    for row in rows:
        cells = []
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            had_facet = cell.pop(_FACET_GROUP, None) is not None
            if str(row.get("group") or "") == "exclusion" and had_facet:
                # The offer lists this exclusion: it APPLIES (A4) — never "Incluida"
                # in one column and "Excluida" in the next for the same clause.
                cell["present"] = True
            cells.append(normalize_cell_value(cell, row.get("group")))
        out.append({**row, "cells": cells})
    return out


# --- Recommendation wording (A3) ------------------------------------------------

_INSURER_NOISE = frozenset(
    {
        "compania", "cia", "de", "del", "la", "las", "el", "los", "y", "e", "seguros",
        "seguro", "generales", "general", "vida", "reaseguros", "aseguradora", "sa", "s",
        "a", "spa", "ltda", "limitada", "chile", "chilena", "chileno", "corp",
        "corporacion", "grupo", "group", "insurance", "company", "co", "inc",
    }
)


def insurer_short_name(name: Any) -> str | None:
    """The distinctive part of an insurer's name, for headers and the recommendation.

    "Southbridge Compañía de Seguros Generales S.A." -> "Southbridge"; "HDI Seguros S.A."
    -> "HDI"; "MAPFRE COMPAÑÍA DE SEGUROS GENERALES DE CHILE S.A." -> "Mapfre". Falls
    back to the trimmed name when every word is generic.
    """
    import unicodedata

    text = str(name or "").strip()
    if not text:
        return None
    kept: list[str] = []
    for word in _re.split(r"[\s,]+", text):
        bare = word.strip(".()")
        folded = "".join(
            ch for ch in unicodedata.normalize("NFKD", bare) if not unicodedata.combining(ch)
        ).lower()
        parts = [p for p in _re.split(r"[^a-z0-9]+", folded) if p]
        if not parts or all(p in _INSURER_NOISE for p in parts):
            continue
        if bare.isupper() and len(bare) > 4:
            bare = bare.capitalize()
        kept.append(bare)
    return " ".join(kept) if kept else text


def reading_labels(readings: list[dict[str, Any]]) -> dict[Any, str]:
    """``{comparison_source_id: insurer label}`` — the ``insurer`` each reading carries,
    else the short name of the read insurer, else "Cotización N" (1-based)."""
    labels: dict[Any, str] = {}
    for index, reading in enumerate(readings, start=1):
        sid = reading.get("comparison_source_id")
        label = reading.get("insurer") or insurer_short_name(
            (reading.get("money_core") or {}).get("insurer_name")
        )
        labels[sid] = label or f"Cotización {index}"
    return labels


_COLUMN_REF_RE = _re.compile(
    r"\b(?:(?:la|las|el|los)\s+)?(?:columna|columnas|column|oferta|cotizaci[oó]n|"
    r"comparison_source_id)\s*(?:n[°º.]?\s*|#\s*|=\s*)?(?P<id>\d+)\b",
    _re.IGNORECASE,
)


def humanize_recommendation(
    recommendation: dict[str, Any] | None, labels: dict[Any, str]
) -> dict[str, Any] | None:
    """Replace every "columna 4" / "comparison_source_id 4" the model still wrote with
    the insurer's name (A3). Ids that are not a column of this comparison stay as they
    are (e.g. a "Folio N° 2025000418877" is not a column reference)."""
    if not isinstance(recommendation, dict) or not labels:
        return recommendation
    by_id = {str(k): v for k, v in labels.items() if k is not None}

    def _sub(text: Any) -> Any:
        if not isinstance(text, str):
            return text

        def _one(match: "_re.Match[str]") -> str:
            label = by_id.get(match.group("id"))
            return label if label else match.group(0)

        return _COLUMN_REF_RE.sub(_one, text)

    out = dict(recommendation)
    out["rationale"] = _sub(out.get("rationale"))
    out["caveats"] = [_sub(c) for c in (out.get("caveats") or [])]
    return out


def _note_missing_totals(
    recommendation: dict[str, Any] | None, readings: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """R5: a caveat per column whose prima total was NOT read — the recommendation must
    not compare (or compute) a price it does not have. The round-2 comparison took the
    exempt premium as net, added IVA and invented three totals."""
    if not isinstance(recommendation, dict):
        return recommendation
    labels = reading_labels(readings)
    missing = [
        labels.get(r.get("comparison_source_id"))
        for r in readings
        if not r.get("is_wrong_file")
        and (r.get("money_core") or {}).get("total_premium_uf") in (None, "")
    ]
    missing = [name for name in missing if name]
    if not missing:
        return recommendation
    names = ", ".join(missing[:-1]) + (" y " if len(missing) > 1 else "") + missing[-1]
    caveat = (
        f"No se leyó la prima total de {names}: reintenta la lectura de esa cotización antes "
        "de comparar precios (no se calcula desde la prima exenta ni la afecta)."
    )
    out = dict(recommendation)
    caveats = [c for c in (out.get("caveats") or []) if c != caveat]
    out["caveats"] = [caveat, *caveats]
    return out


# --- Deterministic pre-match ------------------------------------------------------


def _slug(value: Any) -> str | None:
    from app.schemas.proposal import coverage_key

    text = str(value or "").strip()
    if not text:
        return None
    slug = coverage_key(None, text)
    # ``coverage_key`` marks a non-canonical label ``text:<slug>``; the row key is the slug.
    return slug.split(":", 1)[1] if slug.startswith("text:") else slug


def _class_bucket(group: Any) -> str:
    group = str(group or "other")
    for name, groups in MODEL_BUCKETS.items():
        if group in groups:
            return name
    return "cover"


def _live_readings(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in readings if not r.get("is_wrong_file")]


def _prior_key_by_slug(prior_structure: dict[str, Any]) -> dict[str, str]:
    """``{slug: prior key}`` so a pre-matched row keeps the dictionary's key (carry-forward)."""
    out: dict[str, str] = {}
    for entry in prior_structure.get("dictionary") or []:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        for value in (entry.get("key"), entry.get("label")):
            slug = _slug(value)
            if slug:
                out.setdefault(slug, str(entry["key"]))
    return out


@dataclass
class Prematch:
    """Rows matched in code + what is left for the model."""

    rows: list[dict[str, Any]]  # dimension dicts, cells = [{comparison_source_id, facet_n}]
    used: set[tuple[Any, int]]  # (source_id, n) already placed
    #: ``{bucket: {source_id: [indexed facet, ...]}}`` — the facets still unmatched.
    residual: dict[str, dict[Any, list[dict[str, Any]]]]


def _majority_group(facets: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for facet in facets:
        group = str(facet.get("group") or "other")
        counts[group] = counts.get(group, 0) + 1
    return max(counts, key=lambda g: (counts[g], -list(counts).index(g))) if counts else "other"


def prematch(
    readings: list[dict[str, Any]], prior_structure: dict[str, Any]
) -> Prematch:
    """Rows for the facets whose LABEL SLUG is identical in >= 2 readings (one facet per
    reading with that slug, same group class). Measured on dev's three corpus readings
    (2026-10-19): 114/123 sublimits, 60/60 exclusions and 40/42 deductibles match this way.
    Two facets of one reading with the same slug (two items) are left to the model.
    ``readings`` must be :func:`_indexed`."""
    prior_keys = _prior_key_by_slug(prior_structure)
    by_key: dict[tuple[str, str], dict[Any, list[dict[str, Any]]]] = {}
    order: dict[tuple[str, str], tuple[int, int]] = {}
    for r_index, reading in enumerate(_live_readings(readings)):
        sid = reading.get("comparison_source_id")
        for facet in reading.get("facets") or []:
            slug = _slug(facet.get("label") or facet.get("key"))
            if not slug:
                continue
            key = (_class_bucket(facet.get("group")), slug)
            by_key.setdefault(key, {}).setdefault(sid, []).append(facet)
            order.setdefault(key, (r_index, int(facet["n"])))

    rows: list[dict[str, Any]] = []
    used: set[tuple[Any, int]] = set()
    for key in sorted(by_key, key=lambda k: order[k]):
        per_source = by_key[key]
        if len(per_source) < 2 or any(len(f) != 1 for f in per_source.values()):
            continue
        facets = [f[0] for f in per_source.values()]
        rows.append(
            {
                "key": prior_keys.get(key[1], key[1]),
                "group": _majority_group(facets),
                "label": facets[0].get("label") or key[1],
                "cells": [
                    {"comparison_source_id": sid, "facet_n": int(f[0]["n"])}
                    for sid, f in per_source.items()
                ],
                "_bucket": key[0],
            }
        )
        used.update((sid, int(f[0]["n"])) for sid, f in per_source.items())

    residual: dict[str, dict[Any, list[dict[str, Any]]]] = {}
    for reading in _live_readings(readings):
        sid = reading.get("comparison_source_id")
        for facet in reading.get("facets") or []:
            if (sid, int(facet["n"])) in used:
                continue
            residual.setdefault(_class_bucket(facet.get("group")), {}).setdefault(sid, []).append(facet)
    return Prematch(rows=rows, used=used, residual=residual)


def _fixed_ids(pre: Prematch, bucket: str, columns: list[Any]) -> dict[str, dict[str, Any]]:
    """``{"F1": row, …}`` — the pre-matched rows of ``bucket`` that miss a column (the only
    ones a residual facet can still join), numbered in table order."""
    out: dict[str, dict[str, Any]] = {}
    for row in pre.rows:
        if (bucket == "all" or row.get("_bucket") == bucket) and len(_sources(row)) < len(columns):
            out[f"F{len(out) + 1}"] = row
    return out


def _residual_payload(
    bucket: str,
    readings: list[dict[str, Any]],
    pre: Prematch,
    *,
    with_money: bool = False,
) -> dict[str, Any]:
    """What ONE call sees: the bucket's unmatched facets per column (short wording) and the
    bucket's pre-matched rows that miss a column."""
    columns = [r.get("comparison_source_id") for r in _live_readings(readings)]
    fixed = [
        {"id": fid, "label": row["label"], "has": sorted(_sources(row))}
        for fid, row in _fixed_ids(pre, bucket, columns).items()
    ]
    out_readings = []
    for reading in _live_readings(readings):
        sid = reading.get("comparison_source_id")
        facets = (
            [f for b in pre.residual.values() for f in b.get(sid, [])]
            if bucket == "all"
            else pre.residual.get(bucket, {}).get(sid, [])
        )
        item: dict[str, Any] = {"comparison_source_id": sid, "insurer": reading.get("insurer")}
        if with_money:
            item["money_core"] = reading.get("money_core")
        item["facets"] = [
            {
                k: v
                for k, v in {
                    "n": f.get("n"),
                    "group": f.get("group"),
                    "label": f.get("label"),
                    "value": f.get("value"),
                    "verbatim": _short_text(f.get("verbatim"), RESIDUAL_VERBATIM_CHARS),
                }.items()
                if v is not None
            }
            for f in facets
        ]
        out_readings.append(item)
    return {"fixed_rows": fixed, "readings": out_readings}


def _short_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def apply_rows(
    answer_rows: Any,
    *,
    bucket: str,
    readings: list[dict[str, Any]],
    pre: Prematch,
    new_rows: list[dict[str, Any]],
    prior_structure: dict[str, Any] | None = None,
) -> None:
    """Fold one call's MATCHES into the table (in place).

    A reference must name a column and an UNMATCHED facet of this call; a facet is placed
    once (first reference wins) and a row never holds two facets of one column. A row with
    ``fixed`` (or the ``key`` of a pre-matched row) attaches to that row, only for the
    columns it lacks; otherwise a row of >= 2 facets is a new row whose key / label / group
    come from its facets (key through the prior dictionary's slugs: carry-forward). A
    one-facet row without a target is ignored — the facet stays a row of its own.
    """
    columns = [r.get("comparison_source_id") for r in _live_readings(readings)]
    by_sid_n: dict[tuple[Any, int], dict[str, Any]] = {}
    for reading in _live_readings(readings):
        sid = reading.get("comparison_source_id")
        for facet in reading.get("facets") or []:
            by_sid_n[(sid, int(facet["n"]))] = facet
    allowed = {
        (sid, int(f["n"]))
        for b, per in pre.residual.items()
        if bucket == "all" or b == bucket
        for sid, facets in per.items()
        for f in facets
    }
    fixed_by_id = _fixed_ids(pre, bucket, columns)
    fixed_by_key = {row["key"]: row for row in pre.rows}
    prior_keys = _prior_key_by_slug(prior_structure or {})
    keys = {row["key"] for row in pre.rows} | {row["key"] for row in new_rows}
    for raw in answer_rows if isinstance(answer_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        refs = raw.get("refs")
        if refs is None and isinstance(raw.get("cells"), list):  # legacy shape
            refs = [[c.get("comparison_source_id"), c.get("facet_n")] for c in raw["cells"] if isinstance(c, dict)]
        target = fixed_by_id.get(str(raw.get("fixed") or "").strip()) or fixed_by_key.get(
            str(raw.get("key") or "").strip()
        )
        have = _sources(target) if target is not None else set()
        picked: list[tuple[Any, int]] = []
        for ref in refs if isinstance(refs, list) else []:
            try:
                sid, n = ref[0], int(ref[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if (sid, n) not in allowed or (sid, n) in pre.used or sid in have:
                continue
            if any(sid == other for other, _n in picked):
                continue
            picked.append((sid, n))
        if not picked or (target is None and len(picked) < 2):
            continue
        cells = [{"comparison_source_id": sid, "facet_n": n} for sid, n in picked]
        pre.used.update(picked)
        if target is not None:
            target["cells"] = target["cells"] + cells
            continue
        facets = [by_sid_n[ref] for ref in picked]
        slug = _slug(facets[0].get("label") or facets[0].get("key")) or "fila"
        key = prior_keys.get(slug, slug)
        if key in keys:
            key = f"{key}-{picked[0][0]}-{picked[0][1]}"
        keys.add(key)
        new_rows.append(
            {
                "key": key,
                "group": _majority_group(facets),
                "label": facets[0].get("label") or key,
                "cells": cells,
                "_bucket": _class_bucket(facets[0].get("group")),
            }
        )


def place_leftovers(
    *, readings: list[dict[str, Any]], pre: Prematch, new_rows: list[dict[str, Any]],
    prior_structure: dict[str, Any] | None = None,
) -> int:
    """Every unmatched facet not placed in a match becomes its own row (never lost)."""
    placed = 0
    prior_keys = _prior_key_by_slug(prior_structure or {})
    keys = {row["key"] for row in pre.rows} | {row["key"] for row in new_rows}
    for b, per in pre.residual.items():
        for sid, facets in per.items():
            for facet in facets:
                n = int(facet["n"])
                if (sid, n) in pre.used:
                    continue
                slug = _slug(facet.get("label") or facet.get("key")) or f"fila-{sid}-{n}"
                key = prior_keys.get(slug, slug)
                if key in keys:
                    key = f"{key}-{sid}-{n}"
                keys.add(key)
                pre.used.add((sid, n))
                new_rows.append(
                    {
                        "key": key,
                        "group": str(facet.get("group") or "other"),
                        "label": facet.get("label") or key,
                        "cells": [{"comparison_source_id": sid, "facet_n": n}],
                        "_bucket": b,
                    }
                )
                placed += 1
    return placed


def _with_scope(rows: list[dict[str, Any]], readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    columns = {r.get("comparison_source_id") for r in _live_readings(readings)}
    out = []
    for row in rows:
        row = {k: v for k, v in row.items() if k != "_bucket"}
        row["scope"] = "common" if columns and _sources(row) >= columns else "extra"
        out.append(row)
    return out


def recommend_dimensions(dimensions: list[dict[str, Any]], labels: dict[Any, str]) -> list[dict[str, Any]]:
    """The table as the recommend call needs it: label, group and per-insurer value /
    presence (the wording only when there is no value, short). A fraction of the full
    table's characters (prefill time)."""
    out = []
    for row in dimensions:
        cells = {}
        for cell in row.get("cells") or []:
            name = labels.get(cell.get("comparison_source_id"), str(cell.get("comparison_source_id")))
            value = cell.get("value")
            if value is None:
                value = _short_text(cell.get("verbatim"), 80)
            elif isinstance(value, str):
                value = _short_text(value, RECOMMEND_VALUE_CHARS)
            entry: dict[str, Any] = {}
            if value is not None:
                entry["v"] = value
            if cell.get("unit"):
                entry["u"] = cell["unit"]
            if cell.get("present") is False:
                entry["no"] = True
            cells[name] = entry
        out.append({"label": row.get("label"), "group": row.get("group"), "cells": cells})
    return out


def _call(
    payload: dict[str, Any], model: str, *, with_recommendation: bool = False, timeout: int | None = None
):
    from app.services import ai

    messages = submit_messages(payload, with_recommendation=with_recommendation)

    def _once(extra: dict[str, Any]):
        return call_with_quick_retry(
            lambda: ai.tool_call(
                messages=messages,
                tool_schema=submit_tool_schema(with_recommendation),
                model=model,
                timeout=timeout or _timeout(),
                max_retries=0,
                max_tokens=settings.AI_COMPARE_MAX_TOKENS,
                **extra,
            )
        )

    if not STANDARDIZE_EXTRA_BODY:
        return _once({})
    try:
        return _once({"extra_body": STANDARDIZE_EXTRA_BODY})
    except ai.AIProviderError as exc:
        if type(exc) is not ai.AIProviderError or not _REJECTED_RE.search(str(exc)):
            raise
        return _once({})


def _recommend(dimensions: list[dict[str, Any]], readings: list[dict[str, Any]], timeout: int):
    from app.services import ai
    from app.services.ai_models import AITask, model_for

    labels = reading_labels(readings)
    extra = {"extra_body": RECOMMEND_EXTRA_BODY} if RECOMMEND_EXTRA_BODY else {}
    return call_with_quick_retry(
        lambda: ai.tool_call(
            messages=ai._recommend_messages(recommend_dimensions(dimensions, labels), readings),
            tool_schema=ai._recommend_tool_schema(),
            model=model_for(AITask.RECOMMEND),
            timeout=timeout,
            max_retries=0,
            max_tokens=settings.AI_COMPARE_MAX_TOKENS,
            **extra,
        )
    )


def _table(
    pre: Prematch, new_rows: list[dict[str, Any]], indexed: list[dict[str, Any]], readings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pre-matched + matched + own rows, by group class, each class in table order; cells
    rebuilt from the facets, then the deterministic clean-up (A4)."""
    ordered: list[dict[str, Any]] = []
    for bucket in MODEL_BUCKETS:
        ordered += [row for row in pre.rows + new_rows if row.get("_bucket") == bucket]
    seen = {id(row) for row in ordered}
    ordered += [row for row in pre.rows + new_rows if id(row) not in seen]
    return finalize_dimensions(fill_cells(_with_scope(ordered, indexed), readings), readings)


def standardize_readings(
    readings: list[dict[str, Any]], prior_structure: dict[str, Any]
) -> Standardized:
    """Standardize ``readings`` into one table + a recommendation (provider only, no DB).

    1. :func:`prematch` places, in code, every facet whose label slug repeats across
       readings. 2. Above :data:`GROUP_SPLIT_MIN_FACETS` facets the still-unmatched ones go
       to the model, one call per group class with something left (:data:`MODEL_BUCKETS`),
       answering only the MATCHES as references. Below it, one call matches.
    3. Unmatched facets become rows of their own; :func:`fill_cells` +
       :func:`finalize_dimensions` rebuild the cells. The first failing alignment call in
       bucket order blocks the step (a typed :class:`ai.AIError` propagates) unless it is
       a timeout (:data:`SOFT_ALIGN_TIMEOUT`).
    4. THEN the recommend call reads the FINAL table (review r3, R3) with each insurer's
       name and prima total; a timeout keeps the table with a warning, any other error
       blocks. A column without a read prima total gets a deterministic caveat (R5).
    """
    from app.services import ai
    from app.services.ai_models import AITask, model_for

    started = time.monotonic()
    compare_model = model_for(AITask.COMPARE)
    model_name = compare_model
    warnings: list[str] = []
    call_stats: dict[str, dict[str, Any]] = {}
    prompt_tokens = 0
    completion_tokens = 0

    indexed = _indexed(readings)
    pre = prematch(indexed, prior_structure)
    total_facets = sum(len(r.get("facets") or []) for r in _live_readings(indexed))
    residual_count = sum(len(f) for per in pre.residual.values() for f in per.values())
    call_stats["prematch"] = {
        "rows": len(pre.rows),
        "matched_facets": len(pre.used),
        "residual_facets": residual_count,
    }

    recommendation = None
    new_rows: list[dict[str, Any]] = []
    split = total_facets > GROUP_SPLIT_MIN_FACETS
    batched = False
    batch_count = 1
    if split:
        units = [
            (bucket, _residual_payload(bucket, indexed, pre))
            for bucket in MODEL_BUCKETS
            if any(pre.residual.get(bucket, {}).values())
        ]
        batched = len(units) > 1
        batch_count = max(len(units), 1)
        step_timeout = min(_timeout(), ALIGN_CALL_TIMEOUT_SECONDS)
        # Review r3 (R3): ONLY the alignment calls run here. The recommendation used to
        # run in parallel over a provisional (unfolded) table and, non-deterministically,
        # credited Southbridge with HDI's perjuicio por paralización; it now reads the
        # FINAL table below (+15-40 s, accepted by the owner).
        jobs = {
            bucket: (lambda p=payload: _call(p, compare_model, timeout=step_timeout))
            for bucket, payload in units
        }
        outcomes = run_groups(jobs) if jobs else {}
        timed_out: list[str] = []
        for bucket, _payload in units:  # deterministic: bucket order
            outcome = outcomes[bucket]
            if not outcome.ok:
                error = first_error([outcome])
                if SOFT_ALIGN_TIMEOUT and isinstance(error, ai.AITimeout):
                    timed_out.append(bucket)
                    call_stats[bucket] = {"seconds": round(outcome.seconds, 2), "error": "timeout"}
                    continue
                raise error
            r = outcome.value
            call_stats[bucket] = _stats(r, outcome.seconds)
            parsed = r.parsed or {}
            apply_rows(parsed.get("rows", parsed.get("dimensions")), bucket=bucket, readings=indexed,
                       pre=pre, new_rows=new_rows, prior_structure=prior_structure)
            prompt_tokens += (r.usage or {}).get("prompt_tokens") or 0
            completion_tokens += (r.usage or {}).get("completion_tokens") or 0
            model_name = r.model or compare_model
        if timed_out:
            pending = sum(
                len(f) for bucket in timed_out for f in pre.residual.get(bucket, {}).values()
            )
            warnings.append(
                f"La alineación de {pending} condición(es) tardó más de lo esperado: quedaron "
                "en filas propias. Realinea para intentarlo de nuevo."
            )
    elif residual_count:
        call_started = time.monotonic()
        payload = _residual_payload("all", indexed, pre, with_money=True)
        # R3: the single call only MATCHES; it saw the unmatched facets, not the whole
        # table, so it no longer recommends — the dedicated call below reads the table.
        r = _call(payload, compare_model)
        call_stats["submit"] = _stats(r, time.monotonic() - call_started)
        parsed = r.parsed or {}
        apply_rows(parsed.get("rows", parsed.get("dimensions")), bucket="all", readings=indexed,
                   pre=pre, new_rows=new_rows, prior_structure=prior_structure)
        model_name = r.model or compare_model
        prompt_tokens = (r.usage or {}).get("prompt_tokens") or 0
        completion_tokens = (r.usage or {}).get("completion_tokens") or 0
    leftovers = place_leftovers(readings=indexed, pre=pre, new_rows=new_rows, prior_structure=prior_structure)
    call_stats["prematch"]["own_rows"] = leftovers
    call_stats["prematch"]["matched_rows"] = len([r for r in new_rows if len(r["cells"]) > 1])

    dimensions = _table(pre, new_rows, indexed, readings)
    if not dimensions and total_facets:
        raise ai.AIProviderError(
            "The comparison produced no dimensions; the step is blocked rather than "
            "committing an empty table"
        )

    # ALWAYS produce the recommendation, AFTER the alignment, over the FINAL table
    # (R3) — insurer names + prima total travel in its input (``_recommend_messages``).
    call_started = time.monotonic()
    try:
        rec = _recommend(dimensions, readings, _recommend_timeout(call_started - started))
    except ai.AITimeout:
        if not (SOFT_ALIGN_TIMEOUT and dimensions):
            raise
        # The table is complete and correct; only the advice is missing. Keep the table
        # (a re-align would redo ~1 min of work) and say so.
        call_stats["recommend"] = {"seconds": round(time.monotonic() - call_started, 2), "error": "timeout"}
        warnings.append(
            "La recomendación tardó más de lo esperado y no se generó; la tabla sí quedó "
            "alineada. Realinea para obtenerla."
        )
        rec = None
    if rec is not None:
        call_stats["recommend"] = _stats(rec, time.monotonic() - call_started)
        recommendation = ai._normalize_recommendation((rec.parsed or {}).get("recommendation"))
        prompt_tokens += (rec.usage or {}).get("prompt_tokens") or 0
        completion_tokens += (rec.usage or {}).get("completion_tokens") or 0
        if recommendation is None or recommendation.get("recommended_comparison_source_id") is None:
            warnings.append(
                "La IA no eligió una cotización recomendada: revisa las salvedades y decide."
            )
    # R5: a column without a read prima total is said, never computed.
    recommendation = _note_missing_totals(recommendation, readings)

    # The broker reads insurer names, never "columna 4" (A3).
    recommendation = humanize_recommendation(recommendation, reading_labels(readings))

    return Standardized(
        dimensions=dimensions,
        recommendation=recommendation,
        batched=batched,
        batch_count=batch_count,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        warnings=warnings,
        call_stats=call_stats,
        wall_seconds=time.monotonic() - started,
    )


__all__ = [
    "MODEL_BUCKETS",
    "Prematch",
    "Standardized",
    "apply_rows",
    "dedupe_dimensions",
    "fill_cells",
    "finalize_dimensions",
    "place_leftovers",
    "prematch",
    "recommend_dimensions",
    "humanize_recommendation",
    "insurer_short_name",
    "normalize_cell_value",
    "reading_labels",
    "split_mixed_groups",
    "standardize_readings",
    "submit_messages",
    "submit_tool_schema",
]
