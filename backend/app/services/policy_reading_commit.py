"""Fast reading — the DB side: parties from the core read, and the technical
read's view / confirm / discard (docs/v12/11-fast-reading.md).

suggest -> human confirm -> commit (rule 6): the technical read is stored on its
own ``extraction`` row; only :func:`confirm_technical`, driven by the broker's
confirm request, writes to the policy — and it NEVER overwrites a value the
policy already holds (a confirmed core value, a manual correction): the
difference is reported as a warning instead.

Flushes, then lets :func:`app.services.ai.record_confirmation` commit once, like
every other confirm path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import Extraction, ExtractionStatus
from app.models.case_file import CaseFile
from app.models.document import Document
from app.models.enums import EntityType, PartyRole, WarrantySource
from app.models.policy import Policy
from app.models.policy_party import PolicyParty
from app.models.user import User
from app.schemas.extraction.policy_tiers import LIMIT_KEYS, TECHNICAL_SECTIONS
from app.schemas.policy_reading import TechnicalCounts, TechnicalRead, TechnicalSections
from app.services import policy_reading

__all__ = [
    "add_beneficiary_parties",
    "latest_technical",
    "technical_read_view",
    "confirm_technical",
    "discard_technical",
    "TechnicalConflict",
    "SECTION_LABEL_ES",
]

#: Spanish section names for warnings and the bitácora line.
SECTION_LABEL_ES = {
    "items": "materia asegurada",
    "coverages": "coberturas",
    "deductibles": "deducibles",
    "warranties": "garantías",
    "exclusions": "exclusiones",
    "particular_clauses": "cláusulas particulares",
    "limits": "límites y tasa",
}

#: ``sections.limits`` key -> (policy column, Spanish label, max length or None).
_LIMIT_COLUMNS: dict[str, tuple[str, str, int | None]] = {
    "indemnity_limit": ("indemnity_limit", "límite de indemnización", None),
    "aggregate_limit_uf": ("aggregate_limit_uf", "límite agregado (UF)", None),
    "valuation_basis": ("insured_amount_semantics", "base de indemnización", 255),
    "average_rate_permille": ("average_rate_permille", "tasa promedio (‰)", None),
    "cmf_policy_code": ("cmf_policy_code", "código CMF de la póliza", 32),
}
_DECIMAL_LIMITS = {"aggregate_limit_uf", "average_rate_permille"}


class TechnicalConflict(Exception):
    """The read cannot be confirmed/discarded in its current state -> 409."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        # The structured shape every fast-reading error uses: {code, message}.
        self.detail = {"code": code, "message": message}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# Core read -> parties
# =============================================================================


def _rut(text: Any) -> str | None:
    from app.services.identifiers import InvalidRut, validate_rut

    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return validate_rut(text)
    except InvalidRut:
        return None


def add_beneficiary_parties(
    db: Session,
    policy: Policy,
    beneficiaries: Iterable[Any] | None,
    *,
    additional_insureds: Iterable[Any] | None = None,
) -> list[PolicyParty]:
    """Add the beneficiaries (and any further insureds) the core read found.

    Only when the policy carries NO party of that role yet — the broker's own
    ``PUT /parties`` always wins, and a re-upload never duplicates. A RUT that
    is one of the broker's clients links its ``client_id``. The amount stays in
    ``policy.payload`` (``policy_party`` has no amount column).
    """
    existing_roles = set(
        db.scalars(
            select(PolicyParty.role).where(
                PolicyParty.broker_id == policy.broker_id, PolicyParty.policy_id == policy.id
            )
        ).all()
    )

    def _client_id(rut: str | None) -> int | None:
        from app.services.policy_registry import _client_by_rut

        match = _client_by_rut(db, policy.broker_id, rut)
        return match.id if match is not None else None

    created: list[PolicyParty] = []

    def _add(role: PartyRole, row: dict[str, Any], name_key: str, kind: str | None = None) -> None:
        name = str(row.get(name_key) or row.get("legal_name") or row.get("name") or "").strip()
        if not name:
            return
        rut = _rut(row.get("rut"))
        party = PolicyParty(
            broker_id=policy.broker_id,
            policy_id=policy.id,
            role=role,
            client_id=_client_id(rut),
            name=name[:255],
            rut=rut,
            beneficiary_kind=(kind[:32] if kind else None),
            is_primary=False,
            notify_collections=False,
        )
        db.add(party)
        created.append(party)

    if PartyRole.BENEFICIARY not in existing_roles:
        for row in beneficiaries or []:
            if isinstance(row, dict):
                _add(PartyRole.BENEFICIARY, row, "name", row.get("type") or "otro")
    if PartyRole.ADDITIONAL_INSURED not in existing_roles:
        for row in list(additional_insureds or [])[1:]:  # the first IS the insured
            if isinstance(row, dict):
                _add(PartyRole.ADDITIONAL_INSURED, row, "legal_name")
    if created:
        db.flush()
    return created


# =============================================================================
# The view
# =============================================================================


def latest_technical(db: Session, *, broker_id: int, document_id: int) -> Extraction | None:
    """The newest technical read of the document that was not discarded."""
    for row in policy_reading.technical_rows(db, broker_id=broker_id, document_id=document_id):
        if not policy_reading.is_dismissed(row):
            return row
    return None


def _sections_of(extraction: Extraction) -> dict[str, Any]:
    parsed = extraction.parsed if isinstance(extraction.parsed, dict) else {}
    out: dict[str, Any] = {}
    for section in TECHNICAL_SECTIONS:
        value = parsed.get(section)
        if section == "limits":
            out[section] = dict(value) if isinstance(value, dict) else {}
        else:
            out[section] = [row for row in (value or []) if isinstance(row, dict)]
    return out


def technical_read_view(
    extraction: Extraction | None, *, warnings: list[str] | None = None
) -> TechnicalRead:
    """The ``TechnicalRead`` of one row (``status="none"`` without one)."""
    if extraction is None:
        return TechnicalRead(status="none")
    raw = extraction.raw_output if isinstance(extraction.raw_output, dict) else {}
    base = {
        "extraction_id": extraction.id,
        "started_at": extraction.started_at,
        "finished_at": extraction.finished_at,
        "warnings": list(warnings or []),
    }
    if policy_reading.is_dismissed(extraction):
        return TechnicalRead(status="none", **base)
    if extraction.status in (ExtractionStatus.RUNNING, ExtractionStatus.PENDING):
        if policy_reading.is_stale(extraction):
            return TechnicalRead(
                status="failed", error_code="ai_timeout", message=policy_reading.TIMEOUT_ES, **base
            )
        return TechnicalRead(status="running", **base)
    if extraction.status == ExtractionStatus.FAILED:
        code = raw.get("error_code")
        if code not in ("ai_timeout", "ai_unavailable", "ai_parse"):
            code = "ai_unavailable"
        message = raw.get("message") or policy_reading._ERROR_COPY[code]
        return TechnicalRead(status="failed", error_code=code, message=message, **base)

    sections = _sections_of(extraction)
    section_errors = {
        str(k): str(v) for k, v in (raw.get("section_errors") or {}).items() if v
    }
    message = None
    if section_errors:
        labels = ", ".join(SECTION_LABEL_ES.get(s, s) for s in section_errors)
        message = f"No se pudo leer: {labels}. El resto de los datos está listo para revisar."
    confirmed = policy_reading.is_confirmed(extraction)
    return TechnicalRead(
        status="confirmed" if confirmed else "ready",
        message=message,
        sections=TechnicalSections(**sections),
        counts=TechnicalCounts(
            **{s: len(sections[s]) for s in TECHNICAL_SECTIONS if s != "limits"}
        ),
        section_errors=section_errors,
        confirmed_sections=list(raw.get("confirmed_sections") or []) if confirmed else [],
        **base,
    )


# =============================================================================
# Confirm / discard
# =============================================================================


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _same(current: Any, incoming: Any) -> bool:
    left, right = _decimal(current), _decimal(incoming)
    if left is not None and right is not None:
        return abs(left - right) <= Decimal("0.0001")
    return " ".join(str(current).split()).lower() == " ".join(str(incoming).split()).lower()


def _wanted_sections(sections: list[str] | None) -> list[str]:
    if not sections:
        return list(TECHNICAL_SECTIONS)
    wanted = [s for s in TECHNICAL_SECTIONS if s in set(sections)]
    # The limits come from the same group as the risk: they travel with it.
    if "limits" not in wanted and ({"items", "coverages"} & set(wanted)):
        wanted.append("limits")
    return wanted


def confirm_technical(
    db: Session,
    *,
    policy: Policy,
    extraction: Extraction,
    sections: list[str] | None,
    user: User | None,
) -> TechnicalRead:
    """Commit the confirmed sections to the policy (never overwriting a value).

    Writes ``deductibles``, the limit columns and the warranties (upsert by
    code), and merges every confirmed section into ``policy.payload`` (so the
    payload panel shows them). Sections whose group failed are skipped. Records
    the bitácora event and stamps the confirmation (one commit).
    """
    from app.api.routers.clients import record_activity
    from app.services import ai as ai_service
    from app.services import extraction_commit

    if policy_reading.is_dismissed(extraction):
        raise TechnicalConflict("technical_read_dismissed", "Esta lectura fue descartada.")
    if policy_reading.is_confirmed(extraction):
        raise TechnicalConflict("technical_read_confirmed", "Esta lectura ya fue confirmada.")
    if extraction.status != ExtractionStatus.SUCCEEDED:
        raise TechnicalConflict(
            "technical_read_not_ready", "La lectura técnica no está lista para confirmar."
        )

    raw = dict(extraction.raw_output or {})
    failed_sections = set((raw.get("section_errors") or {}).keys())
    data = _sections_of(extraction)
    warnings: list[str] = []
    confirmed: list[str] = []

    for section in _wanted_sections(sections):
        if section in failed_sections:
            warnings.append(
                f"La sección {SECTION_LABEL_ES[section]} no se pudo leer y no se confirmó."
            )
            continue
        confirmed.append(section)

    payload = dict(policy.payload or {})

    # --- limits -> the typed columns, only when empty ------------------------
    if "limits" in confirmed:
        limits = data["limits"]
        for key in LIMIT_KEYS:
            incoming = limits.get(key)
            if _empty(incoming):
                continue
            column, label, max_len = _LIMIT_COLUMNS[key]
            value: Any = _decimal(incoming) if key in _DECIMAL_LIMITS else str(incoming).strip()
            if value is None:
                continue
            if isinstance(value, str) and max_len:
                value = value[:max_len]
            current = getattr(policy, column)
            if _empty(current):
                setattr(policy, column, value)
            elif not _same(current, value):
                warnings.append(
                    f"La póliza ya tiene {label} ({current}); la lectura dice {value}. "
                    "No se reemplazó: corrígelo en la ficha si corresponde."
                )
            if _empty(payload.get(key)):
                payload[key] = incoming

    # --- deductibles -> the JSON column, only when empty --------------------
    if "deductibles" in confirmed and data["deductibles"]:
        incoming = {
            (str(row.get("peril") or "").strip() or f"item_{index}"): {
                k: v for k, v in row.items() if not _empty(v)
            }
            for index, row in enumerate(data["deductibles"])
        }
        if _empty(policy.deductibles):
            policy.deductibles = incoming
        elif policy.deductibles != incoming:
            warnings.append(
                "La póliza ya tiene deducibles registrados; los leídos no los reemplazaron "
                "(quedan en los datos técnicos)."
            )

    # --- warranties -> upsert by code ---------------------------------------
    if "warranties" in confirmed and data["warranties"]:
        document = db.get(Document, extraction.document_id)
        case = db.get(CaseFile, policy.case_file_id) if policy.case_file_id else None
        if case is not None and case.broker_id != policy.broker_id:
            case = None
        ctx = extraction_commit._Ctx(
            db=db,
            broker_id=policy.broker_id,
            user=user,
            extraction=extraction,
            payload=None,
            document=document,
            case_file=case,
        )
        from app.schemas.extraction.common import parse_flexible_date, parse_int

        for index, row in enumerate(data["warranties"]):
            code = (str(row.get("code") or "").strip() or None)
            if not (code or row.get("title") or row.get("requirement")):
                continue
            extraction_commit._upsert_warranty(
                ctx,
                policy,
                code=code[:16] if code else None,
                title=row.get("title"),
                requirement=row.get("requirement"),
                deadline_days=parse_int(row.get("deadline_days")),
                due_date=parse_flexible_date(row.get("due_date")),
                source=WarrantySource.UNDERWRITING_WARRANTY,
                sort_order=index,
            )
        db.flush()

    # --- every confirmed list section -> policy.payload ---------------------
    for section in confirmed:
        if section == "limits":
            continue
        rows = data[section]
        if not rows:
            continue
        if _empty(payload.get(section)):
            payload[section] = rows
        elif payload.get(section) != rows:
            warnings.append(
                f"Los datos de la póliza ya tenían {SECTION_LABEL_ES[section]}; se conservaron."
            )
    payload["technical_read"] = {
        "extraction_id": extraction.id,
        "confirmed_at": _now().isoformat(),
        "sections": confirmed,
    }
    policy.payload = payload  # a new dict: JSON columns track reassignment

    counts = ", ".join(
        f"{len(data[s])} {SECTION_LABEL_ES[s]}" for s in confirmed if s != "limits" and data[s]
    )
    record_activity(
        db,
        broker_id=policy.broker_id,
        user=user,
        action="policy.technical_confirmed",
        entity_type=EntityType.POLICY,
        entity_id=policy.id,
        description=(
            "Datos técnicos de la póliza confirmados (lectura IA)"
            + (f": {counts}" if counts else "")
        ),
        meta={
            "extraction_id": extraction.id,
            "sections": confirmed,
            "warnings": len(warnings),
            "case_file_id": policy.case_file_id,
        },
    )
    raw["confirmed_sections"] = confirmed
    raw["confirm_warnings"] = warnings
    extraction.raw_output = raw
    db.flush()
    ai_service.record_confirmation(
        db,
        extraction=extraction,
        payload=dict(extraction.parsed or {}),
        user=user,
        target="policy.technical",
        applied=bool(confirmed),
    )
    return technical_read_view(extraction, warnings=warnings)


def discard_technical(db: Session, *, extraction: Extraction, user: User | None) -> TechnicalRead:
    """Mark the suggestion dismissed. Nothing is written to the policy."""
    if policy_reading.is_confirmed(extraction):
        raise TechnicalConflict(
            "technical_read_confirmed", "Una lectura ya confirmada no se puede descartar."
        )
    raw = dict(extraction.raw_output or {})
    raw["dismissed"] = {"at": _now().isoformat(), "by_user_id": getattr(user, "id", None)}
    extraction.raw_output = raw
    db.commit()
    db.refresh(extraction)
    return technical_read_view(extraction)
