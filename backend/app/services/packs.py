"""Expediente packs — the three generated downloadables (spec §8).

``submission`` (broker -> insurers), ``comparison`` (broker -> insured) and
``proposal`` (broker -> winning insurer). Each produces a master PDF built with
reportlab/platypus and, for submission and proposal, a ZIP of the section's
documents assembled in memory.

**No new storage path.** Everything is written through
:func:`app.api.routers.documents.store_generated_document`, so ``document`` stays
the only table that holds an S3 key and ``MEDIA_BACKEND=local`` keeps working
untouched. The keys are FIXED (``documents/case_file/{id}/{pack}-1.{pdf,zip}``)
so regenerating overwrites instead of accumulating.

Failure behaviour (§8.4): a failure marks ``case_pack.status=failed`` with the
error and raises :class:`PackGenerationError` (the router answers 502); an
oversized ZIP raises :class:`PackTooLargeError` (413). No half-written document
row is ever left behind.
"""
from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai import Extraction, ExtractionStatus
from app.models.case_file import CaseFile, CasePack
from app.models.client import Client
from app.models.document import Document, DocumentCategory
from app.models.enums import CaseSection, EntityType, PackKind, PackStatus
from app.models.insurance_line import InsuranceLine
from app.models.insurer import Insurer, InsurerStatus
from app.models.insured import Insured
from app.models.placement import Placement
from app.models.proposal import Proposal, ProposalStatus
from app.models.user import User

__all__ = [
    "PackError",
    "PackGenerationError",
    "PackTooLargeError",
    "Recipient",
    "resolve_recipients",
    "build_submission_pack",
    "build_comparison_pack",
    "build_proposal_pack",
    "build_group_archive",
    "PACK_SECTIONS",
    "PACK_CATEGORY",
]


class PackError(Exception):
    """Base class for pack failures."""


class PackGenerationError(PackError):
    """Generation failed. The router answers 502 with this message."""


class PackTooLargeError(PackError):
    """The assembled ZIP exceeds ``settings.PACK_MAX_MB``. The router answers 413."""


#: Which document sections each pack bundles.
PACK_SECTIONS: dict[PackKind, tuple[CaseSection, ...]] = {
    PackKind.SUBMISSION: (CaseSection.ROOT_PROSPECT, CaseSection.SUBMISSION),
    PackKind.COMPARISON: (CaseSection.INSURER_QUOTES,),
    PackKind.PROPOSAL: (CaseSection.BROKER_PROPOSAL,),
}

#: The document category each generated pack is filed under.
PACK_CATEGORY: dict[PackKind, DocumentCategory] = {
    PackKind.SUBMISSION: DocumentCategory.SUBMISSION_PACK,
    PackKind.COMPARISON: DocumentCategory.COMPARISON_PACK,
    PackKind.PROPOSAL: DocumentCategory.PROPOSAL_PACK,
}

#: Spanish labels for the document index. The UI mirrors these through i18n;
#: the PDF is a Spanish artefact handed to a Chilean insurer, so the server needs
#: its own copy (same reasoning as an LLM prompt: it is output, not an identifier).
CATEGORY_LABELS_ES: dict[str, str] = {
    "prospect_request": "Solicitud del prospecto",
    "business_questionnaire": "Ficha / cuestionario del negocio",
    "insured_values_schedule": "Montos asegurados",
    "loss_history": "Siniestralidad",
    "inspection_report": "Informe de inspección",
    "technical_brief": "Bases técnicas",
    "submission_letter": "Carta de remisión",
    "risk_engineering_plan": "Plan de ingeniería de riesgos",
    "resubmission_letter": "Carta de re-remisión",
    "insurer_quotation": "Cotización de compañía",
    "proposal": "Cotización de compañía",
    "declination": "Declinación",
    "conditional_pronouncement": "Pronunciamiento condicionado",
    "quote_comparison": "Cuadro comparativo",
    "issuance_proposal": "Propuesta de emisión",
    "technical_recommendation": "Recomendación técnica",
    "policy": "Póliza",
    "payment_plan": "Plan de pago",
    "collection_status": "Estado de cobranza",
    "endorsement_proposal": "Propuesta de endoso",
    "endorsement": "Endoso emitido",
    "compliance_notice": "Aviso de cumplimiento",
    "claim_notice": "Denuncio de siniestro",
    "claim_preliminary_report": "Pre-informe de liquidación",
    "claim_final_report": "Informe final de liquidación",
    "broker_closing_note": "Nota de cierre",
    "submission_pack": "Carpeta de cotización",
    "comparison_pack": "Carpeta comparativa",
    "proposal_pack": "Carpeta de propuesta",
    "cmf_certificate": "Certificado CMF",
    "appointment": "Carta de nombramiento",
    "asset_sheet": "Ficha del bien",
    "asset_photo": "Fotografía",
    "valuation": "Tasación",
    "insured_amounts": "Montos asegurados",
    "evidence": "Respaldo",
    "claims_history": "Historial de siniestros",
    "power_of_attorney": "Poder",
    "offering": "Oferta",
    "logo": "Logo",
    "other": "Otro",
}


def category_label(category: DocumentCategory | None) -> str:
    if category is None:
        return "Documento"
    return CATEGORY_LABELS_ES.get(category.value, category.value.replace("_", " "))


# =============================================================================
# Recipients — broker+line -> broker -> line -> global
# =============================================================================

@dataclass(frozen=True)
class Recipient:
    """A native insurer plus the contact the existing precedence resolves to."""

    insurer_id: int
    legal_name: str
    is_native: bool
    contact_name: str | None = None
    contact_email: str | None = None
    resolution_level: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "insurer_id": self.insurer_id,
            "legal_name": self.legal_name,
            "is_native": self.is_native,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "resolution_level": self.resolution_level,
        }


def resolve_recipients(
    db: Session,
    *,
    broker_id: int,
    insurance_line_id: int | None,
    with_contact_only: bool = False,
) -> list[Recipient]:
    """Native insurers for this broker's line, each with its effective contact.

    Reuses the existing ``broker+line -> broker -> line -> global`` resolution in
    ``app.api.routers.insurers`` — there is exactly one implementation of that
    precedence in the codebase and this is it.
    """
    # Lazy: the insurers router imports schemas and deps; keeping the import here
    # avoids an import cycle at module load.
    from app.api.routers.insurers import _resolve_contact

    insurers = list(
        db.scalars(
            select(Insurer)
            .where(Insurer.is_native.is_(True), Insurer.status == InsurerStatus.ACTIVE)
            .order_by(Insurer.legal_name.asc())
        ).all()
    )
    out: list[Recipient] = []
    for insurer in insurers:
        resolved = _resolve_contact(db, insurer.id, broker_id, insurance_line_id)
        if resolved is None and with_contact_only:
            continue
        out.append(
            Recipient(
                insurer_id=insurer.id,
                legal_name=insurer.legal_name,
                is_native=insurer.is_native,
                contact_name=resolved.contact.name if resolved else None,
                contact_email=resolved.contact.email if resolved else None,
                resolution_level=resolved.match_level if resolved else None,
            )
        )
    return out


# =============================================================================
# Data gathering
# =============================================================================

def _case_documents(
    db: Session, case: CaseFile, sections: tuple[CaseSection, ...]
) -> list[Document]:
    if not sections:
        return []
    return list(
        db.scalars(
            select(Document)
            .where(
                Document.broker_id == case.broker_id,
                Document.case_file_id == case.id,
                Document.section.in_(sections),
                Document.category.notin_(tuple(PACK_CATEGORY.values())),
            )
            .order_by(Document.section.asc(), Document.document_code.asc(), Document.id.asc())
        ).all()
    )


def _confirmed_payloads(db: Session, case: CaseFile) -> dict[str, dict[str, Any]]:
    """The parsed payload of the case's usable extractions, keyed by category."""
    rows = db.scalars(
        select(Extraction)
        .where(
            Extraction.broker_id == case.broker_id,
            Extraction.case_file_id == case.id,
            Extraction.status == ExtractionStatus.SUCCEEDED,
        )
        .order_by(Extraction.id.asc())
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.category is None or not isinstance(row.parsed, dict):
            continue
        out[row.category.value] = row.parsed
    return out


def _case_identity(db: Session, case: CaseFile) -> dict[str, Any]:
    client = db.get(Client, case.client_id)
    insured = db.get(Insured, client.insured_id) if client else None
    line = (
        db.get(InsuranceLine, case.insurance_line_id)
        if case.insurance_line_id is not None
        else None
    )
    placement = (
        db.get(Placement, case.placement_id) if case.placement_id is not None else None
    )
    period = None
    if placement is not None:
        if placement.period:
            period = placement.period
        elif placement.period_start and placement.period_end:
            period = f"{placement.period_start:%d-%m-%Y} → {placement.period_end:%d-%m-%Y}"
    return {
        "insured_legal_name": insured.legal_name if insured else None,
        "insured_rut": insured.rut if insured else None,
        "insurance_line": line.name if line else None,
        "period": period,
    }


def _case_proposals(db: Session, case: CaseFile) -> list[Proposal]:
    from app.services.case_files import case_proposals

    return case_proposals(db, case)


# ``_media_bytes`` now lives in ``app.services.storage`` (shared with the PDF
# service). It is re-exported here so existing imports keep working.
from app.services.storage import _media_bytes  # noqa: E402,F401


def _document_bytes(doc: Document) -> bytes | None:
    """Read a stored document back for the ZIP. ``None`` when unreadable."""
    backend = (settings.MEDIA_BACKEND or "local").lower()
    try:
        if backend == "s3":
            import boto3  # noqa: PLC0415

            client = boto3.client("s3", region_name=settings.S3_REGION)
            obj = client.get_object(Bucket=doc.bucket or settings.S3_BUCKET, Key=doc.s3_key)
            return obj["Body"].read()
        path = os.path.join(settings.MEDIA_LOCAL_DIR, doc.s3_key)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
    except Exception:  # noqa: BLE001
        return None
    return None


# =============================================================================
# PDF rendering (reportlab / platypus)
# =============================================================================

def _fmt_uf(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return str(value)
    quantized = number.quantize(Decimal("0.01"))
    whole, _, frac = f"{quantized:,.2f}".partition(".")
    return f"UF {whole.replace(',', '.')},{frac}"


def _styles():
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    sheet = getSampleStyleSheet()
    sheet.add(
        ParagraphStyle(
            name="RadalWordmark", parent=sheet["Title"], fontSize=26, leading=30,
            spaceAfter=2, alignment=TA_LEFT, textColor="#0F766E",
        )
    )
    sheet.add(
        ParagraphStyle(
            name="RadalTagline", parent=sheet["Normal"], fontSize=9, leading=12,
            textColor="#64748B", spaceAfter=18,
        )
    )
    sheet.add(
        ParagraphStyle(
            name="RadalH2", parent=sheet["Heading2"], fontSize=13, leading=16,
            spaceBefore=14, spaceAfter=6, textColor="#0F172A",
        )
    )
    sheet.add(
        ParagraphStyle(
            name="RadalBody", parent=sheet["Normal"], fontSize=9.5, leading=13
        )
    )
    sheet.add(
        ParagraphStyle(
            name="RadalCell", parent=sheet["Normal"], fontSize=8, leading=10
        )
    )
    return sheet


def _table(rows: list[list[Any]], widths: list[float] | None = None):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6FFFA")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _cover_story(db: Session, case: CaseFile, styles, *, title: str) -> list:
    """Radal wordmark + the broker's identity + the case identity."""
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, Spacer

    broker = case.broker
    story: list = [
        Paragraph("RADAL", styles["RadalWordmark"]),
        Paragraph("Plataforma de distribución de seguros", styles["RadalTagline"]),
    ]

    logo = _media_bytes(getattr(broker, "logo_key", None))
    if logo:
        try:
            story.append(Image(io.BytesIO(logo), width=28 * mm, height=28 * mm))
            story.append(Spacer(1, 6))
        except Exception:  # noqa: BLE001 - a broken logo must not fail the pack
            pass

    identity = _case_identity(db, case)
    executive = case.owner
    broker_rows = [
        ["Corredora", broker.legal_name if broker else "—"],
        ["RUT corredora", (broker.rut if broker else None) or "—"],
        ["Registro CMF", (broker.cmf_code if broker else None) or "—"],
        [
            "Ejecutivo",
            f"{executive.full_name} · {executive.email}" if executive else "—",
        ],
    ]
    story.append(Paragraph(title, styles["RadalH2"]))
    story.append(_table([["Emisor", ""], *broker_rows], [45 * mm, 115 * mm]))
    story.append(Spacer(1, 10))

    case_rows = [
        ["Expediente", case.reference or f"#{case.id}"],
        ["Asegurado", identity["insured_legal_name"] or "—"],
        ["RUT asegurado", identity["insured_rut"] or "—"],
        ["Ramo", identity["insurance_line"] or "—"],
        ["Vigencia solicitada", identity["period"] or "—"],
        ["Etapa", case.stage.value],
    ]
    story.append(_table([["Caso", ""], *case_rows], [45 * mm, 115 * mm]))
    return story


def _business_summary_story(db: Session, case: CaseFile, styles) -> list:
    """Section 2 of the submission pack, from the confirmed extractions."""
    from reportlab.platypus import Paragraph

    from reportlab.lib.units import mm

    payloads = _confirmed_payloads(db, case)
    questionnaire = payloads.get("business_questionnaire", {})
    values = payloads.get("insured_values_schedule", {})
    losses = payloads.get("loss_history", {})
    inspection = payloads.get("inspection_report", {})
    brief = payloads.get("technical_brief", {})

    def _get(payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    locations = _get(questionnaire, "locations") or _get(brief, "locations") or []
    bi = _get(values, "business_interruption") or {}
    rows = [
        ["Actividad", _get(questionnaire, "business_activity") or "—"],
        ["Ubicaciones declaradas", str(len(locations)) if locations else "—"],
        [
            "Valor total asegurable (TIV)",
            _fmt_uf(
                _get(values, "program_total_insured_value_uf", "total_physical_assets_uf")
                or _get(brief, "total_program_insured_value_uf")
            ),
        ],
        [
            "Perjuicio por paralización",
            _fmt_uf(bi.get("uf") if isinstance(bi, dict) else None),
        ],
        [
            "Periodo de indemnización",
            str((bi.get("months") if isinstance(bi, dict) else None) or "—"),
        ],
        ["Siniestralidad", f"{_get(losses, 'loss_ratio_pct') or '—'} %"],
        [
            "Inspección",
            " · ".join(
                str(part)
                for part in (
                    _get(inspection, "report_folio"),
                    _get(inspection, "weighted_technical_score"),
                    _get(inspection, "risk_class"),
                )
                if part not in (None, "")
            )
            or "—",
        ],
        [
            "Plazo de oferta",
            str(_get(brief, "offer_deadline") or _get(payloads.get("submission_letter", {}), "offer_deadline") or "—"),
        ],
    ]
    story = [Paragraph("Resumen del negocio", styles["RadalH2"])]
    if not payloads:
        story.append(
            Paragraph(
                "Sin extracciones confirmadas todavía: el resumen se completará "
                "cuando los antecedentes sean analizados y confirmados.",
                styles["RadalBody"],
            )
        )
    story.append(_table([["Concepto", "Valor"], *rows], [55 * mm, 105 * mm]))

    gaps = _get(brief, "gaps") or []
    if isinstance(gaps, list) and gaps:
        gap_rows = [["Código", "Brecha", "Severidad"]]
        for gap in gaps[:20]:
            if not isinstance(gap, dict):
                continue
            gap_rows.append(
                [
                    str(gap.get("code") or "—"),
                    Paragraph(str(gap.get("title") or "—"), styles["RadalCell"]),
                    str(gap.get("severity") or "—"),
                ]
            )
        if len(gap_rows) > 1:
            story.append(Paragraph("Brechas detectadas", styles["RadalH2"]))
            story.append(_table(gap_rows, [20 * mm, 110 * mm, 30 * mm]))
    return story


def _document_index_story(documents: list[Document], styles) -> list:
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph

    story = [Paragraph("Índice de documentos", styles["RadalH2"])]
    if not documents:
        story.append(Paragraph("El expediente aún no tiene documentos.", styles["RadalBody"]))
        return story
    rows: list[list[Any]] = [["Código", "Tipo", "Archivo", "Tamaño", "Fecha"]]
    for doc in documents:
        size = f"{(doc.size_bytes or 0) / 1024:,.0f} KB" if doc.size_bytes else "—"
        rows.append(
            [
                doc.document_code or "—",
                Paragraph(category_label(doc.category), styles["RadalCell"]),
                Paragraph(doc.original_name, styles["RadalCell"]),
                size,
                doc.created_at.strftime("%d-%m-%Y") if doc.created_at else "—",
            ]
        )
    story.append(_table(rows, [16 * mm, 42 * mm, 62 * mm, 18 * mm, 22 * mm]))
    return story


def _recipients_story(recipients: list[Recipient], styles) -> list:
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph

    if not recipients:
        return []
    rows: list[list[Any]] = [["Compañía", "Contacto", "Correo", "Resolución"]]
    for item in recipients:
        rows.append(
            [
                Paragraph(item.legal_name, styles["RadalCell"]),
                item.contact_name or "—",
                item.contact_email or "—",
                item.resolution_level or "—",
            ]
        )
    return [
        Paragraph("Compañías destinatarias", styles["RadalH2"]),
        _table(rows, [52 * mm, 38 * mm, 48 * mm, 22 * mm]),
    ]


def _render_pdf(story_builder) -> bytes:
    """Run a platypus story into bytes. Raises :class:`PackGenerationError`."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate
    except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
        raise PackGenerationError(
            "reportlab is not installed; the pack PDF cannot be generated"
        ) from exc

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Radal",
    )
    try:
        doc.build(story_builder())
    except PackError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PackGenerationError(f"Could not render the pack PDF: {exc}") from exc
    return buffer.getvalue()


def _build_zip(documents: list[Document], master_pdf: bytes, pdf_name: str) -> bytes:
    """Assemble the section's files in memory. 413 above ``PACK_MAX_MB``."""
    limit = max(1, int(settings.PACK_MAX_MB)) * 1024 * 1024
    running = len(master_pdf)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(pdf_name, master_pdf)
        for index, doc in enumerate(documents, start=1):
            data = _document_bytes(doc)
            if data is None:
                continue
            running += len(data)
            if running > limit:
                raise PackTooLargeError(
                    f"The pack exceeds {settings.PACK_MAX_MB} MB; download the "
                    "sections individually"
                )
            name = (
                f"{index:02d} {doc.document_code or ''} "
                f"{category_label(doc.category)} - {doc.original_name}"
            ).replace("  ", " ").strip()
            archive.writestr(name, data)
    payload = buffer.getvalue()
    if len(payload) > limit:
        raise PackTooLargeError(
            f"The pack exceeds {settings.PACK_MAX_MB} MB; download the sections "
            "individually"
        )
    return payload


# =============================================================================
# Pack orchestration
# =============================================================================

def _get_or_create_pack(
    db: Session, *, case: CaseFile, kind: PackKind, section: CaseSection | None
) -> CasePack:
    pack = db.scalars(
        select(CasePack)
        .where(CasePack.case_file_id == case.id, CasePack.kind == kind)
        .order_by(CasePack.id.asc())
    ).first()
    if pack is None:
        pack = CasePack(
            broker_id=case.broker_id,
            case_file_id=case.id,
            kind=kind,
            section=section,
            status=PackStatus.DRAFT,
        )
        db.add(pack)
        db.flush()
    return pack


def _store(
    db: Session,
    *,
    case: CaseFile,
    kind: PackKind,
    data: bytes,
    extension: str,
    mime_type: str,
    user: User | None,
) -> Document:
    from app.api.routers.documents import store_generated_document

    category = PACK_CATEGORY[kind]
    prefix = (settings.DOCUMENT_S3_PREFIX or "documents/").strip("/")
    key = f"{prefix}/case_file/{case.id}/{category.value}-1.{extension}"
    return store_generated_document(
        db,
        broker_id=case.broker_id,
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        category=category,
        data=data,
        original_name=f"{category.value}-{case.reference or case.id}.{extension}",
        mime_type=mime_type,
        key=key,
        phase=case.stage.value,
        uploaded_by_id=user.id if user else None,
    )


def _finish(
    db: Session,
    *,
    pack: CasePack,
    case: CaseFile,
    kind: PackKind,
    pdf: bytes,
    zip_bytes: bytes | None,
    recipients: list[Recipient],
    user: User | None,
) -> CasePack:
    pdf_doc = _store(
        db, case=case, kind=kind, data=pdf, extension="pdf",
        mime_type="application/pdf", user=user,
    )
    pack.pdf_document_id = pdf_doc.id
    if zip_bytes is not None:
        zip_doc = _store(
            db, case=case, kind=kind, data=zip_bytes, extension="zip",
            mime_type="application/zip", user=user,
        )
        pack.zip_document_id = zip_doc.id
    pack.recipients = [r.as_dict() for r in recipients]
    pack.status = PackStatus.GENERATED
    pack.generated_at = datetime.now(timezone.utc)
    pack.generated_by_id = user.id if user else None
    pack.error = None
    db.flush()
    return pack


def _maybe_summarize(
    db: Session, *, pack: CasePack, case: CaseFile, user: User | None
) -> None:
    """Ask the AI service for the pack's Spanish prose, best effort.

    Contract (§8, §7.5): the pack service NEVER talks to the LLM directly — it
    calls ``app.services.ai.summarize_case_pack``, which persists its own
    ``extraction(kind=summary)`` row and returns UNCONFIRMED text. An outage,
    a missing key or a not-yet-landed helper must never fail a pack: the PDF is
    simply rendered without the prose, and the UI keeps offering "resumir".
    """
    if pack.summary:
        return
    try:  # pragma: no cover - exercised only with a live provider
        from app.services import ai as ai_service

        summarize = getattr(ai_service, "summarize_case_pack", None)
        if summarize is None:
            return
        summarize(db, case_file=case, kind=pack.kind, broker_id=case.broker_id, user=user)
    except Exception:  # noqa: BLE001 - prose is a nice-to-have, never a blocker
        return


def _maybe_summarize_proposals(db: Session, proposals: list[Proposal], user: User | None) -> None:
    """Same contract, per competing quotation (comparison pack, §8.2)."""
    for proposal in proposals:
        if proposal.ai_summary:
            continue
        try:  # pragma: no cover - exercised only with a live provider
            from app.services import ai as ai_service

            summarize = getattr(ai_service, "summarize_proposal", None)
            if summarize is None:
                return
            summarize(
                db, proposal=proposal, broker_id=proposal.broker_id, user=user
            )
        except Exception:  # noqa: BLE001
            return


def _guarded(db: Session, pack: CasePack, builder):
    """Run ``builder`` marking the pack failed on any pack error."""
    pack.status = PackStatus.GENERATING
    pack.error = None
    db.flush()
    try:
        return builder()
    except PackError as exc:
        pack.status = PackStatus.FAILED
        pack.error = str(exc)
        db.flush()
        raise
    except Exception as exc:  # noqa: BLE001 - never leak a 500 out of a pack
        pack.status = PackStatus.FAILED
        pack.error = str(exc)
        db.flush()
        raise PackGenerationError(str(exc)) from exc


def build_submission_pack(
    db: Session, *, case: CaseFile, user: User | None = None
) -> CasePack:
    """Master PDF + ZIP of ``root_prospect`` and ``submission`` (spec §8.1)."""
    pack = _get_or_create_pack(
        db, case=case, kind=PackKind.SUBMISSION, section=CaseSection.SUBMISSION
    )
    recipients = resolve_recipients(
        db, broker_id=case.broker_id, insurance_line_id=case.insurance_line_id
    )
    documents = _case_documents(db, case, PACK_SECTIONS[PackKind.SUBMISSION])

    def _run() -> CasePack:
        _maybe_summarize(db, pack=pack, case=case, user=user)
        styles = _styles()

        def story():
            from reportlab.platypus import PageBreak

            parts = _cover_story(db, case, styles, title="Carpeta de cotización")
            parts.append(PageBreak())
            parts.extend(_business_summary_story(db, case, styles))
            parts.extend(_document_index_story(documents, styles))
            parts.extend(_recipients_story(recipients, styles))
            if pack.summary:
                from reportlab.platypus import Paragraph

                parts.append(Paragraph("Resumen ejecutivo", styles["RadalH2"]))
                parts.append(Paragraph(pack.summary, styles["RadalBody"]))
            return parts

        pdf = _render_pdf(story)
        zip_bytes = _build_zip(documents, pdf, "00 Carpeta de cotización.pdf")
        return _finish(
            db, pack=pack, case=case, kind=PackKind.SUBMISSION, pdf=pdf,
            zip_bytes=zip_bytes, recipients=recipients, user=user,
        )

    return _guarded(db, pack, _run)


def build_comparison_pack(
    db: Session, *, case: CaseFile, user: User | None = None
) -> CasePack:
    """The comparativo handed to the insured — PDF only (spec §8.2)."""
    pack = _get_or_create_pack(
        db, case=case, kind=PackKind.COMPARISON, section=CaseSection.INSURER_QUOTES
    )
    proposals = _case_proposals(db, case)

    def _run() -> CasePack:
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph

        _maybe_summarize_proposals(db, proposals, user)
        _maybe_summarize(db, pack=pack, case=case, user=user)
        styles = _styles()

        def story():
            parts = _cover_story(db, case, styles, title="Comparativo de ofertas")
            parts.append(PageBreak())
            parts.append(Paragraph("Resumen económico", styles["RadalH2"]))
            if not proposals:
                parts.append(
                    Paragraph("Sin cotizaciones cargadas todavía.", styles["RadalBody"])
                )
            else:
                rows: list[list[Any]] = [
                    ["Compañía", "Prima afecta", "Prima exenta", "IVA", "Prima total", "Estado"]
                ]
                for proposal in proposals:
                    insurer = proposal.insurer
                    rows.append(
                        [
                            Paragraph(
                                insurer.legal_name if insurer else "—", styles["RadalCell"]
                            ),
                            _fmt_uf(proposal.taxable_premium_uf),
                            _fmt_uf(proposal.exempt_premium_uf),
                            _fmt_uf(proposal.vat_uf),
                            _fmt_uf(proposal.total_premium_uf),
                            proposal.status.value,
                        ]
                    )
                parts.append(_table(rows, [44 * mm, 24 * mm, 24 * mm, 22 * mm, 26 * mm, 20 * mm]))

                # Per-proposal AI summary (suggest -> confirm; unconfirmed text is
                # labelled as such rather than hidden).
                summarised = [p for p in proposals if p.ai_summary]
                if summarised:
                    parts.append(Paragraph("Lectura de cada oferta", styles["RadalH2"]))
                    for proposal in summarised:
                        insurer = proposal.insurer
                        label = insurer.legal_name if insurer else f"Propuesta {proposal.id}"
                        suffix = "" if proposal.is_summary_confirmed else " (borrador)"
                        parts.append(
                            Paragraph(f"<b>{label}{suffix}</b>", styles["RadalBody"])
                        )
                        parts.append(Paragraph(proposal.ai_summary, styles["RadalBody"]))

                accepted = [p for p in proposals if p.status == ProposalStatus.ACCEPTED]
                if accepted:
                    winner = accepted[0]
                    insurer = winner.insurer
                    parts.append(Paragraph("Oferta recomendada", styles["RadalH2"]))
                    parts.append(
                        Paragraph(
                            f"{insurer.legal_name if insurer else '—'} · "
                            f"prima total {_fmt_uf(winner.total_premium_uf)}",
                            styles["RadalBody"],
                        )
                    )
            if pack.summary:
                parts.append(Paragraph("Recomendación", styles["RadalH2"]))
                parts.append(Paragraph(pack.summary, styles["RadalBody"]))
            parts.extend(
                _document_index_story(
                    _case_documents(db, case, PACK_SECTIONS[PackKind.COMPARISON]), styles
                )
            )
            return parts

        pdf = _render_pdf(story)
        return _finish(
            db, pack=pack, case=case, kind=PackKind.COMPARISON, pdf=pdf,
            zip_bytes=None, recipients=[], user=user,
        )

    return _guarded(db, pack, _run)


def build_proposal_pack(
    db: Session, *, case: CaseFile, user: User | None = None
) -> CasePack:
    """The confirmed 07 rendered as a PDF + the accompanying documents (spec §8.3)."""
    pack = _get_or_create_pack(
        db, case=case, kind=PackKind.PROPOSAL, section=CaseSection.BROKER_PROPOSAL
    )
    payloads = _confirmed_payloads(db, case)
    issuance = payloads.get("issuance_proposal", {})
    documents = _case_documents(db, case, PACK_SECTIONS[PackKind.PROPOSAL])

    # ``accompanying_documents[]`` of the 07, resolved against the case's files.
    accompanying = issuance.get("accompanying_documents")
    if isinstance(accompanying, list) and accompanying:
        wanted = {str(item).strip().lower() for item in accompanying if item}
        extra = db.scalars(
            select(Document).where(
                Document.broker_id == case.broker_id,
                Document.case_file_id == case.id,
            )
        ).all()
        known = {doc.id for doc in documents}
        for doc in extra:
            if doc.id in known:
                continue
            haystack = f"{doc.original_name} {doc.category.value}".lower()
            if any(token and token in haystack for token in wanted):
                documents.append(doc)

    def _run() -> CasePack:
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph

        styles = _styles()
        recipients = resolve_recipients(
            db, broker_id=case.broker_id, insurance_line_id=case.insurance_line_id
        )

        def story():
            parts = _cover_story(db, case, styles, title="Propuesta de emisión")
            parts.append(PageBreak())
            parts.append(Paragraph("Condiciones a emitir", styles["RadalH2"]))
            if not issuance:
                parts.append(
                    Paragraph(
                        "Aún no hay una propuesta de emisión (07) confirmada para "
                        "este expediente.",
                        styles["RadalBody"],
                    )
                )
            else:
                rows = [["Concepto", "Valor"]]
                for label, keys in (
                    ("Compañía", ("addressee_insurer",)),
                    ("Cotización aceptada", ("accepted_quotation_number",)),
                    ("Modalidad", ("cover_mode",)),
                    ("Inicio de vigencia", ("period_start", "period_start_at")),
                    ("Término de vigencia", ("period_end", "period_end_at")),
                    ("Monto asegurado", ("total_insured_amount_uf",)),
                    ("Prima afecta", ("net_taxable", "net_premium_taxable_uf")),
                    ("Prima exenta", ("net_exempt", "net_premium_exempt_uf")),
                    ("Prima neta", ("net_total", "net_premium_total_uf")),
                    ("IVA", ("vat_uf",)),
                    ("Prima total", ("gross_uf", "gross_premium_uf")),
                ):
                    value = None
                    for key in keys:
                        if issuance.get(key) not in (None, "", [], {}):
                            value = issuance[key]
                            break
                    if isinstance(value, dict):
                        value = value.get("legal_name") or value.get("name") or str(value)
                    rows.append([label, str(value) if value is not None else "—"])
                parts.append(_table(rows, [55 * mm, 105 * mm]))

                coverages = issuance.get("coverages_to_issue")
                if isinstance(coverages, list) and coverages:
                    cov_rows: list[list[Any]] = [["N°", "Cobertura", "Límite"]]
                    for item in coverages[:60]:
                        if not isinstance(item, dict):
                            continue
                        cov_rows.append(
                            [
                                str(item.get("n") or item.get("number") or "—"),
                                Paragraph(str(item.get("name") or "—"), styles["RadalCell"]),
                                Paragraph(
                                    str(item.get("limit") or item.get("requested_limit") or "—"),
                                    styles["RadalCell"],
                                ),
                            ]
                        )
                    if len(cov_rows) > 1:
                        parts.append(Paragraph("Coberturas", styles["RadalH2"]))
                        parts.append(_table(cov_rows, [12 * mm, 78 * mm, 70 * mm]))
            parts.extend(_document_index_story(documents, styles))
            return parts

        pdf = _render_pdf(story)
        zip_bytes = _build_zip(documents, pdf, "00 Propuesta de emisión.pdf")
        return _finish(
            db, pack=pack, case=case, kind=PackKind.PROPOSAL, pdf=pdf,
            zip_bytes=zip_bytes, recipients=recipients, user=user,
        )

    return _guarded(db, pack, _run)


#: The three builders, keyed by pack kind — the router dispatches on this.
PACK_BUILDERS = {
    PackKind.SUBMISSION: build_submission_pack,
    PackKind.COMPARISON: build_comparison_pack,
    PackKind.PROPOSAL: build_proposal_pack,
}


# =============================================================================
# Group archive — the whole expediente of a group (or of one vigencia)
# =============================================================================

#: Filesystem-safe segment for the archive layout.
_ARCHIVE_SAFE = "abcdefghijklmnopqrstuvwxyz0123456789-_. "


def _archive_segment(value: str | None, *, fallback: str = "sin-clasificar") -> str:
    """One path segment of the archive layout: lowercase, no separators."""
    if not value:
        return fallback
    text = str(value).strip().lower().replace("/", "-").replace("\\", "-")
    cleaned = "".join(ch if ch in _ARCHIVE_SAFE else "-" for ch in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-. ") or fallback


def build_group_archive(
    db: Session,
    *,
    group,
    cases: list[CaseFile],
    user: User | None = None,
    period_label: str | None = None,
) -> Document:
    """ZIP every visible case's documents as one ``archive_pack`` document.

    Layout ``{slug}/{period}/{line}/{section}/{code}-{filename}`` (spec §4.1).
    The caller decides which cases are visible — this function never widens the
    set — and owns the transaction; bytes go through
    ``documents.store_generated_document`` like every other generated artefact,
    so ``document`` stays the only table holding a storage key (rule 8).

    The key is FIXED per (group, period), so regenerating overwrites the bytes
    and the shared link keeps working. Over ``settings.PACK_MAX_MB`` this raises
    :class:`PackTooLargeError` (the router answers 413).
    """
    from app.api.routers.documents import store_generated_document  # noqa: PLC0415

    case_ids = [case.id for case in cases]
    documents: list[Document] = []
    if case_ids:
        documents = list(
            db.scalars(
                select(Document)
                .where(Document.case_file_id.in_(case_ids))
                .order_by(Document.case_file_id.asc(), Document.id.asc())
            ).all()
        )

    line_names: dict[int, str] = {}
    line_ids = {case.insurance_line_id for case in cases if case.insurance_line_id}
    if line_ids:
        line_names = {
            int(line_id): name
            for line_id, name in db.execute(
                select(InsuranceLine.id, InsuranceLine.name).where(
                    InsuranceLine.id.in_(line_ids)
                )
            ).all()
        }

    by_case = {case.id: case for case in cases}
    slug = _archive_segment(getattr(group, "slug", None), fallback=f"group-{group.id}")

    limit = max(1, int(settings.PACK_MAX_MB)) * 1024 * 1024
    running = 0
    seen: set[str] = set()
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for doc in documents:
                data = _document_bytes(doc)
                if data is None:
                    continue
                running += len(data)
                if running > limit:
                    raise PackTooLargeError(
                        f"The archive exceeds {settings.PACK_MAX_MB} MB; download "
                        "the periods separately"
                    )
                case = by_case.get(int(doc.case_file_id or 0))
                period = _archive_segment(
                    (case.period_label if case else None) or period_label,
                    fallback="sin-vigencia",
                )
                line = _archive_segment(
                    line_names.get(int(case.insurance_line_id or 0)) if case else None,
                    fallback="sin-ramo",
                )
                section = _archive_segment(
                    doc.section.value if doc.section is not None else None
                )
                name = f"{doc.document_code or doc.category.value}-{doc.original_name}"
                path = f"{slug}/{period}/{line}/{section}/{name}"
                if path in seen:
                    path = f"{slug}/{period}/{line}/{section}/{doc.id}-{name}"
                seen.add(path)
                archive.writestr(path, data)
            if not seen:
                # An empty ZIP is still a valid answer: the group has no files
                # the caller may see. Say so inside the archive rather than 502.
                archive.writestr(
                    f"{slug}/README.txt",
                    "Este expediente no contiene documentos visibles.\n",
                )
    except PackError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PackGenerationError(f"Could not assemble the group archive: {exc}") from exc

    payload = buffer.getvalue()
    if len(payload) > limit:
        raise PackTooLargeError(
            f"The archive exceeds {settings.PACK_MAX_MB} MB; download the periods "
            "separately"
        )

    prefix = (settings.DOCUMENT_S3_PREFIX or "documents/").strip("/")
    scope = _archive_segment(period_label, fallback="all")
    key = (
        f"{prefix}/account_group/{group.id}/"
        f"{DocumentCategory.ARCHIVE_PACK.value}-{scope}.zip"
    )
    return store_generated_document(
        db,
        broker_id=group.broker_id,
        entity_type=EntityType.ACCOUNT_GROUP,
        entity_id=group.id,
        category=DocumentCategory.ARCHIVE_PACK,
        data=payload,
        original_name=f"{slug}-{scope}.zip",
        mime_type="application/zip",
        key=key,
        uploaded_by_id=user.id if user else None,
    )
