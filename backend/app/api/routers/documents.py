"""``/documents`` — upload, list, download and delete stored files.

**THIS MODULE IS THE ONLY PLACE AN S3 KEY IS CREATED.** Rule 8 of the rebuild:
``document.s3_key`` is the single column in the whole schema that holds a key,
and no client may supply one. Every key is DERIVED here from the official S3
layout (docs/v2-architecture.md §6)::

    documents/{entity_type}/{entity_id}/{category}-{n}.{ext}

``n`` is the next free ordinal for that (entity, category) triple, so re-uploading
a second proposal PDF onto the same placement yields ``proposal-2.pdf``.

Images uploaded as a ``logo`` go through the WEBP 512x512 pipeline in
``app.services.media`` instead and land under the separate ``media/`` namespace —
that prefix is the deterministic derived-avatar route, not a document route. The
row is still recorded in ``document`` so there is exactly one file registry.

Other routers must never write to S3 themselves: they call
:func:`store_generated_document`, which funnels through the same key builder.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import posixpath
import re
from dataclasses import dataclass

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db
from app.core.case_scope import case_view_scope, scope_case_query
from app.core.config import settings
from app.core.permissions import require_permission
from app.models import (
    Asset,
    Broker,
    Claim,
    Client,
    Document,
    DocumentCategory,
    Inspection,
    InspectionRequest,
    InsuranceLine,
    Insured,
    Insurer,
    Offering,
    Placement,
    Policy,
    Proposal,
    QuoteRequest,
    User,
)
from app.models.case_file import CaseFile
from app.models.collection import CollectionPlan
from app.models.endorsement import Endorsement
from app.models.enums import CaseSection, EntityType
from app.models.sales_lead import SalesLead
from app.models.warranty import Warranty
from app.schemas.document import (
    DocumentDownload,
    DocumentListResponse,
    DocumentRead,
    DocumentUpdate,
)
from app.schemas.extraction.registry import (
    UnknownCategory,
    category_for_code,
    spec_for,
)
from app.services import media

router = APIRouter(prefix="/documents", tags=["documents"])

DOWNLOAD_URL_TTL_SECONDS = 900  # 15 minutes for a presigned S3 GET

# Extensions we accept, mapped from the declared MIME when the filename has none.
_MIME_TO_EXT = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/tiff": "tif",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/json": "json",
    "application/zip": "zip",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}

_IMAGE_MIME_PREFIX = "image/"
_SAFE_EXT_RE = re.compile(r"^[A-Za-z0-9]{1,12}$")


# =============================================================================
# Entity resolution — a document may only be attached inside the caller's broker
# =============================================================================

@dataclass(frozen=True)
class _EntityRule:
    model: type
    #: Workspace rows carry broker_id and are filtered by it.
    broker_scoped: bool = True
    #: Canonical rows (insured, insurer) are cross-broker by design.
    canonical: bool = False


_ENTITY_RULES: dict[EntityType, _EntityRule] = {
    EntityType.BROKER: _EntityRule(Broker, broker_scoped=False),
    EntityType.USER: _EntityRule(User),
    EntityType.CLIENT: _EntityRule(Client),
    EntityType.INSURED: _EntityRule(Insured, broker_scoped=False, canonical=True),
    EntityType.INSURER: _EntityRule(Insurer, broker_scoped=False, canonical=True),
    EntityType.ASSET: _EntityRule(Asset),
    EntityType.INSURANCE_LINE: _EntityRule(InsuranceLine, broker_scoped=False),
    EntityType.PLACEMENT: _EntityRule(Placement),
    EntityType.QUOTE_REQUEST: _EntityRule(QuoteRequest),
    EntityType.PROPOSAL: _EntityRule(Proposal),
    EntityType.INSPECTION_REQUEST: _EntityRule(InspectionRequest),
    EntityType.INSPECTION: _EntityRule(Inspection),
    EntityType.POLICY: _EntityRule(Policy),
    EntityType.CLAIM: _EntityRule(Claim),
    EntityType.OFFERING: _EntityRule(Offering),
    # --- Case files (v2 expedientes) ---------------------------------------
    EntityType.CASE_FILE: _EntityRule(CaseFile),
    EntityType.SALES_LEAD: _EntityRule(SalesLead),
    EntityType.ENDORSEMENT: _EntityRule(Endorsement),
    EntityType.COLLECTION_PLAN: _EntityRule(CollectionPlan),
    EntityType.WARRANTY: _EntityRule(Warranty),
}


def resolve_entity(
    db: Session, entity_type: EntityType, entity_id: int, broker_id: int
) -> object:
    """Return the target row, or raise 404 when it is absent / another broker's.

    Deliberately 404 (not 403) for a foreign row: existence outside the tenant is
    not information the caller is entitled to.
    """
    rule = _ENTITY_RULES.get(entity_type)
    if rule is None:  # pragma: no cover - EntityType is exhaustively mapped
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Documents cannot be attached to '{entity_type.value}'",
        )

    row = db.get(rule.model, entity_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.value} {entity_id} not found",
        )

    if entity_type is EntityType.BROKER and row.id != broker_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.value} {entity_id} not found",
        )

    if rule.broker_scoped and getattr(row, "broker_id", None) != broker_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.value} {entity_id} not found",
        )

    # insurance_line: broker_id NULL means the global catalog row — allowed.
    if entity_type is EntityType.INSURANCE_LINE:
        line_broker = getattr(row, "broker_id", None)
        if line_broker is not None and line_broker != broker_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{entity_type.value} {entity_id} not found",
            )

    return row


# =============================================================================
# Key derivation + storage (the only S3 write path)
# =============================================================================

def _document_prefix() -> str:
    return (settings.DOCUMENT_S3_PREFIX or "documents/").strip("/") + "/"


def _extension_for(filename: str | None, mime_type: str | None) -> str:
    """Pick a safe lowercase extension for the stored object."""
    if filename:
        ext = posixpath.splitext(filename)[1].lstrip(".").lower()
        if _SAFE_EXT_RE.match(ext):
            return ext
    if mime_type:
        mapped = _MIME_TO_EXT.get(mime_type.split(";")[0].strip().lower())
        if mapped:
            return mapped
        guessed = mimetypes.guess_extension(mime_type.split(";")[0].strip())
        if guessed:
            ext = guessed.lstrip(".").lower()
            if _SAFE_EXT_RE.match(ext):
                return ext
    return "bin"


def build_document_key(
    db: Session,
    *,
    entity_type: EntityType,
    entity_id: int,
    category: DocumentCategory,
    extension: str,
) -> str:
    """Derive the next free ``documents/{type}/{id}/{category}-{n}.{ext}`` key.

    The ordinal starts at the current count for that (entity, category) pair and
    walks forward until the key is unused — ``document.s3_key`` is UNIQUE, so a
    collision after a delete/re-upload must never raise.
    """
    prefix = f"{_document_prefix()}{entity_type.value}/{entity_id}/"
    existing = db.scalar(
        select(func.count(Document.id)).where(
            Document.entity_type == entity_type,
            Document.entity_id == entity_id,
            Document.category == category,
        )
    )
    n = int(existing or 0) + 1
    while True:
        key = f"{prefix}{category.value}-{n}.{extension}"
        taken = db.scalar(select(Document.id).where(Document.s3_key == key))
        if taken is None:
            return key
        n += 1


def _store_bytes(key: str, data: bytes, content_type: str | None) -> str:
    """Persist the bytes under ``key`` and return a URL for them.

    Backend is chosen by ``MEDIA_BACKEND`` (``local`` for dev, ``s3`` on AWS),
    the same switch the media service uses, so a dev box never needs credentials.
    """
    backend = (settings.MEDIA_BACKEND or "local").lower()
    if backend == "s3":
        try:
            import boto3  # noqa: PLC0415  (lazy optional dep)
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="S3 storage unavailable: boto3 is not installed",
            ) from exc
        try:
            client = boto3.client("s3", region_name=settings.S3_REGION)
            client.put_object(
                Bucket=settings.S3_BUCKET,
                Key=key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not store the file: {exc}",
            ) from exc
        return f"https://{settings.S3_BUCKET}.s3.{settings.S3_REGION}.amazonaws.com/{key}"

    path = os.path.join(settings.MEDIA_LOCAL_DIR, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return f"{settings.MEDIA_PUBLIC_BASE_URL.rstrip('/')}/{key}"


def _delete_bytes(key: str) -> None:
    """Best-effort removal of the stored object; never fails the request."""
    backend = (settings.MEDIA_BACKEND or "local").lower()
    if backend == "s3":
        try:
            import boto3  # noqa: PLC0415

            boto3.client("s3", region_name=settings.S3_REGION).delete_object(
                Bucket=settings.S3_BUCKET, Key=key
            )
        except Exception:  # noqa: BLE001 - orphaned object is preferable to a 500
            return
        return
    try:
        os.remove(os.path.join(settings.MEDIA_LOCAL_DIR, key))
    except OSError:
        return


def _normalize_media_key(media_key: str) -> str:
    """Return the FULL object key for something the media service just stored.

    ``media.process_and_store_avatar`` returns a key relative to its own prefix:
    its S3 backend prepends ``MEDIA_S3_PREFIX`` while its local backend does not.
    ``document.s3_key`` must always be the real, resolvable key, so we normalize
    to the S3 route (``media/...``) and, on the local backend, move the file so
    key and path agree in both environments.
    """
    prefix = (settings.MEDIA_S3_PREFIX or "media/").strip("/")
    full_key = f"{prefix}/{media_key}"
    if (settings.MEDIA_BACKEND or "local").lower() != "s3":
        src = os.path.join(settings.MEDIA_LOCAL_DIR, media_key)
        dst = os.path.join(settings.MEDIA_LOCAL_DIR, full_key)
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
    return full_key


def _download_url(doc: Document) -> tuple[str, int | None]:
    """Return ``(url, ttl_seconds)`` for the stored bytes."""
    backend = (settings.MEDIA_BACKEND or "local").lower()
    if backend == "s3":
        try:
            import boto3  # noqa: PLC0415

            client = boto3.client("s3", region_name=settings.S3_REGION)
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": doc.bucket, "Key": doc.s3_key},
                ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not sign the download URL: {exc}",
            ) from exc
        return url, DOWNLOAD_URL_TTL_SECONDS
    # Local dev: nothing serves MEDIA_LOCAL_DIR statically, so point at the
    # broker-scoped content route, which streams the same bytes back.
    return f"{settings.API_V1_PREFIX}/documents/{doc.id}/content", None


def store_generated_document(
    db: Session,
    *,
    broker_id: int,
    entity_type: EntityType,
    entity_id: int,
    category: DocumentCategory,
    data: bytes,
    original_name: str,
    mime_type: str,
    key: str | None = None,
    phase: str | None = None,
    uploaded_by_id: int | None = None,
) -> Document:
    """Register a SERVER-GENERATED file (e.g. an offering PDF) as a document.

    Other routers call this instead of touching S3, so key derivation stays in
    one place. ``key`` may be supplied when the architecture defines a fixed
    route for that artefact (``offerings/{id}/offering.pdf``); otherwise the
    standard ``documents/...`` key is derived. The caller commits.
    """
    if key is None:
        key = build_document_key(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            category=category,
            extension=_extension_for(original_name, mime_type),
        )
    else:
        existing = db.scalar(select(Document).where(Document.s3_key == key))
        if existing is not None:
            # Fixed-route artefact being regenerated: overwrite bytes, reuse row.
            _store_bytes(key, data, mime_type)
            existing.original_name = original_name
            existing.mime_type = mime_type
            existing.size_bytes = len(data)
            existing.checksum = hashlib.sha256(data).hexdigest()
            db.flush()
            return existing

    _store_bytes(key, data, mime_type)
    doc = Document(
        broker_id=broker_id,
        entity_type=entity_type,
        entity_id=entity_id,
        s3_key=key,
        bucket=settings.S3_BUCKET,
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        category=category,
        phase=phase,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(doc)
    db.flush()
    return doc


def get_document_url(doc: Document) -> str:
    """Public helper for other routers that need a link to a document."""
    url, _ = _download_url(doc)
    return url


def build_download_payload(doc: Document) -> DocumentDownload:
    """The shared ``DocumentDownload`` shape, so every router links files alike."""
    url, ttl = _download_url(doc)
    return DocumentDownload(
        id=doc.id,
        original_name=doc.original_name,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        s3_key=doc.s3_key,
        url=url,
        expires_in_seconds=ttl,
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    entity_type: EntityType = Form(...),
    entity_id: int = Form(..., gt=0),
    category: DocumentCategory = Form(DocumentCategory.OTHER),
    phase: str | None = Form(default=None),
    case_file_id: int | None = Form(default=None),
    section: CaseSection | None = Form(default=None),
    document_code: str | None = Form(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Documents", "Upload")),
) -> Document:
    """Upload a file, store it under the official S3 route, register the row.

    ``case_file_id`` / ``section`` / ``document_code`` file the document inside an
    expediente; the case is proved to belong to the same broker first.
    """
    resolve_entity(db, entity_type, entity_id, broker_id)
    _validate_case_file(db, case_file_id, broker_id, current_user)
    if entity_type is EntityType.CASE_FILE and case_file_id is None:
        case_file_id = entity_id

    category, derived_section, document_code = _classify(
        category, section, document_code
    )
    # A section only means something inside an expediente; outside one the column
    # stays NULL so the plain document list is unchanged.
    section = derived_section if case_file_id is not None else section

    raw = file.file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Empty file"
        )
    max_bytes = settings.DOCUMENT_MAX_UPLOAD_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {settings.DOCUMENT_MAX_UPLOAD_MB} MB)",
        )

    original_name = (file.filename or "upload").strip()[:255]
    mime_type = (file.content_type or "").split(";")[0].strip().lower() or None

    is_logo_image = (
        category is DocumentCategory.LOGO
        and mime_type is not None
        and mime_type.startswith(_IMAGE_MIME_PREFIX)
    )

    if is_logo_image:
        # Logos/avatars are derived assets: WEBP 512x512 under the media/ prefix.
        try:
            stored = media.process_and_store_avatar(
                raw,
                entity_type=entity_type.value,
                entity_id=entity_id,
                content_type=mime_type,
            )
        except media.MediaError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        key = _normalize_media_key(stored.key)
        stored_mime = "image/webp"
        # The bytes were re-encoded, so the upload size is not the stored size.
        stored_size: int | None = None
    else:
        key = build_document_key(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            category=category,
            extension=_extension_for(original_name, mime_type),
        )
        _store_bytes(key, raw, mime_type)
        stored_mime = mime_type
        stored_size = len(raw)

    doc = Document(
        broker_id=broker_id,
        entity_type=entity_type,
        entity_id=entity_id,
        s3_key=key,
        bucket=settings.S3_BUCKET,
        original_name=original_name,
        mime_type=stored_mime,
        size_bytes=stored_size,
        checksum=hashlib.sha256(raw).hexdigest(),
        category=category,
        phase=phase,
        case_file_id=case_file_id,
        section=section,
        document_code=document_code,
        uploaded_by_id=current_user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _classify(
    category: DocumentCategory,
    section: CaseSection | None,
    document_code: str | None,
) -> tuple[DocumentCategory, CaseSection | None, str | None]:
    """Fill the blanks in ``(category, section, document_code)`` from the registry.

    Upload auto-detection, spec §3.1: the client sends what it knows and the
    category registry supplies the rest. A code alone (``document_code="00A"``)
    resolves the category; a category alone supplies the section and the
    canonical code. Anything the caller stated explicitly always wins.
    """
    code = (document_code or "").strip().upper() or None

    if category is DocumentCategory.OTHER and code:
        detected = category_for_code(code, section)
        if detected is not None:
            category = detected

    try:
        spec = spec_for(category)
    except UnknownCategory:
        # Legacy categories (cmf_certificate, logo, evidence, …) carry no section.
        return category, section, code

    if section is None:
        section = spec.section
    if code is None:
        code = spec.code
    return category, section, code


def _visible_case_ids(db: Session, broker_id: int, user: "User | None"):
    """SELECT of the case ids this caller may see (spec v3 §6).

    Shares ``CASE_VIEW_SCOPE`` with ``case_files._visible()``, so a role whose
    ``CaseFiles.View`` grant is ``partial`` cannot file into — or list the
    documents of — a case its own list would hide.
    """
    stmt = select(CaseFile.id).where(CaseFile.broker_id == broker_id)
    return scope_case_query(stmt, broker_id=broker_id, user=user)


def _validate_case_file(
    db: Session,
    case_file_id: int | None,
    broker_id: int,
    user: "User | None" = None,
) -> None:
    """A document may only be filed inside an expediente the caller can see."""
    if case_file_id is None:
        return
    found = db.scalar(
        _visible_case_ids(db, broker_id, user).where(CaseFile.id == case_file_id)
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"case_file {case_file_id} not found",
        )


@router.get("", response_model=DocumentListResponse)
def list_documents(
    entity_type: EntityType | None = Query(default=None),
    entity_id: int | None = Query(default=None, gt=0),
    category: list[DocumentCategory] | None = Query(default=None),
    section: list[CaseSection] | None = Query(default=None),
    case_file_id: int | None = Query(default=None, gt=0),
    document_code: str | None = Query(default=None, max_length=8),
    phase: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "View")),
) -> DocumentListResponse:
    """List the broker's documents, optionally narrowed to one entity or case file.

    ``category`` and ``section`` accept repeats (``?category=a&category=b``), which
    is what the expediente's section accordion needs to fetch one sub-expediente
    at a time.
    """
    if entity_id is not None and entity_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entity_id requires entity_type",
        )

    filters = [Document.broker_id == broker_id]
    if entity_type is not None:
        filters.append(Document.entity_type == entity_type)
    if entity_id is not None:
        filters.append(Document.entity_id == entity_id)
    if category:
        filters.append(Document.category.in_(category))
    if section:
        filters.append(Document.section.in_(section))
    if case_file_id is not None:
        filters.append(Document.case_file_id == case_file_id)
    if _case_narrowing_applies(_user):
        # A narrowed CaseFiles.View must not leak an invisible case's documents
        # — with or without an explicit ``case_file_id`` filter. Documents that
        # belong to no expediente are untouched by the narrowing.
        filters.append(
            or_(
                Document.case_file_id.is_(None),
                Document.case_file_id.in_(_visible_case_ids(db, broker_id, _user)),
            )
        )
    if document_code is not None:
        filters.append(Document.document_code == document_code)
    if phase is not None:
        filters.append(Document.phase == phase)

    total = db.scalar(select(func.count(Document.id)).where(*filters)) or 0
    rows = db.scalars(
        select(Document)
        .where(*filters)
        .order_by(Document.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return DocumentListResponse(
        total=int(total), items=[DocumentRead.model_validate(r) for r in rows]
    )


def _get_owned_document(db: Session, document_id: int, broker_id: int) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.broker_id != broker_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return doc


def _case_narrowing_applies(user: "User | None") -> bool:
    """True when this caller's ``CaseFiles.View`` grant is a NARROWED one.

    Cheap and role-only: it never touches the database, so the un-narrowed
    majority (admin, executive, technician) pays nothing for the guard.
    """
    from app.core.permissions import resolve_role  # noqa: PLC0415  (import cycle)

    if user is None:
        return False
    return case_view_scope(resolve_role(user)) is not None


def _get_visible_document(
    db: Session, document_id: int, broker_id: int, user: "User | None" = None
) -> Document:
    """``_get_owned_document`` plus the case-file narrowing (spec v3 §6).

    A document filed inside an expediente the caller cannot see is a **404** —
    without this a process profile could read, download or stream exactly the
    case material its own listing hides, which is the same hole
    ``get_case_or_404(user)`` closes on the case itself.

    Documents with no ``case_file_id`` (client sheets, asset photos, policy
    PDFs) stay reachable: they belong to no expediente, so the ``Documents``
    grant is the only gate on them.
    """
    doc = _get_owned_document(db, document_id, broker_id)
    if doc.case_file_id is None or not _case_narrowing_applies(user):
        return doc
    found = db.scalar(
        _visible_case_ids(db, broker_id, user).where(CaseFile.id == doc.case_file_id)
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return doc


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "View")),
) -> Document:
    return _get_visible_document(db, document_id, broker_id, _user)


@router.get("/{document_id}/download", response_model=DocumentDownload)
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "View")),
) -> DocumentDownload:
    """Return a short-lived signed URL (S3) or the local content route (dev)."""
    return build_download_payload(
        _get_visible_document(db, document_id, broker_id, _user)
    )


@router.get("/{document_id}/content")
def document_content(
    document_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "View")),
) -> Response:
    """Stream the stored bytes back through the API.

    On S3 the client follows the presigned URL directly and never comes here.
    On the local backend there is no object store to sign against, so this is
    the route ``build_download_payload`` points at — still broker-scoped, so a
    dev download can never cross tenants the way a bare static mount would.
    """
    doc = _get_visible_document(db, document_id, broker_id, _user)
    if (settings.MEDIA_BACKEND or "local").lower() == "s3":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use the presigned URL from /download on the S3 backend",
        )
    path = os.path.join(settings.MEDIA_LOCAL_DIR, doc.s3_key)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stored file for document {doc.id} is missing",
        ) from exc
    filename = (doc.original_name or f"document-{doc.id}").replace('"', "")
    return Response(
        content=data,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: int,
    payload: DocumentUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "Edit")),
) -> Document:
    """Patch metadata only. The key and the bytes are immutable by design."""
    doc = _get_visible_document(db, document_id, broker_id, _user)
    changes = payload.model_dump(exclude_unset=True)
    if "case_file_id" in changes:
        _validate_case_file(db, changes["case_file_id"], broker_id, _user)
    for field, value in changes.items():
        setattr(doc, field, value)
    # Re-categorising a case document re-files it: the section follows the new
    # category unless the caller placed it in one explicitly.
    if "category" in changes and "section" not in changes and doc.case_file_id:
        # The old code belonged to the old category — only an explicit one survives.
        stated_code = changes.get("document_code")
        _, doc.section, doc.document_code = _classify(doc.category, None, stated_code)
    db.commit()
    db.refresh(doc)
    return doc


#: Every FK that points at ``document``. A referenced document cannot be deleted.
_DOCUMENT_REFERENCES: tuple[tuple[type, str, str], ...] = (
    (Proposal, "source_document_id", "proposal"),
    (Inspection, "report_document_id", "inspection"),
    (Placement, "brief_document_id", "placement"),
    (Offering, "pdf_document_id", "offering"),
)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Documents", "Delete")),
) -> Response:
    """Delete the row and the stored bytes, unless an entity still references it."""
    doc = _get_visible_document(db, document_id, broker_id, _user)

    for model, column, label in _DOCUMENT_REFERENCES:
        referrer = db.scalar(
            select(model.id).where(getattr(model, column) == doc.id).limit(1)
        )
        if referrer is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Document {doc.id} is still referenced by {label} {referrer} "
                    f"({column}); detach it first"
                ),
            )

    key = doc.s3_key
    db.delete(doc)
    db.commit()
    _delete_bytes(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = [
    "router",
    "build_document_key",
    "store_generated_document",
    "get_document_url",
    "build_download_payload",
    "resolve_entity",
]
