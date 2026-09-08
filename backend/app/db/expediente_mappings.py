"""Translation layer for the ``EXPEDIENTES DEMO`` corpus -> the v2 case-file model.

``app.db.import_expedientes`` owns the session, the ordering and the S3 upload;
this module owns the *vocabulary*: how a folder name becomes a ``CaseSection``,
how a file name becomes a ``DocumentCategory``, and how the Spanish labels the
corpus prints for brokers, insureds and insurers resolve onto rows that already
exist in the database.

Everything here is PURE — dict lookups and string parsing, no database, no I/O.

Language rule (CLAUDE.md rule 1): every identifier is English. The Spanish
strings below are *data* — folder tokens and file-name keywords read out of the
corpus — not code.

---------------------------------------------------------------------------
The three lookups, applied IN ORDER (spec §9.2)
---------------------------------------------------------------------------

1. ``section_for(folder_parts)`` — keyed on the **leading ordinal** of the top
   folder and the **nested folder token** of the deepest one, never on the exact
   string. The corpus is not consistent: Viña Indómita writes
   ``2. Subexpediente de Comparación (de Compañías a Corredor)`` where everyone
   else writes ``2. Subexpediente de Cotizaciones (...)``, and La Favorita has
   two distinct ``3.`` folders (``a Compañía`` and ``a Asegurado``).

2. ``CATEGORY_BY_CODE`` — keyed on ``(section, code)``, **not on the code
   alone**. The same numeric code means different things in different
   expedientes: La Favorita shifts by one from ``03E``/``03F`` onwards because
   of the extra submission round, so its endorsements are ``09A..09D`` where
   everyone else's are ``07A/07B/08A/08B``.

3. ``CATEGORY_BY_KEYWORD`` — for the real-format carrier files that carry no
   code prefix at all (``Póliza Incendio - Multirriesgo.pdf``,
   ``Endoso de Inclusión - Póliza de Incendio.pdf``, ``Póliza Vehículo 1.pdf``,
   ``20588155 TRBF VIÑA INDOMITA 2024-2025.pdf``) and for the **claim section**,
   where the code genuinely collides across layouts: ``11`` is the pre-informe
   in six expedientes and the denuncio in La Favorita. There the file name is
   the only honest key, so ``CATEGORY_BY_CODE`` deliberately holds no claim
   entry and the keyword table resolves all four documents.

A file that no rule resolves raises ``UnknownCategory`` — nothing is ever
silently filed as ``other``.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.db.fixture_mappings import norm_token
from app.models.document import DocumentCategory
from app.models.enums import CaseSection

__all__ = [
    "UnknownSection",
    "UnknownCategory",
    "SECTION_BY_ORDINAL",
    "SECTION_BY_NESTED_TOKEN",
    "CATEGORY_BY_CODE",
    "CATEGORY_BY_KEYWORD",
    "SECTION_DEFAULT_CATEGORY",
    "section_for",
    "code_of",
    "classify",
    "BROKER_KEY_BY_NAME",
    "BrokerIdentity",
    "INSURED_BY_KEY",
    "InsuredIdentity",
    "INSURER_BY_LABEL",
    "InsurerIdentity",
    "LINE_BY_KEY",
    "LineIdentity",
    "loose_rut",
    "SLUG_MAX",
    "group_slug",
    "period_label",
]


class UnknownSection(ValueError):
    """A corpus folder no section rule recognises."""


class UnknownCategory(ValueError):
    """A corpus file no category rule recognises."""


# =============================================================================
# 1. SECTION_BY_FOLDER
# =============================================================================

# Leading ordinal of the FIRST folder under the expediente root.
SECTION_BY_ORDINAL: dict[str, CaseSection] = {
    "1": CaseSection.SUBMISSION,       # de Corredor a Compañías
    "2": CaseSection.INSURER_QUOTES,   # de Compañías a Corredor / de Comparación
    "3": CaseSection.BROKER_PROPOSAL,  # a Compañía AND a Asegurado
    "4": CaseSection.POLICY_FILE,      # interno Corredor
}

# Token of the DEEPEST folder, checked first: these are nested inside "4.".
SECTION_BY_NESTED_TOKEN: tuple[tuple[str, CaseSection], ...] = (
    ("cobranza", CaseSection.COLLECTION),
    ("endoso", CaseSection.ENDORSEMENT),
    ("siniestro", CaseSection.CLAIM),
)

_ORDINAL_RE = re.compile(r"^\s*(\d+)\s*\.")


def section_for(folder_parts: Sequence[str]) -> CaseSection:
    """Resolve the sub-expediente a file lives in.

    ``folder_parts`` are the directory names BELOW the expediente root, outermost
    first. An empty sequence is the root of the expediente itself.
    """
    parts = [p for p in folder_parts if p]
    if not parts:
        return CaseSection.ROOT_PROSPECT

    deepest = norm_token(parts[-1])
    for token, section in SECTION_BY_NESTED_TOKEN:
        if token in deepest:
            return section

    match = _ORDINAL_RE.match(parts[0])
    if match and match.group(1) in SECTION_BY_ORDINAL:
        return SECTION_BY_ORDINAL[match.group(1)]

    raise UnknownSection(f"No section rule for folder path {list(parts)!r}")


# =============================================================================
# 2. CATEGORY_BY_CODE — keyed on (section, code)
# =============================================================================

CATEGORY_BY_CODE: dict[tuple[CaseSection, str], DocumentCategory] = {
    # --- root of the expediente --------------------------------------------
    (CaseSection.ROOT_PROSPECT, "00A"): DocumentCategory.PROSPECT_REQUEST,
    # --- 1. Subexpediente de Cotización (broker -> insurers) ---------------
    (CaseSection.SUBMISSION, "00B"): DocumentCategory.BUSINESS_QUESTIONNAIRE,
    (CaseSection.SUBMISSION, "00C"): DocumentCategory.INSURED_VALUES_SCHEDULE,
    (CaseSection.SUBMISSION, "00D"): DocumentCategory.LOSS_HISTORY,
    (CaseSection.SUBMISSION, "00E"): DocumentCategory.INSPECTION_REPORT,
    (CaseSection.SUBMISSION, "01"): DocumentCategory.TECHNICAL_BRIEF,
    (CaseSection.SUBMISSION, "02"): DocumentCategory.SUBMISSION_LETTER,
    (CaseSection.SUBMISSION, "03E"): DocumentCategory.RISK_ENGINEERING_PLAN,
    (CaseSection.SUBMISSION, "03F"): DocumentCategory.RESUBMISSION_LETTER,
    # --- 2. Subexpediente de Cotizaciones (insurers -> broker) -------------
    (CaseSection.INSURER_QUOTES, "03"): DocumentCategory.INSURER_QUOTATION,
    (CaseSection.INSURER_QUOTES, "04"): DocumentCategory.INSURER_QUOTATION,
    (CaseSection.INSURER_QUOTES, "05"): DocumentCategory.INSURER_QUOTATION,
    (CaseSection.INSURER_QUOTES, "05B"): DocumentCategory.INSURER_QUOTATION,
    (CaseSection.INSURER_QUOTES, "03A"): DocumentCategory.DECLINATION,
    (CaseSection.INSURER_QUOTES, "03B"): DocumentCategory.DECLINATION,
    (CaseSection.INSURER_QUOTES, "03C"): DocumentCategory.DECLINATION,
    (CaseSection.INSURER_QUOTES, "03D"): DocumentCategory.CONDITIONAL_PRONOUNCEMENT,
    (CaseSection.INSURER_QUOTES, "06"): DocumentCategory.QUOTE_COMPARISON,
    # --- 3. Subexpediente de Propuesta -------------------------------------
    (CaseSection.BROKER_PROPOSAL, "07"): DocumentCategory.ISSUANCE_PROPOSAL,
    (CaseSection.BROKER_PROPOSAL, "07R"): DocumentCategory.TECHNICAL_RECOMMENDATION,
    # --- 4. Subexpediente de Póliza ----------------------------------------
    (CaseSection.POLICY_FILE, "08"): DocumentCategory.POLICY,
    # --- ... / Cobranza -----------------------------------------------------
    (CaseSection.COLLECTION, "09"): DocumentCategory.PAYMENT_PLAN,
    (CaseSection.COLLECTION, "10"): DocumentCategory.COLLECTION_STATUS,
    # --- ... / Endoso -------------------------------------------------------
    (CaseSection.ENDORSEMENT, "07A"): DocumentCategory.ENDORSEMENT_PROPOSAL,
    (CaseSection.ENDORSEMENT, "07B"): DocumentCategory.ENDORSEMENT_PROPOSAL,
    (CaseSection.ENDORSEMENT, "09C"): DocumentCategory.ENDORSEMENT_PROPOSAL,
    (CaseSection.ENDORSEMENT, "08A"): DocumentCategory.ENDORSEMENT,
    (CaseSection.ENDORSEMENT, "08B"): DocumentCategory.ENDORSEMENT,
    (CaseSection.ENDORSEMENT, "09B"): DocumentCategory.ENDORSEMENT,
    (CaseSection.ENDORSEMENT, "09D"): DocumentCategory.ENDORSEMENT,
    (CaseSection.ENDORSEMENT, "09A"): DocumentCategory.COMPLIANCE_NOTICE,
    # --- ... / Siniestro ----------------------------------------------------
    # DELIBERATELY EMPTY. La Favorita runs 11/12/13/14 where the other six run
    # 10/11/12, so the numeric code collides with itself across layouts. The
    # keyword table below is the only honest key for the claim section.
}


# =============================================================================
# 3. CATEGORY_BY_KEYWORD — accent-folded substring, first match wins
# =============================================================================

CATEGORY_BY_KEYWORD: dict[CaseSection, tuple[tuple[str, DocumentCategory], ...]] = {
    CaseSection.CLAIM: (
        ("nota de cierre", DocumentCategory.BROKER_CLOSING_NOTE),
        ("pre-informe", DocumentCategory.CLAIM_PRELIMINARY_REPORT),
        ("preinforme", DocumentCategory.CLAIM_PRELIMINARY_REPORT),
        ("informe final", DocumentCategory.CLAIM_FINAL_REPORT),
        ("denuncio", DocumentCategory.CLAIM_NOTICE),
    ),
    # "Endoso de Inclusión - Póliza de Incendio.pdf" lives beside the 08 in the
    # policy folder, so "endoso" must be tested BEFORE "poliza".
    CaseSection.POLICY_FILE: (
        ("endoso", DocumentCategory.ENDORSEMENT),
        ("plan de pago", DocumentCategory.PAYMENT_PLAN),
        ("poliza", DocumentCategory.POLICY),
        ("trbf", DocumentCategory.POLICY),
    ),
    CaseSection.ENDORSEMENT: (
        ("aviso de cumplimiento", DocumentCategory.COMPLIANCE_NOTICE),
        ("propuesta de endoso", DocumentCategory.ENDORSEMENT_PROPOSAL),
        ("endoso", DocumentCategory.ENDORSEMENT),
    ),
    CaseSection.COLLECTION: (
        ("plan de pago", DocumentCategory.PAYMENT_PLAN),
        ("estado de cobranza", DocumentCategory.COLLECTION_STATUS),
        ("cobranza", DocumentCategory.COLLECTION_STATUS),
    ),
    CaseSection.SUBMISSION: (
        ("plan de ingenieria", DocumentCategory.RISK_ENGINEERING_PLAN),
        ("re-remision", DocumentCategory.RESUBMISSION_LETTER),
        ("carta de remision", DocumentCategory.SUBMISSION_LETTER),
        ("bases tecnicas", DocumentCategory.TECHNICAL_BRIEF),
        ("informe de inspeccion", DocumentCategory.INSPECTION_REPORT),
        ("siniestralidad", DocumentCategory.LOSS_HISTORY),
    ),
    CaseSection.INSURER_QUOTES: (
        ("declinacion", DocumentCategory.DECLINATION),
        ("pronunciamiento condicionado", DocumentCategory.CONDITIONAL_PRONOUNCEMENT),
        ("comparativo", DocumentCategory.QUOTE_COMPARISON),
        ("cotizacion", DocumentCategory.INSURER_QUOTATION),
    ),
    CaseSection.BROKER_PROPOSAL: (
        ("recomendacion tecnica", DocumentCategory.TECHNICAL_RECOMMENDATION),
        ("propuesta de emision", DocumentCategory.ISSUANCE_PROPOSAL),
    ),
    CaseSection.ROOT_PROSPECT: (
        ("solicitud", DocumentCategory.PROSPECT_REQUEST),
    ),
}

# Last resort, only where the whole folder has one meaning.
SECTION_DEFAULT_CATEGORY: dict[CaseSection, DocumentCategory] = {
    CaseSection.ROOT_PROSPECT: DocumentCategory.PROSPECT_REQUEST,
    CaseSection.POLICY_FILE: DocumentCategory.POLICY,
}


# The corpus code prefix: two or three digits plus an optional capital letter,
# followed by whitespace. "20588155 TRBF ..." must NOT match (eight digits).
_CODE_RE = re.compile(r"^(\d{2,3}[A-Z]?)(?=\s)")


def code_of(file_name: str) -> str | None:
    """The corpus code hint at the head of a file name (``00A``, ``07R``, ``09B``)."""
    match = _CODE_RE.match(file_name.strip())
    return match.group(1) if match else None


def classify(section: CaseSection, file_name: str) -> tuple[DocumentCategory, str | None]:
    """``(category, document_code)`` for one corpus file. Raises rather than guess."""
    code = code_of(file_name)
    if code is not None:
        by_code = CATEGORY_BY_CODE.get((section, code))
        if by_code is not None:
            return by_code, code

    haystack = norm_token(file_name)
    for keyword, category in CATEGORY_BY_KEYWORD.get(section, ()):
        if keyword in haystack:
            return category, code

    default = SECTION_DEFAULT_CATEGORY.get(section)
    if default is not None:
        return default, code

    raise UnknownCategory(
        f"No category rule for {file_name!r} in section {section.value!r} "
        f"(code={code!r}) — add it to CATEGORY_BY_CODE or CATEGORY_BY_KEYWORD"
    )


# =============================================================================
# Group identity (spec v3 §2.1, §3.3)
# =============================================================================

# `account_group.slug` is String(80).
SLUG_MAX = 80


def group_slug(name: str) -> str:
    """The ``account_group`` get-or-create key: ``(broker_id, slug)``.

    Must produce the SAME string as ``import_fixtures`` and
    ``scripts/backfill_groups.py`` for the same name, or a backfilled database
    and a re-imported one would hold two different groups for one commercial
    account. The rule: fold accents (NFD, drop combining marks), lowercase,
    every run of non-alphanumerics becomes one ``-``, trim, cap at 80.

    ``"GRUPO VIÑA INDOMITA"`` -> ``"grupo-vina-indomita"``.
    """
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFD", name or "")
        if unicodedata.category(ch) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:SLUG_MAX].strip("-") or "grupo"


def period_label(start: date | None, end: date | None) -> str | None:
    """``2026-2027`` — a GROUPING LABEL only.

    The authoritative period is the pair of full dates on the folder
    (``case_file.period_start`` / ``period_end``): Coccolino runs Vehículos
    ago-ago and Incendio abr-abr, both under the label ``2026-2027``.
    """
    if start is None or end is None:
        return None
    return f"{start.year}-{end.year}"


# =============================================================================
# Identity resolution
# =============================================================================


def loose_rut(raw: str) -> str:
    """Normalize ``76.827.029-5`` -> ``76827029-5`` WITHOUT mod-11 validation.

    Used only for RUTs that are *document facts* and may be typographically
    wrong in the source (the two broker RUTs printed in the corpus both fail
    mod-11). Anything that becomes a real identity column goes through
    ``app.services.identifiers.validate_rut`` instead.
    """
    return raw.replace(".", "").replace(" ", "").strip().upper()


@dataclass(frozen=True)
class BrokerIdentity:
    """How to find an EXISTING broker tenant for a corpus expediente.

    **Never match by the corpus RUT.** The documents print Ossa Covarrubias as
    ``77.245.318-9`` and Fuenzalida SR as ``76.114.982-3``; the fixtures carry
    ``78.069.390-8`` and ``77.290.425-8``. Those are document facts about the
    letterhead, not tenant identity — matching on them would create a duplicate
    tenant. ``corpus_rut`` is therefore carried only into extraction/meta
    payloads. (Both corpus RUTs also fail mod-11, which is the giveaway.)
    """

    key: str
    # Accent-folded substring matched against the broker's legal + trade name.
    name_tokens: tuple[str, ...]
    # Verbatim letterhead, kept for extraction payloads.
    corpus_legal_name: str
    corpus_rut: str
    # Only used if no broker matches — we must never silently reuse the wrong one.
    fallback_legal_name: str
    fallback_trade_name: str


BROKER_KEY_BY_NAME: dict[str, BrokerIdentity] = {
    "ossa_covarrubias": BrokerIdentity(
        key="ossa_covarrubias",
        name_tokens=("ossa covarrubias",),
        corpus_legal_name="OSSA COVARRUBIAS CORREDORES DE SEGUROS LTDA.",
        corpus_rut="77.245.318-9",
        fallback_legal_name="Ossa Covarrubias Corredores de Seguros Ltda.",
        fallback_trade_name="Ossa Covarrubias",
    ),
    "fuenzalida_sr": BrokerIdentity(
        key="fuenzalida_sr",
        name_tokens=("fuenzalida sr", "fuenzalida"),
        corpus_legal_name="FUENZALIDA SR CORREDORES DE SEGUROS SPA",
        corpus_rut="76.114.982-3",
        fallback_legal_name="Fuenzalida SR Corredores de Seguros SpA",
        fallback_trade_name="Fuenzalida SR",
    ),
}


@dataclass(frozen=True)
class InsuredIdentity:
    """Canonical insured, matched and created BY RUT (mod-11 valid, all five)."""

    key: str
    rut: str
    legal_name: str
    trade_name: str | None
    # The broker's commercial account this insured is filed under. GRUPO VIÑA
    # INDOMITA is ONE account across TWO RUTs -> two `client` rows sharing it.
    account: str
    tax_activity: str
    contact_name: str | None
    email: str | None
    address: str | None
    commune: str | None
    region: str | None


INSURED_BY_KEY: dict[str, InsuredIdentity] = {
    "coccolino": InsuredIdentity(
        key="coccolino",
        rut="76.827.029-5",
        legal_name="Coccolino Pastelería SpA",
        trade_name="Coccolino",
        account="COCCOLINO",
        tax_activity="Elaboración y venta al detalle de productos de pastelería y cafetería",
        contact_name="Carolina Pizarro Guzmán",
        email="cpizarro@coccolino-ficticio.cl",
        address="Av. Blanca Estela 1927, Local 2",
        commune="Concón",
        region="Región de Valparaíso",
    ),
    "pacto_food": InsuredIdentity(
        key="pacto_food",
        rut="77.371.326-K",
        legal_name="Pacto Food SpA",
        trade_name="JO Pastelería",
        account="JO PASTELERÍA",
        tax_activity="Elaboración de productos de panadería y pastelería y servicio de cafetería",
        contact_name="Josefina Ortúzar Bulnes",
        email="jortuzar@jopasteleria-ficticio.cl",
        address="Av. Borgoño 25400",
        commune="Concón",
        region="Región de Valparaíso",
    ),
    "vina_indomita": InsuredIdentity(
        key="vina_indomita",
        rut="99.568.600-7",
        legal_name="Viña Indómita SpA",
        trade_name="Viña Indómita",
        account="GRUPO VIÑA INDOMITA",
        tax_activity="Elaboración de vinos — vinificación, guarda y embotellado",
        contact_name="Rodrigo Bustamante Ossa",
        email="rbustamante@vinaindomita-ficticio.cl",
        address="Lote B, Parcela 12B2",
        commune="Casablanca",
        region="Región de Valparaíso",
    ),
    "vina_santa_alicia": InsuredIdentity(
        key="vina_santa_alicia",
        rut="96.688.830-K",
        legal_name="Viña Santa Alicia SpA",
        trade_name="Viña Santa Alicia",
        account="GRUPO VIÑA INDOMITA",
        tax_activity="Elaboración de vinos — vinificación, guarda y embotellado",
        contact_name="Rodrigo Bustamante Ossa",
        email="rbustamante@vinasantaalicia-ficticio.cl",
        address="Camino Santa Rita 1249",
        commune="Pirque",
        region="Región Metropolitana",
    ),
    "la_favorita": InsuredIdentity(
        key="la_favorita",
        rut="76.789.935-1",
        legal_name="Comercializadora La Favorita Ltda",
        trade_name="La Favorita",
        account="LA FAVORITA",
        tax_activity=(
            "Recepción, despiece, envasado, congelado y despacho mayorista de carnes, "
            "cecinas, huevos y pescados"
        ),
        contact_name="Cristián Cabezas Varela",
        email="ccabezas@lafavorita-ficticio.cl",
        address="Lo Blanco 2011",
        commune="La Pintana",
        region="Región Metropolitana",
    ),
}


@dataclass(frozen=True)
class InsurerIdentity:
    """A corpus insurer label -> the identifiers we dedupe by.

    **Dedup is by normalized ``cmf_code`` then ``rut``, NEVER by name.** OCR of
    the same company yields ``HDI Seguros S.A.`` / ``HDI SEGUROS SA`` /
    ``H.D.I.``; the label below is only the importer's authoring shorthand. The
    RUTs are the official CMF general-insurer registry values, which is also
    what the catalog seeded by ``import_fixtures`` carries.
    """

    label: str
    rut: str
    legal_name: str


INSURER_BY_LABEL: dict[str, InsurerIdentity] = {
    "hdi": InsurerIdentity("hdi", "99.061.000-2", "HDI Seguros S.A."),
    "chubb": InsurerIdentity("chubb", "99.225.000-3", "Chubb Seguros Chile S.A."),
    "mapfre": InsurerIdentity(
        "mapfre", "96.508.210-7", "Mapfre Compañía de Seguros Generales de Chile S.A."
    ),
    "porvenir": InsurerIdentity("porvenir", "76.598.625-7", "Aseguradora Porvenir S.A."),
    "bci": InsurerIdentity("bci", "99.147.000-K", "BCI Seguros Generales S.A."),
    "consorcio": InsurerIdentity(
        "consorcio",
        "96.654.180-6",
        "Compañía de Seguros Generales Consorcio Nacional de Seguros S.A.",
    ),
    "southbridge": InsurerIdentity(
        "southbridge", "99.288.000-7", "Southbridge Compañía de Seguros Generales S.A."
    ),
    "unnio": InsurerIdentity("unnio", "76.173.258-7", "Unnio Seguros Generales S.A."),
}

# Prior brokers named on the real carrier policies. TEXT, never a `broker` row
# (spec §3.2 rule 6) — kept here so the importer can put them in `meta`.
PRIOR_BROKER_NAMES: tuple[str, ...] = (
    "MARSH S.A.",
    "MONDACA URBINA PATRICIO JORGE",
    "EGR CORREDORES DE SEGUROS",
)


@dataclass(frozen=True)
class LineIdentity:
    """An insurance line the corpus needs, matched against the seeded catalog."""

    key: str
    # Accent-folded substring matched against `insurance_line.name`.
    name_tokens: tuple[str, ...]
    # Used only when no seeded line matches (Accidentes Personales is not in the
    # 6-line fixture catalog); created GLOBAL, broker_id NULL, exactly like the
    # seeded ones.
    create_name: str
    requires_inspection: bool


LINE_BY_KEY: dict[str, LineIdentity] = {
    "property": LineIdentity(
        key="property",
        name_tokens=("incendio y sismo",),
        create_name="Incendio y Sismo (Property)",
        requires_inspection=True,
    ),
    "liability": LineIdentity(
        key="liability",
        name_tokens=("responsabilidad civil",),
        create_name="Responsabilidad Civil General",
        requires_inspection=False,
    ),
    "fleet": LineIdentity(
        key="fleet",
        name_tokens=("vehiculos motorizados",),
        create_name="Vehículos Motorizados (Flota)",
        requires_inspection=False,
    ),
    "personal_accident": LineIdentity(
        key="personal_accident",
        name_tokens=("accidentes personales",),
        create_name="Accidentes Personales Colectivos",
        requires_inspection=False,
    ),
}
