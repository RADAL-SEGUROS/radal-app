"""``/case-files/{id}/packs`` and ``/packs/{id}`` — the generated downloadables.

Generation is synchronous and bounded (spec §8.4): a failure marks the pack
``failed`` with its error and answers **502**; a ZIP over ``settings.PACK_MAX_MB``
answers **413**. The bytes never touch S3 from here — ``app.services.packs``
funnels everything through ``documents.store_generated_document``.

Sending the carpeta by email is out of scope this pass; the UI renders that
control disabled with a "pronto" chip rather than as a dead button, so there is
deliberately no ``POST /packs/{id}/send`` here.

Gates: generating a pack is ``CaseFiles.Submit`` — the pack is the headline
broker deliverable, and executives and technicians (who both hold Submit) build
it day to day; reserving it to ``Manage`` made it admin-only. Downloading stays
on ``Documents.View``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.case_files import get_case_or_404
from app.api.routers.clients import record_activity
from app.api.routers.documents import build_download_payload
from app.core.case_scope import scope_case_query
from app.models.case_file import CaseFile, CasePack
from app.models.document import Document
from app.models.enums import EntityType, PackKind
from app.models.user import User
from app.schemas.document import DocumentDownload
from app.schemas.pack import (
    CasePackList,
    CasePackRead,
    PackSummaryConfirm,
    RecipientList,
    RecipientRead,
)
from app.services import packs as pack_service

router = APIRouter(prefix="/case-files", tags=["packs"])

#: Pack-scoped routes (download, summary confirmation) hang off ``/packs``.
packs_router = APIRouter(prefix="/packs", tags=["packs"])


# --- Helpers -------------------------------------------------------------------

def _download(db: Session, document_id: int | None) -> DocumentDownload | None:
    if document_id is None:
        return None
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    return build_download_payload(doc)


def _read(db: Session, pack: CasePack) -> CasePackRead:
    payload = CasePackRead.model_validate(pack)
    raw = pack.recipients if isinstance(pack.recipients, list) else []
    payload.recipients = [
        RecipientRead(**item) for item in raw if isinstance(item, dict)
    ]
    payload.pdf = _download(db, pack.pdf_document_id)
    payload.zip = _download(db, pack.zip_document_id)
    return payload


def _get_pack(
    db: Session, pack_id: int, broker_id: int, user: User | None = None
) -> CasePack:
    """Tenant-scoped pack fetch, narrowed to the caller's visible cases.

    ``user`` applies the SAME ``CASE_VIEW_SCOPE`` narrowing the case-file list
    applies (spec v3 §6): a pack is a view of its expediente, so a role that
    cannot see the case must not read its pack by id either.
    """
    stmt = select(CasePack).where(
        CasePack.id == pack_id, CasePack.broker_id == broker_id
    )
    if user is not None:
        visible_cases = scope_case_query(
            select(CaseFile.id).where(CaseFile.broker_id == broker_id),
            broker_id=broker_id,
            user=user,
        )
        stmt = stmt.where(CasePack.case_file_id.in_(visible_cases))
    pack = db.scalars(stmt).first()
    if pack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pack not found")
    return pack


def _generate(
    db: Session, *, case_id: int, broker_id: int, kind: PackKind, user: User
) -> CasePackRead:
    case = get_case_or_404(db, case_id, broker_id, user)
    builder = pack_service.PACK_BUILDERS[kind]
    try:
        pack = builder(db, case=case, user=user)
    except pack_service.PackTooLargeError as exc:
        db.commit()  # keep case_pack.status = failed + the error message
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except pack_service.PackError as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    record_activity(
        db,
        broker_id=broker_id,
        user=user,
        action="case_pack.generated",
        entity_type=EntityType.CASE_FILE,
        entity_id=case.id,
        description=f"Carpeta '{kind.value}' generada",
        meta={"pack_id": pack.id, "kind": kind.value},
    )
    db.commit()
    db.refresh(pack)
    return _read(db, pack)


# --- Endpoints -----------------------------------------------------------------

@router.get("/{case_id}/packs", response_model=CasePackList)
def list_case_packs(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("CaseFiles", "View")),
) -> CasePackList:
    """Every pack generated for this expediente, with its download URLs."""
    case = get_case_or_404(db, case_id, broker_id, _user)
    rows = db.scalars(
        select(CasePack)
        .where(CasePack.case_file_id == case.id)
        .order_by(CasePack.kind.asc(), CasePack.id.asc())
    ).all()
    return CasePackList(case_file_id=case.id, items=[_read(db, row) for row in rows])


@router.post(
    "/{case_id}/packs/submission", response_model=CasePackRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_submission_pack(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> CasePackRead:
    """Master PDF + ZIP of the root and submission sections, plus the recipients."""
    return _generate(
        db, case_id=case_id, broker_id=broker_id, kind=PackKind.SUBMISSION,
        user=current_user,
    )


@router.post(
    "/{case_id}/packs/comparison", response_model=CasePackRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_comparison_pack(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> CasePackRead:
    """The comparativo handed to the insured — PDF only."""
    return _generate(
        db, case_id=case_id, broker_id=broker_id, kind=PackKind.COMPARISON,
        user=current_user,
    )


@router.post(
    "/{case_id}/packs/proposal", response_model=CasePackRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_proposal_pack(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> CasePackRead:
    """The confirmed 07 + the accompanying documents it lists."""
    return _generate(
        db, case_id=case_id, broker_id=broker_id, kind=PackKind.PROPOSAL,
        user=current_user,
    )


@router.get("/{case_id}/recipients", response_model=RecipientList)
def list_case_recipients(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Insurers", "View")),
) -> RecipientList:
    """Native insurers + the contact resolved broker+line → broker → line → global."""
    case = get_case_or_404(db, case_id, broker_id, _user)
    recipients = pack_service.resolve_recipients(
        db, broker_id=broker_id, insurance_line_id=case.insurance_line_id
    )
    return RecipientList(
        case_file_id=case.id,
        insurance_line_id=case.insurance_line_id,
        items=[RecipientRead(**item.as_dict()) for item in recipients],
    )


@packs_router.get("/{pack_id}", response_model=CasePackRead)
def get_pack(
    pack_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("CaseFiles", "View")),
) -> CasePackRead:
    return _read(db, _get_pack(db, pack_id, broker_id, _user))


@packs_router.post("/{pack_id}/summary/confirm", response_model=CasePackRead)
def confirm_pack_summary(
    pack_id: int,
    payload: PackSummaryConfirm,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "Submit")),
) -> CasePackRead:
    """Suggest → human edit → confirm, applied to the pack's Spanish prose."""
    pack = _get_pack(db, pack_id, broker_id, current_user)
    if payload.summary is not None:
        pack.summary = payload.summary
    if payload.is_summary_confirmed and not (pack.summary or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="There is no summary text to confirm",
        )
    pack.is_summary_confirmed = payload.is_summary_confirmed

    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="case_pack.summary_confirmed",
        entity_type=EntityType.CASE_FILE,
        entity_id=pack.case_file_id,
        description="Resumen de la carpeta confirmado",
        meta={"pack_id": pack.id, "confirmed": pack.is_summary_confirmed},
    )
    db.commit()
    db.refresh(pack)
    return _read(db, pack)


@packs_router.get("/{pack_id}/download", response_model=DocumentDownload)
def download_pack(
    pack_id: int,
    part: str = Query(default="pdf", pattern="^(pdf|zip)$"),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "View")),
) -> DocumentDownload:
    """A signed URL (S3) or the static URL (local dev) for one part of the pack."""
    pack = _get_pack(db, pack_id, broker_id, _user)
    document_id = pack.pdf_document_id if part == "pdf" else pack.zip_document_id
    payload = _download(db, document_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"This pack has no '{part}' part",
        )
    return payload


__all__ = ["router", "packs_router"]
