"""Slim, tiered POLICY read schemas (fast reading, docs/v12/11-fast-reading.md).

The single-call ``PolicyExtraction`` read was OUTPUT-bound: a 36 k-char JSON
with 400+ nulls and duplicated fields, 70-90 s per policy. These groups split it
into parallel calls that each return only their own keys:

* **core** (runs inside ``POST /policies/upload``) — ``core_identity_money`` and
  ``core_plan``: what Confirmar / the policy row / the cuotas need;
* **technical** (a second request, a SUGGESTION the broker confirms) —
  ``tech_risk``, ``tech_deductibles``, ``tech_clauses``.

Rules (decision 5): omit empty values; one verbatim text per row at most; no
server-computed field in a schema; exclusions and particular clauses are
number + title only (decision 4 — the full wording stays in the PDF, and the
agent's ``search_document_text`` quotes it on demand).

Coercion is lenient like every registry schema: Chilean numbers via
``parse_uf``, an explicit ``null`` falls back to the default, and a row that
will not coerce is DROPPED on its own instead of taking the whole list with it.
Field names are English (rule 1); the Spanish lives in the prompt guidance.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    Int,
    Pct,
    PolicyDateTime,
    Rate,
    Str,
)

__all__ = [
    "TierGroup",
    "CORE_GROUPS",
    "TECHNICAL_GROUPS",
    "EXTRA_SECTION_GROUPS",
    "TECHNICAL_SECTIONS",
    "SECTION_GROUP",
    "LIMIT_KEYS",
    "BENEFICIARY_TYPES",
    "ITEM_TYPES",
    "TITLE_MAX_CHARS",
    "schema_hint",
    "CoreIdentityMoney",
    "CorePlan",
    "TechRisk",
    "TechDeductibles",
    "TechClauses",
]

#: Exclusion / particular-clause titles are capped (decision 4).
TITLE_MAX_CHARS = 120
#: Free texts kept on a technical row (a deductible's literal clause, a sublimit).
ROW_TEXT_MAX_CHARS = 600

BENEFICIARY_TYPES = ("hipotecario", "prendario", "leasing", "endosatario", "otro")
ITEM_TYPES = ("ubicación", "bien", "vehículo", "persona")


def _fold(text: str) -> str:
    """Lower-case, accent-free — for matching the closed vocabularies."""
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).strip().lower()


def _closed(value: Any, allowed: tuple[str, ...], aliases: dict[str, str], default: str) -> Any:
    """Snap a free word onto a closed vocabulary (accent/case-insensitive)."""
    if not isinstance(value, str) or not value.strip():
        return None
    folded = _fold(value)
    for option in allowed:
        if _fold(option) == folded:
            return option
    for needle, option in aliases.items():
        if needle in folded:
            return option
    return default


def _cap(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    return value


def _lenient_rows(data: Any, rows: dict[str, type[BaseModel]]) -> Any:
    """Drop the rows that will not coerce, one by one (never the whole list).

    Also accepts a single object where a list is expected (``"insured": {...}``).
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key, model in rows.items():
        value = out.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, str)):
            value = [value]
        if not isinstance(value, list):
            out.pop(key, None)
            continue
        kept: list[Any] = []
        for item in value:
            if item is None:
                continue
            try:
                kept.append(model.model_validate(item))
            except ValidationError:
                continue
        out[key] = kept
    return out


class _Group(ExtractionModel):
    """A tier group. Subclasses name their list fields in ``_ROWS``."""

    @model_validator(mode="before")
    @classmethod
    def _rows_one_by_one(cls, data: Any) -> Any:
        rows: dict[str, type[BaseModel]] = {}
        for name, info in cls.model_fields.items():
            annotation = info.annotation
            if get_origin(annotation) is list:
                (inner,) = get_args(annotation) or (None,)
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    rows[name] = inner
        return _lenient_rows(data, rows)


# =============================================================================
# Core — identity + money (call 1) and the payment plan (call 2)
# =============================================================================


class PartySlim(ExtractionModel):
    legal_name: Str = None
    rut: Str = Field(default=None, json_schema_extra={"hint": "str (RUT tal cual)"})


class PolicyholderSlim(PartySlim):
    contact_email: Str = None
    contact_phone: Str = None


class InsurerSlim(PartySlim):
    trade_name: Str = None
    cmf_code: Str = None


class BrokerSlim(ExtractionModel):
    """The broker PRINTED on the policy — text only, never a tenant. Kept in the
    core because ``validate_policy_core`` requires a corredor."""

    legal_name: Str = None


class BeneficiarySlim(ExtractionModel):
    """Replaces ``pledge_creditor``: the spec's acreedor is a beneficiary type."""

    name: Str = None
    rut: Str = None
    type: Str = Field(
        default=None, json_schema_extra={"hint": "hipotecario|prendario|leasing|endosatario|otro"}
    )
    amount_uf: UF = None

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data}
        if isinstance(data, dict):
            data = dict(data)
            if not data.get("name"):
                data["name"] = data.get("legal_name") or data.get("creditor")
            data["type"] = _closed(
                data.get("type") or data.get("kind"),
                BENEFICIARY_TYPES,
                {"hipotec": "hipotecario", "prend": "prendario", "acreedor": "prendario",
                 "leas": "leasing", "endos": "endosatario"},
                "otro",
            )
        return data


class PremiumSlim(ExtractionModel):
    """What the document STATES. The arithmetic is checked server-side
    (``PremiumBlock`` on the merged payload) — never asked of the model."""

    taxable_premium_uf: UF = None
    exempt_premium_uf: UF = None
    net_premium_uf: UF = None
    vat_uf: UF = None
    total_premium_uf: UF = None


class CoreIdentityMoney(_Group):
    policy_number: Str = None
    renews_policy_number: Str = None
    insurer: InsurerSlim | None = None
    insured: list[PartySlim] = Field(default_factory=list)
    policyholder: PolicyholderSlim | None = None
    beneficiaries: list[BeneficiarySlim] = Field(default_factory=list)
    broker: BrokerSlim | None = None
    line_of_business: Str = Field(default=None, json_schema_extra={"hint": "str (solo pista)"})
    coverage_modality: Str = None
    is_provisional_certificate: bool | None = None
    period_start_at: PolicyDateTime = None
    period_end_at: PolicyDateTime = None
    issue_date: FlexDate = None
    currency: Str = Field(default=None, json_schema_extra={"hint": "UF|CLP|USD"})
    total_insured_amount_uf: UF = None
    premium: PremiumSlim | None = None
    commission_pct: Pct = None
    confidence: Pct = Field(default=None, json_schema_extra={"hint": "num 0-100"})
    extraction_notes: list[str] = Field(
        default_factory=list, json_schema_extra={"hint": "[str] (máx. 3, breves)"}
    )

    @model_validator(mode="after")
    def _short_notes(self) -> "CoreIdentityMoney":
        notes = [str(n)[:200] for n in self.extraction_notes if str(n or "").strip()][:3]
        object.__setattr__(self, "extraction_notes", notes)
        return self


class InstallmentSlim(ExtractionModel):
    number: Int = None
    coupon_number: Str = None
    due_date: FlexDate = None
    gross_amount_uf: UF = None
    net_premium_uf: UF = None
    commission_uf: UF = None


class CorePlan(_Group):
    payment_mode: Str = None
    installments: list[InstallmentSlim] = Field(default_factory=list)


# =============================================================================
# Technical — risk, deductibles + warranties, clause titles
# =============================================================================


class RiskItem(ExtractionModel):
    """One insured object: merges locations / vehicles / insured amounts."""

    type: Str = Field(default=None, json_schema_extra={"hint": "ubicación|bien|vehículo|persona"})
    description: Str = None
    address_or_plate: Str = None
    insured_amount_uf: UF = None

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"description": data}
        if isinstance(data, dict) and data.get("type") is not None:
            data = dict(data)
            data["type"] = _closed(
                data.get("type"),
                ITEM_TYPES,
                {"ubic": "ubicación", "local": "ubicación", "direc": "ubicación",
                 "vehic": "vehículo", "auto": "vehículo", "camion": "vehículo",
                 "patente": "vehículo", "person": "persona", "trabaj": "persona",
                 "asegurado": "persona"},
                "bien",
            )
        return data


class CoverageSlim(ExtractionModel):
    """A coverage with its sublimit merged into ONE text."""

    code: Str = Field(default=None, json_schema_extra={"hint": "str (código POL/CAD)"})
    name: Str = None
    limit_uf: UF = None
    sublimit_text: Str = None

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data}
        if isinstance(data, dict):
            data = dict(data)
            data["sublimit_text"] = _cap(data.get("sublimit_text"), ROW_TEXT_MAX_CHARS)
        return data


class DeductibleSlim(ExtractionModel):
    peril: Str = None
    pct: Pct = None
    min_uf: UF = None
    max_uf: UF = None
    days: Int = None
    text: Str = Field(default=None, json_schema_extra={"hint": "str (literal)"})

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"text": data}
        if isinstance(data, dict):
            data = dict(data)
            data["text"] = _cap(data.get("text"), ROW_TEXT_MAX_CHARS)
        return data


class WarrantySlim(ExtractionModel):
    code: Str = None
    title: Str = None
    requirement: Str = None
    deadline_days: Int = None
    due_date: FlexDate = None

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"title": data}
        if isinstance(data, dict):
            data = dict(data)
            data["requirement"] = _cap(data.get("requirement"), ROW_TEXT_MAX_CHARS)
        return data


class TitleRow(ExtractionModel):
    """An exclusion or particular clause: number + title ONLY (≤ 120 chars)."""

    number: Str = None
    title: Str = None

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: Any) -> Any:
        if isinstance(data, str):
            data = {"title": data}
        if isinstance(data, dict):
            data = dict(data)
            if not data.get("title"):
                data["title"] = data.get("name") or data.get("text")
            data = {"number": data.get("number"), "title": _cap(data.get("title"), TITLE_MAX_CHARS)}
        return data


class TechRisk(_Group):
    items: list[RiskItem] = Field(default_factory=list)
    coverages: list[CoverageSlim] = Field(default_factory=list)
    indemnity_limit: Str = None
    aggregate_limit_uf: UF = None
    valuation_basis: Str = None
    average_rate_permille: Rate = None
    cmf_policy_code: Str = None


class TechSublimits(_Group):
    """An EXPLICIT table of coverage extensions / sublimits, one row each (same row
    shape as a coverage); empty when the policy has none (review r2: every row of
    the coverage table is a ``tech_risk`` coverage)."""

    sublimits: list[CoverageSlim] = Field(default_factory=list)


class TechDeductibles(_Group):
    deductibles: list[DeductibleSlim] = Field(default_factory=list)
    warranties: list[WarrantySlim] = Field(default_factory=list)


class TechClauses(_Group):
    exclusions: list[TitleRow] = Field(default_factory=list)
    particular_clauses: list[TitleRow] = Field(default_factory=list)


# =============================================================================
# Compact hints — a few hundred chars, not a 13.6 k JSON Schema
# =============================================================================

_TYPE_TOKENS = {
    "Decimal": "num",
    "int": "int",
    "bool": "bool",
    "date": "\"AAAA-MM-DD\"",
    "datetime": "\"AAAA-MM-DDTHH:MM\"",
    "str": "str",
}


def _token(annotation: Any) -> str:
    """A one-word type token for an annotation (``Annotated[Decimal|None, …]`` -> num)."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list:
        inner = args[0] if args else str
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return f"[{_render(inner)}]"
        return f"[{_token(inner)}]"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _render(annotation)
    if args:  # Annotated[...] / Union[...]: the first non-None member decides
        for arg in args:
            if arg is type(None):
                continue
            token = _token(arg)
            if token:
                return token
    name = getattr(annotation, "__name__", "")
    return _TYPE_TOKENS.get(name, "")


def _render(model: type[BaseModel]) -> str:
    parts = []
    for name, info in model.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        hint = extra.get("hint") if extra else None
        parts.append(f"\"{name}\":{hint or _token(info.annotation) or 'str'}")
    return "{" + ",".join(parts) + "}"


def schema_hint(model: type[BaseModel]) -> str:
    """The group's compact output template, e.g. ``{"policy_number":str,…}``."""
    return _render(model)


# =============================================================================
# The groups
# =============================================================================


@dataclass(frozen=True)
class TierGroup:
    """One provider call of a tiered read."""

    name: str
    schema: type[BaseModel]
    guidance: str  # Spanish, LLM prompt
    max_tokens: int

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.schema.model_fields)

    @property
    def hint(self) -> str:
        return schema_hint(self.schema)


_CORE_IDENTITY_MONEY = TierGroup(
    name="core_identity_money",
    schema=CoreIdentityMoney,
    max_tokens=4000,
    guidance="""Registra la IDENTIDAD y el DINERO de la póliza.
- policy_number: el N° de póliza; renews_policy_number: la póliza que renueva, si lo dice.
- insurer: la compañía emisora (razón social, nombre de fantasía, RUT y código CMF solo si están impresos).
- insured: TODOS los asegurados (uno o más), cada uno con razón social y RUT. policyholder: el contratante, con correo y teléfono si aparecen.
- beneficiaries: beneficiarios / acreedores (hipotecario, prendario, leasing, endosatario u otro) con su RUT y monto UF si los indica.
- broker: el corredor impreso en la póliza, SOLO como texto.
- is_provisional_certificate: true si el documento es un certificado provisorio o de cobertura y no la póliza definitiva.
- Vigencia con hora (convención 12:00 si el documento no indica otra); issue_date = fecha de emisión.
- premium: lo que el documento DECLARA de la prima total de la póliza (afecta, exenta, neta, IVA, total/bruta). NO uses los montos de una cuota.
- commission_pct: la comisión del corredor en %.""",
)

_CORE_PLAN = TierGroup(
    name="core_plan",
    schema=CorePlan,
    max_tokens=4000,
    guidance="""Registra SOLO la FORMA DE PAGO y el PLAN DE CUOTAS.
- payment_mode: la forma de pago tal como la indica (PAC, PAT, contado, aviso de vencimiento, cuponera…).
- installments: TODAS las cuotas, en orden: número, n° de cupón si existe, fecha de vencimiento, monto bruto UF (con IVA), prima neta UF y comisión UF si la tabla las trae.
- Copia cada cuota como aparece; no la calcules ni la repartas. La suma de las cuotas suele igualar la prima bruta: si no cuadra, igual transcribe lo que dice.""",
)

_TECH_RISK = TierGroup(
    name="tech_risk",
    schema=TechRisk,
    max_tokens=6000,
    guidance="""Registra la MATERIA ASEGURADA y las COBERTURAS.
- items: cada ubicación, bien, vehículo o persona asegurada (type = ubicación|bien|vehículo|persona), con su descripción, dirección o patente y monto asegurado UF.
- coverages: TODAS las filas del CUADRO DE COBERTURAS de la póliza (la tabla o lista de coberturas contratadas), UNA por fila y en el orden del documento, sin omitir ninguna. En vehículos y otros ramos también son coberturas las asistencias (grúa, 24/7), el vehículo de reemplazo, los daños en el extranjero, la responsabilidad civil, la defensa penal, el valor a nuevo, el taller de marca, huelga / terrorismo, sismo / fenómenos de la naturaleza…: si la póliza la lista como cobertura, es una fila. Cada fila con su código POL/CAD si aparece, nombre, límite UF (o monto asegurado) y, en sublimit_text, su condición en una frase literal breve. Solo las filas de un cuadro APARTE titulado "extensiones de cobertura" o "sublímites" NO van aquí: las extrae otra llamada paralela.
- indemnity_limit (texto literal), aggregate_limit_uf, valuation_basis (base de indemnización: valor de reposición, valor real…), average_rate_permille (tasa promedio por mil), cmf_policy_code (código POL/CAD de las condiciones generales).""",
)

_TECH_SUBLIMITS = TierGroup(
    name="tech_sublimits",
    schema=TechSublimits,
    max_tokens=6000,
    guidance="""Registra SOLO un CUADRO EXPLÍCITO de EXTENSIONES DE COBERTURA y/o SUBLÍMITES: una tabla o lista APARTE del cuadro de coberturas, titulada así ("extensiones de cobertura", "sublímites", "cláusulas adicionales con sublímite").
- sublimits: UNA fila por cada extensión o partida con sublímite de ese cuadro: name (su nombre), limit_uf (el sublímite en UF si es un monto) y en sublimit_text la condición literal breve ("10% del monto asegurado, máx UF 25.000", "No otorgada").
- No juntes varias extensiones en un mismo texto: cada una es su propia fila, en el orden del documento.
- Si la póliza NO tiene ese cuadro aparte, devuelve sublimits: []. Nunca repitas aquí las filas del cuadro de coberturas (las extrae otra llamada paralela).""",
)

_TECH_DEDUCTIBLES = TierGroup(
    name="tech_deductibles",
    schema=TechDeductibles,
    max_tokens=6000,
    guidance="""Registra los DEDUCIBLES y las GARANTÍAS.
- deductibles: uno por peligro, con pct, min_uf, max_uf o days cuando el texto los da, y en text la cláusula LITERAL ("5% de la pérdida, mínimo UF 25"). Incendio suele ser % de la PÉRDIDA; sismo, % del MONTO ASEGURADO del ítem afectado con mínimo UF: transcribe la base tal como la dice el documento. Si no estás seguro de un número, deja solo el text.
- warranties: garantías / recomendaciones / requisitos (códigos R-n, G-n, M-n si existen) con título, requisito literal breve, plazo en días o fecha límite.""",
)

_TECH_CLAUSES = TierGroup(
    name="tech_clauses",
    schema=TechClauses,
    max_tokens=6000,
    guidance=f"""Registra SOLO los TÍTULOS de las EXCLUSIONES y de las CLÁUSULAS PARTICULARES / ADICIONALES.
- exclusions y particular_clauses: por cada una, su número (si lo tiene) y su TÍTULO (máx. {TITLE_MAX_CHARS} caracteres).
- NUNCA copies el texto completo de la cláusula: el texto queda en el PDF y se consulta aparte.""",
)

CORE_GROUPS: tuple[TierGroup, ...] = (_CORE_IDENTITY_MONEY, _CORE_PLAN)
TECHNICAL_GROUPS: tuple[TierGroup, ...] = (_TECH_RISK, _TECH_SUBLIMITS, _TECH_DEDUCTIBLES, _TECH_CLAUSES)

#: The reviewable sections of a technical read, and the group that fills each.
TECHNICAL_SECTIONS: tuple[str, ...] = (
    "items",
    "coverages",
    "deductibles",
    "warranties",
    "exclusions",
    "particular_clauses",
    "limits",
)
#: The scalar keys of ``tech_risk`` that travel together as ``sections.limits``.
LIMIT_KEYS: tuple[str, ...] = (
    "indemnity_limit",
    "aggregate_limit_uf",
    "valuation_basis",
    "average_rate_permille",
    "cmf_policy_code",
)
SECTION_GROUP: dict[str, str] = {
    "items": "tech_risk",
    "coverages": "tech_risk",
    "limits": "tech_risk",
    "deductibles": "tech_deductibles",
    "warranties": "tech_deductibles",
    "exclusions": "tech_clauses",
    "particular_clauses": "tech_clauses",
}
#: Sections filled by MORE than one group: Coberturas = every row of the coverage
#: table (``tech_risk``) followed by an explicit extensions/sublimits table
#: (``tech_sublimits``) — kept apart because a single call packed all 41
#: sublimits of a TRBF policy into ONE text. A sublimit row named like a
#: coverage row is dropped on merge (``policy_reading.technical_sections``).
EXTRA_SECTION_GROUPS: dict[str, tuple[str, ...]] = {"coverages": ("tech_sublimits",)}
