"""Fast, tiered POLICY reading (docs/v12/11-fast-reading.md).

A one-call policy read was output-bound (70-90 s, near the 12 000-token cap)
and lost by the browser at the 60 s CloudFront edge. This module splits it:

* **Core tier** — ``core_identity_money`` + ``core_plan``, run IN PARALLEL inside
  ``POST /policies/upload``. Their merge is a regular ``PolicyExtraction``, so
  the EXISTING commit path (``_commit_policy`` -> ``validate_policy_core``, the
  plan, ``ensure_default_parties``, client resolution) is untouched. One failed
  core group fails the read (typed :class:`AIError`, failed extraction row).
* **Technical tier** — ``tech_risk`` + ``tech_deductibles`` + ``tech_clauses``, a
  SECOND request, stored as a SUGGESTION on its own ``extraction`` row; nothing
  reaches the policy until the broker confirms (rule 6). A failed group empties
  only its sections.

Threading rule: the provider calls run in a small pool (:mod:`ai_parallel`)
and receive ONLY plain data (the prepared message list). They never touch the
SQLAlchemy session — every DB read happens before the pool, every write after.

The prompts are Spanish because the documents are (rule 1).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai import Extraction, ExtractionKind, ExtractionStatus
from app.models.case_file import CaseFile
from app.models.document import Document, DocumentCategory
from app.models.user import User
from app.schemas.extraction.policy_tiers import (
    CORE_GROUPS,
    LIMIT_KEYS,
    SECTION_GROUP,
    EXTRA_SECTION_GROUPS,
    TECHNICAL_GROUPS,
    TECHNICAL_SECTIONS,
    TierGroup,
)
from app.services import ai as ai_service
from app.services.ai_error_copy import AI_PARSE_ES, AI_PROVIDER_DOWN_ES, AI_TIMEOUT_ES
from app.services.ai_parallel import GroupOutcome, run_groups

logger = logging.getLogger(__name__)

__all__ = [
    "CORE_PROMPT_VERSION",
    "TECHNICAL_PROMPT_VERSION",
    "READ_TIMEOUT_SECONDS",
    "TIMEOUT_ES",
    "UNAVAILABLE_ES",
    "PARSE_ES",
    "GroupRead",
    "build_base_messages",
    "run_tier",
    "merge_core",
    "technical_sections",
    "read_policy_core",
    "read_policy_technical",
    "technical_error_code",
    "is_technical",
    "is_dismissed",
]

#: Every extraction row records the version that produced it.
CORE_PROMPT_VERSION = "policy-core-v1"
TECHNICAL_PROMPT_VERSION = "policy-technical-v1"

#: Per provider call — same reasoning as ``budget_read.FAST_READ_TIMEOUT_SECONDS``: the
#: provider's throughput swings, the server finishes what the 60 s edge cut, and the SPA
#: recovers it (the upload dialog says the policy may already be registered; the technical
#: card polls ``running``). Kept under the Lambda's 120 s.
READ_TIMEOUT_SECONDS = 90

#: A RUNNING technical row older than this was killed with its Lambda (120 s).
STALE_RUNNING_SECONDS = 150

# User-facing copy (Spanish: it reaches the screen). One vocabulary for every
# fast read — the comparison reads use the same module.
TIMEOUT_ES = AI_TIMEOUT_ES
UNAVAILABLE_ES = AI_PROVIDER_DOWN_ES
PARSE_ES = AI_PARSE_ES


# =============================================================================
# Prompting — the document block is shared, only the last message differs
# =============================================================================

_TIER_SYSTEM = """Eres un extractor de pólizas de seguros del mercado chileno.
Lees UNA póliza emitida y devuelves UN objeto JSON, nada más.

REGLAS DE SALIDA
- JSON crudo y válido. Sin ```, sin comentarios, sin texto antes o después.
- Devuelve SOLO las claves que te pide la tarea. OMITE las claves sin valor: no escribas null,
  "" ni listas vacías. NUNCA inventes un valor.

MONTOS Y CIFRAS
- Los montos son UF salvo que el documento diga otra moneda. El punto es separador de miles y la
  coma es decimal: "UF 17.920" -> 17920 ; "UF 12,99" -> 12.99.
- Prima chilena: neta = afecta + exenta ; IVA = 0,19 x AFECTA (la cobertura de sismo es exenta, el
  IVA NUNCA se calcula sobre la neta) ; total = neta + IVA. Informa lo que el documento DECLARA,
  no calcules lo que falta.
- Las tasas son por mil. Los porcentajes van de 0 a 100.

FECHAS
- Formato ISO "AAAA-MM-DD"; con hora "AAAA-MM-DDTHH:MM". La vigencia corre de 12:00 a 12:00
  salvo que el documento indique otra hora.

IDENTIDAD
- Los RUT se copian tal cual, con su dígito verificador (el sistema los normaliza).
- La compañía se identifica por su RUT o código CMF; si no están impresos, deja solo el nombre.

NO RECONCILIES
- Registra lo que dice ESTA póliza. Si difiere de la propuesta o del contexto, transcríbela igual:
  la diferencia es el producto, no un error que corregir."""


def build_base_messages(
    document: Document, text: str, *, context_prepend: str = ""
) -> list[tuple[str, str]]:
    """The messages every group shares: rules, optional account context, the document.

    Built ONCE per read; each group appends its own task message, so the long
    common prefix is identical across the parallel calls (provider-side cache).
    """
    messages: list[tuple[str, str]] = [("system", _TIER_SYSTEM)]
    if context_prepend:
        messages.append(("system", context_prepend))
    messages.append(
        (
            "human",
            f"Archivo: {document.original_name}\nCategoría: póliza emitida (08)\n\n"
            "--- INICIO DEL DOCUMENTO ---\n"
            f"{text}\n"
            "--- FIN DEL DOCUMENTO ---",
        )
    )
    return messages


def _task_message(group: TierGroup) -> tuple[str, str]:
    return (
        "human",
        f"TAREA ({group.name})\n{group.guidance}\n\n"
        "Devuelve SOLO estas claves (omite las que no tengan valor), con esta forma:\n"
        f"{group.hint}\n\nDevuelve ahora el objeto JSON.",
    )


# =============================================================================
# One group = one provider call (runs in a worker thread: NO session access)
# =============================================================================


@dataclass
class GroupRead:
    """What one group call produced — plain data, safe to build in a thread."""

    name: str
    payload: Any
    parsed: dict[str, Any]
    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def data(self) -> dict[str, Any]:
        """The group's coerced payload as JSON-safe data."""
        if self.payload is not None and hasattr(self.payload, "model_dump"):
            return self.payload.model_dump(mode="json")
        return dict(self.parsed or {})


def _finish_reason(message: Any) -> str | None:
    meta = getattr(message, "response_metadata", None)
    if isinstance(meta, dict):
        reason = meta.get("finish_reason")
        return str(reason) if reason else None
    return None


def _typed(exc: BaseException) -> ai_service.AIError:
    """Every failure as a typed AIError; a timeout carries the honest copy."""
    wrapped = ai_service._wrap_provider_error(exc)
    if isinstance(wrapped, ai_service.AITimeout):
        return ai_service.AITimeout(TIMEOUT_ES)
    return wrapped


def _invoke_group(
    group: TierGroup, messages: Sequence[tuple[str, str]], *, timeout: float
) -> GroupRead:
    """One ``json_mode`` call for ``group`` (the monkeypatch seam for tests).

    ``with_structured_output(..., method="json_mode", include_raw=True)``: DeepInfra
    supports JSON mode, not strict tool schemas. The fence-tolerant JSON recovery
    and the drop-bad-fields coercion are the same as every registry read.
    """
    llm = ai_service._chat_model(temperature=0.0, max_tokens=group.max_tokens, timeout=timeout)
    try:
        structured = llm.with_structured_output(group.schema, method="json_mode", include_raw=True)
        answer = structured.invoke(list(messages))
    except Exception as exc:  # noqa: BLE001 - typed AIError or nothing
        raise _typed(exc) from exc

    if isinstance(answer, dict):
        raw_message = answer.get("raw")
        payload = answer.get("parsed")
    else:  # pragma: no cover - include_raw always yields the dict shape
        raw_message, payload = None, answer

    content = (
        ai_service._message_text(raw_message, allow_reasoning=True) if raw_message is not None else ""
    )
    prompt_tokens, completion_tokens = ai_service._message_usage(raw_message)
    finish_reason = _finish_reason(raw_message)
    warnings: list[str] = []

    parsed: dict[str, Any] | None = None
    if content.strip():
        try:
            parsed = ai_service._extract_json_object(content)
        except ai_service.AIParseError:
            parsed = None
    if parsed is None and payload is not None and hasattr(payload, "model_dump"):
        parsed = payload.model_dump(mode="json")
    if parsed is None:
        if not content.strip():
            raise ai_service.AIProviderError("The AI provider returned an empty message")
        detail = " (output truncated: finish_reason=length)" if finish_reason == "length" else ""
        raise ai_service.AIParseError(f"The model did not return a JSON object{detail}")

    if payload is None or not isinstance(payload, group.schema):
        payload, coerce_warnings = ai_service._coerce_payload(group.schema, parsed)
        warnings.extend(coerce_warnings)
    if finish_reason == "length":
        warnings.append(f"{group.name}: the answer hit the output budget and may be incomplete")

    return GroupRead(
        name=group.name,
        payload=payload,
        parsed=parsed,
        content=content,
        model=ai_service._message_model(raw_message),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finish_reason=finish_reason,
        warnings=warnings,
    )


def _call_timeout() -> float:
    return float(min(READ_TIMEOUT_SECONDS, settings.AI_TIMEOUT_SECONDS or READ_TIMEOUT_SECONDS))


def run_tier(
    groups: Iterable[TierGroup],
    base_messages: Sequence[tuple[str, str]],
    *,
    timeout: float | None = None,
) -> dict[str, GroupOutcome]:
    """Run every group of a tier in parallel; ``{name: GroupOutcome(value=GroupRead)}``.

    Never raises: the caller decides whether a failed group sinks the read.
    """
    per_call = timeout if timeout is not None else _call_timeout()
    base = list(base_messages)

    def _call(group: TierGroup):
        messages = [*base, _task_message(group)]
        return lambda: _invoke_group(group, messages, timeout=per_call)

    return run_groups({group.name: _call(group) for group in groups})


def _telemetry(outcomes: dict[str, GroupOutcome]) -> dict[str, Any]:
    """Per-group seconds / tokens / finish_reason for ``extraction.raw_output``."""
    out: dict[str, Any] = {}
    for name, outcome in outcomes.items():
        row: dict[str, Any] = {"seconds": round(outcome.seconds, 2)}
        if outcome.ok and isinstance(outcome.value, GroupRead):
            read: GroupRead = outcome.value
            row.update(
                {
                    "prompt_tokens": read.prompt_tokens,
                    "completion_tokens": read.completion_tokens,
                    "finish_reason": read.finish_reason,
                    "model": read.model,
                    "warnings": read.warnings,
                    "content": read.content,
                }
            )
        else:
            error = _typed(outcome.error) if outcome.error is not None else None
            row.update(
                {
                    "error": f"{type(error).__name__}: {error}" if error else None,
                    "error_code": technical_error_code(error),
                }
            )
        out[name] = row
    return out


def _token_sum(outcomes: dict[str, GroupOutcome], attr: str) -> int | None:
    values = [
        getattr(o.value, attr)
        for o in outcomes.values()
        if o.ok and isinstance(o.value, GroupRead) and getattr(o.value, attr) is not None
    ]
    return sum(values) if values else None


def technical_error_code(exc: BaseException | None) -> str | None:
    """``ai_timeout`` | ``ai_parse`` | ``ai_unavailable`` (the TechnicalRead contract)."""
    if exc is None:
        return None
    if isinstance(exc, ai_service.AITimeout):
        return "ai_timeout"
    if isinstance(exc, ai_service.AIParseError):
        return "ai_parse"
    return "ai_unavailable"


_ERROR_COPY = {"ai_timeout": TIMEOUT_ES, "ai_parse": PARSE_ES, "ai_unavailable": UNAVAILABLE_ES}


# =============================================================================
# Merging (pure)
# =============================================================================

_CORE_KEYS = frozenset(k for g in CORE_GROUPS for k in g.keys)
_TECHNICAL_KEYS = frozenset(k for g in TECHNICAL_GROUPS for k in g.keys)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_value(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_value(v) for v in value)
    return True


def _prune(value: Any) -> Any:
    """Drop empty keys and empty rows (the model was told to omit them)."""
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if _has_value(v)}
    if isinstance(value, list):
        return [_prune(v) for v in value if _has_value(v)]
    return value


def merge_core(identity: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Merge the two core groups into the shape ``PolicyExtraction`` / the committer read.

    * each group's OWN keys come from that group;
    * ``insured`` (a list in the slim schema) -> ``insured`` = the first party
      (what client resolution reads) + ``insured_parties`` = the whole list;
    * ``beneficiaries`` and unknown extra keys survive (``extra="allow"``: an
      unanticipated value is worth more to the reviewer than a dropped one);
    * keys owned by the TECHNICAL tier never ride on the core (they arrive as a
      suggestion, confirmed separately).
    """
    identity = dict(identity or {})
    plan = dict(plan or {})
    identity_keys = set(CORE_GROUPS[0].keys)
    plan_keys = set(CORE_GROUPS[1].keys)

    merged: dict[str, Any] = {}
    for key in identity_keys:
        if key in identity:
            merged[key] = identity[key]
    for key in plan_keys:
        if key in plan:
            merged[key] = plan[key]
    for source in (identity, plan):
        for key, value in source.items():
            if key in _CORE_KEYS or key in _TECHNICAL_KEYS:
                continue
            merged.setdefault(key, value)

    merged = _prune(merged)

    parties = merged.get("insured")
    if isinstance(parties, dict):
        parties = [parties]
    if isinstance(parties, list):
        parties = [p for p in parties if isinstance(p, dict) and _has_value(p)]
        if parties:
            merged["insured"] = parties[0]
            if len(parties) > 1:
                merged["insured_parties"] = parties
        else:
            merged.pop("insured", None)
    return merged


def technical_sections(datas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The TechnicalRead ``sections`` from the technical groups' data (pure)."""
    risk = _prune(datas.get("tech_risk") or {})
    sublimits = _prune(datas.get("tech_sublimits") or {})
    deductibles = _prune(datas.get("tech_deductibles") or {})
    clauses = _prune(datas.get("tech_clauses") or {})
    return {
        "items": list(risk.get("items") or []),
        "coverages": list(risk.get("coverages") or []) + list(sublimits.get("sublimits") or []),
        "deductibles": list(deductibles.get("deductibles") or []),
        "warranties": list(deductibles.get("warranties") or []),
        "exclusions": list(clauses.get("exclusions") or []),
        "particular_clauses": list(clauses.get("particular_clauses") or []),
        "limits": {key: risk.get(key) for key in LIMIT_KEYS},
    }


def _core_confidence(identity: dict[str, Any], merged: dict[str, Any]) -> Decimal:
    raw = ai_service._to_decimal(identity.get("confidence"))
    if raw is not None and Decimal(0) <= raw <= Decimal(100):
        return raw.quantize(Decimal("0.001"))
    wanted = ("policy_number", "insurer", "insured", "period_start_at", "period_end_at",
              "premium", "installments")
    filled = sum(1 for key in wanted if _has_value(merged.get(key)))
    return (Decimal(100) * Decimal(filled) / Decimal(len(wanted))).quantize(Decimal("0.001"))


# =============================================================================
# Core tier (inside POST /policies/upload)
# =============================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _context_for(db: Session, document: Document, broker_id: int) -> str:
    if document.case_file_id is None:
        return ""
    case_file = db.get(CaseFile, document.case_file_id)
    if case_file is None or case_file.broker_id != broker_id:
        return ""
    return ai_service._account_extraction_context(db, case_file, DocumentCategory.POLICY)


#: A retry of ``POST /policies/upload`` (after a 422 ``insured_unresolved`` /
#: ``insurer_unresolved``) re-uses an unconfirmed core read this recent instead
#: of paying the provider again — the PDF is read ONCE per upload attempt chain.
CORE_REUSE_WINDOW = timedelta(minutes=30)

REUSED_READ_ES = (
    "Se reutilizó la lectura de este PDF hecha hace un momento (no se volvió a leer)."
)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def reusable_core_read(
    db: Session, *, document: Document, broker_id: int, now: datetime | None = None
) -> Extraction | None:
    """The latest SUCCEEDED core read of ``document`` that a retry may re-use.

    Same broker + document + folder context (the prompt carries the folder's
    context, so a read made under another folder is not re-used), the current
    ``CORE_PROMPT_VERSION``, created within :data:`CORE_REUSE_WINDOW`, and NOT
    yet confirmed into a policy (``raw_output.confirmation`` — a committed read
    is never replayed).
    """
    cutoff = (now or _now()) - CORE_REUSE_WINDOW
    stmt = (
        select(Extraction)
        .where(
            Extraction.broker_id == broker_id,
            Extraction.document_id == document.id,
            Extraction.prompt_version == CORE_PROMPT_VERSION,
            Extraction.status == ExtractionStatus.SUCCEEDED,
            Extraction.parsed.isnot(None),
        )
        .order_by(Extraction.id.desc())
        .limit(5)
    )
    if document.case_file_id is None:
        stmt = stmt.where(Extraction.case_file_id.is_(None))
    else:
        stmt = stmt.where(Extraction.case_file_id == document.case_file_id)
    for row in db.scalars(stmt).all():
        created = _as_aware(row.created_at or row.started_at)
        if created is None or created < cutoff:
            return None  # newest first: everything after is older still
        if is_confirmed(row):
            return None  # the latest read already became a policy: read afresh
        return row
    return None


def _reuse_core_read(
    extraction: Extraction, spec: Any
) -> "ai_service.DocumentExtractionResult | None":
    """Rebuild the upload's result from a stored core read (no provider call)."""
    from app.schemas.extraction.policy import PolicyExtraction

    stored = extraction.parsed if isinstance(extraction.parsed, dict) else None
    if not stored:
        return None
    raw = extraction.raw_output if isinstance(extraction.raw_output, dict) else {}
    try:
        payload = PolicyExtraction.model_validate(stored)
        warnings: list[str] = []
    except Exception:  # noqa: BLE001 — fall back to the lenient coercion
        payload, warnings = ai_service._coerce_payload(PolicyExtraction, stored)
    if payload is None:
        return None
    previous = raw.get("warnings")
    if isinstance(previous, list):
        warnings = [*(str(w) for w in previous), *warnings]
    merged = raw.get("parsed") if isinstance(raw.get("parsed"), dict) else stored
    return ai_service.DocumentExtractionResult(
        extraction=extraction,
        spec=spec,
        payload=payload,
        parsed=merged,
        warnings=[*warnings, REUSED_READ_ES],
    )


def read_policy_core(
    db: Session,
    *,
    document_id: int,
    broker_id: int,
    user: User | None = None,
    reuse_recent: bool = True,
) -> "ai_service.DocumentExtractionResult":
    """The upload's read: two parallel core groups merged into a ``PolicyExtraction``.

    Same contract as :func:`app.services.ai.extract_document` for the POLICY
    category (the upload router and the committer are unchanged): exactly ONE
    ``extraction`` row in every outcome; :class:`DocumentUnavailable` before any
    row; any failed group -> the row is marked failed and a typed
    :class:`AIError` is raised.

    ``reuse_recent``: a retry of the same upload (the broker resolved the
    insured / the insurer after a 422) re-uses the latest unconfirmed
    SUCCEEDED core read of the document (:func:`reusable_core_read`) instead of
    calling the provider again — no new row, and the result carries
    :data:`REUSED_READ_ES` among its warnings.
    """
    from app.schemas.extraction.registry import spec_for

    document = ai_service.get_document(db, document_id, broker_id)
    if document is None:
        raise ai_service.DocumentUnavailable(f"Document {document_id} not found in this workspace")
    spec = spec_for(DocumentCategory.POLICY)
    if reuse_recent:
        previous = reusable_core_read(db, document=document, broker_id=broker_id)
        if previous is not None:
            reused = _reuse_core_read(previous, spec)
            if reused is not None:
                return reused
    text = ai_service.load_document_text(document)  # fails before any row exists

    extraction = Extraction(
        broker_id=broker_id,
        document_id=document.id,
        case_file_id=document.case_file_id,
        category=DocumentCategory.POLICY,
        kind=ExtractionKind.POLICY,
        model=settings.AI_MODEL,
        prompt_version=CORE_PROMPT_VERSION,
        status=ExtractionStatus.RUNNING,
        started_at=_now(),
        created_by_id=getattr(user, "id", None),
    )
    db.add(extraction)
    db.flush()
    base = build_base_messages(document, text, context_prepend=_context_for(db, document, broker_id))
    # Release the transaction before the (long) provider calls; the threads
    # below never see the session.
    db.commit()

    started = time.monotonic()
    outcomes = run_tier(CORE_GROUPS, base)
    wall = round(time.monotonic() - started, 2)
    raw: dict[str, Any] = {"tier": "core", "wall_seconds": wall, "groups": _telemetry(outcomes)}

    failed = {name: o for name, o in outcomes.items() if not o.ok}
    if failed:
        errors = {name: _typed(o.error) for name, o in failed.items()}
        extraction.status = ExtractionStatus.FAILED
        extraction.error = "; ".join(
            f"{name}: {type(err).__name__}: {err}" for name, err in errors.items()
        )[:4000]
        extraction.raw_output = raw
        extraction.prompt_tokens = _token_sum(outcomes, "prompt_tokens")
        extraction.completion_tokens = _token_sum(outcomes, "completion_tokens")
        extraction.finished_at = _now()
        db.commit()
        first = next(iter(errors.values()))
        if isinstance(first, (ai_service.AINotConfigured, ai_service.DocumentUnavailable)):
            raise first
        if any(isinstance(err, ai_service.AITimeout) for err in errors.values()):
            raise ai_service.AITimeout(TIMEOUT_ES)
        if isinstance(first, ai_service.AIParseError):
            raise ai_service.AIParseError(PARSE_ES)
        raise ai_service.AIProviderError(UNAVAILABLE_ES)

    reads: dict[str, GroupRead] = {name: o.value for name, o in outcomes.items()}
    identity = reads["core_identity_money"].data()
    plan = reads["core_plan"].data()
    merged = merge_core(identity, plan)

    from app.schemas.extraction.policy import PolicyExtraction

    payload, warnings = ai_service._coerce_payload(PolicyExtraction, merged)
    for read in reads.values():
        warnings = [*read.warnings, *warnings]

    raw["parsed"] = merged
    # Kept so a re-used read (a retry of the upload) reports the same warnings.
    raw["warnings"] = list(warnings)
    extraction.model = reads["core_identity_money"].model or settings.AI_MODEL
    extraction.prompt_tokens = _token_sum(outcomes, "prompt_tokens")
    extraction.completion_tokens = _token_sum(outcomes, "completion_tokens")
    extraction.raw_output = raw
    extraction.parsed = payload.model_dump(mode="json") if payload is not None else merged
    extraction.confidence = _core_confidence(identity, merged)
    extraction.status = ExtractionStatus.SUCCEEDED
    extraction.finished_at = _now()
    db.commit()
    db.refresh(extraction)

    return ai_service.DocumentExtractionResult(
        extraction=extraction,
        spec=spec,
        payload=payload,
        parsed=merged,
        warnings=warnings,
    )


# =============================================================================
# Technical tier (POST /policies/{id}/technical-read)
# =============================================================================


def is_technical(extraction: Extraction) -> bool:
    return extraction.prompt_version == TECHNICAL_PROMPT_VERSION


def is_dismissed(extraction: Extraction) -> bool:
    raw = extraction.raw_output if isinstance(extraction.raw_output, dict) else {}
    return isinstance(raw.get("dismissed"), dict)


def is_confirmed(extraction: Extraction) -> bool:
    raw = extraction.raw_output if isinstance(extraction.raw_output, dict) else {}
    return isinstance(raw.get("confirmation"), dict)


def is_stale(extraction: Extraction, *, now: datetime | None = None) -> bool:
    """A RUNNING row whose request died with its Lambda."""
    if extraction.status != ExtractionStatus.RUNNING:
        return False
    started = extraction.started_at
    if started is None:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (now or _now()) - started > timedelta(seconds=STALE_RUNNING_SECONDS)


def technical_rows(db: Session, *, broker_id: int, document_id: int) -> list[Extraction]:
    """Every technical read of a document, newest first."""
    return list(
        db.scalars(
            select(Extraction)
            .where(
                Extraction.broker_id == broker_id,
                Extraction.document_id == document_id,
                Extraction.prompt_version == TECHNICAL_PROMPT_VERSION,
            )
            .order_by(Extraction.id.desc())
        ).all()
    )


def read_policy_technical(
    db: Session,
    *,
    document: Document,
    broker_id: int,
    policy_id: int | None = None,
    user: User | None = None,
) -> Extraction:
    """Run the three technical groups in parallel and store ONE suggestion row.

    Returns the row in every outcome: ``succeeded`` when at least one group
    answered (a failed group leaves its sections empty and is reported in
    ``raw_output.section_errors``), ``failed`` when every group failed (with
    ``raw_output.error_code``). Raises only :class:`DocumentUnavailable` (no
    readable text -> no row) and :class:`AINotConfigured` (after the row).
    """
    text = ai_service.load_document_text(document)

    extraction = Extraction(
        broker_id=broker_id,
        document_id=document.id,
        case_file_id=document.case_file_id,
        category=DocumentCategory.POLICY,
        kind=ExtractionKind.POLICY,
        model=settings.AI_MODEL,
        prompt_version=TECHNICAL_PROMPT_VERSION,
        status=ExtractionStatus.RUNNING,
        started_at=_now(),
        created_by_id=getattr(user, "id", None),
        raw_output={"tier": "technical", "policy_id": policy_id},
    )
    db.add(extraction)
    db.flush()
    base = build_base_messages(document, text)
    db.commit()  # a concurrent GET sees "running"; the threads never see the session

    started = time.monotonic()
    outcomes = run_tier(TECHNICAL_GROUPS, base)
    wall = round(time.monotonic() - started, 2)

    datas: dict[str, dict[str, Any]] = {}
    group_errors: dict[str, ai_service.AIError] = {}
    warnings: list[str] = []
    for name, outcome in outcomes.items():
        if outcome.ok and isinstance(outcome.value, GroupRead):
            datas[name] = outcome.value.data()
            warnings.extend(outcome.value.warnings)
        else:
            group_errors[name] = _typed(outcome.error) if outcome.error else ai_service.AIProviderError("")

    section_errors: dict[str, str] = {}
    for section, group in SECTION_GROUP.items():
        for owner in (group, *EXTRA_SECTION_GROUPS.get(section, ())):
            if owner in group_errors:
                section_errors[section] = _ERROR_COPY[
                    technical_error_code(group_errors[owner]) or "ai_unavailable"
                ]
                break
    raw: dict[str, Any] = {
        "tier": "technical",
        "policy_id": policy_id,
        "wall_seconds": wall,
        "groups": _telemetry(outcomes),
        "section_errors": section_errors,
        "warnings": warnings,
    }
    extraction.prompt_tokens = _token_sum(outcomes, "prompt_tokens")
    extraction.completion_tokens = _token_sum(outcomes, "completion_tokens")
    extraction.finished_at = _now()

    if not datas:
        codes = [technical_error_code(err) for err in group_errors.values()]
        code = "ai_timeout" if "ai_timeout" in codes else ("ai_parse" if "ai_parse" in codes else "ai_unavailable")
        raw["error_code"] = code
        raw["message"] = _ERROR_COPY[code]
        extraction.status = ExtractionStatus.FAILED
        extraction.error = "; ".join(
            f"{name}: {type(err).__name__}: {err}" for name, err in group_errors.items()
        )[:4000]
        extraction.raw_output = raw
        db.commit()
        db.refresh(extraction)
        not_configured = next(
            (e for e in group_errors.values() if isinstance(e, ai_service.AINotConfigured)), None
        )
        if not_configured is not None:
            raise not_configured
        return extraction

    first = next(iter(outcomes.values()))
    if first.ok and isinstance(first.value, GroupRead):
        extraction.model = first.value.model or settings.AI_MODEL
    sections = technical_sections(datas)
    raw["parsed"] = sections
    extraction.raw_output = raw
    extraction.parsed = sections
    counts = sum(len(sections[s]) for s in TECHNICAL_SECTIONS if s != "limits")
    groups_ok = len(datas)
    extraction.confidence = Decimal(
        100 * groups_ok / len(TECHNICAL_GROUPS) if counts else 0
    ).quantize(Decimal("0.001"))
    extraction.status = ExtractionStatus.SUCCEEDED
    db.commit()
    db.refresh(extraction)
    return extraction


def jsonable(value: Any) -> Any:
    """JSON-safe copy (Decimals/dates as strings)."""
    return json.loads(json.dumps(value, default=str))
