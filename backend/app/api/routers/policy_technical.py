"""``/policies/{id}/technical-read`` — the policy's technical data, read as a
SUGGESTION in a second request (fast reading, docs/v12/11-fast-reading.md).

``POST /policies/upload`` runs only the CORE read (identity, money, cuotas) and
answers ``technical_read: {status: "pending"}``; the SPA then fires
``POST /policies/{id}/technical-read``, which runs the three technical groups in
parallel against the policy's source PDF and stores ONE ``extraction`` row.
Nothing reaches the policy until ``…/confirm`` (rule 6); ``…/discard`` drops
the suggestion.

Permissions mirror the upload (``Policies.Create``); reading the suggestion is
``Policies.View``. Another tenant's policy is a 404, never a 403.

Every error is structured ``detail = {"code", "message"}`` with Spanish copy
(``services.ai_error_copy`` for AI failures: ``ai_timeout`` · ``ai_unavailable`` ·
``ai_parse`` · ``document_unavailable``; plus ``not_found`` · ``no_source_document`` ·
``technical_read_confirmed`` · ``technical_read_dismissed`` ·
``technical_read_not_ready``), so the SPA shows the server's words as-is.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.models.ai import Extraction
from app.models.document import Document
from app.models.policy import Policy
from app.models.user import User
from app.schemas.policy_reading import TechnicalRead, TechnicalReadConfirm
from app.services import ai as ai_service
from app.services import policy_reading
from app.services import policy_reading_commit as technical
from app.services.ai_error_copy import ai_user_error

router = APIRouter(prefix="/policies", tags=["policies"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail={"code": "not_found", "message": message}
    )


def get_policy_or_404(db: Session, policy_id: int, broker_id: int) -> Policy:
    """The caller's own policy; another tenant's row is a 404 (never a 403)."""
    policy = db.scalars(
        select(Policy).where(Policy.id == policy_id, Policy.broker_id == broker_id)
    ).first()
    if policy is None:
        raise _not_found("No encontramos esa póliza.")
    return policy


def _ai_error(exc: ai_service.AIError) -> HTTPException:
    """A typed AI failure: same status + ``X-Radal-AI-Error`` as everywhere,
    structured ``{code, message}`` body (Spanish)."""
    from app.api.routers.ai import _ai_http_error

    base = _ai_http_error(exc)
    return HTTPException(
        status_code=base.status_code, detail=ai_user_error(exc), headers=base.headers
    )


def _source_document(db: Session, policy: Policy, broker_id: int) -> Document:
    """The policy's PDF, or 409 ``no_source_document`` (nothing to read)."""
    document = (
        ai_service.get_document(db, policy.source_document_id, broker_id)
        if policy.source_document_id is not None
        else None
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "no_source_document",
                "message": (
                    "La póliza no tiene su PDF de respaldo: súbelo para leer los datos técnicos."
                ),
            },
        )
    return document


def _own_technical(
    db: Session, policy: Policy, extraction_id: int, broker_id: int
) -> Extraction:
    """A technical read of THIS policy's document in this workspace, else 404."""
    extraction = ai_service.get_extraction(db, extraction_id, broker_id)
    if (
        extraction is None
        or not policy_reading.is_technical(extraction)
        or policy.source_document_id is None
        or extraction.document_id != policy.source_document_id
    ):
        raise _not_found("No encontramos esa lectura técnica para esta póliza.")
    return extraction


@router.get("/{policy_id}/technical-read", response_model=TechnicalRead)
def get_technical_read(
    policy_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Policies", "View")),
) -> TechnicalRead:
    """The latest technical read of the policy's PDF, or ``{status: "none"}``."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    if policy.source_document_id is None:
        return TechnicalRead(status="none")
    return technical.technical_read_view(
        technical.latest_technical(db, broker_id=broker_id, document_id=policy.source_document_id)
    )


@router.post("/{policy_id}/technical-read", response_model=TechnicalRead)
def start_technical_read(
    policy_id: int,
    force: bool = Query(default=False, description="Re-read even when an unconfirmed result exists"),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> TechnicalRead:
    """Run the three technical groups in parallel; return the stored suggestion.

    Idempotent-ish: an unconfirmed succeeded read (or one still running) is
    returned as-is unless ``?force=true``. Every group failing is a 200 with
    ``status="failed"`` and an ``error_code`` (the row is persisted either way).
    """
    policy = get_policy_or_404(db, policy_id, broker_id)
    document = _source_document(db, policy, broker_id)

    if not force:
        latest = technical.latest_technical(db, broker_id=broker_id, document_id=document.id)
        if latest is not None:
            view = technical.technical_read_view(latest)
            if view.status in ("ready", "running"):
                return view

    try:
        extraction = policy_reading.read_policy_technical(
            db,
            document=document,
            broker_id=broker_id,
            policy_id=policy.id,
            user=current_user,
        )
    except ai_service.AIError as exc:
        raise _ai_error(exc) from exc
    return technical.technical_read_view(extraction)


@router.post(
    "/{policy_id}/technical-read/{extraction_id}/confirm", response_model=TechnicalRead
)
def confirm_technical_read(
    policy_id: int,
    extraction_id: int,
    payload: TechnicalReadConfirm | None = Body(default=None),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> TechnicalRead:
    """Commit the reviewed sections to the policy (default: every section).

    Never overwrites a value the policy already holds — the difference comes
    back in ``warnings``.
    """
    policy = get_policy_or_404(db, policy_id, broker_id)
    extraction = _own_technical(db, policy, extraction_id, broker_id)
    try:
        return technical.confirm_technical(
            db,
            policy=policy,
            extraction=extraction,
            sections=list(payload.sections) if payload and payload.sections else None,
            user=current_user,
        )
    except technical.TechnicalConflict as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail) from exc


@router.post(
    "/{policy_id}/technical-read/{extraction_id}/discard", response_model=TechnicalRead
)
def discard_technical_read(
    policy_id: int,
    extraction_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Policies", "Create")),
) -> TechnicalRead:
    """Drop the suggestion. Nothing is written to the policy."""
    policy = get_policy_or_404(db, policy_id, broker_id)
    extraction = _own_technical(db, policy, extraction_id, broker_id)
    try:
        return technical.discard_technical(db, extraction=extraction, user=current_user)
    except technical.TechnicalConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail) from exc
