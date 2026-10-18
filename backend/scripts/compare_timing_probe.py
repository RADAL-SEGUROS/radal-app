"""Manual LIVE timing probe for the comparison path (docs/v12/11-fast-reading.md).

NOT part of the suite (the suite never reaches the provider). Runs the grouped,
parallel cotización read (``app.services.budget_read``) on each PDF given — the PDFs in
parallel too, as the SPA uploads them — and prints per-group seconds / tokens / facet
counts; optionally the legacy single-call read of the FIRST PDF as a baseline, and the
standardization over the readings. No database: transient ``Document`` objects with an
absolute ``s3_key`` (``ai.load_document_text`` reads local paths first).

    cd backend
    MEDIA_BACKEND=local .venv/bin/python -m scripts.compare_timing_probe a.pdf b.pdf c.pdf \\
        [--baseline] [--no-standardize] [--env-file PATH]

The key comes from ``backend/.env`` (or ``--env-file``) and is NEVER printed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR, Settings, settings
from app.models.document import Document, DocumentCategory
from app.services import ai
from app.services.ai_models import AITask, model_for
from app.services.budget_read import read_budget_proposal
from app.services.comparison_standardize import standardize_readings


def _load_env(path: Path) -> None:
    """Copy the AI settings of ``path`` onto the live ``settings`` (key never printed)."""
    if not path.is_file():
        return
    loaded = Settings(_env_file=str(path))
    for name in ("AI_API_KEY", "AI_BASE_URL", "AI_MODEL", "AI_MODEL_TIERS",
                 "AI_COMPARE_MAX_INPUT_CHARS", "AI_COMPARE_MAX_TOKENS", "AI_EXTRACTION_MAX_TOKENS"):
        setattr(settings, name, getattr(loaded, name))


def _document(path: Path) -> Document:
    return Document(
        id=0,
        broker_id=0,
        s3_key=str(path.resolve()),
        original_name=path.name,
        mime_type="application/pdf",
        category=DocumentCategory.BUDGET_PROPOSAL,
    )


def _read_one(path: Path) -> dict[str, Any]:
    from app.schemas.extraction.registry import spec_for

    spec = spec_for(DocumentCategory.BUDGET_PROPOSAL)
    document = _document(path)
    text = ai.load_document_text(document)
    started = time.monotonic()
    read = read_budget_proposal(spec, document, text, model=model_for(AITask.EXTRACT))
    wall = time.monotonic() - started
    payload, warnings = (None, [])
    if read.parsed is not None:
        payload, warnings = ai._coerce_payload(spec.schema, dict(read.parsed))
    return {"path": path, "chars": len(text), "read": read, "wall": wall,
            "payload": payload, "warnings": warnings}


def _baseline(path: Path) -> None:
    """The OLD single ``record_budget_proposal`` call, for comparison."""
    from app.schemas.extraction.budget_proposal import budget_proposal_tool_schema
    from app.schemas.extraction.registry import spec_for

    spec = spec_for(DocumentCategory.BUDGET_PROPOSAL)
    document = _document(path)
    text = ai.load_document_text(document)
    started = time.monotonic()
    try:
        result = ai.tool_call(
            messages=ai._budget_proposal_messages(spec, document, text),
            tool_schema=budget_proposal_tool_schema(),
            model=model_for(AITask.EXTRACT),
            timeout=180,
            max_retries=0,
            schema=spec.schema,
        )
    except ai.AIError as exc:
        print(f"  BASELINE {path.name}: FAILED after {time.monotonic() - started:.1f}s "
              f"({type(exc).__name__}: {exc})")
        return
    usage = result.usage or {}
    facets = (result.parsed or {}).get("facets") or []
    print(f"  BASELINE {path.name}: {time.monotonic() - started:.1f}s · "
          f"out {usage.get('completion_tokens')} tok · in {usage.get('prompt_tokens')} tok · "
          f"{len(facets)} facets · finish={result.finish_reason}")


def _group_counts(facets: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for facet in facets:
        group = getattr(facet, "group", None) or "other"
        counts[group] = counts.get(group, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--baseline", action="store_true", help="also time the OLD single call (first PDF)")
    parser.add_argument("--no-standardize", action="store_true")
    parser.add_argument(
        "--sequential", action="store_true", help="read the PDFs one after another (as the SPA uploads them)"
    )
    parser.add_argument("--env-file", type=Path, default=BASE_DIR / ".env")
    args = parser.parse_args(argv)

    _load_env(args.env_file)
    if not settings.AI_API_KEY:
        print("AI_API_KEY is not configured (backend/.env or --env-file); nothing to probe.")
        return 2
    print(f"model={model_for(AITask.EXTRACT)} · compare_max_input={settings.AI_COMPARE_MAX_INPUT_CHARS}")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1 if args.sequential else len(args.pdfs)) as pool:
        results = list(pool.map(_read_one, args.pdfs))
    print(f"\nREADS ({'one after another' if args.sequential else 'all PDFs in parallel'}): {time.monotonic() - started:.1f}s wall")
    for item in results:
        read = item["read"]
        status = "OK" if read.error is None else f"FAILED ({type(read.error).__name__}: {read.error})"
        print(f"- {item['path'].name} · {item['chars']} chars · {item['wall']:.1f}s · {status}")
        for name, stat in read.group_stats.items():
            print(f"    {name:18s} {stat.get('seconds'):6.1f}s · out {stat.get('completion_tokens')} tok · "
                  f"in {stat.get('prompt_tokens')} tok · facets {stat.get('facet_count', '-')} · "
                  f"finish={stat.get('finish_reason')}{' · ' + stat['error'] if 'error' in stat else ''}")
        payload = item["payload"]
        if payload is not None:
            print(f"    merged: {len(payload.facets)} facets {_group_counts(payload.facets)} · "
                  f"type={payload.document_type} · total={payload.total_premium_uf} · "
                  f"net={payload.net_premium_uf} · vat={payload.vat_uf} · insurer={payload.insurer_name!r}")
        for warning in item["warnings"][:3]:
            print(f"    warning: {warning}")

    if args.baseline:
        print("\nBASELINE (old single call):")
        _baseline(args.pdfs[0])

    ok = [item for item in results if item["payload"] is not None]
    if not args.no_standardize and ok:
        readings = []
        for index, item in enumerate(ok, start=1):
            parsed = item["payload"].model_dump(mode="json")
            readings.append(ai.build_reading(
                comparison_source_id=index,
                proposal_id=None,
                is_wrong_file=parsed.get("document_type") == "not_a_proposal",
                wrong_file_reason=parsed.get("rejection_reason"),
                money_core={k: parsed.get(k) for k in ai._COMPARE_CORE_KEYS if parsed.get(k) is not None},
                facets=parsed.get("facets") or [],
            ))
        blob = ai._compare_input_blob(readings, {"dictionary": []})
        print(f"\nSTANDARDIZE {len(readings)} readings · input {len(blob)} chars")
        try:
            result = standardize_readings(readings, {"dictionary": []})
        except ai.AIError as exc:
            print(f"  FAILED ({type(exc).__name__}: {exc})")
            return 1
        print(f"  {result.wall_seconds:.1f}s wall · batched={result.batched} ({result.batch_count}) · "
              f"{len(result.dimensions)} dimensions · pick="
              f"{(result.recommendation or {}).get('recommended_comparison_source_id')}")
        print("  calls: " + json.dumps(result.call_stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
