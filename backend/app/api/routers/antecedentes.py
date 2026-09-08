"""Antecedentes expediente endpoints (v6) — extract → validate → register → PDF.

Mounted at ``{API_V1_PREFIX}``:

    POST /case-files/{id}/antecedentes/process    consolidate → stage a SUGGESTION
    GET  /case-files/{id}/antecedentes            the in-progress / registered record
    POST /case-files/{id}/antecedentes/register   validate + commit the human payload
    GET  /case-files/{id}/antecedentes/pdf        the branded PDF Document (download link)

    GET  /line-record-schemas                     list ramo schemas (+ global seed)
    GET  /line-record-schemas/{insurance_line_id} resolve the active schema for a ramo
    POST /line-record-schemas                     create a broker ramo schema
    PUT  /line-record-schemas/{id}                edit a broker ramo schema

Doctrine (CLAUDE.md rule 6, extended for v6): the consolidation SUGGESTS and
persists exactly one ``extraction`` row; nothing is written to the expediente
until the human confirms it at ``/register``. Every query on both new tables
filters ``broker_id``; a foreign row is a 404, never a 403 (rule 2). The generated
PDF is a ``document`` row (rule 8) reached only through the owned expediente.

No provider hiccup becomes a 500: every :class:`AIError` maps through
``_ai_http_error`` to a precise 502/503/504/422, and a PDF-engine failure maps to
502 (mirroring the pack builder).
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.ai import _ai_http_error
from app.api.routers.documents import build_download_payload, store_generated_document
from app.services.pdf_templates import _sum_money, _fmt_money
from app.models.ai import Extraction
from app.models.broker import Broker
from app.models.case_file import CaseFile
from app.models.document import DocumentCategory
from app.models.enums import EntityType, RecordExpedienteStatus
from app.models.insurance_line import InsuranceLine
from app.models.line_record_schema import LineRecordSchema
from app.models.record_expediente import RecordExpediente
from app.models.user import User
from app.schemas.ai import ExtractionRead
from app.schemas.antecedentes import (
    AntecedentesRead,
    AntecedentesRegisterRequest,
    LineAssignmentRequest,
    LineRecordSchemaCreate,
    LineRecordSchemaRead,
    LineRecordSchemaUpdate,
    LineUsage,
    RamoSchema,
    RequiredField,
)
from app.schemas.document import DocumentDownload
from app.services import ai as ai_service
from app.services import pdf_templates
from app.services.storage import _media_bytes

router = APIRouter(tags=["antecedentes"])


# --- Shared resolution -------------------------------------------------------


def _account_or_404(db: Session, case_file_id: int, broker_id: int) -> CaseFile:
    case = ai_service.get_account_case_file(db, case_file_id, broker_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {case_file_id} not found",
        )
    return case


def _expediente(db: Session, case_file_id: int, broker_id: int) -> RecordExpediente | None:
    return db.scalars(
        select(RecordExpediente).where(
            RecordExpediente.broker_id == broker_id,
            RecordExpediente.case_file_id == case_file_id,
        )
    ).first()


# --- Derived totals (v7): CODE computes, the AI never supplies a total ---------
# A total is a DERIVED value: code sums the structured "materias aseguradas"
# matrix. It is never an AI-extracted field and never a mandatory input the human
# fills — so the "monto total asegurado" always equals the table by construction.


def _is_derived_total(section_key: str | None, field: dict) -> bool:
    """A money field that code derives (the matrix grand total), not a human/AI
    input. By convention: a ``money_uf`` field in the ``total_insured`` section,
    or any field the definition flags ``"computed": "matrix_total"``."""
    if field.get("computed") == "matrix_total":
        return True
    return section_key == "total_insured" and str(field.get("type") or "").lower() == "money_uf"


def _matrix_grand_total(definition: dict | None, payload: dict | None) -> "Decimal | None":
    """Code sum of every ``money_uf`` cell across the ``totals: true`` matrices
    (the ubicaciones × materias aseguradas table). This IS the monto total
    asegurado — derived, never asked of the model."""
    payload = payload if isinstance(payload, dict) else {}
    total = None
    for section in (definition or {}).get("sections", []) or []:
        secval = payload.get(section.get("key"))
        if not isinstance(secval, dict):
            continue
        for field in section.get("fields", []) or []:
            if str(field.get("type") or "").lower() == "list" and field.get("totals"):
                rows = secval.get(field.get("key")) or []
                if not isinstance(rows, list):
                    continue
                for col in field.get("fields", []) or []:
                    if str(col.get("type") or "").lower() == "money_uf":
                        summed = _sum_money(rows, col.get("key"))
                        if summed is not None:
                            total = summed if total is None else total + summed
    return total


def _with_derived_totals(definition: dict | None, payload: dict | None) -> dict | None:
    """Return a COPY of ``payload`` with every derived-total field set to the
    code-computed matrix grand total. Never mutates the stored ORM payload."""
    if not isinstance(payload, dict):
        return payload
    total = _matrix_grand_total(definition, payload)
    if total is None:
        return payload
    out = copy.deepcopy(payload)
    for section in (definition or {}).get("sections", []) or []:
        skey = section.get("key")
        for field in section.get("fields", []) or []:
            if _is_derived_total(skey, field):
                sec = out.setdefault(skey, {})
                if isinstance(sec, dict):
                    sec[field.get("key")] = _fmt_money(total)
    return out


# --- Dynamic completeness (v7) -----------------------------------------------
# Walk the resolved LINE definition against the payload to compute the mandatory
# checklist (``required_fields``) and the still-empty subset (``missing_required``).


def _value_is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _required_fields(definition: dict | None) -> list[RequiredField]:
    """Every mandatory top-level field of the line, addressed by section+key."""
    out: list[RequiredField] = []
    for section in (definition or {}).get("sections", []) or []:
        skey = section.get("key")
        for field in section.get("fields", []) or []:
            if not field.get("required"):
                continue
            # A code-derived total is never a mandatory human input.
            if _is_derived_total(skey, field):
                continue
            fkey = field.get("key")
            if not skey or not fkey:
                continue
            out.append(
                RequiredField(
                    section=str(skey),
                    field=str(fkey),
                    label=str(field.get("label") or fkey),
                )
            )
    return out


def _missing_required(
    definition: dict | None, payload: dict | None
) -> list[RequiredField]:
    """The mandatory fields still empty in ``payload`` (a required field or a
    required list left empty)."""
    payload = payload if isinstance(payload, dict) else {}
    missing: list[RequiredField] = []
    for req in _required_fields(definition):
        section_val = payload.get(req.section)
        section_val = section_val if isinstance(section_val, dict) else {}
        if _value_is_empty(section_val.get(req.field)):
            missing.append(req)
    return missing


def _antecedentes_read(
    db: Session,
    case: CaseFile,
    broker_id: int,
    *,
    expediente: RecordExpediente | None = None,
    schema_row: LineRecordSchema | None = None,
    warnings: list[str] | None = None,
) -> AntecedentesRead:
    if schema_row is None:
        schema_row = ai_service.resolve_account_line_record_schema(db, case, broker_id)
    definition = (
        RamoSchema.model_validate(schema_row.definition) if schema_row else None
    )
    raw_definition = schema_row.definition if schema_row else None
    # The monto total asegurado is DERIVED (code sum of the materias matrix), so
    # the response always shows the reconciled total, never a stored/AI number.
    payload = _with_derived_totals(raw_definition, expediente.payload if expediente else None)
    required_fields = _required_fields(raw_definition)
    missing_required = _missing_required(raw_definition, payload)
    # No line resolved → there is nothing to complete, so ``complete`` is False
    # (the CTA stays disabled with a reason). With a line, complete iff nothing
    # mandatory is missing.
    complete = bool(schema_row) and not missing_required
    status_val = (
        expediente.status.value if expediente else RecordExpedienteStatus.DRAFT.value
    )
    schema_version = None
    if expediente and expediente.schema_version is not None:
        schema_version = expediente.schema_version
    elif schema_row is not None:
        schema_version = schema_row.version
    # Embed the full source extraction (the audit trail) so the review UI can
    # render its provenance card. Broker-filtered — a foreign row never leaks.
    extraction_row = None
    if expediente and expediente.source_extraction_id is not None:
        extraction_row = db.scalars(
            select(Extraction).where(
                Extraction.id == expediente.source_extraction_id,
                Extraction.broker_id == broker_id,
            )
        ).first()
    return AntecedentesRead(
        case_file_id=case.id,
        status=status_val,
        insurance_line_id=case.insurance_line_id,
        schema_id=(schema_row.id if schema_row else None),
        schema_version=schema_version,
        line_name=(schema_row.name if schema_row else None),
        schema=definition,
        payload=payload,
        confidence=(expediente.ai_confidence if expediente else None),
        extraction_id=(expediente.source_extraction_id if expediente else None),
        extraction=(
            ExtractionRead.model_validate(extraction_row) if extraction_row else None
        ),
        registered_at=(expediente.registered_at if expediente else None),
        registered_by_id=(expediente.registered_by_id if expediente else None),
        pdf_available=bool(expediente and expediente.pdf_document_id),
        required_fields=required_fields,
        missing_required=missing_required,
        complete=complete,
        warnings=warnings or [],
    )


# --- Antecedentes expediente -------------------------------------------------


@router.post(
    "/case-files/{case_file_id}/antecedentes/process",
    response_model=AntecedentesRead,
)
def process_antecedentes(
    case_file_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> AntecedentesRead:
    """Consolidate the account's antecedentes into a suggested per-ramo record.

    Runs the AI SUGGEST: writes exactly one extraction row and stages the result
    on a ``review`` expediente. Nothing is committed until ``/register``.
    """
    case = _account_or_404(db, case_file_id, broker_id)
    try:
        result = ai_service.consolidate_antecedentes(
            db, case_file_id=case.id, broker_id=broker_id, user=current_user
        )
    except ai_service.AIError as exc:
        raise _ai_http_error(exc) from exc

    expediente = _expediente(db, case.id, broker_id)
    return _antecedentes_read(
        db,
        case,
        broker_id,
        expediente=expediente,
        schema_row=result.line_record_schema,
        warnings=result.warnings,
    )


@router.get(
    "/case-files/{case_file_id}/antecedentes",
    response_model=AntecedentesRead,
)
def read_antecedentes(
    case_file_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> AntecedentesRead:
    """The registered / in-progress expediente (schema + payload + status)."""
    case = _account_or_404(db, case_file_id, broker_id)
    expediente = _expediente(db, case.id, broker_id)
    return _antecedentes_read(db, case, broker_id, expediente=expediente)


@router.post(
    "/case-files/{case_file_id}/antecedentes/register",
    response_model=AntecedentesRead,
)
def register_antecedentes(
    case_file_id: int,
    body: AntecedentesRegisterRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> AntecedentesRead:
    """Validate the human-completed payload against the dynamic model and register."""
    case = _account_or_404(db, case_file_id, broker_id)
    schema_row = ai_service.resolve_account_line_record_schema(db, case, broker_id)
    if schema_row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La cuenta no tiene una línea de antecedentes asignada",
        )

    from pydantic import ValidationError

    model = ai_service.build_dynamic_schema(schema_row.definition)
    try:
        validated = model.model_validate(body.payload or {})
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El registro no cumple el esquema: {exc.error_count()} errores",
        ) from exc

    stored = validated.model_dump(mode="json")

    expediente = _expediente(db, case.id, broker_id)
    if expediente is None:
        expediente = RecordExpediente(broker_id=broker_id, case_file_id=case.id)
        db.add(expediente)
    expediente.payload = stored
    expediente.status = RecordExpedienteStatus.REGISTERED
    expediente.insurance_line_id = case.insurance_line_id
    expediente.line_record_schema_id = schema_row.id
    expediente.schema_version = schema_row.version
    expediente.registered_at = datetime.now(timezone.utc)
    expediente.registered_by_id = current_user.id
    db.commit()
    db.refresh(expediente)

    return _antecedentes_read(
        db, case, broker_id, expediente=expediente, schema_row=schema_row
    )


@router.post(
    "/case-files/{case_file_id}/line",
    response_model=AntecedentesRead,
)
def assign_account_line(
    case_file_id: int,
    body: LineAssignmentRequest,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Edit")),
) -> AntecedentesRead:
    """Assign a line (or an adopted template) to an account, re-resolving the
    Bases-Técnicas schema. Broker-scoped: a foreign account or a foreign line is
    a 404 (rule 2). The line's ramo must match the account's."""
    case = _account_or_404(db, case_file_id, broker_id)
    line = _visible_schema_or_404(db, body.line_record_schema_id, broker_id)
    if (
        case.insurance_line_id is not None
        and line.insurance_line_id != case.insurance_line_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La línea pertenece a otro ramo que el de la cuenta",
        )
    case.line_record_schema_id = line.id
    db.commit()
    db.refresh(case)

    expediente = _expediente(db, case.id, broker_id)
    return _antecedentes_read(
        db, case, broker_id, expediente=expediente, schema_row=line
    )


def _logo_datauri(broker: Broker) -> str | None:
    """The broker logo as a ``data:`` URI, or ``None`` for Radal-only branding."""
    key = broker.logo_key
    if not key:
        return None
    raw = _media_bytes(key)
    if not raw:
        return None
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else "webp"
    mime = {
        "webp": "image/webp",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
    }.get(ext, "image/webp")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _cover_meta(case: CaseFile) -> list[dict]:
    insured = getattr(getattr(case, "client", None), "insured", None)
    line = getattr(case, "insurance_line", None)
    vigencia = case.period_label
    if not vigencia and case.period_start and case.period_end:
        vigencia = f"{case.period_start.isoformat()} — {case.period_end.isoformat()}"
    return [
        {"label": "Razón social", "value": getattr(insured, "legal_name", None), "required": True},
        {"label": "RUT", "value": getattr(insured, "rut", None), "required": True},
        {"label": "Ramo", "value": getattr(line, "name", None), "required": True},
        {"label": "Vigencia", "value": vigencia, "required": True},
    ]


def _pdf_sections(definition: dict, payload: dict | None) -> list[dict]:
    """Map the sectioned schema + payload onto generic template Section dicts."""
    payload = payload or {}
    out: list[dict] = []
    for section in (definition or {}).get("sections", []) or []:
        skey = section.get("key")
        slabel = section.get("label") or skey
        secval = payload.get(skey) if isinstance(payload, dict) else None
        secval = secval if isinstance(secval, dict) else {}

        # Vigencia renders as one prose line ("desde las 12:00 h del …"), per the
        # broker convention, rather than raw start/end date fields.
        if skey == "vigencia":
            def _fmt_date(raw: Any) -> str | None:
                if not raw:
                    return None
                parts = str(raw).split("-")
                if len(parts) == 3 and len(parts[0]) == 4:
                    return f"{parts[2]}-{parts[1]}-{parts[0]}"
                return str(raw)

            start = _fmt_date(secval.get("start_date"))
            end = _fmt_date(secval.get("end_date"))
            sentence = (
                f"Desde las 12:00 h del {start or '—'} hasta las 12:00 h del {end or '—'}"
                if (start or end)
                else None
            )
            out.append(
                {
                    "kind": "fields",
                    "heading": slabel,
                    "full_width": True,
                    "fields": [
                        {"label": "Vigencia solicitada", "value": sentence, "required": True}
                    ],
                }
            )
            continue

        field_items: list[dict] = []
        tables: list[dict] = []
        for field in section.get("fields", []) or []:
            fkey = field.get("key")
            flabel = field.get("label") or fkey
            ftype = str(field.get("type") or "text").lower()
            required = bool(field.get("required"))
            value = secval.get(fkey)
            if ftype == "list":
                columns = [
                    {
                        "key": col.get("key"),
                        "label": col.get("label") or col.get("key"),
                        "required": bool(col.get("required")),
                        # Money/number columns right-align and feed the totals row.
                        "money": str(col.get("type") or "").lower()
                        in ("money_uf", "number", "percent"),
                    }
                    for col in (field.get("fields") or [])
                ]
                if not columns:
                    columns = [
                        {"key": "label", "label": flabel},
                        {"key": "value", "label": "Valor"},
                        {"key": "amount_uf", "label": "UF", "money": True},
                    ]
                rows = value if isinstance(value, list) else []
                tables.append(
                    {
                        "kind": "table",
                        "heading": flabel,
                        "columns": columns,
                        "rows": rows,
                        "required": required,
                        # A ``totals: true`` list renders a summing Total row
                        # (the ubicaciones × materias matrix). Total label in the
                        # first column, money columns summed leniently.
                        "totals": bool(field.get("totals")),
                    }
                )
            elif ftype == "group":
                group = value if isinstance(value, dict) else {}
                for sub in field.get("fields") or []:
                    subkey = sub.get("key")
                    sublabel = sub.get("label") or subkey
                    field_items.append(
                        {
                            "label": f"{flabel} · {sublabel}",
                            "value": group.get(subkey),
                            "required": bool(sub.get("required")),
                        }
                    )
            else:
                display = value
                hint = field.get("unit")
                # Percent fields read as "10 %" inline, not a bare "10".
                if ftype == "percent" and value not in (None, ""):
                    display = f"{str(value).strip().rstrip('%').strip()} %"
                    hint = None
                field_items.append(
                    {
                        "label": flabel,
                        "value": display,
                        "required": required,
                        "hint": hint,
                    }
                )

        if field_items:
            out.append({"kind": "fields", "heading": slabel, "fields": field_items})
            # Any tables from a section that ALSO has scalar fields are sub-parts
            # (e.g. the siniestros table under "Siniestralidad") — no own numeral.
            for tbl in tables:
                tbl["unnumbered"] = True
        elif not tables:
            out.append({"kind": "fields", "heading": slabel, "fields": []})
        out.extend(tables)
    return out


@router.get(
    "/case-files/{case_file_id}/antecedentes/pdf",
    response_model=DocumentDownload,
)
async def antecedentes_pdf(
    case_file_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> DocumentDownload:
    """Generate (or return) the branded antecedentes PDF as a download link."""
    case = _account_or_404(db, case_file_id, broker_id)
    expediente = _expediente(db, case.id, broker_id)
    if expediente is None or expediente.status != RecordExpedienteStatus.REGISTERED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El expediente aún no está registrado",
        )

    schema_row = None
    if expediente.line_record_schema_id is not None:
        schema_row = db.get(LineRecordSchema, expediente.line_record_schema_id)
        if schema_row is not None and schema_row.broker_id not in (broker_id, None):
            schema_row = None
    if schema_row is None:
        schema_row = ai_service.resolve_account_line_record_schema(db, case, broker_id)
    definition = schema_row.definition if schema_row else {"sections": []}

    # Can't send incomplete Bases Técnicas — the frontend gates the button on
    # ``complete`` but a direct call must still be refused (rule: no half-sent
    # técnicas). 409 lists what is missing.
    missing = _missing_required(definition, expediente.payload)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Faltan campos obligatorios de las Bases Técnicas: "
                + ", ".join(m.label for m in missing)
            ),
        )

    broker = db.get(Broker, broker_id)
    insured = getattr(getattr(case, "client", None), "insured", None)
    branding = {
        "radal_wordmark": "Radal.",
        "broker_logo_datauri": _logo_datauri(broker) if broker else None,
        "broker_name": (broker.trade_name or broker.legal_name) if broker else None,
        "broker_rut": broker.rut if broker else None,
        "cmf_code": broker.cmf_code if broker else None,
        "cover_meta": _cover_meta(case),
        # v7 rename: the completed antecedentes ARE the Bases Técnicas — no
        # separate document. Cover eyebrow + running-header right label say so.
        "eyebrow": "Bases Técnicas",
        "running_title": "Bases Técnicas",
    }
    title = getattr(insured, "legal_name", None) or case.title

    pdf_payload = _with_derived_totals(definition, expediente.payload)
    html = pdf_templates.render_expediente_html(
        title=title, branding=branding, sections=_pdf_sections(definition, pdf_payload)
    )

    # Function-scoped import so pytest collection never needs Chromium.
    from app.services.pdf import PDFGenerationError, render_html_to_pdf

    try:
        data = await render_html_to_pdf(html)
    except PDFGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo generar el PDF: {exc}",
        ) from exc

    document = store_generated_document(
        db,
        broker_id=broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        category=DocumentCategory.ANTECEDENTES_PACK,
        data=data,
        original_name=f"bases-tecnicas-{case.id}.pdf",
        mime_type="application/pdf",
        uploaded_by_id=current_user.id,
    )
    expediente.pdf_document_id = document.id
    db.commit()
    db.refresh(document)
    return build_download_payload(document)


# --- Ramo-schema maintainer CRUD ---------------------------------------------

schemas_router = APIRouter(
    prefix="/line-record-schemas", tags=["antecedentes-schemas"]
)


def _schema_read(
    row: LineRecordSchema, usage: LineUsage | None = None
) -> LineRecordSchemaRead:
    return LineRecordSchemaRead(
        id=row.id,
        broker_id=row.broker_id,
        insurance_line_id=row.insurance_line_id,
        insurance_line_name=getattr(row.insurance_line, "name", None),
        name=row.name,
        version=row.version,
        is_active=row.is_active,
        is_global=row.broker_id is None,
        is_template=bool(row.is_template),
        usage=usage or LineUsage(),
        definition=RamoSchema.model_validate(row.definition or {"sections": []}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _line_usage(db: Session, broker_id: int) -> dict[int, LineUsage]:
    """For every line the broker's accounts resolve to: how many accounts and
    distinct groups land on it. Iterates the broker's account case_files and
    resolves each one's line (v7 order) — demo-scale, correctness over cleverness."""
    from collections import defaultdict

    from app.models.enums import CaseFileKind

    accounts = db.scalars(
        select(CaseFile).where(
            CaseFile.broker_id == broker_id,
            CaseFile.kind == CaseFileKind.ACCOUNT,
        )
    ).all()
    counts: dict[int, int] = defaultdict(int)
    groups: dict[int, set] = defaultdict(set)
    for account in accounts:
        resolved = ai_service.resolve_account_line_record_schema(db, account, broker_id)
        if resolved is None:
            continue
        counts[resolved.id] += 1
        if account.account_group_id is not None:
            groups[resolved.id].add(account.account_group_id)
    return {
        sid: LineUsage(accounts=counts[sid], groups=len(groups[sid])) for sid in counts
    }


@schemas_router.get("", response_model=list[LineRecordSchemaRead])
def list_ramo_schemas(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> list[LineRecordSchemaRead]:
    """Every ramo schema visible to the broker — its own rows and the global seed."""
    rows = db.scalars(
        select(LineRecordSchema)
        .where(
            or_(
                LineRecordSchema.broker_id == broker_id,
                LineRecordSchema.broker_id.is_(None),
            )
        )
        .order_by(LineRecordSchema.insurance_line_id, LineRecordSchema.version.desc())
    ).all()
    usage = _line_usage(db, broker_id)
    return [_schema_read(row, usage.get(row.id)) for row in rows]


@schemas_router.get("/{insurance_line_id}", response_model=LineRecordSchemaRead)
def resolve_ramo_schema(
    insurance_line_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> LineRecordSchemaRead:
    """The active schema for a ramo (the broker's own row wins over the seed)."""
    row = ai_service.resolve_line_record_schema(
        db, insurance_line_id=insurance_line_id, broker_id=broker_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No schema for insurance line {insurance_line_id}",
        )
    return _schema_read(row)


def _own_line_or_404(db: Session, insurance_line_id: int, broker_id: int) -> InsuranceLine:
    """The ramo (``insurance_line``) the broker may build a line on: its own row
    OR a Radal-global one (``broker_id`` NULL — the shipped ramo catalogue)."""
    line = db.scalars(
        select(InsuranceLine).where(
            InsuranceLine.id == insurance_line_id,
            or_(
                InsuranceLine.broker_id == broker_id,
                InsuranceLine.broker_id.is_(None),
            ),
        )
    ).first()
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Insurance line {insurance_line_id} not found",
        )
    return line


def _visible_schema_or_404(
    db: Session, schema_id: int, broker_id: int
) -> LineRecordSchema:
    """A line/template the broker may read: its own row or a global one. A
    foreign broker's row is a 404 (rule 2), never a 403."""
    row = db.scalars(
        select(LineRecordSchema).where(
            LineRecordSchema.id == schema_id,
            or_(
                LineRecordSchema.broker_id == broker_id,
                LineRecordSchema.broker_id.is_(None),
            ),
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schema {schema_id} not found",
        )
    return row


@schemas_router.post(
    "", response_model=LineRecordSchemaRead, status_code=status.HTTP_201_CREATED
)
def create_ramo_schema(
    body: LineRecordSchemaCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> LineRecordSchemaRead:
    """Create a broker-owned LINE (v7). Optionally clone ``from_template_id`` (a
    global template's definition + ramo), then edit freely. A broker line is
    never a template (``is_template=False``); its name is unique per broker."""
    template: LineRecordSchema | None = None
    if body.from_template_id is not None:
        template = _visible_schema_or_404(db, body.from_template_id, broker_id)

    insurance_line_id = body.insurance_line_id or (
        template.insurance_line_id if template else None
    )
    if insurance_line_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="insurance_line_id is required when not cloning a template",
        )
    _own_line_or_404(db, insurance_line_id, broker_id)

    if body.definition is not None:
        definition = body.definition.model_dump()
    elif template is not None:
        definition = dict(template.definition or {"sections": []})
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="definition is required when not cloning a template",
        )

    row = LineRecordSchema(
        broker_id=broker_id,
        insurance_line_id=insurance_line_id,
        name=body.name,
        version=body.version or 1,
        is_active=body.is_active,
        is_template=False,
        definition=definition,
        created_by_id=current_user.id,
    )
    db.add(row)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001 - a duplicate NAME clash is a 409
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una línea con ese nombre",
        ) from exc
    db.refresh(row)
    return _schema_read(row)


@schemas_router.put("/{schema_id}", response_model=LineRecordSchemaRead)
def update_ramo_schema(
    schema_id: int,
    body: LineRecordSchemaUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> LineRecordSchemaRead:
    """Edit a broker-owned ramo schema. The global seed is read-only here."""
    row = db.scalars(
        select(LineRecordSchema).where(
            LineRecordSchema.id == schema_id,
            LineRecordSchema.broker_id == broker_id,
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schema {schema_id} not found",
        )
    if body.name is not None:
        row.name = body.name
    if body.definition is not None:
        row.definition = body.definition.model_dump()
    if body.version is not None:
        row.version = body.version
    if body.is_active is not None:
        row.is_active = body.is_active
    db.commit()
    db.refresh(row)
    return _schema_read(row)


@schemas_router.delete("/{schema_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ramo_schema(
    schema_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> Response:
    """Delete a broker-owned ramo schema. The global seed is read-only here:
    a global row (``broker_id`` NULL) or another tenant's row never matches the
    broker filter, so it surfaces as a 404 (rule 2), never a 403."""
    row = db.scalars(
        select(LineRecordSchema).where(
            LineRecordSchema.id == schema_id,
            LineRecordSchema.broker_id == broker_id,
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schema {schema_id} not found",
        )
    # Block deletion while any account resolves to this line (rule: no dead
    # references). Direct assignments and registered expedientes both count.
    usage = _line_usage(db, broker_id).get(row.id)
    in_use = usage.accounts if usage else 0
    registered = db.scalar(
        select(func.count())
        .select_from(RecordExpediente)
        .where(
            RecordExpediente.broker_id == broker_id,
            RecordExpediente.line_record_schema_id == row.id,
        )
    ) or 0
    total_in_use = max(in_use, int(registered))
    if total_in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No se puede eliminar: la línea está en uso por {total_in_use} "
                "cuenta(s). Reasigna esas cuentas primero."
            ),
        )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
