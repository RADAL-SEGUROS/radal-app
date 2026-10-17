"""The ONE Spanish label per ``CaseStage`` the backend writes into prose.

Activity descriptions (the bitácora), stage-guard reasons, the expediente PDF
and the journey summaries all read :data:`STAGE_LABELS_ES`. The values mirror
``frontend/src/locales/es/cases.json`` → ``stages`` so a stage reads the same in
a server sentence and in the rail (review v12 #3: the bitácora printed
``policy_issued → active`` and "lead, intake, pre_underwriting…").

Labels are OUTPUT, never identifiers (rule 1): code keeps the enum, prose gets
:func:`stage_label`.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.enums import CaseStage

STAGE_LABELS_ES: dict[CaseStage, str] = {
    CaseStage.LEAD: "Interés",
    CaseStage.INTAKE: "Antecedentes",
    CaseStage.PRE_UNDERWRITING: "Presuscripción",
    CaseStage.TECHNICAL_BASIS: "Bases técnicas",
    CaseStage.MARKET_SUBMISSION: "Envío al mercado",
    CaseStage.QUOTES_RECEIVED: "Cotizaciones recibidas",
    CaseStage.COMPARISON: "Comparación",
    CaseStage.INSURED_DECISION: "Decisión del asegurado",
    CaseStage.PROPOSAL_ISSUED: "Propuesta emitida",
    CaseStage.RATIFIED: "Ratificación",
    CaseStage.POLICY_ISSUED: "Póliza emitida",
    CaseStage.MIRROR_VALIDATION: "Validación espejo",
    CaseStage.ACTIVE: "Vigente",
    CaseStage.RENEWAL_REVIEW: "Revisión de renovación",
    CaseStage.ENDORSEMENT_REQUESTED: "Endoso solicitado",
    CaseStage.ENDORSEMENT_PROPOSED: "Endoso propuesto",
    CaseStage.ENDORSEMENT_ISSUED: "Endoso emitido",
    CaseStage.ENDORSEMENT_APPLIED: "Endoso aplicado",
    CaseStage.COLLECTION_SCHEDULED: "Plan de pago",
    CaseStage.COLLECTION_IN_PROGRESS: "Cobranza en curso",
    CaseStage.COLLECTION_OVERDUE: "Cobranza morosa",
    CaseStage.COLLECTION_SETTLED: "Cobranza saldada",
    CaseStage.COLLECTION_SUSPENDED: "Cobertura suspendida",
    CaseStage.CLAIM_REPORTED: "Siniestro denunciado",
    CaseStage.CLAIM_ADJUSTING: "En liquidación",
    CaseStage.CLAIM_PRELIMINARY: "Pre-informe",
    CaseStage.CLAIM_FINAL: "Informe final",
    CaseStage.CLAIM_SETTLED: "Siniestro liquidado",
    CaseStage.CLOSED: "Cerrado",
}


def stage_label(stage: CaseStage | str | None) -> str:
    """The Spanish label of a stage (an unknown value reads ``"—"``, never a code)."""
    if stage is None:
        return "—"
    try:
        key = stage if isinstance(stage, CaseStage) else CaseStage(str(stage))
    except ValueError:
        return "—"
    return STAGE_LABELS_ES.get(key, "—")


def stage_list(stages: Iterable[CaseStage | str]) -> str:
    """``"Antecedentes, Presuscripción y Bases técnicas"`` — a Spanish list."""
    labels = [stage_label(s) for s in stages]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " y " + labels[-1]


def stages_omitted(count: int) -> str:
    """``"1 etapa omitida"`` / ``"10 etapas omitidas"``."""
    return f"{count} etapa omitida" if count == 1 else f"{count} etapas omitidas"


_STAGE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(sorted((re.escape(s.value) for s in CaseStage), key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
)
_RULE_CODES = (
    re.compile(r",\s*R\d{1,3}\b"),  # "(nunca bloquea, R42)" -> "(nunca bloquea)"
    re.compile(r"\s*\(\s*R\d{1,3}\s*\)"),  # " (R42)" -> ""
    re.compile(r"\s*\bR\d{1,3}\b"),  # any other bare rule id
)
_LEGACY_SKIP = re.compile(r"^Etapa (?P<src>[a-z_]+) → (?P<dst>[a-z_]+) \(omitidas: (?P<list>[a-z_, ]+)\)$")


def humanize_system_text(text: str | None) -> str | None:
    """Rewrite a SYSTEM-written description stored before the review-v12 fix.

    ``"Etapa lead → policy_issued (omitidas: lead, intake, …)"`` becomes
    ``"Saltó de Interés a Póliza emitida; 10 etapas omitidas"``; any remaining
    stage code becomes its label and rule ids (``R42``) are dropped. Never
    applied to text a user typed (notes, stage notes by a user).
    """
    if not text:
        return text
    match = _LEGACY_SKIP.match(text.strip())
    if match:
        skipped = [t for t in (x.strip() for x in match.group("list").split(",")) if t]
        out = f"Saltó de {stage_label(match.group('src'))} a {stage_label(match.group('dst'))}"
        return out + (f"; {stages_omitted(len(skipped))}" if skipped else "")
    out = _STAGE_TOKEN.sub(lambda m: stage_label(m.group(1)), text)
    for pattern in _RULE_CODES:
        out = pattern.sub("", out)
    out = out.replace("no corrió (nunca bloquea)", "no se realizó (no bloquea la vigencia)")
    return out.replace("  ", " ")


__all__ = [
    "STAGE_LABELS_ES",
    "humanize_system_text",
    "stage_label",
    "stage_list",
    "stages_omitted",
]
