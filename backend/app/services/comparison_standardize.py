"""The comparison STANDARDIZATION calls, batches in parallel (docs/v12/11-fast-reading.md).

``ai.submit_comparison`` assembles the readings (DB) and persists the snapshot (DB); this
module only talks to the provider, so it never touches a session from a worker thread and
the manual timing probe can run it on in-memory readings.

Semantics are EXACTLY the sequential ones: the batches never saw each other's output (each
batch gets the same ``prior_structure``), so running them at the same time changes nothing
but the wall clock. The merge happens afterwards, in batch order, with the same
deterministic :func:`ai._merge_dimensions`; the first failing batch IN BATCH ORDER blocks
the step, as the sequential loop would have.

Output diet (measured live 2026-10-18: three dense offers = 77.8 k chars of input, three
batches, every batch > 50 s): the model no longer RE-COPIES each cell's verbatim. Each
facet of the input carries a number ``n``; a cell names it (``facet_n``) and the code copies
the reading's own ``verbatim`` (and ``value`` / ``present`` when the model left them out)
into the cell. The table shape the grid / PDF / propuesta read is unchanged, and the wording
is now byte-identical to the reading instead of a model re-copy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.ai_parallel import run_groups
from app.services.budget_read import FACET_GROUP_SPLIT, call_with_quick_retry, first_error

#: Per ``submit_comparison`` call. Above the reads' 50 s on purpose: a dense batch was
#: measured live at > 50 s even in parallel (2026-10-18), and the step must still FINISH
#: inside the Lambda's 120 s — the SPA re-reads the comparison when the 60 s edge cuts
#: the answer (``pollComparison``), so a completed, committed alignment is recovered.
STANDARDIZE_CALL_TIMEOUT_SECONDS = 70
#: The whole step's provider budget (batches + recommend), under the Lambda's 120 s.
STANDARDIZE_BUDGET_SECONDS = 110
RECOMMEND_TIMEOUT_MIN_SECONDS = 15
RECOMMEND_TIMEOUT_MAX_SECONDS = 45
#: Above this many facets (all readings together) the table is standardized by FACET GROUP:
#: one parallel call per ``FACET_GROUP_SPLIT`` bucket, each seeing EVERY reading but only
#: that bucket's facets. Batching by reading made every batch re-emit the whole table
#: (~105 rows, 6.6 k tokens, 54 s for ONE offer — live 2026-10-18); a group slice emits only
#: its own rows, and dimensions never cross groups, so the merge is a plain concatenation.
GROUP_SPLIT_MIN_FACETS = 30


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


_FACET_REF_INSTRUCTION = (
    "\n\nCELDAS POR REFERENCIA: cada facet de cada lectura trae un número `n`. En cada celda "
    "indica `facet_n` = el `n` de la facet de ESA lectura (su comparison_source_id) que "
    "corresponde a la dimensión. NO copies la redacción literal en la celda: el sistema la "
    "copia desde la lectura. Informa `value` / `present` solo si los normalizas; si no, "
    "omítelos. Omite las claves sin valor."
)


def _indexed(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The readings as the model sees them: every facet numbered ``n`` (1-based)."""
    out: list[dict[str, Any]] = []
    for reading in readings:
        facets = [
            {"n": index, **facet}
            for index, facet in enumerate(reading.get("facets") or [], start=1)
            if isinstance(facet, dict)
        ]
        out.append({**reading, "facets": facets})
    return out


def submit_messages(
    readings: list[dict[str, Any]], prior_structure: dict[str, Any]
) -> list[dict[str, str]]:
    from app.services import ai

    messages = ai._submit_comparison_messages(_indexed(readings), prior_structure)
    messages[0] = {**messages[0], "content": messages[0]["content"] + _FACET_REF_INSTRUCTION}
    return messages


def submit_tool_schema() -> dict[str, Any]:
    """``submit_comparison`` with cells that REFERENCE a facet instead of re-copying it."""
    from app.services import ai

    schema = ai._submit_comparison_tool_schema()
    params = schema["parameters"]
    dimension = params["properties"]["dimensions"]["items"]
    cell = dimension["properties"]["cells"]["items"]
    cell_props = {k: v for k, v in cell["properties"].items() if k != "verbatim"}
    cell_props["facet_n"] = {
        "type": "integer",
        "description": "El número `n` de la facet de esa lectura que llena la celda.",
    }
    new_cell = {**cell, "properties": cell_props}
    new_dimension = {
        **dimension,
        "properties": {**dimension["properties"], "cells": {"type": "array", "items": new_cell}},
    }
    return {
        **schema,
        "parameters": {
            **params,
            "properties": {
                **params["properties"],
                "dimensions": {"type": "array", "items": new_dimension},
            },
        },
    }


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
            cells.append(cell)
        dimension["cells"] = cells
    return dimensions


def _group_instruction(groups: tuple[str, ...]) -> str:
    return (
        "\n\nEN ESTA LLAMADA estandariza SOLO las dimensiones de los grupos "
        f"{', '.join(groups)} (son las únicas facetas que recibes; los demás grupos los "
        "estandariza otra llamada paralela). NO entregues `recommendation`: se calcula aparte."
    )


def group_slices(
    readings: list[dict[str, Any]], prior_structure: dict[str, Any]
) -> list[tuple[str, list[dict[str, Any]], dict[str, Any], tuple[str, ...]]]:
    """``(name, readings, prior_structure, groups)`` per non-empty ``FACET_GROUP_SPLIT`` bucket.

    Every slice keeps EVERY reading (with its money core, so the model knows the columns)
    but only the bucket's facets, and the prior dictionary entries of those groups. A
    facet whose group is not in any bucket goes to the last bucket (``other``).
    """
    buckets = list(FACET_GROUP_SPLIT.items())

    def _bucket_of(group: Any) -> str:
        group = str(group or "other")
        for name, groups in buckets:
            if group in groups:
                return name
        return buckets[-1][0]

    dictionary = [e for e in (prior_structure.get("dictionary") or []) if isinstance(e, dict)]
    slices = []
    for name, groups in buckets:
        sliced = [
            {
                **reading,
                "facets": [
                    f for f in (reading.get("facets") or [])
                    if isinstance(f, dict) and _bucket_of(f.get("group")) == name
                ],
            }
            for reading in readings
        ]
        if not any(r["facets"] for r in sliced):
            continue
        prior = {
            **prior_structure,
            "dictionary": [e for e in dictionary if _bucket_of(e.get("group")) == name],
        }
        slices.append((name, sliced, prior, groups))
    return slices


def _submit(
    readings: list[dict[str, Any]],
    prior_structure: dict[str, Any],
    model: str,
    *,
    groups: tuple[str, ...] | None = None,
):
    from app.services import ai

    messages = submit_messages(readings, prior_structure)
    if groups:
        messages[0] = {**messages[0], "content": messages[0]["content"] + _group_instruction(groups)}
    return call_with_quick_retry(
        lambda: ai.tool_call(
            messages=messages,
            tool_schema=submit_tool_schema(),
            model=model,
            timeout=_timeout(),
            max_retries=0,
            max_tokens=settings.AI_COMPARE_MAX_TOKENS,
        )
    )


def standardize_readings(
    readings: list[dict[str, Any]], prior_structure: dict[str, Any]
) -> Standardized:
    """Standardize ``readings`` into one table + a recommendation (provider only, no DB).

    One ``submit_comparison`` call when the assembled input fits
    ``AI_COMPARE_MAX_INPUT_CHARS``; otherwise the batches run IN PARALLEL and merge in
    batch order; then the ``recommend_comparison`` call runs when no pick was named.
    A typed :class:`ai.AIError` propagates (BLOCK-ON-PROVIDER-DOWN).
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
    batched = False
    batch_count = 1

    blob = ai._compare_input_blob(readings, prior_structure)
    total_facets = sum(len(r.get("facets") or []) for r in readings)
    slices = group_slices(readings, prior_structure) if total_facets > GROUP_SPLIT_MIN_FACETS else []
    if len(slices) > 1:
        batched = True
        batch_count = len(slices)
        outcomes = run_groups(
            {
                name: (lambda rb=sliced, pb=prior, g=groups: _submit(rb, pb, compare_model, groups=g))
                for name, sliced, prior, groups in slices
            }
        )
        merged: list[dict[str, Any]] = []
        for name, sliced, _prior, _groups in slices:  # deterministic: bucket order
            outcome = outcomes[name]
            if not outcome.ok:
                raise first_error([outcome])
            r = outcome.value
            call_stats[name] = _stats(r, outcome.seconds)
            # ``facet_n`` is numbered within THIS slice's readings.
            slice_dimensions = fill_cells(
                ai._normalize_dimensions((r.parsed or {}).get("dimensions")), sliced
            )
            if not slice_dimensions:
                raise ai.AIProviderError(
                    "A comparison group produced no dimensions (truncated or empty model "
                    "answer); the step is blocked"
                )
            merged = ai._merge_dimensions(merged, slice_dimensions)
            prompt_tokens += (r.usage or {}).get("prompt_tokens") or 0
            completion_tokens += (r.usage or {}).get("completion_tokens") or 0
            model_name = r.model or compare_model
        dimensions = merged
        recommendation = None  # the always-recommend step below picks
    elif len(blob) <= settings.AI_COMPARE_MAX_INPUT_CHARS:
        call_started = time.monotonic()
        result = _submit(readings, prior_structure, compare_model)
        call_stats["submit"] = _stats(result, time.monotonic() - call_started)
        parsed = result.parsed or {}
        dimensions = fill_cells(ai._normalize_dimensions(parsed.get("dimensions")), readings)
        recommendation = ai._normalize_recommendation(parsed.get("recommendation"))
        model_name = result.model or compare_model
        prompt_tokens = (result.usage or {}).get("prompt_tokens") or 0
        completion_tokens = (result.usage or {}).get("completion_tokens") or 0
        # HARD GUARD: an empty table means a truncated / empty model answer.
        if not dimensions:
            raise ai.AIProviderError(
                "The comparison produced no dimensions (truncated or empty model "
                "answer); the step is blocked rather than committing an empty table"
            )
    else:
        batched = True
        batches = ai._batch_readings(readings, settings.AI_COMPARE_MAX_INPUT_CHARS, prior_structure)
        batch_count = len(batches)
        names = [f"batch_{index + 1}" for index in range(batch_count)]
        outcomes = run_groups(
            {
                name: (lambda batch=batch: _submit(batch, prior_structure, compare_model))
                for name, batch in zip(names, batches)
            }
        )
        merged: list[dict[str, Any]] = []
        # Deterministic merge in BATCH order (the order the sequential loop used).
        for name in names:
            outcome = outcomes[name]
            if not outcome.ok:
                raise first_error([outcome])  # first failing batch in order blocks
            r = outcome.value
            call_stats[name] = _stats(r, outcome.seconds)
            batch_dimensions = fill_cells(
                ai._normalize_dimensions((r.parsed or {}).get("dimensions")), readings
            )
            # HARD GUARD per batch: a truncated batch would silently drop a whole
            # insurer's dimensions from the merged table.
            if not batch_dimensions:
                raise ai.AIProviderError(
                    "A comparison batch produced no dimensions (truncated or empty "
                    "model answer); the step is blocked"
                )
            merged = ai._merge_dimensions(merged, batch_dimensions)
            prompt_tokens += (r.usage or {}).get("prompt_tokens") or 0
            completion_tokens += (r.usage or {}).get("completion_tokens") or 0
            model_name = r.model or compare_model
        dimensions = merged
        recommendation = None  # produced by the unified always-recommend step below
        if not dimensions:
            raise ai.AIProviderError(
                "The merged comparison produced no dimensions; the step is blocked"
            )
        warnings.append(
            f"Comparison input exceeded {settings.AI_COMPARE_MAX_INPUT_CHARS} chars; "
            f"ran in {batch_count} batches and merged."
        )

    # ALWAYS produce a recommendation: if the holistic / merged answer named no
    # pick (or gave none at all), run the dedicated recommend call over the table.
    if recommendation is None or recommendation.get("recommended_comparison_source_id") is None:
        call_started = time.monotonic()
        rec_timeout = _recommend_timeout(call_started - started)
        rec = call_with_quick_retry(
            lambda: ai.tool_call(
                messages=ai._recommend_messages(dimensions, readings),
                tool_schema=ai._recommend_tool_schema(),
                model=model_for(AITask.RECOMMEND),
                timeout=rec_timeout,
                max_retries=0,
                max_tokens=settings.AI_COMPARE_MAX_TOKENS,
            )
        )
        call_stats["recommend"] = _stats(rec, time.monotonic() - call_started)
        rec_value = ai._normalize_recommendation((rec.parsed or {}).get("recommendation"))
        if rec_value is not None:
            recommendation = rec_value
        prompt_tokens += (rec.usage or {}).get("prompt_tokens") or 0
        completion_tokens += (rec.usage or {}).get("completion_tokens") or 0
        # SOFT GUARD: a pickless recommendation degrades to a warning, never a 500.
        if recommendation is None or recommendation.get("recommended_comparison_source_id") is None:
            warnings.append(
                "The AI recommendation named no pick "
                "(recommended_comparison_source_id is null); review the caveats manually."
            )

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


__all__ = ["Standardized", "fill_cells", "group_slices", "standardize_readings", "submit_messages", "submit_tool_schema"]
