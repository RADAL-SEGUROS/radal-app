"""Live timing probe for the tiered POLICY read (fast reading, docs/v12/11).

MANUAL ONLY — this makes REAL provider calls (it is not part of the suite, and
the suite's conftest blanks the key anyway). Needs ``AI_API_KEY`` in the env or
``backend/.env``; the key is never printed. No database: each PDF is wrapped in
a transient, unsaved ``Document`` whose ``s3_key`` is the absolute path (the
loader reads local files first).

Usage (from ``backend/``)::

    MEDIA_BACKEND=local .venv/bin/python -m scripts.ai_timing_probe a.pdf [b.pdf …] \
        [--tier core|technical|both]

Prints, per PDF and tier: wall clock, per-group seconds / prompt + completion
tokens / finish_reason / item counts, and the core sanity checks (policy
number, premium reconciliation, Σ cuotas vs the gross premium).
"""
from __future__ import annotations

import argparse
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.document import Document, DocumentCategory
from app.schemas.extraction.policy import PolicyExtraction
from app.schemas.extraction.policy_tiers import CORE_GROUPS, TECHNICAL_GROUPS
from app.services import ai as ai_service
from app.services import policy_reading
from app.services.ai_parallel import GroupOutcome


def _counts(data: dict[str, Any]) -> str:
    parts = [f"{key}={len(value)}" for key, value in data.items() if isinstance(value, list) and value]
    return ", ".join(parts) or "-"


def _print_groups(outcomes: dict[str, GroupOutcome]) -> None:
    for name, outcome in outcomes.items():
        if outcome.ok:
            read: policy_reading.GroupRead = outcome.value
            print(
                f"    {name:<22} {outcome.seconds:6.1f}s  in={read.prompt_tokens}  "
                f"out={read.completion_tokens}  finish={read.finish_reason}  "
                f"items: {_counts(read.data())}"
                + (f"  warnings={len(read.warnings)}" if read.warnings else "")
            )
        else:
            error = policy_reading._typed(outcome.error)
            print(f"    {name:<22} {outcome.seconds:6.1f}s  FAILED {type(error).__name__}: {error}")


def _core_checks(outcomes: dict[str, GroupOutcome]) -> None:
    if not all(o.ok for o in outcomes.values()):
        print("    core: a group failed -> the upload would answer a typed AI error")
        return
    identity = outcomes["core_identity_money"].value.data()
    plan = outcomes["core_plan"].value.data()
    merged = policy_reading.merge_core(identity, plan)
    payload, warnings = ai_service._coerce_payload(PolicyExtraction, merged)
    premium = payload.premium
    print(f"    policy_number      : {payload.policy_number}")
    print(f"    insurer            : {payload.insurer.legal_name} (rut={payload.insurer.rut}, "
          f"cmf={payload.insurer.cmf_code})")
    print(f"    insured            : {payload.insured.legal_name} ({payload.insured.rut})"
          + (f" +{len(merged.get('insured_parties', [])) - 1} more" if merged.get("insured_parties") else ""))
    broker = merged.get("broker") or {}
    print(f"    corredor (printed) : {broker.get('legal_name') or broker.get('name') or 'NOT READ -> core 422 not_a_policy'}")
    print(f"    vigencia           : {payload.period_start_at} -> {payload.period_end_at}")
    print(f"    premium            : taxable={premium.taxable_premium_uf} exempt={premium.exempt_premium_uf} "
          f"net={premium.net_premium_uf} vat={premium.vat_uf} total={premium.total_premium_uf}")
    print(f"    premium reconciles : {'yes' if not premium.inconsistencies else premium.inconsistencies}")
    rows = [row for row in payload.installments if row.gross_amount_uf is not None]
    total = sum((row.gross_amount_uf for row in rows), Decimal("0"))
    gross = premium.total_premium_uf
    balanced = gross is not None and abs(total - gross) <= Decimal("0.05")
    print(f"    cuotas             : {len(payload.installments)} (Σ gross = {total} vs total {gross}"
          f" -> {'balanced' if balanced else 'DOES NOT balance'})")
    print(f"    commission_pct     : {payload.commission_pct}  payment_mode: {payload.payment_mode}")
    beneficiaries = merged.get("beneficiaries") or []
    if beneficiaries:
        print(f"    beneficiaries      : {[(b.get('name'), b.get('type')) for b in beneficiaries]}")
    if warnings:
        print(f"    coercion warnings  : {warnings}")


def probe(path: Path, tiers: list[str]) -> dict[str, float]:
    document = Document(
        id=0,
        broker_id=0,
        s3_key=str(path.resolve()),
        original_name=path.name,
        mime_type="application/pdf",
        category=DocumentCategory.POLICY,
    )
    started = time.monotonic()
    text = ai_service.load_document_text(document)
    base = policy_reading.build_base_messages(document, text)
    print(f"\n=== {path.name}  ({len(text):,} chars of text, loaded in {time.monotonic() - started:.1f}s)")

    walls: dict[str, float] = {}
    for tier in tiers:
        groups = CORE_GROUPS if tier == "core" else TECHNICAL_GROUPS
        started = time.monotonic()
        outcomes = policy_reading.run_tier(groups, base)
        walls[tier] = time.monotonic() - started
        out_tokens = sum(
            (o.value.completion_tokens or 0) for o in outcomes.values() if o.ok
        )
        print(f"  [{tier}] wall {walls[tier]:.1f}s  (output tokens Σ {out_tokens:,})")
        _print_groups(outcomes)
        if tier == "core":
            _core_checks(outcomes)
    return walls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--tier", choices=("core", "technical", "both"), default="both")
    args = parser.parse_args(argv)

    if not settings.AI_API_KEY:
        print("AI_API_KEY is not configured (env or backend/.env) — nothing to probe.")
        return 2
    print(f"model={settings.AI_MODEL}  base_url={settings.AI_BASE_URL}  "
          f"per-call timeout={policy_reading._call_timeout():.0f}s  key=configured")
    tiers = ["core", "technical"] if args.tier == "both" else [args.tier]
    summary: list[tuple[str, dict[str, float]]] = []
    for pdf in args.pdfs:
        if not pdf.is_file():
            print(f"skip: {pdf} is not a file")
            continue
        summary.append((pdf.name, probe(pdf, tiers)))

    print("\n=== summary (wall seconds)")
    for name, walls in summary:
        print(f"  {name[:60]:<60} " + "  ".join(f"{tier}={secs:.1f}s" for tier, secs in walls.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
