"""``/case-files/{id}/expediente`` — the EXPEDIENTE COMPLETO of a grupo-cuenta.

Two read-only endpoints over one aggregate (``app.services.expediente``):

    GET /case-files/{case_id}/expediente        the whole account as JSON
    GET /case-files/{case_id}/expediente/pdf    the same aggregate as a PDF

**The PDF is rendered ON DEMAND and streamed — it is never persisted.** No new
``PackKind`` and no new ``DocumentCategory`` value exist for it, deliberately:
the requirement is that the document is ALWAYS up to date, and there is no
Alembic in this project (CLAUDE.md rule 10 — a new stored enum value would need
a hand-written migration in ``scripts/migrate_case_files.py`` on every engine).
A streamed render satisfies both: nothing to migrate, nothing to go stale. The
comparison / propuesta PDFs, which ARE artifacts a broker sends out, keep their
stored ``document`` rows.

House rules: every query filters the authenticated broker, another tenant's case
is a **404 never a 403**, and the case is resolved through
``case_files.get_case_or_404`` WITH the user, so the ``CASE_VIEW_SCOPE``
narrowing that hides a case from its own list hides it here too (group-layer
rule 8).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.case_files import get_case_or_404
from app.models.broker import Broker
from app.models.case_file import CaseFile
from app.models.user import User
from app.schemas.expediente import AccountExpediente
from app.services import case_files as machine
from app.services import expediente as aggregator
from app.services import pdf_templates

router = APIRouter(prefix="/case-files", tags=["expediente"])


def _account_or_422(db: Session, case_id: int, broker_id: int, user: User) -> CaseFile:
    """The folder, or 404 (foreign / invisible) / 422 (not an account folder).

    Only an ``account``/``renewal`` folder HAS an expediente completo: the five
    journey milestones and the money core belong to the vigencia, not to a
    post-sale child (endoso / cobranza / siniestro).
    """
    case = get_case_or_404(db, case_id, broker_id, user)
    if case.kind not in machine.ACCOUNT_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "not_an_account",
                "case_file_id": case.id,
                "kind": case.kind.value,
                "detail": (
                    "El expediente completo existe solo para una carpeta de cuenta "
                    "(grupo-cuenta o renovación)."
                ),
            },
        )
    return case


@router.get("/{case_id}/expediente", response_model=AccountExpediente)
def get_account_expediente(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
    activity_limit: int = Query(default=20, ge=1, le=200),
) -> AccountExpediente:
    """Everything we know about the grupo-cuenta, computed fresh on every read.

    Identity + group + empresas, the five journey milestones with their REAL
    completion dates (off ``case_file_stage_event``, never inferred), the
    antecedentes index, the comparación with its named AI recommendation, the
    propuesta, the pólizas, every document (no S3 key — rule 8), the reconciled
    money core and the last ``activity_limit`` bitácora entries. Anything that
    does not exist yet is ``null`` next to a Spanish ``*_pending_reason``.
    """
    case = _account_or_422(db, case_id, broker_id, current_user)
    return aggregator.build_expediente(
        db, case=case, broker_id=broker_id, activity_limit=activity_limit
    )


@router.get(
    "/{case_id}/expediente/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_account_expediente_pdf(
    case_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("CaseFiles", "View")),
) -> Response:
    """The same aggregate as a branded PDF, rendered on demand (never stored).

    Mirrors the ``/comparisons/{id}/pdf`` pipeline — the branded Playwright
    renderer in ``app.services.pdf`` — but returns the bytes instead of a stored
    ``document``: this artifact must always reflect the folder as it is right
    now. A rendering failure is a 502, never a 500.
    """
    case = _account_or_422(db, case_id, broker_id, current_user)
    payload = aggregator.build_expediente(
        db, case=case, broker_id=broker_id, activity_limit=20
    )

    # The cover/running-frame identity the antecedentes + comparison PDFs use.
    from app.api.routers.antecedentes import _cover_meta, _logo_datauri

    broker = db.get(Broker, broker_id)
    branding = {
        "radal_wordmark": "Radal.",
        "broker_logo_datauri": _logo_datauri(broker) if broker else None,
        "broker_name": (broker.trade_name or broker.legal_name) if broker else None,
        "broker_rut": broker.rut if broker else None,
        "cmf_code": broker.cmf_code if broker else None,
        "cover_meta": _cover_meta(case),
        "eyebrow": "Expediente completo",
        "running_title": "Expediente completo",
    }
    title = (
        (payload.group.name if payload.group else None)
        or next((c.legal_name for c in payload.clients if c.legal_name), None)
        or case.title
        or "Expediente completo"
    )

    html = pdf_templates.render_expediente_completo_html(
        title=title,
        branding=branding,
        data=aggregator.expediente_pdf_data(db, broker_id, case, payload),
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

    filename = f"expediente-{case.reference or case.id}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
