"""Agent skills (v12, docs/v12/05-agent.md §3) — progressive disclosure.

A **skill** is a named, versioned instruction pack plus the subset of tools it
relies on. The base system prompt only lists each skill's title and one-liner;
the model pulls the full playbook into the conversation by calling the native
READ tool ``load_skill(key)``, and the thread remembers it in
``agent_thread.active_skills`` so later turns carry the instructions in the
system prompt (budgeted, truncated with the loop's marker).

Skills never widen authority: they are prose. Every tool a playbook names still
runs under the caller's own grants (reads) or as a proposal a human confirms
(writes) — CLAUDE.md rule 6 is untouched.

Identifiers are English; titles, descriptions, instructions and triggers are
Spanish because they are LLM prompt / user-facing copy (CLAUDE.md rule 1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "SkillSpec",
    "SKILLS",
    "UnknownSkill",
    "SkillDisabled",
    "skill_enabled",
    "disabled_reason",
    "list_skills",
    "get_skill",
    "active_keys",
    "normalize_skill_keys",
    "skills_index_prompt",
    "active_skills_prompt",
    "MAX_ACTIVE_SKILLS_CHARS",
]

#: Budget for the active skills' instructions inside the system prompt.
MAX_ACTIVE_SKILLS_CHARS = 6_000


@dataclass(frozen=True)
class SkillSpec:
    key: str  # english id
    title: str  # Spanish
    description: str  # Spanish one-liner (base prompt + UI)
    instructions: str  # Spanish, the full playbook (LLM prompt)
    tools: tuple[str, ...]  # tool names it relies on (must exist in TOOL_REGISTRY)
    triggers: tuple[str, ...]  # Spanish keywords for auto-suggestion in the UI
    version: int = 1
    # Product feature flag key (``services.features``). While it is off the
    # skill is not listed, not loadable and never rides in a system prompt.
    feature: str | None = None


class UnknownSkill(ValueError):
    """One or more skill keys are not in the registry. -> 422."""

    def __init__(self, keys: Sequence[str]):
        self.keys = list(keys)
        super().__init__("Habilidad desconocida: " + ", ".join(self.keys))


class SkillDisabled(UnknownSkill):
    """A known skill whose product feature is off ("pronto"). -> 422."""

    def __init__(self, keys: Sequence[str], reason: str):
        self.keys = list(keys)
        self.reason = reason
        ValueError.__init__(self, reason)


_COLLECTIONS_REVIEW = SkillSpec(
    key="collections_review",
    title="Revisar cobranza",
    description=(
        "Cuotas vencidas, riesgo de término y qué consultar a cada compañía; "
        "propone consultas de estado y recordatorios."
    ),
    instructions="""PLAYBOOK · REVISAR COBRANZA
1. Parte con get_collection_overview: vencidas (cantidad y UF), riesgo de término, vencimientos a 7 y 30 días, pagos sin confirmar y respuestas de compañías pendientes de revisar.
2. Lista las vencidas con list_installments(bucket="overdue"). Prioriza: primero las con riesgo de término (flag termination_risk), después más días de atraso y mayor saldo en UF.
3. Revisa el nivel de cobro de las cuotas que vas a gestionar (get_collection_mode):
   - registro (record): Radal solo anota; no salen recordatorios. Si la persona quiere gestionarla, propone start_collection (la cobranza asistida funciona con cualquier compañía).
   - asistida (assisted): Radal recuerda al pagador y consulta a la compañía por correo. Propone send_payment_reminder al pagador y/o send_status_inquiry a la compañía.
   - nativa (native): la compañía cobra por Forpay. Puedes proponer create_payment_link para enviarlo al pagador.
4. NUNCA afirmes que una cuota está pagada o impaga sin evidencia. Antes revisa list_pending_evidence y list_status_inquiries. Una cuota sin evidencia es "sin evidencia de pago", no "impaga". Evidencia declarada (declared) es la palabra del corredor o del pagador; insurer_confirmed / provider_confirmed es confirmación real.
5. Si no sabes el estado real, PREGUNTA A LA COMPAÑÍA (send_status_inquiry) en vez de suponer. Una consulta por póliza con todas sus cuotas pendientes.
6. No dupliques: si la póliza ya tiene una consulta abierta (draft, sent o replied), menciónala en vez de proponer otra; si una respuesta espera revisión, pide revisarla en la bandeja de respuestas.
7. Cierra con un resumen breve: total vencido en UF, cuántas en riesgo de término, las acciones propuestas (pendientes de confirmar en las tarjetas) y lo que falta (correo del pagador, correo de cobranza de la compañía).""",
    tools=(
        "get_collection_overview",
        "list_installments",
        "get_collection_mode",
        "list_pending_evidence",
        "list_status_inquiries",
        "get_policy",
        "send_status_inquiry",
        "send_payment_reminder",
        "record_payment_promise",
        "start_collection",
        "create_payment_link",
    ),
    triggers=(
        "cobranza", "cuotas", "vencidas", "vencida", "morosidad", "atraso",
        "pago", "pagos", "recordatorio", "consulta a la compañía", "término",
    ),
)

_POLICY_INTAKE = SkillSpec(
    key="policy_intake",
    title="Registrar una póliza",
    description=(
        "Registrar una póliza desde PDF o a mano, completar partes y datos, "
        "plan de pago e iniciar la cobranza."
    ),
    instructions="""PLAYBOOK · REGISTRAR UNA PÓLIZA
1. Pregunta el origen. Con el PDF de la póliza lo mejor es subirlo en Pólizas > Subir póliza: el lector lo extrae y la persona revisa antes de guardar (tú no subes archivos). Sin PDF, se registra a mano.
2. A mano, los datos obligatorios son: asegurado (cliente existente, o RUT + nombre), compañía, ramo y subtipo del catálogo (get_catalog) y vigencia desde/hasta. Busca primero al cliente (search_entities o list_policies con q=RUT) antes de proponer uno nuevo.
3. Propone create_policy_manual SOLO con datos que la persona entregó. Nunca inventes número de póliza, primas ni comisión. Sin número queda "Sin n°" y se completa después.
4. Primas en UF: neta = afecta + exenta; IVA = 0,19 × la parte AFECTA (el sismo es exento); total = neta + IVA. Si solo te dan el total, pásalo tal cual y no derives el resto.
5. Luego revisa check_policy_files: PDF de respaldo, plan de pago, correo del pagador, comisión. Rellena datos vacíos con update_policy_fields (solo completa campos vacíos; un dato ya registrado se corrige en la ficha con su motivo).
6. Con plan de pago cargado, ofrece start_collection. El plan de pago y las partes (contratante, pagador) se cargan en la ficha de la póliza.""",
    tools=(
        "search_entities",
        "get_catalog",
        "list_policies",
        "get_policy",
        "check_policy_files",
        "create_policy_manual",
        "update_policy_fields",
        "start_collection",
    ),
    triggers=(
        "registrar póliza", "nueva póliza", "ingresar póliza", "cargar póliza",
        "póliza manual", "subir póliza", "emitida",
    ),
)

_PARTIAL_JOURNEY = SkillSpec(
    key="partial_journey",
    title="Recorrido parcial de una cuenta",
    description=(
        "Ubicar en qué etapa está una colocación, qué se omitió y cuándo saltar "
        "o activar una etapa."
    ),
    instructions="""PLAYBOOK · RECORRIDO PARCIAL
1. Ubica la cuenta (search_entities, get_group_tree o la referencia @) y léela con check_case_files: etapas hechas, actual, omitidas y pendientes, si ya tiene póliza y a qué etapas se puede saltar.
2. Los recorridos parciales son normales: una cuenta puede partir desde la póliza (PDF o a mano), desde cotizaciones de portal o desde el proceso de mercado completo. Una etapa omitida no es un error; no presiones por completarla.
3. Saltar (skip_case_to_stage): solo hacia adelante y solo a quotes_received, insured_decision, policy_issued o active. policy_issued y active exigen que la cuenta ya tenga póliza.
4. Activar una etapa omitida (activate_case_stage): solo si la cuenta aún no tiene póliza. Con póliza, los cambios se hacen por endoso o corrección.
5. Si la herramienta rechaza la acción, explica el motivo tal como lo entrega.""",
    tools=(
        "search_entities",
        "get_group_tree",
        "get_case_file",
        "check_case_files",
        "skip_case_to_stage",
        "activate_case_stage",
    ),
    triggers=(
        "etapa", "saltar", "omitir", "omitida", "recorrido", "viaje",
        "colocación", "activar etapa",
    ),
)

_FILE_CHECK = SkillSpec(
    key="file_check",
    title="Revisar archivos faltantes",
    description=(
        "Revisar antecedentes y documentos faltantes de una cuenta o de una póliza."
    ),
    instructions="""PLAYBOOK · ARCHIVOS FALTANTES
1. Cuenta: check_case_files entrega los archivos recomendados del ramo (cuáles están y cuáles faltan, los obligatorios primero), las cargas libres y las extracciones. list_documents da el detalle de cada archivo.
2. Póliza: check_policy_files entrega el "Falta: X" (PDF de respaldo, n° de póliza, plan de pago, prima bruta, correo del pagador, primas, comisión) y qué funciones quedan deshabilitadas por eso.
3. Prioriza lo que bloquea la operación: sin plan o sin prima bruta no hay cobranza; sin correo del pagador no salen avisos; sin n° de póliza no se puede endosar.
4. Tú no subes archivos: indica dónde hacerlo (Antecedentes de la cuenta, o Subir póliza en la ficha).
5. Puedes proponer update_policy_fields para rellenar datos vacíos que la persona te dicte, o add_note para dejar el pendiente anotado.""",
    tools=(
        "check_case_files",
        "check_policy_files",
        "list_documents",
        "get_policy",
        "update_policy_fields",
        "add_note",
    ),
    triggers=(
        "falta", "faltan", "faltantes", "archivos", "documentos", "antecedentes",
        "respaldo", "pdf",
    ),
)

_PORTFOLIO_ANALYSIS = SkillSpec(
    key="portfolio_analysis",
    title="Análisis de cartera",
    description=(
        "Análisis de cartera: primas, compañías, ramos y vencimientos. Solo lectura."
    ),
    instructions="""PLAYBOOK · ANÁLISIS DE CARTERA (SOLO LECTURA)
1. Este análisis no propone escrituras, salvo que la persona lo pida explícitamente.
2. Segmenta con list_policies y sus filtros (status, insurer_id, line_code, subtype_id, collection, missing). Cada llamada trae a lo más 20 filas: usa el campo total para contar y NO extrapoles desde la muestra; si total > filas, dilo ("muestra de N de M").
3. Primas en UF: suma solo lo que traen las filas y di sobre cuántas pólizas sumaste.
4. Vencimientos: las pólizas con fecha de término próxima son candidatas a renovación.
5. Compañías y ramos: agrupa por compañía y por ramo/subtipo; el catálogo está en get_catalog.
6. Cobranza de la cartera: get_collection_overview.
7. Nunca inventes cifras ni porcentajes; cita de qué herramienta sale cada número.""",
    tools=(
        "list_policies",
        "get_policy",
        "get_catalog",
        "get_collection_overview",
        "search_entities",
    ),
    triggers=(
        "cartera", "portafolio", "análisis", "primas", "ranking", "compañías",
        "ramos", "vencimientos", "renovaciones",
    ),
)

_PORTAL_QUOTES = SkillSpec(
    key="portal_quotes",
    title="Cotizar en portales",
    description=(
        "Buscar cotizaciones en los portales de las compañías; explicar fallas "
        "y alternativas."
    ),
    instructions="""PLAYBOOK · COTIZAR EN PORTALES
1. Revisa el subtipo en get_catalog (hoy HDI cotiza vehículos particulares, VEH-01) y las búsquedas previas con list_portal_searches / get_portal_search.
2. Para cotizar un vehículo se necesitan: patente, marca, modelo, año, uso (particular o comercial), comuna, RUT del asegurado y fecha de inicio. Pide lo que falte; no inventes datos.
3. Propone run_portal_search. Queda en cola y la ejecuta el robot de Radal; avisa que el resultado aparece en Búsqueda en portales.
4. Si una búsqueda falló, explica el motivo en simple según error_kind: credentials_missing / login_failed (revisa la cuenta del portal en Configuración), layout_changed / portal_unavailable (el portal cambió o está caído; el equipo Radal ya fue avisado), captcha, timeout, no_results. Ofrece las alternativas que trae la búsqueda: reintentar, subir la cotización en PDF al comparador u otro portal.
5. Una cotización de portal no es una póliza: nunca la presentes como contratada.""",
    tools=(
        "get_catalog",
        "list_portal_searches",
        "get_portal_search",
        "run_portal_search",
        "get_case_file",
    ),
    feature="portal_search",
    triggers=(
        "portal", "portales", "cotizar", "cotización", "hdi", "patente",
        "vehículo", "auto",
    ),
)

#: The registry, in display order.
SKILLS: dict[str, SkillSpec] = {
    spec.key: spec
    for spec in (
        _COLLECTIONS_REVIEW,
        _POLICY_INTAKE,
        _PARTIAL_JOURNEY,
        _FILE_CHECK,
        _PORTFOLIO_ANALYSIS,
        _PORTAL_QUOTES,
    )
}


def _validate_skills() -> None:
    """Import-time guard: every skill names only tools that exist."""
    from app.services.agent_tools import TOOL_REGISTRY

    for spec in SKILLS.values():
        missing = [name for name in spec.tools if name not in TOOL_REGISTRY]
        if missing:
            raise ValueError(f"Skill {spec.key!r} names unknown tools {missing!r}")
        if not spec.title or not spec.description or not spec.instructions:
            raise ValueError(f"Skill {spec.key!r} must carry title, description, instructions")


_validate_skills()


def skill_enabled(spec: SkillSpec) -> bool:
    """Whether the skill's product feature is on (no feature = always on)."""
    from app.services.features import feature_enabled

    return spec.feature is None or feature_enabled(spec.feature)


def disabled_reason(spec: SkillSpec) -> str | None:
    """The Spanish "pronto" reason when the skill is switched off, else ``None``."""
    from app.services.features import feature_reason

    if skill_enabled(spec):
        return None
    return feature_reason(spec.feature or "") or "Habilidad no disponible por ahora."


def list_skills() -> list[SkillSpec]:
    """The catalogue the UI and the base prompt see: enabled skills only."""
    return [spec for spec in SKILLS.values() if skill_enabled(spec)]


def get_skill(key: str) -> SkillSpec | None:
    """Raw registry lookup (enabled or not) — callers check :func:`skill_enabled`."""
    return SKILLS.get(str(key or "").strip())


def normalize_skill_keys(keys: Iterable[str] | None) -> list[str]:
    """Strip + dedupe preserving order; :class:`UnknownSkill` for a bad key."""
    out: list[str] = []
    unknown: list[str] = []
    disabled: list[str] = []
    for raw in keys or []:
        key = str(raw or "").strip()
        if not key:
            continue
        if key not in SKILLS:
            unknown.append(key)
        elif not skill_enabled(SKILLS[key]):
            disabled.append(key)
        elif key not in out:
            out.append(key)
    if unknown:
        raise UnknownSkill(unknown)
    if disabled:
        raise SkillDisabled(disabled, disabled_reason(SKILLS[disabled[0]]) or "")
    return out


def active_keys(raw: object) -> list[str]:
    """A thread's stored ``active_skills`` cleaned of anything no longer known
    or switched off (a retired or "pronto" skill must never break a thread)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        key = str(item or "").strip()
        if key in SKILLS and skill_enabled(SKILLS[key]) and key not in out:
            out.append(key)
    return out


def skills_index_prompt() -> str:
    """The base prompt's skill catalogue: titles + one-liners only."""
    lines = [
        "HABILIDADES",
        "- Para tareas de varios pasos carga la habilidad que corresponda con "
        "load_skill(key) y sigue su playbook. Habilidades disponibles:",
    ]
    for spec in list_skills():
        lines.append(f"  · {spec.key} — {spec.title}: {spec.description}")
    return "\n".join(lines)


def active_skills_prompt(
    keys: Sequence[str], *, budget: int = MAX_ACTIVE_SKILLS_CHARS, marker: str = "…"
) -> str:
    """The loaded playbooks for the system prompt, within ``budget`` chars.

    Each skill gets an even share of the budget; an over-long playbook is cut
    and ends with ``marker`` (the loop's truncation marker).
    """
    specs = [SKILLS[k] for k in active_keys(list(keys))]
    if not specs or budget <= 0:
        return ""
    header = "HABILIDADES ACTIVAS EN ESTA CONVERSACIÓN"
    share = max((budget - len(header)) // len(specs) - 2, 0)
    blocks = [header]
    for spec in specs:
        text = f"[{spec.key}] {spec.title}\n{spec.instructions}"
        if len(text) > share:
            text = text[: max(share - len(marker), 0)] + marker
        blocks.append(text)
    return "\n\n".join(blocks)
