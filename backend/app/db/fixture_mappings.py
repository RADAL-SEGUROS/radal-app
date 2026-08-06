"""Translation layer: the team's Spanish fixture keys -> the v2 English model.

The source package (``radal-data-mvp``) was generated against the v1 Spanish
schema. Rather than asking for another round-trip we map it here, once, in one
place. Everything in this module is PURE: dict lookups, string parsing and small
value transforms — no database, no I/O. ``app.db.import_fixtures`` owns the
session, the ordering and the S3 upload; this module owns the vocabulary.

Mapping table of record: ``docs/v2-data-modeling-decisions.md`` §11.

Note on language: the *identifiers* produced here are English (rule 1). The
*values* that are genuinely free text written by a human — an inspector's
finding, a coverage description, an insurer's warranty clause — stay in Spanish,
because that is data, not code.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.models.client import ClientStatus
from app.models.document import DocumentCategory
from app.models.enums import EntityType, Priority
from app.models.inspection import InspectionRequestStatus, InspectionStatus
from app.models.insurance_line import CmfLineKind
from app.models.placement import PlacementStatus
from app.models.proposal import ProposalStatus

# =============================================================================
# Small value helpers
# =============================================================================


def strip_accents(text: str) -> str:
    """Fold accents so lookups survive 'observación' vs 'observacion'."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def norm_token(value: str | None) -> str:
    """Lowercase, de-accented, trimmed — the shape every vocabulary map is keyed by."""
    return strip_accents((value or "").strip().lower())


def to_decimal(value: Any) -> Decimal | None:
    """Exact numeric from JSON. Goes through ``str`` so floats never bleed in."""
    if value is None or value == "":
        return None
    return Decimal(str(value))


def to_date(value: Any) -> date | None:
    """Parse ``YYYY-MM-DD`` or a full ISO timestamp down to its date part."""
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def to_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp (with or without offset) and return it in UTC.

    The fixtures carry ``-04:00`` (Chile). Timestamps are stored UTC per the
    conventions, so we convert rather than truncate. A bare date becomes midnight
    UTC.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            parsed = to_date(text)
            if parsed is None:
                return None
            dt = datetime(parsed.year, parsed.month, parsed.day)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_REGION_PREFIX = re.compile(r"^regi[oó]n\b", re.IGNORECASE)


def split_address(full: str | None) -> tuple[str | None, str | None, str | None]:
    """Split ``"Calle 123, Depto 3, Vitacura, Región Metropolitana"``.

    Returns ``(street, commune, region)``. Only splits when the trailing segment
    actually names a region — otherwise the whole string is the street, because
    guessing a commune out of an arbitrary address would invent data.
    """
    if not full:
        return None, None, None
    parts = [p.strip() for p in full.split(",") if p.strip()]
    if len(parts) >= 3 and _REGION_PREFIX.match(strip_accents(parts[-1])):
        return ", ".join(parts[:-2]) or None, parts[-2], parts[-1]
    return full.strip() or None, None, None


# =============================================================================
# Vocabularies (Spanish source token -> English model value)
# =============================================================================

# --- RBAC: usuario.subrol -> role key in app.core.roles_config ---------------
# "suscripcion" (technical underwriting analyst) and "operaciones" (post-sale
# operations) both land on broker_technician: that role owns proposal
# standardization and policy servicing, which is exactly what both do.
ROLE_BY_SUBROLE: dict[str, str] = {
    "admin": "broker_admin",
    "corredor": "broker_executive",
    "inspector": "broker_inspector",
    "suscripcion": "broker_technician",
    "operaciones": "broker_technician",
}

PRIORITY_BY_URGENCY: dict[str, Priority] = {
    "baja": Priority.LOW,
    "media": Priority.NORMAL,
    "normal": Priority.NORMAL,
    "alta": Priority.HIGH,
    "urgente": Priority.URGENT,
}

CLIENT_STATUS: dict[str, ClientStatus] = {
    "prospecto": ClientStatus.PROSPECT,
    "onboarding": ClientStatus.ONBOARDING,
    "activo": ClientStatus.ACTIVE,
    "archivado": ClientStatus.ARCHIVED,
}

PLACEMENT_STATUS: dict[str, PlacementStatus] = {
    "borrador": PlacementStatus.DRAFT,
    "inspeccion": PlacementStatus.INSPECTION,
    "pre_suscripcion": PlacementStatus.PRE_UNDERWRITING,
    "cotizando": PlacementStatus.QUOTING,
    "negociando": PlacementStatus.NEGOTIATING,
    "adjudicado": PlacementStatus.AWARDED,
    "activo": PlacementStatus.ACTIVE,
    "cerrado": PlacementStatus.CLOSED,
}

PROPOSAL_STATUS: dict[str, ProposalStatus] = {
    "borrador": ProposalStatus.DRAFT,
    "enviada": ProposalStatus.SUBMITTED,
    "recibida": ProposalStatus.SUBMITTED,
    "aceptada": ProposalStatus.ACCEPTED,
    "adjudicada": ProposalStatus.ACCEPTED,
    "rechazada": ProposalStatus.REJECTED,
    "retirada": ProposalStatus.WITHDRAWN,
    "vencida": ProposalStatus.EXPIRED,
}

INSPECTION_REQUEST_STATUS: dict[str, InspectionRequestStatus] = {
    "pendiente": InspectionRequestStatus.PENDING,
    "agendada": InspectionRequestStatus.SCHEDULED,
    "en_proceso": InspectionRequestStatus.IN_PROGRESS,
    "completada": InspectionRequestStatus.COMPLETED,
    "cancelada": InspectionRequestStatus.CANCELLED,
}

INSPECTION_STATUS: dict[str, InspectionStatus] = {
    "borrador": InspectionStatus.DRAFT,
    "en_revision": InspectionStatus.IN_REVIEW,
    "emitida": InspectionStatus.ISSUED,
    "archivada": InspectionStatus.ARCHIVED,
}

CMF_LINE_KIND: dict[str, CmfLineKind] = {
    "ramo": CmfLineKind.LINE,
    "subdivision": CmfLineKind.SUBLINE,
}

# --- Polymorphic entity names (documento.entidad, actividad.entidad, ...) ----
ENTITY_TYPE: dict[str, EntityType] = {
    "corredora": EntityType.BROKER,
    "usuario": EntityType.USER,
    "cliente": EntityType.CLIENT,
    "asegurado": EntityType.INSURED,
    "aseguradora_entidad": EntityType.INSURER,
    "aseguradora": EntityType.INSURER,
    "activo": EntityType.ASSET,
    "ramo": EntityType.INSURANCE_LINE,
    "proceso_ramo": EntityType.PLACEMENT,
    "cotizacion": EntityType.QUOTE_REQUEST,
    "oferta": EntityType.PROPOSAL,
    "solicitud_inspeccion": EntityType.INSPECTION_REQUEST,
    "inspeccion": EntityType.INSPECTION,
    "poliza": EntityType.POLICY,
    "siniestro": EntityType.CLAIM,
}

# --- documento.categoria -> DocumentCategory (the 13 observed + safety valve)
DOCUMENT_CATEGORY: dict[str, DocumentCategory] = {
    "certificado_cmf": DocumentCategory.CMF_CERTIFICATE,
    "nombramiento": DocumentCategory.APPOINTMENT,
    "logo": DocumentCategory.LOGO,
    "ficha_bien": DocumentCategory.ASSET_SHEET,
    "foto_bien": DocumentCategory.ASSET_PHOTO,
    "valorizacion": DocumentCategory.VALUATION,
    "detalle_montos_asegurados": DocumentCategory.INSURED_AMOUNTS,
    "evidencia": DocumentCategory.EVIDENCE,
    "informe_inspeccion": DocumentCategory.INSPECTION_REPORT,
    "bases_tecnicas": DocumentCategory.TECHNICAL_BRIEF,
    "siniestralidad_sisgen": DocumentCategory.CLAIMS_HISTORY,
    "propuesta": DocumentCategory.PROPOSAL,
    "poder_representante": DocumentCategory.POWER_OF_ATTORNEY,
}

# --- actividad.tipo -> the verb half of "{entity_type}.{verb}" --------------
ACTIVITY_VERB: dict[str, str] = {
    "alta_prospecto": "created",
    "registro_activo": "created",
    "perfil_creado": "created",
    "confirmacion_asegurado": "confirmed",
    "cotizacion_enviada": "sent",
    "ofertas_completas": "proposals_complete",
    "informe_emitido": "issued",
    "adjudicacion": "awarded",
}

# --- activo.tipo_activo -> the open asset_type vocabulary -------------------
ASSET_TYPE: dict[str, str] = {
    "planta_industrial": "industrial_plant",
    "centro_distribucion": "distribution_centre",
    "clinica": "clinic",
    "bodega": "warehouse",
    "oficina": "office",
    "flota": "fleet",
}


# =============================================================================
# Asset attributes — HYBRID promotion (docs §6)
# =============================================================================

# The 10 keys common to every asset in the corpus. These become COLUMNS.
PROMOTED_ASSET_ATTRS: dict[str, str] = {
    "superficie_construida_m2": "built_area_m2",
    "superficie_terreno_m2": "land_area_m2",
    "ano_construccion": "construction_year",
    "estructura": "structure",
    "pisos": "floors",
    "actividad": "activity",
    "proteccion_incendio": "fire_protection",
    "energia": "power_supply",
    "distancia_bomberos_km": "fire_station_distance_km",
    "sismicidad": "seismic_zone",
}

# The type-specific tail that stays in `asset.attributes` JSON.
TAIL_ASSET_ATTRS: dict[str, str] = {
    "refrigeracion": "refrigeration",
    "altura_rack_m": "rack_height_m",
    "muelles": "loading_docks",
    "camas": "beds",
    "pabellones": "operating_rooms",
    "gases_medicinales": "medical_gases",
}

# Sub-keys of the two structured promoted columns and of the nested tail objects.
_NESTED_ATTR_KEYS: dict[str, dict[str, str]] = {
    "proteccion_incendio": {
        "red_humeda": "wet_riser",
        "red_seca": "dry_riser",
        "sprinklers": "sprinklers",
        "extintores": "extinguishers",
        "detectores_humo": "smoke_detectors",
        "brigada": "fire_brigade",
    },
    "energia": {
        "empalme_kva": "service_kva",
        "grupo_electrogeno_kva": "generator_kva",
        "tableros": "switchboards",
        "termografia": "thermography",
        "transferencia": "transfer_switch",
    },
    "refrigeracion": {
        "refrigerante": "refrigerant",
        "sala_maquinas_segregada": "segregated_machine_room",
    },
    "gases_medicinales": {
        "oxigeno": "oxygen",
        "vacio_aire": "vacuum_and_air",
    },
}


def _rename_nested(source_key: str, value: Any) -> Any:
    """Rename the sub-keys of a structured attribute; values pass through."""
    mapping = _NESTED_ATTR_KEYS.get(source_key)
    if not mapping or not isinstance(value, dict):
        return value
    return {mapping.get(k, k): v for k, v in value.items()}


class AssetAttributes:
    """The result of splitting ``activo.atributos`` into columns + JSON tail."""

    __slots__ = ("columns", "tail", "unmapped")

    def __init__(self, columns: dict[str, Any], tail: dict[str, Any], unmapped: list[str]):
        self.columns = columns
        self.tail = tail
        self.unmapped = unmapped


def split_asset_attributes(attrs: dict[str, Any] | None) -> AssetAttributes:
    """Promote the 10 shared underwriting attributes; keep the rest as JSON.

    ``unmapped`` lists source keys that had no English name yet — they are kept
    (verbatim key) in the tail rather than dropped, and the importer logs them so
    a new asset type never silently loses data.
    """
    columns: dict[str, Any] = {}
    tail: dict[str, Any] = {}
    unmapped: list[str] = []
    for key, value in (attrs or {}).items():
        if key in PROMOTED_ASSET_ATTRS:
            columns[PROMOTED_ASSET_ATTRS[key]] = _rename_nested(key, value)
        elif key in TAIL_ASSET_ATTRS:
            tail[TAIL_ASSET_ATTRS[key]] = _rename_nested(key, value)
        else:
            unmapped.append(key)
            tail[key] = value
    return AssetAttributes(columns, tail, unmapped)


# =============================================================================
# Inspection checklist — JSON, structure keys normalized to English (docs §7)
# =============================================================================

CHECKLIST_RESULT: dict[str, str] = {
    "ok": "ok",
    "observacion": "observation",
    "critico": "critical",
    "no_aplica": "not_applicable",
    "na": "not_applicable",
}


def normalize_checklist(checklist: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rewrite the checklist's *structure* keys to English, keeping the prose.

    The section names and item texts are what the inspector actually wrote —
    Spanish data that must survive verbatim. Only the shape
    (``secciones``/``items``/``resultado``/``resumen``) is code-facing, so only
    that is translated.
    """
    if not checklist:
        return None
    sections = []
    for section in checklist.get("secciones") or []:
        items = []
        for item in section.get("items") or []:
            raw_result = norm_token(item.get("resultado"))
            entry: dict[str, Any] = {
                "item": item.get("item"),
                "result": CHECKLIST_RESULT.get(raw_result, raw_result or None),
            }
            if item.get("nota"):
                entry["note"] = item["nota"]
            items.append(entry)
        sections.append({"name": section.get("nombre"), "items": items})

    summary_src = checklist.get("resumen") or {}
    out: dict[str, Any] = {"version": checklist.get("version"), "sections": sections}
    if summary_src:
        out["summary"] = {
            "ok": summary_src.get("items_ok"),
            "observations": summary_src.get("items_observacion"),
            "critical": summary_src.get("items_criticos"),
        }
    return out


BOUNDARY_ORIENTATION: dict[str, str] = {
    "norte": "north",
    "sur": "south",
    "este": "east",
    "oeste": "west",
    "noreste": "northeast",
    "noroeste": "northwest",
    "sureste": "southeast",
    "suroeste": "southwest",
    "frente": "front",
    "fondo": "rear",
}


def is_aggravating(agravante: str | None) -> bool:
    """True unless the report explicitly says there is no aggravating factor."""
    token = norm_token(agravante)
    return bool(token) and not token.startswith("sin agravante")


# =============================================================================
# Deductibles — prose -> structured JSON keyed by peril (docs §3)
# =============================================================================

PERIL_KEY: dict[str, str] = {
    "incendio": "fire",
    "sismo": "earthquake",
    "terremoto": "earthquake",
    "otros": "other",
    "agua": "water",
    "robo": "theft",
    "rc": "liability",
    "equipo_electronico": "electronic_equipment",
    "lucro_cesante": "business_interruption",
}

# "5%" / "1,5%" / "1.5 %"
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
# "mínimo UF 25" / "UF 15" / "UF 1.200"
_UF_RE = re.compile(r"uf\s*([\d.,]+)", re.IGNORECASE)
_NIL_RE = re.compile(r"sin deducible", re.IGNORECASE)


def _num(text: str) -> float | None:
    """Parse a Chilean-formatted number: '.' groups thousands, ',' is decimal."""
    cleaned = text.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _basis_of(clause: str) -> str | None:
    """Which quantity the percentage applies to — the whole point of §3."""
    folded = strip_accents(clause.lower())
    if "monto asegurado" in folded or "suma asegurada" in folded:
        return "insured_amount"
    if "perdida" in folded or "siniestro" in folded or "dano" in folded:
        return "loss"
    return None


def parse_deductible(text: str | None) -> dict[str, Any] | None:
    """Parse one peril's deductible prose into the structured shape.

    Target shape (docs §3)::

        {"basis": "loss", "pct": 5.0, "min_uf": 25.0}

    Reality is prose written by nine different underwriters, so the result also
    carries provenance:

    * ``text`` — the original wording, always kept. Nothing is lost.
    * ``confidence`` — ``"high"`` when a single clause parsed cleanly,
      ``"partial"`` when the wording is compound (e.g. *"Sin deducible en
      incendio; 10% de la pérdida mínimo UF 30 en adicionales"*) and a human or
      the AI extractor should confirm it.
    * ``nil_deductible`` — set when the clause waives the deductible outright.

    This is deliberately conservative: it never guesses a basis it cannot see in
    the words, and a percentage with no recognisable basis is flagged rather than
    assumed to be a percentage of the loss.
    """
    if not text or not text.strip():
        return None

    original = text.strip()
    clauses = [c.strip() for c in re.split(r"[;.]\s+|;", original) if c.strip()]
    nil = bool(_NIL_RE.search(original))

    # Prefer the clause that actually carries a percentage or a UF minimum;
    # a bare "Sin deducible" clause carries no numbers to extract.
    scored = [c for c in clauses if _PCT_RE.search(c) or _UF_RE.search(c)] or clauses
    clause = scored[0]

    pct_match = _PCT_RE.search(clause)
    pct = _num(pct_match.group(1)) if pct_match else None

    uf_match = _UF_RE.search(clause)
    min_uf = _num(uf_match.group(1)) if uf_match else None

    # The basis is what the PERCENTAGE applies to, so it is only meaningful when
    # there is one. Without a percentage a bare "UF 15 toda otra pérdida" is a
    # flat amount — the word "pérdida" there describes the trigger, not a base.
    if pct is not None:
        basis = _basis_of(clause) or "unknown"
    elif min_uf is not None:
        basis = "fixed"
    else:
        basis = "unknown"

    result: dict[str, Any] = {"basis": basis, "text": original}
    if pct is not None:
        result["pct"] = pct
    if min_uf is not None:
        result["min_uf"] = min_uf
    if nil:
        result["nil_deductible"] = True

    clean = (
        basis in ("loss", "insured_amount")
        and pct is not None
        and min_uf is not None
        and not nil
        and len(scored) == 1
    ) or (basis == "fixed" and not nil and len(scored) == 1)
    result["confidence"] = "high" if clean else "partial"
    return result


def parse_deductibles(block: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Map ``oferta.deducible`` to ``{peril_en: {...}}``.

    Returns the JSON payload plus the list of perils whose prose only parsed
    partially, so the importer can report exactly what needs review.
    """
    if not block:
        return None, []
    out: dict[str, Any] = {}
    needs_review: list[str] = []
    for peril, prose in block.items():
        key = PERIL_KEY.get(norm_token(peril), norm_token(peril))
        parsed = parse_deductible(prose if isinstance(prose, str) else None)
        if parsed is None:
            continue
        out[key] = parsed
        if parsed.get("confidence") != "high":
            needs_review.append(key)
    return (out or None), needs_review


__all__ = [
    "AssetAttributes",
    "ACTIVITY_VERB",
    "ASSET_TYPE",
    "BOUNDARY_ORIENTATION",
    "CHECKLIST_RESULT",
    "CLIENT_STATUS",
    "CMF_LINE_KIND",
    "DOCUMENT_CATEGORY",
    "ENTITY_TYPE",
    "INSPECTION_REQUEST_STATUS",
    "INSPECTION_STATUS",
    "PERIL_KEY",
    "PLACEMENT_STATUS",
    "PRIORITY_BY_URGENCY",
    "PROMOTED_ASSET_ATTRS",
    "PROPOSAL_STATUS",
    "ROLE_BY_SUBROLE",
    "TAIL_ASSET_ATTRS",
    "is_aggravating",
    "norm_token",
    "normalize_checklist",
    "parse_deductible",
    "parse_deductibles",
    "split_address",
    "split_asset_attributes",
    "strip_accents",
    "to_date",
    "to_datetime",
    "to_decimal",
]
