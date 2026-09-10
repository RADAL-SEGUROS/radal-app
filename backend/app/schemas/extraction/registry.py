"""The document category registry — one :class:`CategorySpec` per extractable category.

This module is the contract three other layers depend on:

* the **AI service** looks up the Pydantic schema, the Spanish prompt guidance
  and the prompt version for a category (``spec_for``);
* the **importer** classifies a corpus file by its code token
  (``CODE_TO_CATEGORY`` / ``category_for_code``);
* the **UI** renders any suggestion form generically from ``GET /ai/categories``,
  which is just this registry serialised.

``guidance`` is **Spanish prose**. That is deliberate and allowed: the source
documents are Spanish, so the prompt is Spanish (CLAUDE.md rule 1 — Spanish
belongs in locale values and in LLM prompts, nowhere else).

``proposal`` is a DEPRECATED alias of ``insurer_quotation`` and maps to the very
same spec object. That aliasing is what keeps ``pages/proposals/upload.tsx`` and
the existing proposal tests working while new code writes ``insurer_quotation``.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel

from app.models.ai import ExtractionKind
from app.models.document import DocumentCategory
from app.models.enums import CaseSection, DocumentDirection
from app.schemas.extraction.broker_closing_note import BrokerClosingNoteExtraction
from app.schemas.extraction.budget_proposal import BudgetProposalExtraction
from app.schemas.extraction.business_questionnaire import BusinessQuestionnaireExtraction
from app.schemas.extraction.claim_final_report import ClaimFinalReportExtraction
from app.schemas.extraction.claim_notice import ClaimNoticeExtraction
from app.schemas.extraction.claim_preliminary_report import ClaimPreliminaryReportExtraction
from app.schemas.extraction.compliance_notice import ComplianceNoticeExtraction
from app.schemas.extraction.conditional_pronouncement import (
    ConditionalPronouncementExtraction,
)
from app.schemas.extraction.collection_status import CollectionStatusExtraction
from app.schemas.extraction.declination import DeclinationExtraction
from app.schemas.extraction.endorsement import EndorsementExtraction
from app.schemas.extraction.endorsement_proposal import EndorsementProposalExtraction
from app.schemas.extraction.inspection_report import InspectionReportExtraction
from app.schemas.extraction.insured_values_schedule import InsuredValuesScheduleExtraction
from app.schemas.extraction.insurer_quotation import InsurerQuotationExtraction
from app.schemas.extraction.issuance_proposal import IssuanceProposalExtraction
from app.schemas.extraction.loss_history import LossHistoryExtraction
from app.schemas.extraction.payment_plan import PaymentPlanExtraction
from app.schemas.extraction.policy import PolicyExtraction
from app.schemas.extraction.prospect_request import ProspectRequestExtraction
from app.schemas.extraction.quote_comparison import QuoteComparisonExtraction
from app.schemas.extraction.resubmission_letter import ResubmissionLetterExtraction
from app.schemas.extraction.risk_engineering_plan import RiskEngineeringPlanExtraction
from app.schemas.extraction.submission_letter import SubmissionLetterExtraction
from app.schemas.extraction.technical_brief import TechnicalBriefExtraction
from app.schemas.extraction.technical_recommendation import (
    TechnicalRecommendationExtraction,
)

__all__ = [
    "CategorySpec",
    "CATEGORY_REGISTRY",
    "CODE_TO_CATEGORY",
    "CODE_ALIASES",
    "SECTION_CODE_TO_CATEGORY",
    "UnknownCategory",
    "spec_for",
    "has_spec",
    "category_for_code",
    "registry_as_json",
]

# The legacy proposal prompt version. Kept verbatim so the extraction rows the
# existing /ai/proposals/extract flow writes stay comparable across the change.
LEGACY_PROPOSAL_PROMPT_VERSION = "proposal-extract-v1"


class UnknownCategory(LookupError):
    """No registry schema exists for that document category. -> 422 at the router."""


@dataclass(frozen=True)
class CategorySpec:
    """Everything the pipeline needs to read one kind of document."""

    category: DocumentCategory
    code: str | None
    section: CaseSection
    direction: DocumentDirection
    schema: type[BaseModel]
    module: str
    prompt_version: str
    guidance: str
    prefill_target: str
    extraction_kind: ExtractionKind

    @property
    def codes(self) -> tuple[str, ...]:
        """Every corpus code token that maps to this category."""
        return tuple(
            code for code, category in CODE_ALIASES.items() if category is self.category
        )


def _spec(
    category: DocumentCategory,
    code: str | None,
    section: CaseSection,
    direction: DocumentDirection,
    schema: type[BaseModel],
    module: str,
    guidance: str,
    prefill_target: str,
    *,
    prompt_version: str | None = None,
    extraction_kind: ExtractionKind = ExtractionKind.CASE_DOCUMENT,
) -> CategorySpec:
    return CategorySpec(
        category=category,
        code=code,
        section=section,
        direction=direction,
        schema=schema,
        module=module,
        prompt_version=prompt_version or f"{category.value}-v1",
        guidance=guidance.strip(),
        prefill_target=prefill_target,
        extraction_kind=extraction_kind,
    )


_PKG = "app.schemas.extraction"

# --- The registry ------------------------------------------------------------
# Order follows the journey: root -> submission -> quotes -> proposal -> policy
# -> collection -> endorsement -> claim.

_SPECS: list[CategorySpec] = [
    _spec(
        DocumentCategory.PROSPECT_REQUEST,
        "00A",
        CaseSection.ROOT_PROSPECT,
        DocumentDirection.INSURED_TO_BROKER,
        ProspectRequestExtraction,
        f"{_PKG}.prospect_request",
        """
Es la SOLICITUD DEL PROSPECTO: una carta del asegurado (o su representante) al corredor.
Extrae la identidad del solicitante (razón social, RUT, nombre de fantasía), quién firma y
con qué cargo, la línea de seguro que menciona, la póliza vigente (número, compañía,
vencimiento) y el corredor actual si lo nombra — ese corredor es SOLO TEXTO, nunca un dato
de sistema. Registra el hecho que gatilla la búsqueda (siniestro, alza de prima, rechazo)
con su fecha y monto, las preocupaciones y las peticiones concretas, una por una y con las
palabras del documento.
""",
        "client + contacts + placement(intake) + document checklist",
    ),
    _spec(
        DocumentCategory.BUSINESS_QUESTIONNAIRE,
        "00B",
        CaseSection.SUBMISSION,
        DocumentDirection.INSURED_TO_BROKER,
        BusinessQuestionnaireExtraction,
        f"{_PKG}.business_questionnaire",
        """
Es la FICHA DEL NEGOCIO que completa el asegurado. Extrae los datos maestros de la empresa
(razón social, RUT, giro, código de actividad, dirección comercial, representante legal),
los indicadores (antigüedad, dotación, ventas anuales UF, margen de contribución + costos
fijos) y TODAS las tablas: ubicaciones, construcción y protecciones, flota (patente, chasis
o VIN, marca, modelo, año, uso, valor), conductores autorizados, nómina de trabajadores y
perfiles de riesgo. El VIN se copia tal cual aparece: NO lo corrijas ni lo cruces con otro
documento. Las preguntas abiertas van en `questionnaire` con pregunta y respuesta literales.
""",
        "client master data + asset (10 promoted columns + attributes JSON) + placement questionnaire",
    ),
    _spec(
        DocumentCategory.INSURED_VALUES_SCHEDULE,
        "00C",
        CaseSection.SUBMISSION,
        DocumentDirection.BROKER_TO_INSURERS,
        InsuredValuesScheduleExtraction,
        f"{_PKG}.insured_values_schedule",
        """
Es la PLANILLA DE MONTOS ASEGURADOS (normalmente un Excel con varias hojas). Los nombres de
las partidas NO son un vocabulario fijo: cada fila de `value_matrix` lleva la partida tal
como está escrita en `label` y su monto en `amount_uf`. Los montos son UF con punto de miles
y coma decimal ("UF 17.920" son diecisiete mil novecientos veinte). Extrae también la base
de valorización, la fecha de valorización, el perjuicio por paralización (monto, base, meses
y deducible), el total del programa, la comparación valor actual vs valorizado, el
infraseguro y la regla proporcional si aparecen.
""",
        "asset value schedule + quote_request.declared_value_uf + quote_line_item rows",
    ),
    _spec(
        DocumentCategory.LOSS_HISTORY,
        "00D",
        CaseSection.SUBMISSION,
        DocumentDirection.INSURER_TO_BROKER,
        LossHistoryExtraction,
        f"{_PKG}.loss_history",
        """
Es el CERTIFICADO DE SINIESTRALIDAD emitido por una compañía. Extrae los períodos cubiertos,
la compañía emisora y la fecha de emisión, y una fila por siniestro (período, fecha,
descripción, monto UF, cobertura, estado). Los totales (denunciado, indemnizado, rechazado,
recuperos, reservas), la prima acumulada, la siniestralidad en porcentaje, la frecuencia y
la severidad media se registran tal como los declara ESTE certificado. No los recalcules ni
los compares con otros documentos.
""",
        "historical claim rows (status only) + loss-ratio KPIs on the case",
    ),
    _spec(
        DocumentCategory.INSPECTION_REPORT,
        "00E",
        CaseSection.SUBMISSION,
        DocumentDirection.THIRD_PARTY_TO_BROKER,
        InspectionReportExtraction,
        f"{_PKG}.inspection_report",
        """
Es el INFORME DE INSPECCIÓN DE RIESGO de una empresa inspectora. Extrae el folio, la
inspectora, el inspector y su credencial, las fechas de visita e informe, el puntaje de cada
módulo con su ponderación, el puntaje técnico ponderado, la clase de riesgo y el veredicto.
Las matrices (construcción por ubicación, protección contra incendio, carga combustible,
protección contra robo, escenarios de exposición, MFL y PML, cumplimiento normativo) se
copian fila por fila. Cada RECOMENDACIÓN se extrae con su código (R-1, G-4, M-5…), su
categoría A/B/C/D, el plazo, el tipo y el costo estimado: ese código es la llave que después
aparece en la póliza, en la cobranza y en el siniestro.
""",
        "inspection (scores -> columns, checklist -> JSON, boundaries -> child) + warranty rows",
        extraction_kind=ExtractionKind.INSPECTION,
    ),
    _spec(
        DocumentCategory.TECHNICAL_BRIEF,
        "01",
        CaseSection.SUBMISSION,
        DocumentDirection.BROKER_TO_INSURERS,
        TechnicalBriefExtraction,
        f"{_PKG}.technical_brief",
        """
Son las BASES TÉCNICAS que el corredor envía al mercado. Definen qué se pide cotizar.
Extrae la línea, la modalidad de cobertura, la vigencia solicitada (fecha Y hora: la
convención es a las 12:00), la comisión del corredor, las ubicaciones y sus montos, el
límite de indemnización y su base. Las COBERTURAS SOLICITADAS y los DEDUCIBLES SOLICITADOS
se copian literalmente, con su numeración: el comparador alinea después las ofertas contra
exactamente esa redacción. Extrae también las brechas (código B-n, título, severidad), las
garantías aceptadas, las instrucciones de cotización, el plazo de oferta y la fecha estimada
de adjudicación.
""",
        "quote_request header + requested coverages/deductibles + placement.brief_document_id",
    ),
    _spec(
        DocumentCategory.SUBMISSION_LETTER,
        "02",
        CaseSection.SUBMISSION,
        DocumentDirection.BROKER_TO_INSURERS,
        SubmissionLetterExtraction,
        f"{_PKG}.submission_letter",
        """
Es la CARTA DE REMISIÓN con que el corredor despacha la carpeta al mercado. Lo decisivo es
la LISTA DE COMPAÑÍAS DESTINATARIAS: extrae cada una con su nombre y, si aparecen, su RUT y
su código CMF. Extrae además el contenido de la carpeta (código y título de cada anexo), los
puntos de suscripción destacados, el plazo de oferta, la fecha estimada de adjudicación y el
ofrecimiento de visita en terreno.
""",
        "quote_request dispatch: recipient_insurer_ids, sent_at, due_at, one pending proposal slot",
    ),
    _spec(
        DocumentCategory.RISK_ENGINEERING_PLAN,
        "03E",
        CaseSection.SUBMISSION,
        DocumentDirection.BROKER_AND_INSURED_TO_INSURERS,
        RiskEngineeringPlanExtraction,
        f"{_PKG}.risk_engineering_plan",
        """
Es el PLAN DE INGENIERÍA DE RIESGOS. Extrae la inversión total, la duración, el puntaje y la
clase antes y después, y CADA MEDIDA con su código, alcance, plazo en días, costo UF,
proveedor y cotización de referencia. Extrae también los hitos de verificación, la carta
Gantt por mes, el financiamiento, la aritmética de la decisión (costo del plan contra prima
ahorrada) y, muy importante, el MECANISMO DE REBAJA DE DEDUCIBLE con su redacción literal.
""",
        "warranty rows with source=engineering_measure + budget/deadline",
    ),
    _spec(
        DocumentCategory.RESUBMISSION_LETTER,
        "03F",
        CaseSection.SUBMISSION,
        DocumentDirection.BROKER_TO_INSURERS,
        ResubmissionLetterExtraction,
        f"{_PKG}.resubmission_letter",
        """
Es la CARTA DE RE-REMISIÓN: la segunda vuelta al mercado. Extrae el número de ronda, el
monto asegurado propuesto, el nuevo plazo, las compañías destinatarias y, fila por fila, la
respuesta de cada compañía en la primera ronda (cotizó, declinó, condicionó o no respondió)
con sus fundamentos literales. Extrae la nueva evidencia acompañada, la petición específica
a cada compañía y el argumento de cierre.
""",
        "quote_request.round_no += 1; per-insurer proposal.outcome",
    ),
    _spec(
        DocumentCategory.INSURER_QUOTATION,
        "03",
        CaseSection.INSURER_QUOTES,
        DocumentDirection.INSURER_TO_BROKER,
        InsurerQuotationExtraction,
        f"{_PKG}.insurer_quotation",
        """
Es una COTIZACIÓN de una compañía de seguros.

IDENTIDAD DE LA COMPAÑÍA — crítico: informa el RUT y el código CMF tal como están impresos.
NUNCA los deduzcas del nombre; si no aparecen, deja null.

MONTOS en UF, como números: sin "UF", sin punto de miles, con punto decimal
("UF 1.234,56" -> 1234.56). La estructura de prima es
    neta = afecta + exenta;  IVA = 0,19 x AFECTA (la cobertura de sismo es exenta, el IVA
    nunca se calcula sobre la neta);  total = neta + IVA.
Informa cada componente que el documento declare; no inventes los demás. Las tasas son POR
MIL (1,32 para "1,32 por mil") y la tasa total = tasa afecta + tasa exenta.

DEDUCIBLES: una fila por peligro (incendio, sismo, robo, perjuicio por paralización…), con
`basis` = "loss" cuando es % de la pérdida, "insured_amount" cuando es % del monto asegurado
del ítem afectado, "fixed" para un monto fijo y "days" para un período de espera. Copia
además la redacción literal en `requested` / `offered` / `text`: en esta cartera la redacción
ES la cobertura.

COBERTURAS y EXCLUSIONES: una entrada por ítem, con la numeración del documento y el texto
literal en español. La vigencia de la oferta va en días hábiles.
""",
        "proposal (money -> columns, deductibles -> JSON, coverages -> proposal_coverage)",
        prompt_version=LEGACY_PROPOSAL_PROMPT_VERSION,
        extraction_kind=ExtractionKind.PROPOSAL,
    ),
    _spec(
        DocumentCategory.BUDGET_PROPOSAL,
        None,
        CaseSection.INSURER_QUOTES,
        DocumentDirection.INSURER_TO_BROKER,
        BudgetProposalExtraction,
        f"{_PKG}.budget_proposal",
        """
Es una OFERTA / COTIZACIÓN de una compañía de seguros, leída de forma DINÁMICA para el
comparador incremental. Devuelves TRES cosas en un solo objeto JSON.

1) DISCRIMINADOR DE ARCHIVO (detección de archivo equivocado):
   - `document_type` = "budget_proposal" solo si el documento ES una oferta de una aseguradora
     (una propuesta/cotización con prima, vigencia o coberturas). Si es cualquier otra cosa
     (una carta, una planilla de montos, una póliza emitida, un correo, un documento en blanco),
     `document_type` = "not_a_proposal" y explica por qué en `rejection_reason`, en español.
   - `document_type_confidence` va de 0 a 100.

2) NÚCLEO FIJO de prima y vigencia (montos en UF, punto decimal, sin punto de miles):
   la estructura es neta = afecta + exenta ; IVA = 0,19 x AFECTA (el sismo es exento, el IVA
   nunca se calcula sobre la neta) ; total = neta + IVA. Las tasas son POR MIL. Informa el RUT
   y el código CMF de la compañía tal como aparecen; NUNCA los deduzcas del nombre. La vigencia
   de la oferta va en días hábiles.

3) FACETS: una lista DINÁMICA de dimensiones de la oferta, una por línea. Cada facet lleva:
   `group` ("coverage", "exclusion", "deductible", "sublimit", "clause", "warranty" u "other"),
   `key` (un slug estable en minúsculas, p. ej. "sismo"), `label` (etiqueta corta en español),
   `description` (resumen en lenguaje llano de qué dice esa dimensión), `verbatim` (la redacción
   EXACTA copiada del documento — en esta cartera la redacción ES la cobertura), `present`
   (true si la ampara, false si la excluye) y `value` (montos, porcentajes, mínimos UF, base del
   deducible, días…). No inventes facets: extrae solo lo que el documento declara.
""",
        "comparison_source.facets + the fixed core -> the proposal money/period columns",
        prompt_version="budget-proposal-v1",
        extraction_kind=ExtractionKind.PROPOSAL,
    ),
    _spec(
        DocumentCategory.DECLINATION,
        "03A",
        CaseSection.INSURER_QUOTES,
        DocumentDirection.INSURER_TO_BROKER,
        DeclinationExtraction,
        f"{_PKG}.declination",
        """
Es una DECLINACIÓN: la compañía no cotiza. Extrae el pronunciamiento literal y su fecha, la
compañía (RUT y código CMF si aparecen), el monto solicitado y CADA MOTIVO de declinación
numerado, con su fundamento textual — la próxima ronda se construye respondiéndolos uno a
uno. Si la compañía ya tiene una póliza con este asegurado, extrae su número y si ofrece o
no renovarla en las condiciones vigentes.
""",
        "proposal with status=rejected, outcome=declined, reasons in notes + meta",
    ),
    _spec(
        DocumentCategory.CONDITIONAL_PRONOUNCEMENT,
        "03D",
        CaseSection.INSURER_QUOTES,
        DocumentDirection.INSURER_TO_BROKER,
        ConditionalPronouncementExtraction,
        f"{_PKG}.conditional_pronouncement",
        """
Es un PRONUNCIAMIENTO CONDICIONADO: la compañía cotizará sólo si se cumplen ciertas medidas.
Separa con cuidado las medidas EXIGIDAS ANTES DE COTIZAR de las GARANTÍAS POSTERIORES A LA
EMISIÓN: son dos listas distintas y se convierten en garantías de distinto tipo. Extrae
además las anticipaciones de cobertura, la respuesta al mecanismo de deducible, las
condiciones anticipadas, el folio de inspección aceptado y el plazo comprometido para
cotizar (en días hábiles).
""",
        "proposal.outcome=conditional + warranty rows (pre-quotation vs post-issuance)",
    ),
    _spec(
        DocumentCategory.QUOTE_COMPARISON,
        "06",
        CaseSection.INSURER_QUOTES,
        DocumentDirection.BROKER_TO_INSURED,
        QuoteComparisonExtraction,
        f"{_PKG}.quote_comparison",
        """
Es el CUADRO COMPARATIVO que el corredor prepara para el asegurado. Extrae una fila por
oferta comparada (compañía, número de cotización, prima total y neta, monto asegurado, tasa,
resumen de deducibles), la advertencia metodológica literal, el hallazgo central, la matriz
de resolución de brechas, las comparaciones de coberturas, deducibles y modalidades, la
simulación de escenarios, la evaluación ponderada y la OFERTA RECOMENDADA con sus razones.
""",
        "case_pack(kind=comparison) summary + proposal.ai_summary seeds",
    ),
    _spec(
        DocumentCategory.ISSUANCE_PROPOSAL,
        "07",
        CaseSection.BROKER_PROPOSAL,
        DocumentDirection.BROKER_TO_INSURER,
        IssuanceProposalExtraction,
        f"{_PKG}.issuance_proposal",
        """
Es la PROPUESTA DE EMISIÓN que el corredor envía a la compañía ganadora. Este documento es
la LÍNEA BASE DEL ESPEJO: la póliza que se emita después se compara campo por campo contra
él, así que extrae exactamente lo que dice, sin completar con otros documentos.
Incluye la cotización aceptada y su fecha, la cláusula de validación espejo literal, el
contratante y los beneficiarios, la vigencia con hora (convención 12:00), el límite y la
base de indemnización, las materias aseguradas y sus montos por partida, sublímites,
coberturas, deducibles, exclusiones y cláusulas particulares a emitir, las garantías, la
prima desglosada (afecta, exenta, neta, IVA = 0,19 x afecta, total), la comisión, el plan de
pago y sus cuotas, y los documentos que acompañan la propuesta.
""",
        "the winning proposal promoted + the mirror baseline for the issued policy",
    ),
    _spec(
        DocumentCategory.TECHNICAL_RECOMMENDATION,
        "07R",
        CaseSection.BROKER_PROPOSAL,
        DocumentDirection.BROKER_TO_INSURED,
        TechnicalRecommendationExtraction,
        f"{_PKG}.technical_recommendation",
        """
Es la RECOMENDACIÓN TÉCNICA del corredor al asegurado. Extrae la compañía propuesta, el
número de cotización, el monto asegurado, la vigencia, la prima bruta anual y la forma de
pago; los relatos de origen y de proceso de mercado en su redacción literal; el resumen del
programa, la resolución de brechas, la comparación costo-beneficio, el alza de prima en UF y
en porcentaje, las obligaciones del asegurado, la ruta de rebaja de deducible, las
alternativas descartadas y, muy importante, el texto de la ORDEN DE COLOCACIÓN y los bloques
de firma.
""",
        "case_pack(kind=comparison) insured-facing summary; signing advances to proposal_issued",
    ),
    _spec(
        DocumentCategory.POLICY,
        "08",
        CaseSection.POLICY_FILE,
        DocumentDirection.INSURER_TO_BROKER,
        PolicyExtraction,
        f"{_PKG}.policy",
        """
Es la PÓLIZA EMITIDA por la compañía. Extrae el número de póliza, la que renueva, la
compañía, el código POL/CAD de condiciones CMF y los códigos de cláusulas adicionales, el
contratante, el asegurado y el corredor impreso (SOLO TEXTO), la vigencia con hora
(convención 12:00), la fecha y el lugar de emisión, la referencia a la propuesta que le da
origen y la declaración de emisión en los términos de esa propuesta.
Extrae íntegras las tablas: ubicaciones, montos asegurados por partida, vehículos,
conductores, trabajadores, planes de accidentes personales, acreedores prendarios con su
ESTIPULACIÓN LITERAL, coberturas, sublímites, deducibles, exclusiones con sus contra-
excepciones, cláusulas particulares, garantías, desglose de prima (afecta, exenta, neta,
IVA = 0,19 x afecta, total), comisión, forma de pago y cuotas.
Si la póliza dice algo distinto de la propuesta, transcríbelo IGUAL: la diferencia es el
producto, no un error que corregir.
""",
        "policy + policy_location + coverage_item + warranty + collection_plan/installments",
        extraction_kind=ExtractionKind.POLICY,
    ),
    _spec(
        DocumentCategory.PAYMENT_PLAN,
        "09",
        CaseSection.COLLECTION,
        DocumentDirection.INSURER_TO_BROKER,
        PaymentPlanExtraction,
        f"{_PKG}.payment_plan",
        """
Es el PLAN DE PAGO de una póliza. Extrae el número de plan, la póliza, el responsable de
pago, la prima total, la forma de pago (cupones, PAC, PAT, transferencia, contado), el
número de cuotas y CADA CUOTA con su número, cupón, fecha de vencimiento, monto bruto UF,
prima neta y comisión. Extrae también la tasa de interés mensual, la cláusula de conversión
UF y la cláusula de no pago (artículo 528) en su redacción literal.
""",
        "collection_plan + collection_installment",
    ),
    _spec(
        DocumentCategory.COLLECTION_STATUS,
        "10",
        CaseSection.COLLECTION,
        DocumentDirection.INTERNAL,
        CollectionStatusExtraction,
        f"{_PKG}.collection_status",
        """
Es el ESTADO DE COBRANZA a una fecha. Extrae la fecha de corte, la prima bruta original, la
composición de prima con SIGNO (prima base más cada endoso), el total del período y cada
cuota con su estado (pendiente, pagada, pagada con atraso, morosa, abonada, anulada), fecha
de pago y días de atraso. Registra los eventos del artículo 528 con su fecha y hora, el
TÉRMINO DE COBERTURA por no pago (fecha y hora, días sin cobertura, costo de rehabilitación)
y el estado de cada garantía. Las alertas activas y la nota de gestión van literales.
""",
        "collection_plan status + installment statuses + warranty.status + case alerts",
    ),
    _spec(
        DocumentCategory.ENDORSEMENT_PROPOSAL,
        "07A",
        CaseSection.ENDORSEMENT,
        DocumentDirection.BROKER_TO_INSURER,
        EndorsementProposalExtraction,
        f"{_PKG}.endorsement_proposal",
        """
Es una PROPUESTA DE ENDOSO del corredor a la compañía. El verbo en mayúsculas del motivo
(INCLUYE, EXCLUYE, AUMENTA, DISMINUYE, MODIFICA) es la señal del tipo de endoso: cópialo
literal. Extrae la base contractual (número de cláusula), la vigencia solicitada con hora,
el estado anterior y lo que se agrega (ubicación, vehículo, trabajadores, asegurado
adicional), la tabla de efecto antes/después/delta, los días de endoso y los días no
corridos, y las primas adicionales CON SIGNO: afecta, exenta, neta, IVA = 0,19 x afecta,
bruta y delta de comisión. Un endoso administrativo puede tener todos los montos en cero:
eso es válido.
""",
        "endorsement with status=proposed + proposal_document_id",
    ),
    _spec(
        DocumentCategory.ENDORSEMENT,
        "08A",
        CaseSection.ENDORSEMENT,
        DocumentDirection.INSURER_TO_BROKER,
        EndorsementExtraction,
        f"{_PKG}.endorsement",
        """
Es un ENDOSO EMITIDO por la compañía. Extrae el folio del endoso tal cual (por ejemplo
"791237-4"), la póliza, el tipo, los endosos previos, la vigencia del endoso con hora, la
fecha de emisión y el motivo literal. Extrae los montos movidos, el cuadro posterior al
endoso, el movimiento de prima con signo (o `premium_impact_none` si el endoso no mueve
prima), los cambios de deducible, las modificaciones de garantías, el resultado de la
reinspección, las estipulaciones prendarias literales, el cambio de nómina y la declaración
de cese de cobertura si la hubiere.
""",
        "endorsement -> status=issued; applies deltas to policy + collection_plan on confirm",
    ),
    _spec(
        DocumentCategory.COMPLIANCE_NOTICE,
        "09A",
        CaseSection.ENDORSEMENT,
        DocumentDirection.BROKER_TO_INSURER,
        ComplianceNoticeExtraction,
        f"{_PKG}.compliance_notice",
        """
Es el AVISO DE CUMPLIMIENTO DE GARANTÍAS del corredor a la compañía. Extrae la base
contractual, la fecha del aviso y el plazo de reinspección, y CADA MEDIDA CUMPLIDA con su
código (R-n, G-n, M-n), la verificación y su costo real. Extrae el estado del plan completo,
el presupuesto total contra el gasto real, el sobrecosto en porcentaje y las peticiones
concretas a la compañía.
""",
        "warranty.status + completed_on + opens the expected endorsement(status=draft)",
    ),
    _spec(
        DocumentCategory.CLAIM_NOTICE,
        "11",
        CaseSection.CLAIM,
        DocumentDirection.BROKER_TO_INSURER,
        ClaimNoticeExtraction,
        f"{_PKG}.claim_notice",
        """
Es el DENUNCIO DE SINIESTRO. La HORA importa: extrae la ocurrencia, el aviso y la fecha de
conocimiento con fecha Y hora cuando el documento las declare, porque la franquicia horaria
y el plazo de denuncio se cuentan desde ahí. Extrae el relato literal, la línea de tiempo, la
causa probable, los daños declarados por partida con su monto, los subtotales (daño
material, remoción de escombros, honorarios) y el total estimado. Las COBERTURAS INVOCADAS se
copian con su numeración de póliza. Incluye trabajadores afectados, terceros, reclamantes,
vehículo y conductor, parte policial, medidas de mitigación, anexos y las peticiones del
corredor.
""",
        "claim + claim_item(kind=…) + tasks from broker_requests",
    ),
    _spec(
        DocumentCategory.CLAIM_PRELIMINARY_REPORT,
        "12",
        CaseSection.CLAIM,
        DocumentDirection.ADJUSTER_TO_PARTIES,
        ClaimPreliminaryReportExtraction,
        f"{_PKG}.claim_preliminary_report",
        """
Es el PRE-INFORME DE LIQUIDACIÓN del liquidador. Extrae el liquidador con su empresa y su
número de registro CMF, las fechas de designación, primera inspección e informe, la base
legal y las pericias ordenadas. Extrae la verificación de cobertura, el pronunciamiento
preliminar, el ANÁLISIS DE GARANTÍAS (qué código se cumplió y cuál no) y la cuantificación
de daños partida por partida: lo denunciado frente a lo determinado. Copia literalmente el
análisis de la franquicia horaria y la ventana de manifestación cuando aparezcan: en esta
cartera esa redacción decidió la cobertura.
""",
        "claim.coverage_ruling + claim_item.determined_uf + warranty.status",
    ),
    _spec(
        DocumentCategory.CLAIM_FINAL_REPORT,
        "13",
        CaseSection.CLAIM,
        DocumentDirection.ADJUSTER_TO_PARTIES,
        ClaimFinalReportExtraction,
        f"{_PKG}.claim_final_report",
        """
Es el INFORME FINAL DE LIQUIDACIÓN. Extrae el pronunciamiento de cobertura, las coberturas
amparadas, el pronunciamiento sobre exclusiones y garantías, el daño material final por
partida, la regla proporcional si se aplicó (factor, monto asegurado, valor a riesgo), la
determinación del perjuicio por paralización, los deducibles aplicados, la verificación de
límites y la liquidación final con el monto pagado y su fecha. Extrae también el costo total
para la compañía, el recupero, la siniestralidad de la cuenta, el efecto de la ingeniería de
riesgos y los contrafactuales (qué habría pasado con el programa anterior).
""",
        "claim closure columns + claim_item final figures + claim.loss_ratio_pct",
    ),
    _spec(
        DocumentCategory.BROKER_CLOSING_NOTE,
        "14",
        CaseSection.CLAIM,
        DocumentDirection.BROKER_TO_INSURED,
        BrokerClosingNoteExtraction,
        f"{_PKG}.broker_closing_note",
        """
Es la NOTA DE CIERRE del corredor al asegurado. Extrae el siniestro y la póliza, el monto
pagado y su fecha, los días entre el denuncio y el pago, los deducibles soportados y el daño
total. Extrae el historial de siniestros, los FACTORES DECISIVOS en las palabras del
documento, el contexto de renovación, la posición de negociación y las acciones que se piden
al asegurado con sus fechas: de ahí nace el expediente de renovación.
""",
        "claim closure summary + seeds case_file(kind=renewal) with dated actions",
    ),
]


def _build_registry() -> dict[DocumentCategory, CategorySpec]:
    registry: dict[DocumentCategory, CategorySpec] = {spec.category: spec for spec in _SPECS}
    # The deprecated `proposal` category resolves to the very same spec object,
    # which is what keeps upload.tsx and the existing proposal tests working.
    registry[DocumentCategory.PROPOSAL] = registry[DocumentCategory.INSURER_QUOTATION]
    return registry


CATEGORY_REGISTRY: Mapping[DocumentCategory, CategorySpec] = MappingProxyType(_build_registry())


# --- Corpus code hints -------------------------------------------------------
# The same numeric code means different things in different expedientes (La
# Favorita shifts by one because of its extra round), so the importer keys on
# (section, code) via SECTION_CODE_TO_CATEGORY and only falls back to this flat
# map when the section is unknown.

CODE_ALIASES: Mapping[str, DocumentCategory] = MappingProxyType(
    {
        "00A": DocumentCategory.PROSPECT_REQUEST,
        "00B": DocumentCategory.BUSINESS_QUESTIONNAIRE,
        "00C": DocumentCategory.INSURED_VALUES_SCHEDULE,
        "00D": DocumentCategory.LOSS_HISTORY,
        "00E": DocumentCategory.INSPECTION_REPORT,
        "01": DocumentCategory.TECHNICAL_BRIEF,
        "02": DocumentCategory.SUBMISSION_LETTER,
        "03": DocumentCategory.INSURER_QUOTATION,
        "03A": DocumentCategory.DECLINATION,
        "03B": DocumentCategory.DECLINATION,
        "03C": DocumentCategory.DECLINATION,
        "03D": DocumentCategory.CONDITIONAL_PRONOUNCEMENT,
        "03E": DocumentCategory.RISK_ENGINEERING_PLAN,
        "03F": DocumentCategory.RESUBMISSION_LETTER,
        "04": DocumentCategory.INSURER_QUOTATION,
        "05": DocumentCategory.INSURER_QUOTATION,
        "05B": DocumentCategory.INSURER_QUOTATION,
        "06": DocumentCategory.QUOTE_COMPARISON,
        "07": DocumentCategory.ISSUANCE_PROPOSAL,
        "07A": DocumentCategory.ENDORSEMENT_PROPOSAL,
        "07B": DocumentCategory.ENDORSEMENT_PROPOSAL,
        "07R": DocumentCategory.TECHNICAL_RECOMMENDATION,
        "08": DocumentCategory.POLICY,
        "08A": DocumentCategory.ENDORSEMENT,
        "08B": DocumentCategory.ENDORSEMENT,
        "09": DocumentCategory.PAYMENT_PLAN,
        "09A": DocumentCategory.COMPLIANCE_NOTICE,
        "09B": DocumentCategory.ENDORSEMENT,
        "09C": DocumentCategory.ENDORSEMENT_PROPOSAL,
        "09D": DocumentCategory.ENDORSEMENT,
        "10": DocumentCategory.COLLECTION_STATUS,
        "11": DocumentCategory.CLAIM_NOTICE,
        "12": DocumentCategory.CLAIM_PRELIMINARY_REPORT,
        "13": DocumentCategory.CLAIM_FINAL_REPORT,
        "14": DocumentCategory.BROKER_CLOSING_NOTE,
    }
)

# Backwards-compatible name used by the importer and by upload auto-detection.
CODE_TO_CATEGORY: Mapping[str, DocumentCategory] = CODE_ALIASES

# Codes whose meaning depends on the sub-expediente they were filed in.
SECTION_CODE_TO_CATEGORY: Mapping[tuple[CaseSection, str], DocumentCategory] = MappingProxyType(
    {
        (CaseSection.COLLECTION, "09"): DocumentCategory.PAYMENT_PLAN,
        (CaseSection.COLLECTION, "10"): DocumentCategory.COLLECTION_STATUS,
        (CaseSection.COLLECTION, "11"): DocumentCategory.COLLECTION_STATUS,
        (CaseSection.ENDORSEMENT, "09"): DocumentCategory.ENDORSEMENT,
        (CaseSection.CLAIM, "10"): DocumentCategory.CLAIM_NOTICE,
        (CaseSection.CLAIM, "11"): DocumentCategory.CLAIM_NOTICE,
        (CaseSection.CLAIM, "12"): DocumentCategory.CLAIM_PRELIMINARY_REPORT,
        (CaseSection.CLAIM, "13"): DocumentCategory.CLAIM_FINAL_REPORT,
        (CaseSection.CLAIM, "14"): DocumentCategory.BROKER_CLOSING_NOTE,
        (CaseSection.INSURER_QUOTES, "05B"): DocumentCategory.INSURER_QUOTATION,
        (CaseSection.BROKER_PROPOSAL, "07"): DocumentCategory.ISSUANCE_PROPOSAL,
        (CaseSection.ENDORSEMENT, "07"): DocumentCategory.ENDORSEMENT_PROPOSAL,
    }
)


# --- Lookups -----------------------------------------------------------------


def spec_for(category: "DocumentCategory | str | None") -> CategorySpec:
    """The spec for a category. Raises :class:`UnknownCategory` when there is none.

    Accepts the enum, its value, or ``None`` (which is always unknown) so the
    router can pass whatever the client sent without pre-validating it.
    """
    if category is None:
        raise UnknownCategory("No document category given")
    if not isinstance(category, DocumentCategory):
        try:
            category = DocumentCategory(str(category))
        except ValueError as exc:
            raise UnknownCategory(f"Unknown document category {category!r}") from exc
    try:
        return CATEGORY_REGISTRY[category]
    except KeyError as exc:
        raise UnknownCategory(
            f"No extraction schema is registered for category {category.value!r}"
        ) from exc


def has_spec(category: "DocumentCategory | str | None") -> bool:
    try:
        spec_for(category)
    except UnknownCategory:
        return False
    return True


def category_for_code(
    code: str | None, section: "CaseSection | None" = None
) -> DocumentCategory | None:
    """Resolve a corpus filename code (``"00A"``, ``"07R"``, ``"09B"``) to a category.

    The section wins when the same code means different things in different
    sub-expedientes; the flat map is the fallback.
    """
    if not code:
        return None
    token = code.strip().upper()
    if section is not None:
        found = SECTION_CODE_TO_CATEGORY.get((section, token))
        if found is not None:
            return found
    return CODE_ALIASES.get(token)


def _field_entries(spec: CategorySpec) -> list[dict[str, object]]:
    """Describe a schema's top-level fields so the UI can render a form generically."""
    entries: list[dict[str, object]] = []
    for name, field in spec.schema.model_fields.items():
        annotation = field.annotation
        entries.append(
            {
                "name": name,
                "type": getattr(annotation, "__name__", None) or str(annotation),
                "required": field.is_required(),
                "description": field.description,
            }
        )
    return entries


def registry_as_json() -> list[dict[str, object]]:
    """The whole registry as plain JSON — the payload of ``GET /ai/categories``."""
    seen: set[str] = set()
    items: list[dict[str, object]] = []
    for category, spec in CATEGORY_REGISTRY.items():
        items.append(
            {
                "category": category.value,
                "is_alias": category is not spec.category,
                "canonical_category": spec.category.value,
                "code": spec.code,
                "codes": list(spec.codes),
                "section": spec.section.value,
                "direction": spec.direction.value,
                "prompt_version": spec.prompt_version,
                "module": spec.module,
                "schema_name": spec.schema.__name__,
                "prefill_target": spec.prefill_target,
                "extraction_kind": spec.extraction_kind.value,
                "guidance": spec.guidance,
                "fields": _field_entries(spec),
            }
        )
        seen.add(category.value)
    items.sort(key=lambda item: (str(item["section"]), str(item["code"] or ""), str(item["category"])))
    return items
