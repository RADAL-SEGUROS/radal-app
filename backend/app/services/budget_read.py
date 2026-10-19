"""Parallel, grouped read of ONE cotización (fast reading, docs/v12/11-fast-reading.md).

The single ``record_budget_proposal`` call took 59-100 s per offer on dev — OUTPUT-bound
(≈150 tok/s on GLM-5.3-Flash), mostly the facet list with its verbatim wording — so the
browser lost it at CloudFront's 60 s. Here the same read runs as independent provider calls
at the same time (:func:`app.services.ai_parallel.run_groups`):

* ``money_core`` — the wrong-file verdict + the fixed money/period core (small output);
* ``facets`` — the open facet list; when the document text is long it is split by facet
  ``group`` into THREE calls: ``facets_sublimits`` (sublimit), ``facets_cover`` (coverage ·
  deductible · warranty) and ``facets_exclusions`` (exclusion · clause · other) — see
  :data:`FACET_GROUP_SPLIT` for why three and not two.

Every call sends the SAME system prompt + document text (only the final instruction
differs), so the provider can reuse the cached prefix. The groups are merged back into the
exact ``BudgetProposalExtraction`` shape the router / promote / grid / standardizer consume.

This module is DB-free on purpose: threads never touch a SQLAlchemy session, and the manual
timing probe (``scripts/compare_timing_probe.py``) runs it without a database.
``app.services.ai.extract_budget_proposal`` owns the ``extraction`` row (rule 6).
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from app.core.config import settings
from app.services.ai_parallel import GroupOutcome, run_groups

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.document import Document
    from app.schemas.extraction.registry import CategorySpec

logger = logging.getLogger(__name__)

#: Per provider call. NOT the 60 s edge: GLM-5.3-Flash's throughput swings 40-150 tok/s
#: (live 2026-10-18), and the server finishes a request the edge has already cut (dev logs:
#: entries committed at 100 s) — the SPA recovers it by re-reading. So a call may use most
#: of the Lambda's 120 s; above that it is cut anyway.
FAST_READ_TIMEOUT_SECONDS = 90

#: A failure faster than this (a 429, a 5xx, an empty answer) is retried ONCE; a slow
#: failure is not — the retry would push the request past the edge timeout.
QUICK_RETRY_WINDOW_SECONDS = 8.0

#: Output budgets. The core is a few hundred tokens; a facet call at ~150 tok/s cannot
#: usefully emit more than ~7 500 tokens inside the per-call timeout.
MONEY_CORE_MAX_TOKENS = 4_000
FACETS_MAX_TOKENS = 8_000

#: From this many characters of document text the facet list is split by group.
FACET_SPLIT_MIN_CHARS = 8_000

#: The fixed core the ``money_core`` call returns (``BudgetProposalExtraction`` names).
MONEY_CORE_KEYS: tuple[str, ...] = (
    "document_type",
    "document_type_confidence",
    "rejection_reason",
    "quotation_number",
    "insurer_name",
    "insurer_rut",
    "insurer_cmf_code",
    "period_start_at",
    "period_end_at",
    "coverage_start",
    "coverage_end",
    "validity_business_days",
    "taxable_premium_uf",
    "exempt_premium_uf",
    "net_premium_uf",
    "vat_uf",
    "total_premium_uf",
    "taxable_rate_permille",
    "exempt_rate_permille",
    "comprehensive_rate_permille",
    "commission_pct",
)

#: How the facet vocabulary (``budget_proposal._FACET_GROUPS``) splits on a long document:
#: FOUR calls of roughly equal OUTPUT. Sized from the live probes of 2026-10-18 (three corpus
#: offers, ~17 k chars, ~105 facets each): the provider streamed 62-100 tok/s per call, so a
#: call must stay near ~1.5-2 k tokens to land well inside 50 s. With three calls the
#: ``coverage · deductible · warranty`` call ran 47-52 s (it also re-emitted facets of other
#: groups, dropped on merge); sublimits alone are the largest group (~41 per offer).
#: The comparison standardizer reuses these buckets (``comparison_standardize``).
FACET_GROUP_SPLIT: dict[str, tuple[str, ...]] = {
    "facets_sublimits": ("sublimit",),
    "facets_cover": ("coverage", "deductible"),
    "facets_exclusions": ("exclusion",),
    "facets_conditions": ("warranty", "clause", "other"),
}

#: The wording each facet carries is the decisive sentence, not the whole paragraph:
#: the comparator caps it at ``COMPARE_VERBATIM_CHARS`` anyway, and every extra character
#: is output time. Exclusions / clauses keep only their title (the PDF has the full text).
#: What each split call owns, in the insurer's own vocabulary — the group labels alone were
#: not enough: forced to ``coverage | deductible``, the model filed every "extensión de
#: cobertura" row of the sublimits table as a coverage and re-emitted it (60 facets, 46 s).
_GROUP_DEFINITIONS: dict[tuple[str, ...], str] = {
    ("sublimit",): (
        "Este grupo es el CUADRO DE EXTENSIONES DE COBERTURA Y SUBLÍMITES: cada extensión, "
        "cláusula adicional o partida con su sublímite otorgado (o 'No otorgada') es una facet "
        "`sublimit`."
    ),
    ("coverage", "deductible"): (
        "Este grupo son SOLO las coberturas PRINCIPALES o básicas (el amparo de cada materia: "
        "incendio, sismo, todo riesgo, perjuicio por paralización…, normalmente menos de 10) y "
        "los DEDUCIBLES. Las extensiones de cobertura con sublímite NO van aquí: son `sublimit` "
        "y las extrae otra llamada."
    ),
    ("exclusion",): "Este grupo son las EXCLUSIONES (cada una con su número y materia).",
    ("warranty", "clause", "other"): (
        "Este grupo son las GARANTÍAS / requisitos de suscripción, las CLÁUSULAS y condiciones "
        "particulares (base de valoración, rehabilitación, coaseguro, inspección, ajuste…) y "
        "cualquier otra condición relevante que no sea cobertura, sublímite, deducible ni "
        "exclusión."
    ),
}

_VERBATIM_RULE = (
    " En `verbatim` copia EXACTA solo la frase decisiva (máximo 300 caracteres); en "
    "exclusiones y cláusulas, solo su título o primera frase (máximo 160 caracteres)."
)

_GATEWAY_TIMEOUT_RE = re.compile(r"returned (?:504|408)\b")
_RATE_LIMIT_RE = re.compile(r"returned 429\b")

#: 429 handling (DeepInfra caps simultaneous requests per account; seen live 2026-10-18
#: with 15 calls in flight): up to two more attempts, 1.5 s / 3 s apart (+ jitter).
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 1.5


def _is_rate_limited(exc: BaseException) -> bool:
    return bool(_RATE_LIMIT_RE.search(str(exc)))


# --- Tool schemas (slices of the published slim schema) -------------------------


def _full_tool_schema() -> dict[str, Any]:
    from app.schemas.extraction.budget_proposal import budget_proposal_tool_schema

    return budget_proposal_tool_schema()


def money_core_tool_schema() -> dict[str, Any]:
    """The ``money_core`` tool: verdict + fixed core, NO facets.

    EVERY key is ``required`` and nullable (review round 2, A1): with optional keys
    GLM-5.3-Flash reasoned every premium correctly and then emitted only 3-5 of them in
    the arguments (live 2026-10-19, all three corpus offers: exempt premium + commission,
    no insurer, no total). Forced to write each key — the value or ``null`` — it returns
    the full core (insurer, afecta/exenta/neta/IVA/total, rates) in ~8 s. ``null`` is
    dropped by the schema coercion exactly like an omitted key.
    """
    props = _full_tool_schema()["parameters"]["properties"]
    properties: dict[str, Any] = {}
    for key in MONEY_CORE_KEYS:
        if key not in props:
            continue
        prop = dict(props[key])
        kind = prop.get("type")
        if key != "document_type" and isinstance(kind, str):
            prop["type"] = [kind, "null"]
        properties[key] = prop
    return {
        "name": "record_budget_core",
        "description": (
            "Registra el veredicto de archivo y el núcleo fijo de prima y vigencia de UNA "
            "oferta/cotización de seguro chilena. No inventes valores. Escribe TODAS las "
            "claves: el valor que el documento declara, o null si no lo declara. RELLENA "
            "TODOS los campos de prima que el documento declare (afecta, exenta, neta, IVA, "
            "total) y el nombre de la compañía: no informes solo uno."
        ),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
    }


#: The core is INCOMPLETE when none of these came back: no insurer name and no
#: premium total/net. One quick re-ask (inside the core's own thread, in parallel with
#: the slower facet calls, so it costs no wall time) before the read is accepted.
CORE_COMPLETENESS_KEYS: tuple[str, ...] = ("insurer_name", "total_premium_uf", "net_premium_uf")


def core_is_incomplete(parsed: dict[str, Any] | None) -> bool:
    """True for a NON-wrong-file core that lacks the insurer name or both premium totals."""
    if not isinstance(parsed, dict):
        return True
    if parsed.get("document_type") == "not_a_proposal":
        return False

    def _has(key: str) -> bool:
        value = parsed.get(key)
        return value is not None and str(value).strip() != ""

    return not _has("insurer_name") or not (_has("total_premium_uf") or _has("net_premium_uf"))


def facets_tool_schema(groups: tuple[str, ...] | None = None) -> dict[str, Any]:
    """The ``facets`` tool, optionally restricted to a subset of facet groups."""
    facets = _full_tool_schema()["parameters"]["properties"]["facets"]
    facets = {**facets, "items": {**facets["items"], "properties": dict(facets["items"]["properties"])}}
    if groups:
        group_prop = dict(facets["items"]["properties"]["group"])
        group_prop["enum"] = list(groups)
        group_prop["description"] = " | ".join(groups)
        facets["items"]["properties"]["group"] = group_prop
    present = dict(facets["items"]["properties"].get("present") or {"type": "boolean"})
    present["description"] = (
        "true si la oferta incluye esta dimensión; en una EXCLUSIÓN, true = la oferta "
        "CONTIENE esa exclusión. false solo si el documento dice expresamente que NO la otorga."
    )
    facets["items"]["properties"]["present"] = present
    verbatim = dict(facets["items"]["properties"].get("verbatim") or {"type": "string"})
    verbatim["description"] = "Redacción EXACTA de la frase decisiva del documento." + _VERBATIM_RULE
    facets["items"]["properties"]["verbatim"] = verbatim
    return {
        "name": "record_budget_facets",
        "description": (
            "Registra las facets (dimensiones dinámicas) de UNA oferta/cotización de seguro "
            "chilena. En CADA facet copia en `verbatim` la redacción EXACTA del documento, "
            "palabra por palabra; `value` es solo el número/límite/porcentaje ya extraído, "
            "NUNCA reemplaza a `verbatim`. No inventes facets."
        ),
        "parameters": {
            "type": "object",
            "properties": {"facets": facets},
            "required": ["facets"],
        },
    }


# --- Messages ----------------------------------------------------------------------


def _messages(spec: "CategorySpec", document: "Document", text: str, instruction: str) -> list[dict[str, str]]:
    """Same system + document prefix for every group; only the closing instruction differs."""
    from app.services import ai

    system = (
        f"{ai._GENERIC_EXTRACTION_SYSTEM}\n\n"
        f"GUÍA PARA ESTA CATEGORÍA ({spec.category.value})\n{spec.guidance}"
    )
    user = (
        f"Archivo: {document.original_name}\n"
        f"Categoría: {spec.category.value}\n\n"
        "--- INICIO DEL DOCUMENTO ---\n"
        f"{text}\n"
        "--- FIN DEL DOCUMENTO ---\n\n"
        f"{instruction}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_CORE_INSTRUCTION = (
    "EN ESTA LLAMADA registra SOLO las partes 1) DISCRIMINADOR DE ARCHIVO y 2) NÚCLEO FIJO de "
    "prima y vigencia. NO incluyas facets: otra llamada paralela las extrae. Escribe TODAS las "
    "claves de la función: el valor que el documento declara, o null si no lo declara. Llama a "
    "la función record_budget_core."
)

#: The re-ask when the first core came back without the insurer or the premium totals.
_CORE_REPAIR_INSTRUCTION = (
    _CORE_INSTRUCTION
    + " ATENCIÓN: una lectura anterior omitió el nombre de la compañía y/o las primas. Busca "
    "en el cuadro de tarificación la PRIMA NETA AFECTA, la PRIMA NETA EXENTA, la PRIMA NETA "
    "TOTAL, el IVA y la PRIMA BRUTA TOTAL (total a pagar), y el nombre de la compañía en el "
    "encabezado, y regístralos todos."
)


def _facets_instruction(groups: tuple[str, ...] | None) -> str:
    base = (
        "EN ESTA LLAMADA registra SOLO la parte 3) FACETS. NO repitas el veredicto ni el núcleo "
        "de prima: otra llamada paralela los extrae."
    )
    if groups:
        definition = _GROUP_DEFINITIONS.get(tuple(groups))
        if definition:
            base += " " + definition
        others = [g for split in FACET_GROUP_SPLIT.values() for g in split if g not in groups]
        base += (
            f" Incluye SOLO las facets de los grupos: {', '.join(groups)}. Las de los grupos "
            f"{', '.join(others)} las extrae otra llamada paralela: no las incluyas (el sistema "
            "las descarta)."
        )
    return base + _VERBATIM_RULE + " Omite las claves sin valor. Llama a la función record_budget_facets."


# --- Plan --------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupPlan:
    """One provider call of the grouped read."""

    name: str
    tool_schema: dict[str, Any]
    instruction: str
    max_tokens: int
    keys: tuple[str, ...]  # the top-level keys this group is trusted for
    #: The facet groups this call owns (split reads); a facet of another group is dropped.
    facet_groups: tuple[str, ...] | None = None


def plan_groups(text: str) -> list[GroupPlan]:
    """The calls for a document of this length (``money_core`` always first)."""
    plans = [
        GroupPlan(
            name="money_core",
            tool_schema=money_core_tool_schema(),
            instruction=_CORE_INSTRUCTION,
            max_tokens=MONEY_CORE_MAX_TOKENS,
            keys=MONEY_CORE_KEYS,
        )
    ]
    if len(text) >= FACET_SPLIT_MIN_CHARS:
        for name, groups in FACET_GROUP_SPLIT.items():
            plans.append(
                GroupPlan(
                    name=name,
                    tool_schema=facets_tool_schema(groups),
                    instruction=_facets_instruction(groups),
                    max_tokens=FACETS_MAX_TOKENS,
                    keys=("facets",),
                    facet_groups=groups,
                )
            )
    else:
        plans.append(
            GroupPlan(
                name="facets",
                tool_schema=facets_tool_schema(None),
                instruction=_facets_instruction(None),
                max_tokens=FACETS_MAX_TOKENS,
                keys=("facets",),
            )
        )
    return plans


# --- Calls -------------------------------------------------------------------------


def as_timeout_if_gateway(exc: BaseException) -> BaseException:
    """A provider-side 504/408 is a timeout too: honest copy, not "unavailable"."""
    from app.services import ai

    if type(exc) is ai.AIProviderError and _GATEWAY_TIMEOUT_RE.search(str(exc)):
        return ai.AITimeout(str(exc))
    return exc


def call_with_quick_retry(fn: Callable[[], Any], *, window: float = QUICK_RETRY_WINDOW_SECONDS) -> Any:
    """Run ``fn`` (one ``tool_call`` with ``max_retries=0``); retry ONCE on a FAST failure.

    ``tool_call``'s own retries are disabled for these reads: two retries of a 50 s
    timeout would hold the request for 150 s while the browser gave up at 60 s.
    A timeout is never retried here either.
    """
    from app.services import ai

    started = time.monotonic()
    try:
        return fn()
    except ai.AITimeout:
        raise
    except (ai.AIProviderError, ai.AIParseError) as exc:
        if type(as_timeout_if_gateway(exc)) is ai.AITimeout:
            raise as_timeout_if_gateway(exc) from exc
        if time.monotonic() - started > window:
            raise
        if not _is_rate_limited(exc):
            logger.info("AI call failed fast (%s); retrying once", exc)
            return fn()
        # 429: the provider caps simultaneous requests per account. Back off (with jitter,
        # so parallel groups don't retry in lockstep) and try up to twice more, still only
        # inside the fast window.
        last: BaseException = exc
        for attempt in range(RATE_LIMIT_RETRIES):
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1) + random.uniform(0, 1.0))
            try:
                return fn()
            except (ai.AIProviderError, ai.AIParseError) as again:
                last = again
                if not _is_rate_limited(again) or time.monotonic() - started > window * 2:
                    raise
        raise last


def first_error(outcomes: list[GroupOutcome]) -> BaseException | None:
    """The error to surface for a failed parallel step: the first in plan order, typed."""
    from app.services import ai

    for outcome in outcomes:
        if not outcome.ok:
            exc = outcome.error
            if not isinstance(exc, ai.AIError):
                exc = ai._wrap_provider_error(exc)
            return as_timeout_if_gateway(exc)
    return None


# --- Merge -------------------------------------------------------------------------


def _facet_identity(facet: dict[str, Any]) -> str:
    from app.schemas.proposal import coverage_key

    return coverage_key(None, str(facet.get("key") or facet.get("label") or facet.get("verbatim") or ""))


def merge_facets(lists: list[list[Any]]) -> list[dict[str, Any]]:
    """Concatenate the facet lists in plan order, dropping CROSS-call duplicates by key.

    Within one call every facet is kept as before (two "Sismo" rows for two items are
    legitimate); a facet a later call repeats (same ``key`` / ``label`` slug as an
    earlier call's facet — e.g. terrorism as a coverage with ``present=false`` AND as an
    exclusion) is dropped. The ``MAX_FACETS`` cap is applied by the schema on coercion.
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for facets in lists:
        rows = [f for f in (facets or []) if isinstance(f, dict)]
        identities = [_facet_identity(f) for f in rows]
        for facet, identity in zip(rows, identities):
            if identity and identity in seen:
                continue
            merged.append(facet)
        seen.update(i for i in identities if i)
    return merged


def normalize_exclusion_presence(facets: list[Any]) -> list[Any]:
    """An ``exclusion`` facet is an item of the offer's exclusion list, so ``present``
    means "the offer CONTAINS this exclusion" — always ``True`` (review round 2, A4).

    The model mixed both readings ("false = it excludes it" in one offer, "true = the
    exclusion is there" in another), so the same exclusion read as opposite across
    columns. The decisive wording stays in ``verbatim``.
    """
    out: list[Any] = []
    for facet in facets or []:
        if isinstance(facet, dict) and str(facet.get("group") or "") == "exclusion":
            facet = {**facet, "present": True}
        out.append(facet)
    return out


def _missing_core(parsed: dict[str, Any] | None) -> int:
    if not isinstance(parsed, dict):
        return len(MONEY_CORE_KEYS)
    return sum(1 for key in MONEY_CORE_KEYS if parsed.get(key) in (None, ""))


def _core_with_repair(run: Callable[[str], Any]) -> Any:
    """The ``money_core`` call, re-asked ONCE when it came back incomplete.

    Runs in the core's own worker thread, while the (slower) facet calls are still
    streaming, so the repair costs no wall time in the usual case. The more complete
    answer wins; tokens of both attempts are summed on the returned result. A failing
    repair keeps the first answer (it is still a valid, if thin, read).
    """
    from dataclasses import replace

    from app.services import ai

    first = run(_CORE_INSTRUCTION)
    if not core_is_incomplete(first.parsed):
        return first
    try:
        second = run(_CORE_REPAIR_INSTRUCTION)
    except ai.AIError as exc:
        logger.info("money_core repair failed (%s); keeping the first answer", exc)
        return first
    better = second if _missing_core(second.parsed) < _missing_core(first.parsed) else first
    usage: dict[str, Any] = {}
    for name in ("prompt_tokens", "completion_tokens"):
        usage[name] = ((first.usage or {}).get(name) or 0) + ((second.usage or {}).get(name) or 0)
    raw = {**(better.raw or {}), "repaired": True}
    return replace(better, usage=usage, raw=raw)


# --- Runner ------------------------------------------------------------------------
# --- Runner ------------------------------------------------------------------------


@dataclass
class BudgetRead:
    """The merged outcome of the grouped read (no DB)."""

    parsed: dict[str, Any] | None
    raw: dict[str, Any]
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    wall_seconds: float
    error: BaseException | None = None
    group_stats: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_budget_proposal(
    spec: "CategorySpec", document: "Document", text: str, *, model: str
) -> BudgetRead:
    """Run every group of the read in parallel and merge the parsed parts.

    Any failed group fails the read (``error`` set, ``parsed`` None): a column missing
    its exclusions would read as BROADER cover in the comparator — silently wrong. The
    per-group seconds / tokens / finish_reason ride on ``raw`` for the audit row either way.
    """
    from app.services import ai

    plans = plan_groups(text)
    timeout = min(int(settings.AI_TIMEOUT_SECONDS), FAST_READ_TIMEOUT_SECONDS)

    def _once(plan: GroupPlan, instruction: str) -> Any:
        messages = _messages(spec, document, text, instruction)
        return call_with_quick_retry(
            lambda: ai.tool_call(
                messages=messages,
                tool_schema=plan.tool_schema,
                model=model,
                timeout=timeout,
                max_retries=0,
                max_tokens=plan.max_tokens,
                schema=spec.schema,
            )
        )

    def _call(plan: GroupPlan) -> Callable[[], Any]:
        if plan.name != "money_core":
            return lambda: _once(plan, plan.instruction)
        return lambda: _core_with_repair(lambda instruction: _once(plan, instruction))

    started = time.monotonic()
    outcomes = run_groups({plan.name: _call(plan) for plan in plans})
    wall = time.monotonic() - started

    stats: dict[str, dict[str, Any]] = {}
    groups_raw: dict[str, Any] = {}
    prompt_tokens = 0
    completion_tokens = 0
    model_name: str | None = None
    for plan in plans:
        outcome = outcomes[plan.name]
        entry: dict[str, Any] = {"seconds": round(outcome.seconds, 2)}
        if outcome.ok:
            result = outcome.value
            usage = result.usage or {}
            entry.update(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                finish_reason=result.finish_reason,
                model=result.model,
            )
            if "facets" in plan.keys:
                entry["facet_count"] = len((result.parsed or {}).get("facets") or [])
            elif (result.raw or {}).get("repaired"):
                entry["repaired"] = True
            prompt_tokens += usage.get("prompt_tokens") or 0
            completion_tokens += usage.get("completion_tokens") or 0
            model_name = model_name or result.model
            groups_raw[plan.name] = {
                **entry,
                "content": (result.raw or {}).get("content"),
                "reasoning": result.reasoning,
            }
        else:
            entry["error"] = f"{type(outcome.error).__name__}: {outcome.error}"
            groups_raw[plan.name] = dict(entry)
        stats[plan.name] = entry

    raw: dict[str, Any] = {
        "parallel": True,
        "wall_seconds": round(wall, 2),
        "groups": groups_raw,
    }
    error = first_error([outcomes[plan.name] for plan in plans])
    if error is not None:
        return BudgetRead(
            parsed=None, raw=raw, model=model_name, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, wall_seconds=wall, error=error,
            group_stats=stats,
        )

    merged: dict[str, Any] = {}
    facet_lists: list[list[Any]] = []
    for plan in plans:
        parsed = outcomes[plan.name].value.parsed or {}
        if "facets" in plan.keys:
            facets = parsed.get("facets")
            facets = facets if isinstance(facets, list) else []
            if plan.facet_groups:
                # A split call may still re-emit another call's groups: those are owned
                # (and read) by that other call, so keep only this call's own groups.
                facets = [
                    f for f in facets
                    if isinstance(f, dict) and str(f.get("group") or "other") in plan.facet_groups
                ]
            facet_lists.append(normalize_exclusion_presence(facets))
        else:
            merged.update({key: parsed[key] for key in plan.keys if key in parsed})
    merged["facets"] = merge_facets(facet_lists)
    raw["parsed"] = merged
    return BudgetRead(
        parsed=merged, raw=raw, model=model_name, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, wall_seconds=wall, group_stats=stats,
    )


__all__ = [
    "BudgetRead",
    "CORE_COMPLETENESS_KEYS",
    "core_is_incomplete",
    "normalize_exclusion_presence",
    "FACET_GROUP_SPLIT",
    "FACET_SPLIT_MIN_CHARS",
    "FAST_READ_TIMEOUT_SECONDS",
    "MONEY_CORE_KEYS",
    "GroupPlan",
    "as_timeout_if_gateway",
    "call_with_quick_retry",
    "facets_tool_schema",
    "first_error",
    "merge_facets",
    "money_core_tool_schema",
    "plan_groups",
    "read_budget_proposal",
]
