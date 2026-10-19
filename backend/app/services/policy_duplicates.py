"""Duplicate policy uploads (review round 2, owner decision D2).

``POST /policies/upload`` of a policy the broker ALREADY has is a **409
``policy_duplicate``** with no side effects: no notification, no plan, no
technical re-read, nothing confirmed reopened. Two checks, both broker-scoped:

* :func:`duplicate_by_checksum` — BEFORE the read: the uploaded bytes are the
  exact file an existing policy was registered from (SHA-256 on ``document``).
  No AI call at all.
* :func:`duplicate_by_number` — right AFTER the core read, before any commit:
  same ``policy_number`` and the same insurer when it is resolvable. The read
  insurer is resolved by normalised RUT / CMF code (rule 7 — never by name);
  when the PDF prints the insurer only by name, the same number plus an
  insurer whose NAME SUGGESTION matches the existing policy's insurer is
  enough (a suggestion is used only to *recognise* the existing row, never to
  pick an insurer).

A policy that merely stands in for the issued one (a draft, a provisional
certificate, a vía-Manual row with no PDF on file) is NOT a duplicate: the
issued document completes it, as before.

:func:`discard_request_document` removes the document of the refused request
when it is an orphan the same user uploaded moments ago (the re-upload copy
that would otherwise appear three times in Portafolio → Documentos).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.insurer import Insurer
from app.models.policy import Policy, PolicyStatus
from app.models.user import User

#: A document is "just uploaded" (and may be removed on a 409) for this long.
FRESH_UPLOAD_WINDOW = timedelta(minutes=60)

__all__ = [
    "FRESH_UPLOAD_WINDOW",
    "discard_request_document",
    "duplicate_by_checksum",
    "duplicate_by_number",
    "duplicate_detail",
]


def _is_registered(policy: Policy) -> bool:
    """An issued policy with its PDF on file — what a re-upload duplicates."""
    if policy.status == PolicyStatus.DRAFT or bool(getattr(policy, "is_provisional", False)):
        return False
    return policy.source_document_id is not None or bool(policy.has_source_document)


def duplicate_by_checksum(db: Session, *, broker_id: int, document: Document) -> Policy | None:
    """The broker's policy registered from a file with the SAME bytes, if any."""
    if not document.checksum:
        return None
    rows = db.scalars(
        select(Policy)
        .join(Document, Document.id == Policy.source_document_id)
        .where(
            Policy.broker_id == broker_id,
            Document.broker_id == broker_id,
            Document.checksum == document.checksum,
        )
        .order_by(Policy.id.asc())
    ).all()
    for policy in rows:
        if _is_registered(policy):
            return policy
    return None


def _insurer_by_identity(db: Session, ref: Any) -> tuple[bool, Insurer | None]:
    """``(has_identity, insurer)`` from the read RUT / CMF code — lookup only."""
    from app.services.identifiers import (  # noqa: PLC0415
        InvalidCmf,
        InvalidRut,
        normalize_codigo_cmf,
        validate_rut,
    )

    code_text = (getattr(ref, "cmf_code", None) or "").strip()
    rut_text = (getattr(ref, "rut", None) or "").strip()
    code = rut = None
    if code_text:
        try:
            code = normalize_codigo_cmf(code_text)
        except InvalidCmf:
            code = None
    if rut_text:
        try:
            rut = validate_rut(rut_text)
        except InvalidRut:
            rut = None
    if not code and not rut:
        return False, None
    if code:
        found = db.scalars(select(Insurer).where(Insurer.cmf_code == code)).first()
        if found is not None:
            return True, found
    if rut:
        found = db.scalars(select(Insurer).where(Insurer.rut == rut)).first()
        if found is not None:
            return True, found
    return True, None


def duplicate_by_number(
    db: Session,
    *,
    broker_id: int,
    payload: Any,
    chosen_insurer_id: int | None = None,
) -> Policy | None:
    """The broker's registered policy this read IS (see the module doc)."""
    from app.services.extraction_commit import (  # noqa: PLC0415 - heavy module
        _read_insurer_name,
        insurer_name_suggestions,
    )

    number = (getattr(payload, "policy_number", None) or "").strip()
    if not number:
        return None
    policy = db.scalars(
        select(Policy).where(Policy.broker_id == broker_id, Policy.policy_number == number)
    ).first()
    if policy is None or not _is_registered(policy):
        return None

    ref = getattr(payload, "insurer", None)
    has_identity, insurer = _insurer_by_identity(db, ref)
    if has_identity:
        # The PDF states who issued it: same company or not a duplicate.
        return policy if insurer is not None and insurer.id == policy.insurer_id else None
    if chosen_insurer_id is not None:
        return policy if chosen_insurer_id == policy.insurer_id else None
    read_name = _read_insurer_name(ref) if ref is not None else None
    if not read_name:
        # Nothing tells the two apart: the folio is the broker's policy.
        return policy
    suggestions = insurer_name_suggestions(db, broker_id, read_name, limit=10)
    return policy if any(s["id"] == policy.insurer_id for s in suggestions) else None


def duplicate_detail(db: Session, policy: Policy) -> dict[str, Any]:
    """The structured 409 body (Spanish ``reason``, shown verbatim)."""
    insurer = db.get(Insurer, policy.insurer_id) if policy.insurer_id else None
    insurer_name = (insurer.trade_name or insurer.legal_name) if insurer is not None else None
    company = insurer_name or "la compañía registrada"
    return {
        "code": "policy_duplicate",
        "reason": f"Esta póliza ya existe: N° {policy.policy_number} de {company.rstrip('.')}.",
        "policy_id": policy.id,
        "policy_number": policy.policy_number,
        "insurer_name": insurer_name,
        "case_file_id": policy.case_file_id,
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _referenced(db: Session, table_name: str, row_id: int, *, skip: set[str]) -> bool:
    """True when any FK column in the schema points at ``table_name.id = row_id``
    (tables in ``skip`` excluded). Read from the metadata, so a new FK to
    ``document`` is covered without touching this module."""
    from app.models.base_class import Base  # noqa: PLC0415

    for table in Base.metadata.sorted_tables:
        if table.name in skip:
            continue
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.column.table.name != table_name or fk.column.name != "id":
                    continue
                hit = db.execute(
                    select(column).where(column == row_id).limit(1)
                ).first()
                if hit is not None:
                    return True
    return False


def discard_request_document(
    db: Session,
    *,
    document: Document,
    user: User,
    now: datetime | None = None,
) -> bool:
    """Remove the refused request's document when it is a fresh orphan.

    Only when the SAME user uploaded it within :data:`FRESH_UPLOAD_WINDOW` and
    nothing references it (no policy/plan/proposal/… FK; its own extraction
    rows excepted, and those only when nothing references THEM). Commits and
    removes the stored bytes. Returns ``True`` when the document is gone.
    """
    from app.models.ai import Extraction  # noqa: PLC0415

    moment = now or datetime.now(timezone.utc)
    created = _aware(document.created_at)
    if document.uploaded_by_id != user.id or created is None:
        return False
    if moment - created > FRESH_UPLOAD_WINDOW:
        return False
    if _referenced(db, "document", document.id, skip={"extraction"}):
        return False
    extractions = db.scalars(
        select(Extraction).where(Extraction.document_id == document.id)
    ).all()
    for row in extractions:
        if _referenced(db, "extraction", row.id, skip=set()):
            return False

    from app.api.routers.documents import _delete_bytes  # noqa: PLC0415 - storage lives there

    key = document.s3_key
    for row in extractions:
        db.delete(row)
    db.flush()
    db.delete(document)
    db.commit()
    _delete_bytes(key)
    return True
