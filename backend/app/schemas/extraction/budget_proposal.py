"""``budget_proposal`` (v8) — the DYNAMIC per-proposal comparison extraction.

One insurer offer, read the incremental-comparison way (docs, ``comparison.py``):

  * a WRONG-FILE discriminator folded into the SAME structured call — the model
    decides ``budget_proposal`` vs ``not_a_proposal`` and, when it rejects,
    explains why, so a router can render a rejection card without a second pass;
  * the KEPT FIXED money/period core, reusing the exact field names and
    ``AliasChoices`` of :mod:`app.schemas.extraction.insurer_quotation` so the
    core commits to the typed ``proposal`` columns with no lossy remap and is
    invariant-checked by the SHARED ``reconcile_money`` (never re-implemented);
  * an OPEN, SLIM ``facets`` list — the heterogeneous, per-line coverage /
    exclusion / deductible / sublimit / clause / warranty dimensions that ramo
    variance lives in, each carrying the ``verbatim`` wording (in this cartera
    the exact wording IS the coverage) plus a FREE-shape ``value``.

Phase 1 rewired this read to **tool/function calling** (GLM-5.3-Flash on
DeepInfra supports it). The tool schema this module exposes is deliberately SLIM
— no 10-named-leaf value sub-object, no lenient-number regex the model must
satisfy. Numeric coercion happens in Python (the ``common.py`` ``UF`` / ``Pct``
BeforeValidators) AFTER the JSON arguments are parsed, never as a schema
constraint the model has to honour. Every leaf stays lenient (``extra="allow"``)
so the service's ``_coerce_payload`` drop-bad-fields loop preserves a partial
read; the facet list is CAPPED to bound the ``extra="allow"`` blast radius.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, Field, model_validator

from app.schemas.extraction.common import (
    UF,
    ExtractionModel,
    FlexDate,
    Int,
    Pct,
    PolicyDateTime,
    Rate,
    RootExtraction,
    Str,
)

__all__ = ["BudgetProposalExtraction", "Facet", "MAX_FACETS", "budget_proposal_tool_schema"]

# The ``extra="allow"`` leniency that keeps unknown dimensions is also a garbage
# vector, so the facet list is hard-capped. 120 is comfortably above the widest
# corpus offer (a Todo Riesgo programme rarely lists past ~60 lines).
MAX_FACETS = 120

_FACET_GROUPS = (
    "coverage",
    "exclusion",
    "deductible",
    "sublimit",
    "clause",
    "warranty",
    "other",
)

FacetGroup = Literal[
    "coverage",
    "exclusion",
    "deductible",
    "sublimit",
    "clause",
    "warranty",
    "other",
]


class Facet(ExtractionModel):
    """One open dimension of an offer: a coverage, exclusion, deductible, ...

    SLIM by design (Phase 1): the reviewer / alignment pass sorts on ``group``
    and ``label`` and never loses the decisive wording (``verbatim``). ``value``
    is a FREE optional string / number (a limit, a percentage, a UF minimum, a
    waiting period in days) — whatever the offer states, kept as-is; numeric
    coercion is a Python concern, not something the model must format.

    ``key`` and ``description`` are retained as OPTIONAL extras so the existing
    alignment path (``ai._source_facets`` / ``_canonical_key``) keeps working
    when they are present; the slim tool schema no longer asks the model for
    them.
    """

    key: Str = Field(
        default=None, description="Stable snake_case slug for alignment, e.g. 'sismo'"
    )
    group: FacetGroup = Field(
        default="other",
        description="coverage | exclusion | deductible | sublimit | clause | warranty | other",
    )
    label: Str = Field(default=None, description="Short human label, in Spanish")
    description: Str = Field(
        default=None, description="Plain-language summary of the dimension"
    )
    verbatim: Str = Field(
        default=None, description="The exact wording copied from the document"
    )
    present: bool | None = Field(
        default=None,
        description="True if the offer includes it, False if it explicitly excludes it",
    )
    value: Any = Field(
        default=None,
        description="Free value: a UF limit, a percentage, a UF minimum, days…",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_group(cls, data: Any) -> Any:
        """Fold an unknown ``group`` back to ``other`` instead of failing.

        A strict Literal that rejected would make ``_coerce_payload`` drop the
        WHOLE facets list on a single stray value; this keeps the leaf lenient.
        """
        if isinstance(data, dict):
            group = data.get("group")
            if isinstance(group, str) and group.strip().lower() not in _FACET_GROUPS:
                data = {**data, "group": "other"}
        return data


class BudgetProposalExtraction(RootExtraction):
    """One insurer offer read dynamically: verdict + fixed core + open facets."""

    # --- (a) wrong-file discriminator ---------------------------------------
    document_type: Literal["budget_proposal", "not_a_proposal"] = Field(
        default="budget_proposal",
        description="budget_proposal if this is an insurer offer, else not_a_proposal",
    )
    document_type_confidence: Pct = Field(
        default=None, description="0-100 confidence in the document_type verdict"
    )
    rejection_reason: Str = Field(
        default=None,
        description="Why the file is not an offer — filled only when not_a_proposal",
    )

    # --- (b) fixed money/period core (names + aliases from insurer_quotation) -
    quotation_number: Str = Field(default=None, description="Número de cotización, verbatim")
    insurer_name: Str = Field(default=None, description="Company name as printed (never matched on)")
    insurer_rut: Str = None
    insurer_cmf_code: Str = None

    period_start_at: PolicyDateTime = None
    period_end_at: PolicyDateTime = None
    coverage_start: FlexDate = None
    coverage_end: FlexDate = None
    validity_business_days: Int = Field(
        default=None,
        validation_alias=AliasChoices("validity_business_days", "validity_days"),
        description="Vigencia de la oferta en días hábiles",
    )

    taxable_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("taxable_premium_uf", "net_premium_taxable_uf"),
    )
    exempt_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("exempt_premium_uf", "net_premium_exempt_uf"),
    )
    net_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("net_premium_uf", "net_premium_total_uf"),
    )
    vat_uf: UF = None
    total_premium_uf: UF = Field(
        default=None,
        validation_alias=AliasChoices("total_premium_uf", "gross_premium_uf"),
    )

    taxable_rate_permille: Rate = None
    exempt_rate_permille: Rate = None
    comprehensive_rate_permille: Rate = Field(
        default=None,
        validation_alias=AliasChoices("comprehensive_rate_permille", "average_rate"),
    )
    commission_pct: Pct = Field(
        default=None,
        validation_alias=AliasChoices("commission_pct", "broker_commission_pct"),
    )

    # --- (c) the open, heterogeneous dimensions ------------------------------
    facets: list[Facet] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cap_facets(self) -> "BudgetProposalExtraction":
        """Truncate (never reject) an over-long facet list — a partial read wins."""
        if len(self.facets) > MAX_FACETS:
            object.__setattr__(self, "facets", self.facets[:MAX_FACETS])
        return self


def budget_proposal_tool_schema() -> dict[str, Any]:
    """The SLIM OpenAI function-tool schema for the budget-proposal read.

    Returned as the ``function`` object (``name`` / ``description`` /
    ``parameters``) so the caller wraps it as ``{"type": "function", "function":
    <this>}``. It is intentionally hand-built and light: numbers are plain
    ``number`` / ``integer`` (no lenient-number regex), dates are plain strings,
    and each facet is the SLIM ``{group, label, verbatim, present, value}`` shape
    — not the old 10-leaf value object. Fields are almost all optional (only
    ``document_type`` is required); an unstated field is simply omitted, which
    the Python-side ``_drop_explicit_nulls`` + BeforeValidators then normalise.
    """
    string = {"type": "string"}
    number = {"type": "number"}

    return {
        "name": "record_budget_proposal",
        "description": (
            "Registra UNA oferta/cotización de seguro leída de un documento del "
            "mercado chileno: el veredicto de archivo, el núcleo fijo de prima y "
            "vigencia, y una lista dinámica de facets. No inventes valores; omite "
            "lo que el documento no declare. RELLENA TODOS los campos del núcleo de "
            "prima que el documento declare (afecta, exenta, neta, IVA, total): no "
            "informes solo uno. En CADA facet copia en `verbatim` la redacción "
            "EXACTA del documento, palabra por palabra; `value` es solo el número/"
            "límite/porcentaje ya extraído, NUNCA reemplaza a `verbatim`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                # (a) wrong-file discriminator
                "document_type": {
                    "type": "string",
                    "enum": ["budget_proposal", "not_a_proposal"],
                    "description": (
                        "budget_proposal si el documento ES una oferta de una "
                        "aseguradora; de lo contrario not_a_proposal."
                    ),
                },
                "document_type_confidence": {
                    "type": "number",
                    "description": "Confianza 0-100 en el veredicto document_type.",
                },
                "rejection_reason": {
                    "type": "string",
                    "description": "Por qué no es una oferta (solo si not_a_proposal), en español.",
                },
                # (b) fixed money/period core
                "quotation_number": {"type": "string", "description": "Número de cotización, verbatim."},
                "insurer_name": {"type": "string", "description": "Nombre de la compañía tal como aparece."},
                "insurer_rut": {"type": "string", "description": "RUT de la compañía, tal como aparece."},
                "insurer_cmf_code": {"type": "string", "description": "Código CMF de la compañía."},
                "period_start_at": {"type": "string", "description": "Inicio de vigencia del seguro, ISO."},
                "period_end_at": {"type": "string", "description": "Término de vigencia del seguro, ISO."},
                "coverage_start": {"type": "string", "description": "Inicio de cobertura AAAA-MM-DD."},
                "coverage_end": {"type": "string", "description": "Término de cobertura AAAA-MM-DD."},
                "validity_business_days": {
                    "type": "integer",
                    "description": "Vigencia de la oferta en días hábiles.",
                },
                "taxable_premium_uf": {"type": "number", "description": "Prima afecta (UF)."},
                "exempt_premium_uf": {"type": "number", "description": "Prima exenta (UF)."},
                "net_premium_uf": {"type": "number", "description": "Prima neta = afecta + exenta (UF)."},
                "vat_uf": {"type": "number", "description": "IVA = 0,19 x afecta (UF)."},
                "total_premium_uf": {"type": "number", "description": "Total a pagar = neta + IVA (UF)."},
                "taxable_rate_permille": {"type": "number", "description": "Tasa afecta, por mil."},
                "exempt_rate_permille": {"type": "number", "description": "Tasa exenta, por mil."},
                "comprehensive_rate_permille": {"type": "number", "description": "Tasa total, por mil."},
                "commission_pct": {"type": "number", "description": "Comisión del corredor, 0-100."},
                # (c) open, heterogeneous dimensions — SLIM
                "facets": {
                    "type": "array",
                    "description": (
                        "Dimensiones dinámicas de la oferta, una por línea. No inventes; "
                        "extrae solo lo que el documento declara."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "group": {
                                "type": "string",
                                "enum": list(_FACET_GROUPS),
                                "description": "coverage | exclusion | deductible | sublimit | clause | warranty | other",
                            },
                            "label": {"type": "string", "description": "Etiqueta corta, en español."},
                            "verbatim": {
                                "type": "string",
                                "description": (
                                    "OBLIGATORIO. La redacción EXACTA, palabra por "
                                    "palabra, copiada del documento para esta "
                                    "dimensión. NO resumas ni parafrasees: copia el "
                                    "texto tal cual aparece."
                                ),
                            },
                            "present": {
                                "type": "boolean",
                                "description": "true si la ampara, false si la excluye.",
                            },
                            "value": {
                                "type": ["string", "number"],
                                "description": (
                                    "SOLO el valor ya extraído: un límite UF, un %, un "
                                    "mínimo UF, días… La redacción textual va en "
                                    "`verbatim`, no aquí."
                                ),
                            },
                        },
                        "required": ["group", "verbatim"],
                    },
                },
            },
            "required": ["document_type"],
        },
    }
