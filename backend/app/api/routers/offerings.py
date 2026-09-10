"""``/offerings`` — package a quote's recommended proposal into a shareable link.

An offering is what the broker actually sends the insured: one quote request,
one highlighted proposal, an unguessable ``share_token`` and a generated PDF.

Two surfaces:
  * the broker surface (``/offerings``), tenant-scoped and permission-gated;
  * the PUBLIC surface (``/public/offerings/{share_token}``), unauthenticated —
    the token IS the credential. It answers a deliberately narrow projection: no
    broker id, no commission, no internal notes.

The PDF is a STUB for now: a valid, openable one-page PDF carrying the headline
figures, so the "download / share" controls are wired end-to-end rather than
dead. Branded generation replaces :func:`build_offering_pdf` later; nothing else
has to change.

S3 discipline: this module never writes a key itself. It calls
``documents.store_generated_document`` so ``document`` stays the only place a
key is minted (rule 8), using the fixed architecture route
``offerings/{offering_id}/offering.pdf``.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db
from app.api.routers.documents import (
    build_download_payload,
    get_document_url,
    store_generated_document,
)
from app.core.config import settings
from app.core.permissions import require_permission
from app.models import (
    Broker,
    Document,
    DocumentCategory,
    Insurer,
    Offering,
    OfferingStatus,
    Proposal,
    QuoteRequest,
    User,
)
from app.models.enums import EntityType
from app.schemas.document import DocumentDownload
from app.models.proposal import ProposalStatus
from app.schemas.offering import (
    OfferingCreate,
    OfferingDecisionRequest,
    OfferingListResponse,
    OfferingPublicProposal,
    OfferingPublicRead,
    OfferingRead,
    OfferingSend,
    OfferingUpdate,
)

#: A proposal the insured may still choose from the public surface: everything
#: that has not been rejected/withdrawn/expired.
_OPEN_PROPOSAL_STATUSES = (
    ProposalStatus.DRAFT,
    ProposalStatus.SUBMITTED,
    ProposalStatus.ACCEPTED,
)

router = APIRouter(prefix="/offerings", tags=["offerings"])
public_router = APIRouter(prefix="/public/offerings", tags=["offerings"])

#: Fixed S3 route from docs/v2-architecture.md §6.
OFFERING_PDF_KEY = "offerings/{offering_id}/offering.pdf"
SHARE_TOKEN_BYTES = 24
SHARE_PATH = "/o"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; compare in UTC regardless of backend."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _share_url(token: str) -> str:
    """The public link the broker pastes into WhatsApp/email."""
    origins = settings.cors_origins_list
    base = origins[0].rstrip("/") if origins else ""
    return f"{base}{SHARE_PATH}/{token}"


def _new_share_token(db: Session) -> str:
    """An unguessable, unique share token."""
    for _ in range(8):
        token = secrets.token_urlsafe(SHARE_TOKEN_BYTES)
        if db.scalar(select(Offering.id).where(Offering.share_token == token)) is None:
            return token
    raise HTTPException(  # pragma: no cover - astronomically unlikely
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Could not allocate a share token",
    )


# =============================================================================
# Stub PDF generation
# =============================================================================

def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_offering_pdf(title: str, lines: list[str]) -> bytes:
    """Build a minimal but VALID single-page PDF (Helvetica, A4).

    Hand-rolled on purpose: it keeps the dependency surface at zero while the
    branded generator is still to come. Byte offsets for the xref table are
    computed as the objects are appended, which is what makes the file valid.
    """
    ops = ["BT", "/F1 18 Tf", "1 0 0 1 56 780 Tm", f"({_pdf_escape(title)}) Tj", "/F1 11 Tf"]
    for line in lines:
        ops += ["0 -22 Td", f"({_pdf_escape(line)}) Tj"]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _pdf_lines(
    db: Session, offering: Offering, quote: QuoteRequest, proposal: Proposal | None
) -> list[str]:
    """The headline figures rendered into the stub PDF."""
    lines = [
        f"Oferta #{offering.id}",
        f"Cotizacion #{quote.id}",
        f"Objeto asegurado: {quote.insured_object or '-'}",
        f"Valor declarado (UF): {quote.declared_value_uf if quote.declared_value_uf is not None else '-'}",
        "",
    ]
    if proposal is None:
        lines.append("Propuesta recomendada: (pendiente de seleccion)")
        return lines

    insurer = db.get(Insurer, proposal.insurer_id)
    lines += [
        f"Aseguradora: {(insurer.trade_name or insurer.legal_name) if insurer else '-'}",
        f"Modalidad: {proposal.modality or '-'}",
        f"Prima neta (UF): {proposal.net_premium_uf if proposal.net_premium_uf is not None else '-'}",
        f"IVA (UF): {proposal.vat_uf if proposal.vat_uf is not None else '-'}",
        f"Prima total (UF): {proposal.total_premium_uf if proposal.total_premium_uf is not None else '-'}",
        f"Tasa integral (por mil): {proposal.comprehensive_rate_permille if proposal.comprehensive_rate_permille is not None else '-'}",
        f"Vigencia: {proposal.coverage_start or '-'} / {proposal.coverage_end or '-'}",
        "",
        "Documento preliminar generado por Radal.",
    ]
    return lines


def _generate_pdf_document(
    db: Session, offering: Offering, *, user_id: int | None
) -> Document:
    """(Re)generate the offering PDF and register/refresh its document row."""
    quote = db.get(QuoteRequest, offering.quote_request_id)
    proposal = (
        db.get(Proposal, offering.selected_proposal_id)
        if offering.selected_proposal_id is not None
        else None
    )
    payload = build_offering_pdf(
        "Radal - Propuesta de seguro",
        _pdf_lines(db, offering, quote, proposal),
    )
    return store_generated_document(
        db,
        broker_id=offering.broker_id,
        entity_type=EntityType.OFFERING,
        entity_id=offering.id,
        category=DocumentCategory.OFFERING,
        data=payload,
        original_name=f"oferta-{offering.id}.pdf",
        mime_type="application/pdf",
        key=OFFERING_PDF_KEY.format(offering_id=offering.id),
        phase="offering",
        uploaded_by_id=user_id,
    )


# =============================================================================
# Tenant-scoped helpers
# =============================================================================

def _require_quote(db: Session, quote_request_id: int, broker_id: int) -> QuoteRequest:
    quote = db.get(QuoteRequest, quote_request_id)
    if quote is None or quote.broker_id != broker_id:
        raise HTTPException(
            status_code=404, detail=f"Quote request {quote_request_id} not found"
        )
    return quote


def _require_proposal(
    db: Session, proposal_id: int, broker_id: int, quote_request_id: int
) -> Proposal:
    proposal = db.get(Proposal, proposal_id)
    if proposal is None or proposal.broker_id != broker_id:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    if proposal.quote_request_id != quote_request_id:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Proposal {proposal_id} belongs to quote request "
                f"{proposal.quote_request_id}, not {quote_request_id}"
            ),
        )
    return proposal


def _get_offering(db: Session, offering_id: int, broker_id: int) -> Offering:
    offering = db.get(Offering, offering_id)
    if offering is None or offering.broker_id != broker_id:
        raise HTTPException(status_code=404, detail="Offering not found")
    return offering


def _to_read(offering: Offering) -> OfferingRead:
    data = OfferingRead.model_validate(offering)
    data.share_url = _share_url(offering.share_token)
    return data


# =============================================================================
# Broker endpoints
# =============================================================================

@router.post("", response_model=OfferingRead, status_code=status.HTTP_201_CREATED)
def create_offering(
    payload: OfferingCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Offerings", "Create")),
) -> OfferingRead:
    """Create a shareable offering: token + stub PDF, ready to send."""
    _require_quote(db, payload.quote_request_id, broker_id)
    if payload.selected_proposal_id is not None:
        _require_proposal(
            db, payload.selected_proposal_id, broker_id, payload.quote_request_id
        )

    offering = Offering(
        broker_id=broker_id,
        quote_request_id=payload.quote_request_id,
        selected_proposal_id=payload.selected_proposal_id,
        share_token=_new_share_token(db),
        expires_at=payload.expires_at,
        status=OfferingStatus.DRAFT,
        created_by_id=current_user.id,
    )
    db.add(offering)
    db.flush()  # need offering.id for the fixed PDF route

    doc = _generate_pdf_document(db, offering, user_id=current_user.id)
    offering.pdf_document_id = doc.id

    db.commit()
    db.refresh(offering)
    return _to_read(offering)


@router.get("", response_model=OfferingListResponse)
def list_offerings(
    quote_request_id: int | None = Query(default=None, gt=0),
    offering_status: OfferingStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Offerings", "View")),
) -> OfferingListResponse:
    filters = [Offering.broker_id == broker_id]
    if quote_request_id is not None:
        filters.append(Offering.quote_request_id == quote_request_id)
    if offering_status is not None:
        filters.append(Offering.status == offering_status)

    total = db.scalar(select(func.count(Offering.id)).where(*filters)) or 0
    rows = db.scalars(
        select(Offering)
        .where(*filters)
        .order_by(Offering.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return OfferingListResponse(
        total=int(total), items=[_to_read(o) for o in rows]
    )


@router.get("/{offering_id}", response_model=OfferingRead)
def get_offering(
    offering_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Offerings", "View")),
) -> OfferingRead:
    return _to_read(_get_offering(db, offering_id, broker_id))


@router.patch("/{offering_id}", response_model=OfferingRead)
def update_offering(
    offering_id: int,
    payload: OfferingUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Offerings", "Edit")),
) -> OfferingRead:
    """Change the recommended proposal / expiry. Regenerates the PDF when needed."""
    offering = _get_offering(db, offering_id, broker_id)
    data = payload.model_dump(exclude_unset=True)

    regenerate = False
    if "selected_proposal_id" in data:
        if data["selected_proposal_id"] is not None:
            _require_proposal(
                db,
                data["selected_proposal_id"],
                broker_id,
                offering.quote_request_id,
            )
        regenerate = data["selected_proposal_id"] != offering.selected_proposal_id

    for field, value in data.items():
        setattr(offering, field, value)

    if regenerate:
        doc = _generate_pdf_document(db, offering, user_id=current_user.id)
        offering.pdf_document_id = doc.id

    db.commit()
    db.refresh(offering)
    return _to_read(offering)


@router.post("/{offering_id}/send", response_model=OfferingRead)
def record_offering_sent(
    offering_id: int,
    payload: OfferingSend,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Offerings", "Submit")),
) -> OfferingRead:
    """Record HOW the offering was delivered: whatsapp | email | download | link.

    Radal does not send the message itself in this pass — the broker shares the
    link — so this is an explicit audit stamp, not a delivery trigger.
    """
    offering = _get_offering(db, offering_id, broker_id)
    if offering.selected_proposal_id is None:
        raise HTTPException(
            status_code=422,
            detail="Select a recommended proposal before sending the offering",
        )
    expires_at = _as_aware(offering.expires_at)
    if expires_at is not None and expires_at <= _utcnow():
        raise HTTPException(status_code=409, detail="This offering has expired")

    offering.sent_via = payload.channel
    offering.sent_at = payload.sent_at or _utcnow()
    if offering.status in (OfferingStatus.DRAFT, OfferingStatus.EXPIRED):
        offering.status = OfferingStatus.SENT
    db.commit()
    db.refresh(offering)
    return _to_read(offering)


@router.get("/{offering_id}/pdf", response_model=DocumentDownload)
def download_offering_pdf(
    offering_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Offerings", "View")),
) -> DocumentDownload:
    """A link to the generated PDF (presigned on S3, static in local dev)."""
    offering = _get_offering(db, offering_id, broker_id)
    if offering.pdf_document_id is None:
        raise HTTPException(status_code=404, detail="This offering has no PDF yet")
    doc = db.get(Document, offering.pdf_document_id)
    if doc is None or doc.broker_id != broker_id:
        raise HTTPException(status_code=404, detail="This offering has no PDF yet")
    return build_download_payload(doc)


@router.post("/{offering_id}/pdf", response_model=OfferingRead)
def regenerate_offering_pdf(
    offering_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Offerings", "Edit")),
) -> OfferingRead:
    """Rebuild the PDF from the current proposal figures (same fixed S3 route)."""
    offering = _get_offering(db, offering_id, broker_id)
    doc = _generate_pdf_document(db, offering, user_id=current_user.id)
    offering.pdf_document_id = doc.id
    db.commit()
    db.refresh(offering)
    return _to_read(offering)


@router.delete("/{offering_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_offering(
    offering_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Offerings", "Delete")),
) -> Response:
    """Delete a draft offering. Once shared it is audit history — keep it."""
    offering = _get_offering(db, offering_id, broker_id)
    if offering.status is not OfferingStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Offering {offering.id} is {offering.status.value}; a shared "
                "offering is kept for audit"
            ),
        )
    # Detach the PDF first: DELETE /documents refuses a referenced document.
    # The document row itself stays in the file registry and is removed there.
    offering.pdf_document_id = None
    db.delete(offering)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Public surface — the share token IS the credential
# =============================================================================

@public_router.get("/{share_token}", response_model=OfferingPublicRead)
def get_offering_by_token(
    share_token: str,
    db: Session = Depends(get_db),
) -> OfferingPublicRead:
    """Resolve a share link. UNAUTHENTICATED by design — no broker scoping.

    Only the narrow public projection is returned. A draft is treated as not yet
    shared (404) and an expired link answers 410 Gone.
    """
    offering = db.scalar(select(Offering).where(Offering.share_token == share_token))
    if offering is None or offering.status is OfferingStatus.DRAFT:
        raise HTTPException(status_code=404, detail="Offering not found")

    expires_at = _as_aware(offering.expires_at)
    if expires_at is not None and expires_at <= _utcnow():
        if offering.status is not OfferingStatus.EXPIRED:
            offering.status = OfferingStatus.EXPIRED
            db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This offering has expired")

    if offering.viewed_at is None:
        offering.viewed_at = _utcnow()
    if offering.status is OfferingStatus.SENT:
        offering.status = OfferingStatus.VIEWED
    db.commit()
    db.refresh(offering)

    quote = db.get(QuoteRequest, offering.quote_request_id)
    broker = db.get(Broker, offering.broker_id)

    recommended = None
    if offering.selected_proposal_id is not None:
        recommended = db.get(Proposal, offering.selected_proposal_id)

    # EVERY open proposal of the quote, in clear language, so the insured can
    # compare and choose. Commission and broker-internal fields never appear.
    open_proposals = (
        db.scalars(
            select(Proposal)
            .where(
                Proposal.quote_request_id == offering.quote_request_id,
                Proposal.broker_id == offering.broker_id,
                Proposal.status.in_(_OPEN_PROPOSAL_STATUSES),
            )
            .order_by(Proposal.total_premium_uf.is_(None), Proposal.total_premium_uf.asc())
        ).all()
        if quote is not None
        else []
    )
    public_proposals = [
        _public_proposal(
            db, prop, recommended=(prop.id == offering.selected_proposal_id)
        )
        for prop in open_proposals
    ]
    public_proposal = (
        _public_proposal(db, recommended, recommended=True)
        if recommended is not None
        else None
    )

    pdf_url = None
    if offering.pdf_document_id is not None:
        doc = db.get(Document, offering.pdf_document_id)
        if doc is not None:
            pdf_url = get_document_url(doc)

    return OfferingPublicRead(
        share_token=offering.share_token,
        status=offering.status,
        sent_at=offering.sent_at,
        viewed_at=offering.viewed_at,
        expires_at=offering.expires_at,
        broker_name=(broker.trade_name or broker.legal_name) if broker else None,
        insured_object=quote.insured_object if quote else None,
        declared_value_uf=quote.declared_value_uf if quote else None,
        proposal=public_proposal,
        proposals=public_proposals,
        decided_proposal_id=offering.decided_proposal_id,
        decided_note=offering.decided_note,
        decided_at=offering.decided_at,
        pdf_url=pdf_url,
    )


@public_router.post("/{share_token}/decision", response_model=OfferingPublicRead)
def record_offering_decision(
    share_token: str,
    payload: OfferingDecisionRequest,
    db: Session = Depends(get_db),
) -> OfferingPublicRead:
    """Record the insured's choice. UNAUTHENTICATED — the token IS the credential.

    This is a PUBLIC WRITE path, so the narrow projection is the only guard: the
    body may only name a ``proposal_id`` that belongs to THIS offering's quote
    (else 422), and the response never carries a broker-internal field. It is
    IDEMPOTENT — re-posting the same choice is a 200 no-op; changing it is
    allowed until the broker acts on the decision. It does NOT advance the case
    stage: the broker picks the decision up and mints the broker_proposal.
    """
    offering = db.scalar(select(Offering).where(Offering.share_token == share_token))
    if offering is None or offering.status is OfferingStatus.DRAFT:
        raise HTTPException(status_code=404, detail="Offering not found")

    expires_at = _as_aware(offering.expires_at)
    if expires_at is not None and expires_at <= _utcnow():
        if offering.status is not OfferingStatus.EXPIRED:
            offering.status = OfferingStatus.EXPIRED
            db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This offering has expired")

    # The chosen proposal must belong to this offering's quote (tenant + quote
    # scoped). A foreign or unrelated proposal is a 422, never a silent accept.
    proposal = db.scalar(
        select(Proposal).where(
            Proposal.id == payload.proposal_id,
            Proposal.broker_id == offering.broker_id,
            Proposal.quote_request_id == offering.quote_request_id,
        )
    )
    if proposal is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Proposal {payload.proposal_id} does not belong to this offering's "
                "quote"
            ),
        )

    already = (
        offering.decided_proposal_id == proposal.id
        and (offering.decided_note or None) == (payload.note or None)
    )
    if not already:
        offering.decided_proposal_id = proposal.id
        offering.decided_note = payload.note
        offering.decided_at = _utcnow()
        if offering.status is not OfferingStatus.ACCEPTED:
            offering.status = OfferingStatus.ACCEPTED
        db.commit()
        db.refresh(offering)

    return get_offering_by_token(share_token, db=db)


__all__ = ["router", "public_router", "build_offering_pdf"]


def _public_proposal(
    db: Session, proposal: Proposal, *, recommended: bool
) -> OfferingPublicProposal:
    """One proposal's headline figures for the public surface — no commission."""
    insurer = db.get(Insurer, proposal.insurer_id)
    return OfferingPublicProposal(
        id=proposal.id,
        insurer_name=(insurer.trade_name or insurer.legal_name) if insurer else None,
        insurer_cmf_code=insurer.cmf_code if insurer else None,
        modality=proposal.modality,
        is_recommended=recommended,
        total_premium_uf=proposal.total_premium_uf,
        net_premium_uf=proposal.net_premium_uf,
        vat_uf=proposal.vat_uf,
        comprehensive_rate_permille=proposal.comprehensive_rate_permille,
        validity_business_days=proposal.validity_business_days,
        coverage_start=proposal.coverage_start,
        coverage_end=proposal.coverage_end,
    )
