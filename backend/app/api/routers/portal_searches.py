"""``/portal-searches`` — insurer-portal quoting via RPA (HDI first) (v12, 02 §8).

Owner: lane **B6** (RPA), with ``app/services/rpa/``.

Gates: reading portals and runs is ``Integrations.View``; starting, retrying
and cancelling a run is ``Integrations.Submit``; the broker's portal
credentials are ``Settings.Manage`` (write-only: responses say ``has_secret``).

Scope: every run, quote and credential is filtered by the caller's
``broker_id``; another tenant's run is a **404** on every path (detail, retry,
cancel, the ``case_file_id`` filter). A run filed under an expediente is also
subject to the caller's case narrowing (``CASE_VIEW_SCOPE``), the same
mechanism ``get_case_or_404`` applies, so a run is never visible here when its
case is hidden elsewhere.

Execution: ``POST`` queues the run; job ``rpa.run`` executes it, or — with
``RPA_INLINE`` (dev) — a FastAPI ``BackgroundTasks`` right after the response.

Product flag: with ``PORTAL_SEARCH_ENABLED`` off (the default — portals need
each insurer's own user + password to apply filters) starting or retrying a
run is a **409** ``feature_disabled`` carrying the Spanish "pronto" reason, and
every portal lists as ``coming_soon``. Reads (runs, credentials) keep working.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db, require_permission
from app.api.routers.clients import record_activity
from app.core.case_scope import case_view_scope, scope_case_query
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.permissions import resolve_role
from app.models.case_file import CaseFile
from app.models.catalog import InsuranceSubtype
from app.models.document import Document
from app.models.enums import CredentialStatus, EntityType, PortalRunStatus
from app.models.insurer import Insurer
from app.models.rpa import PortalCredential, PortalQuote, PortalSearchRun
from app.models.user import User
from app.schemas.portal_searches import (
    Alternative,
    CredentialRead,
    CredentialWrite,
    PortalRead,
    QuoteRead,
    RunCreate,
    RunPage,
    RunRead,
    RunSummary,
    StepRead,
    SubtypeRef,
)
from app.services.features import PORTAL_SEARCH, PORTAL_SEARCH_DISABLED_REASON, portal_search_enabled
from app.services.notifications import close_for_origin
from app.services.rpa import engine
from app.services.rpa.base import InputInvalid, PortalConnector
from app.services.rpa.registry import (
    CONNECTORS,
    UPCOMING_PORTALS,
    UPCOMING_REASON,
    get_connector,
    is_upcoming,
)

router = APIRouter(prefix="/portal-searches", tags=["portal-searches"])

# --- Spanish, user-facing -------------------------------------------------------
NOT_FOUND = "Búsqueda no encontrada"
PORTAL_NOT_FOUND = "Portal no encontrado"
MSG_UNKNOWN_PORTAL = "Portal desconocido."
MSG_INVALID_INPUT = "Revisa los datos de la cotización."
MSG_UNKNOWN_SUBTYPE = "Subtipo de seguro desconocido."
MSG_UNSUPPORTED_SUBTYPE = "{portal} aún no cotiza este subtipo en Radal."
MSG_RETRY_STATE = "Solo se puede reintentar una búsqueda fallida o cancelada."
MSG_RETRY_IN_FLIGHT = "Ya hay un reintento de esta búsqueda en curso."
MSG_CANCEL_STATE = "La búsqueda ya terminó; no se puede cancelar."
MSG_PASSWORD_REQUIRED = "La clave es obligatoria."
MSG_UPLOAD_ALTERNATIVE = "Sube la cotización en PDF al comparador."

_RETRYABLE = (PortalRunStatus.FAILED, PortalRunStatus.CANCELLED)
_CANCELLABLE = (PortalRunStatus.QUEUED, PortalRunStatus.RUNNING)


def _require_feature() -> None:
    """409 while the product keeps portal search "pronto" (``PORTAL_SEARCH_ENABLED``)."""
    if not portal_search_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "feature_disabled",
                "feature": PORTAL_SEARCH,
                "message": PORTAL_SEARCH_DISABLED_REASON,
            },
        )


def _unprocessable(code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": code, "message": message, **extra},
    )


# =============================================================================
# Scope helpers
# =============================================================================

def _visible_runs(broker_id: int, user: User):
    """The tenant's runs, narrowed by the caller's case view scope (rule 8)."""
    stmt = select(PortalSearchRun).where(PortalSearchRun.broker_id == broker_id)
    if case_view_scope(resolve_role(user)) is not None:
        visible_cases = scope_case_query(
            select(CaseFile.id).where(CaseFile.broker_id == broker_id), broker_id=broker_id, user=user
        )
        stmt = stmt.where(
            or_(PortalSearchRun.case_file_id.is_(None), PortalSearchRun.case_file_id.in_(visible_cases))
        )
    return stmt


def _get_run_or_404(db: Session, run_id: int, *, broker_id: int, user: User) -> PortalSearchRun:
    run = db.scalar(_visible_runs(broker_id, user).where(PortalSearchRun.id == run_id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return run


def _check_case(db: Session, case_file_id: int, *, broker_id: int, user: User) -> CaseFile:
    from app.api.routers.case_files import get_case_or_404  # noqa: PLC0415  (heavy router)

    return get_case_or_404(db, case_file_id, broker_id, user)


_STATUS_LABELS = {
    "rpa_disabled": "No disponible en este ambiente",
    "not_configured": "En configuración",
    "layout_changed": "En mantención",
    "coming_soon": "Pronto",
}


def portal_status_label(status_v: str, code: str | None) -> str:
    """ONE Spanish status per portal, consistent with its one reason."""
    if status_v == "available":
        return "Disponible"
    return _STATUS_LABELS.get(code or "", "No disponible")


def _connector_or_404(portal: str) -> PortalConnector:
    connector = get_connector(portal)
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PORTAL_NOT_FOUND)
    return connector


# =============================================================================
# Projections
# =============================================================================

def _credential_read(row: PortalCredential | None, portal: str) -> CredentialRead | None:
    if row is None:
        return None
    return CredentialRead(
        portal=portal,
        username=row.username,
        has_secret=bool(row.secret_ciphertext),
        status=row.status,
        last_verified_at=row.last_verified_at,
        last_error=row.last_error,
        updated_at=row.updated_at,
    )


def _subtype_refs(db: Session, codes: tuple[str, ...]) -> list[SubtypeRef]:
    if not codes:
        return []
    rows = {
        r.code: r
        for r in db.scalars(select(InsuranceSubtype).where(InsuranceSubtype.code.in_(codes))).all()
    }
    return [
        SubtypeRef(id=rows[c].id, code=c, name=rows[c].name) if c in rows else SubtypeRef(code=c)
        for c in codes
    ]


def _portal_name(key: str) -> str:
    connector = get_connector(key)
    if connector is not None:
        return connector.name
    return dict(UPCOMING_PORTALS).get(key, key)


def _search_key(run: PortalSearchRun) -> tuple[Any, ...]:
    """What makes two runs "the same search" (a retry clones exactly these)."""
    return (
        run.portal,
        run.insurance_subtype_id,
        run.case_file_id,
        json.dumps(run.input or {}, sort_keys=True, default=str),
    )


def _live_runs(db: Session, broker_id: int, *, portal: str | None = None) -> list[PortalSearchRun]:
    """The broker's queued / running runs (few: a broker rarely has many in flight)."""
    stmt = select(PortalSearchRun).where(
        PortalSearchRun.broker_id == broker_id, PortalSearchRun.status.in_(_CANCELLABLE)
    )
    if portal is not None:
        stmt = stmt.where(PortalSearchRun.portal == portal)
    return list(db.scalars(stmt.order_by(PortalSearchRun.id)).all())


def _live_keys(db: Session, broker_id: int, *, portal: str | None = None) -> set[tuple[Any, ...]]:
    return {_search_key(r) for r in _live_runs(db, broker_id, portal=portal)}


def _retry_blocked(run: PortalSearchRun, live_keys: set[tuple[Any, ...]]) -> bool:
    """A failed/cancelled run whose search is already queued or running again."""
    return run.status in _RETRYABLE and _search_key(run) in live_keys


def _summary_fields(
    db: Session,
    run: PortalSearchRun,
    *,
    quote_count: int | None = None,
    live_keys: set[tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    subtype_code = None
    subtype_name = None
    if run.insurance_subtype_id is not None:
        row = db.execute(
            select(InsuranceSubtype.code, InsuranceSubtype.name).where(
                InsuranceSubtype.id == run.insurance_subtype_id
            )
        ).first()
        if row is not None:
            subtype_code, subtype_name = row[0], row[1]
    if quote_count is None:
        quote_count = int(
            db.scalar(select(func.count(PortalQuote.id)).where(PortalQuote.run_id == run.id)) or 0
        )
    failed = run.status == PortalRunStatus.FAILED and run.error_kind is not None
    if live_keys is None:
        live_keys = _live_keys(db, run.broker_id, portal=run.portal) if run.status in _RETRYABLE else set()
    return {
        "id": run.id,
        "portal": run.portal,
        "portal_name": _portal_name(run.portal),
        "status": run.status,
        "error_kind": run.error_kind,
        "user_message_key": run.error_kind.value if failed else None,
        "case_file_id": run.case_file_id,
        "insurance_subtype_id": run.insurance_subtype_id,
        "insurance_subtype_code": subtype_code,
        "requested_by_user_id": run.requested_by_user_id,
        "input": dict(run.input or {}),
        "quote_count": quote_count,
        "connector_version": run.connector_version,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "can_retry": (
            portal_search_enabled() and run.status in _RETRYABLE and not _retry_blocked(run, live_keys)
        ),
        "can_cancel": run.status in _CANCELLABLE,
        **_will_not_run(db, run),
        "insurance_subtype_name": subtype_name,
        "input_display": input_display(run.input or {}),
    }


#: What a queued run that cannot start says (review v12 #16).
MSG_WILL_NOT_RUN = "No se ejecutará: {reason}"

_USE_ES = {"particular": "Particular", "commercial": "Comercial"}


def _will_not_run(db: Session, run: PortalSearchRun) -> dict[str, Any]:
    if run.status != PortalRunStatus.QUEUED:
        return {"will_not_run": False, "will_not_run_reason": None}
    if not portal_search_enabled():
        return {"will_not_run": True, "will_not_run_reason": PORTAL_SEARCH_DISABLED_REASON}
    connector = get_connector(run.portal)
    if connector is None:
        return {"will_not_run": True, "will_not_run_reason": MSG_UNKNOWN_PORTAL}
    availability = engine.portal_availability(db, connector, broker_id=run.broker_id)
    if availability.available:
        return {"will_not_run": False, "will_not_run_reason": None}
    reason = (availability.reason or "el portal no está disponible").rstrip(".")
    reason = reason[:1].lower() + reason[1:]
    return {"will_not_run": True, "will_not_run_reason": MSG_WILL_NOT_RUN.format(reason=reason) + "."}


def input_display(data: dict[str, Any]) -> dict[str, str]:
    """The run's input for people: ``2026-10-27`` -> ``27-10-2026``, RUT dotted."""
    from datetime import date as _date  # noqa: PLC0415

    from app.services.identifiers import display_rut  # noqa: PLC0415

    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        text = str(value)
        if key.endswith("_date") or key == "date":
            try:
                text = _date.fromisoformat(text[:10]).strftime("%d-%m-%Y")
            except ValueError:
                pass
        elif key.endswith("rut"):
            text = display_rut(text)
        elif key == "use":
            text = _USE_ES.get(text, text)
        out[str(key)] = text
    return out


def step_label(connector: PortalConnector | None, name: str, index: int) -> str:
    """The Spanish label of a logged step (data in ``selectors/*.json``)."""
    spec = getattr(connector, "spec", None) or {}
    for step in spec.get("steps") or []:
        if step.get("name") == name and step.get("label"):
            return str(step["label"])
    extra = (spec.get("step_labels") or {}).get(name)
    if extra:
        return str(extra)
    return f"Paso {index}"


def _alternatives(db: Session, run: PortalSearchRun, *, live_keys: set[tuple[Any, ...]]) -> list[Alternative]:
    connector = get_connector(run.portal)
    availability = engine.portal_availability(db, connector, broker_id=run.broker_id) if connector else None
    in_flight = _retry_blocked(run, live_keys)
    enabled = portal_search_enabled()
    retry_ok = (
        enabled and run.status in _RETRYABLE and not in_flight
        and availability is not None and availability.available
    )
    retry_reason = None
    if not retry_ok:
        if not enabled:
            retry_reason = PORTAL_SEARCH_DISABLED_REASON
        elif run.status not in _RETRYABLE:
            retry_reason = MSG_RETRY_STATE
        elif in_flight:
            retry_reason = MSG_RETRY_IN_FLIGHT
        elif availability is not None:
            retry_reason = availability.reason
    out = [
        Alternative(kind="retry", available=retry_ok, reason=retry_reason, portal=run.portal,
                    portal_name=_portal_name(run.portal)),
        Alternative(kind="upload_quote_pdf", available=True, reason=MSG_UPLOAD_ALTERNATIVE,
                    case_file_id=run.case_file_id),
    ]
    for key, other in CONNECTORS.items():
        if key == run.portal:
            continue
        avail = engine.portal_availability(db, other, broker_id=run.broker_id)
        out.append(Alternative(kind="other_portal", available=enabled and avail.available,
                               reason=avail.reason if enabled else PORTAL_SEARCH_DISABLED_REASON,
                               portal=key, portal_name=other.name))
    for key, name in UPCOMING_PORTALS:
        out.append(Alternative(kind="other_portal", available=False, reason=UPCOMING_REASON,
                               portal=key, portal_name=name))
    return out


def _run_read(db: Session, run: PortalSearchRun) -> RunRead:
    quotes = db.scalars(
        select(PortalQuote).where(PortalQuote.run_id == run.id, PortalQuote.broker_id == run.broker_id)
        .order_by(PortalQuote.id)
    ).all()
    insurer_ids = {q.insurer_id for q in quotes if q.insurer_id}
    names = (
        dict(db.execute(select(Insurer.id, Insurer.legal_name).where(Insurer.id.in_(insurer_ids))).all())
        if insurer_ids else {}
    )
    quote_rows = [
        QuoteRead(
            id=q.id,
            insurer_id=q.insurer_id,
            insurer_name=names.get(q.insurer_id),
            plan_name=q.plan_name,
            premium_uf=q.premium_uf,
            premium_clp=q.premium_clp,
            deductible=q.deductible,
            coverages=q.coverages,
            proposal_id=q.proposal_id,
        )
        for q in quotes
    ]
    connector = get_connector(run.portal)
    steps = [
        StepRead(
            name=str(s.get("name") or "step"),
            label=step_label(connector, str(s.get("name") or ""), index),
            op=s.get("op"),
            ok=bool(s.get("ok")),
            detail=s.get("detail"),
            at=s.get("at"),
            elapsed_ms=s.get("elapsed_ms"),
        )
        for index, s in enumerate(
            (step for step in (run.steps or []) if isinstance(step, dict)), start=1
        )
    ]
    screenshot_url = None
    if run.screenshot_document_id:
        doc = db.get(Document, run.screenshot_document_id)
        if doc is not None and doc.broker_id == run.broker_id:
            from app.api.routers.documents import get_document_url  # noqa: PLC0415

            try:
                screenshot_url = get_document_url(doc)
            except Exception:  # noqa: BLE001 - a link failure must not fail the read
                screenshot_url = None
    live_keys = _live_keys(db, run.broker_id, portal=run.portal) if run.status in _RETRYABLE else set()
    return RunRead(
        **_summary_fields(db, run, quote_count=len(quote_rows), live_keys=live_keys),
        steps=steps,
        quotes=quote_rows,
        screenshot_document_id=run.screenshot_document_id,
        screenshot_url=screenshot_url,
        alternatives=_alternatives(db, run, live_keys=live_keys) if run.status in _RETRYABLE else [],
    )


# =============================================================================
# Portals + credentials
# =============================================================================

@router.get("/portals", response_model=list[PortalRead])
def list_portals(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Integrations", "View")),
) -> list[PortalRead]:
    """Built connectors (available | maintenance) plus the "pronto" portals.

    With the product flag off every portal — built or not — reads
    ``status="coming_soon"`` with the flag's Spanish reason.
    """
    enabled = portal_search_enabled()
    out: list[PortalRead] = []
    for key, connector in CONNECTORS.items():
        availability = engine.portal_availability(db, connector, broker_id=broker_id)
        cred = engine.get_credential(db, broker_id=broker_id, portal=key)
        flag_fields: dict[str, Any] = (
            {
                "status": availability.status,
                "status_label": portal_status_label(availability.status, availability.code),
                "maintenance_reason": availability.reason,
                "maintenance_code": availability.code,
                "maintenance_scope": availability.scope,
            }
            if enabled
            else {
                "status": "coming_soon",
                "status_label": portal_status_label("coming_soon", "coming_soon"),
                "maintenance_reason": PORTAL_SEARCH_DISABLED_REASON,
                "maintenance_code": "coming_soon",
                "maintenance_scope": None,
                "coming_soon": True,
            }
        )
        out.append(
            PortalRead(
                key=key,
                name=connector.name,
                version=connector.version,
                **flag_fields,
                supported_subtypes=list(connector.supported_subtypes),
                subtypes=_subtype_refs(db, connector.supported_subtypes),
                requires_credentials=connector.requires_credentials,
                has_credentials=engine.has_usable_credential(cred),
                credential=_credential_read(cred, key),
                input_fields=connector.input_fields(),
            )
        )
    for key, name in UPCOMING_PORTALS:
        out.append(
            PortalRead(
                key=key,
                name=name,
                status="maintenance" if enabled else "coming_soon",
                status_label=portal_status_label("maintenance", "coming_soon"),
                maintenance_reason=UPCOMING_REASON if enabled else PORTAL_SEARCH_DISABLED_REASON,
                maintenance_code="coming_soon",
                coming_soon=True,
            )
        )
    return out


@router.put("/credentials/{portal}", response_model=CredentialRead)
def put_credentials(
    portal: str,
    payload: CredentialWrite,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> CredentialRead:
    """Store the broker's portal login. The password is encrypted and never returned."""
    connector = _connector_or_404(portal)
    row = engine.get_credential(db, broker_id=broker_id, portal=connector.key)
    if row is None:
        if not payload.password:
            raise _unprocessable("password_required", MSG_PASSWORD_REQUIRED)
        row = PortalCredential(broker_id=broker_id, portal=connector.key)
        db.add(row)
    elif not payload.password and not row.secret_ciphertext:
        raise _unprocessable("password_required", MSG_PASSWORD_REQUIRED)
    row.username = payload.username
    if payload.password:
        row.secret_ciphertext = encrypt_secret(payload.password)
    row.status = CredentialStatus.UNVERIFIED
    row.last_error = None
    row.last_verified_at = None
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="portal_credential.updated",
        entity_type=EntityType.BROKER,
        entity_id=broker_id,
        description=f"Credenciales de {connector.name} actualizadas",
        meta={"portal": connector.key, "password_changed": bool(payload.password)},
    )
    db.commit()
    db.refresh(row)
    return _credential_read(row, connector.key)  # type: ignore[return-value]


@router.delete("/credentials/{portal}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credentials(
    portal: str,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Settings", "Manage")),
) -> Response:
    connector = _connector_or_404(portal)
    row = engine.get_credential(db, broker_id=broker_id, portal=connector.key)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credenciales no encontradas")
    db.delete(row)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="portal_credential.deleted",
        entity_type=EntityType.BROKER,
        entity_id=broker_id,
        description=f"Credenciales de {connector.name} eliminadas",
        meta={"portal": connector.key},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Runs
# =============================================================================

def _require_available(db: Session, connector: PortalConnector, *, broker_id: int) -> None:
    availability = engine.portal_availability(db, connector, broker_id=broker_id)
    if not availability.available:
        raise _unprocessable(
            "portal_maintenance",
            availability.reason or "Portal en mantención.",
            maintenance_code=availability.code,
            maintenance_scope=availability.scope,
        )


def _queue(background: BackgroundTasks, run: PortalSearchRun) -> None:
    if settings.RPA_INLINE:
        background.add_task(engine.execute_run_by_id, run.id)


@router.post("", response_model=RunRead, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: RunCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Integrations", "Submit")),
) -> RunRead:
    """Queue a portal search (validated; executed by ``rpa.run`` or inline)."""
    _require_feature()
    key = payload.portal.strip().lower()
    connector = get_connector(key)
    if connector is None:
        if is_upcoming(key):
            raise _unprocessable("portal_coming_soon", UPCOMING_REASON)
        raise _unprocessable("unknown_portal", MSG_UNKNOWN_PORTAL)
    if payload.case_file_id is not None:
        _check_case(db, payload.case_file_id, broker_id=broker_id, user=current_user)
    subtype = db.get(InsuranceSubtype, payload.insurance_subtype_id)
    if subtype is None or not subtype.is_active:
        raise _unprocessable("unknown_subtype", MSG_UNKNOWN_SUBTYPE)
    if subtype.code not in connector.supported_subtypes:
        raise _unprocessable(
            "unsupported_subtype",
            MSG_UNSUPPORTED_SUBTYPE.format(portal=connector.name),
            supported_subtypes=list(connector.supported_subtypes),
        )
    try:
        clean = connector.validate_input(payload.input)
    except InputInvalid as exc:
        raise _unprocessable("invalid_input", MSG_INVALID_INPUT, errors=exc.errors) from exc
    _require_available(db, connector, broker_id=broker_id)

    run = PortalSearchRun(
        broker_id=broker_id,
        portal=connector.key,
        case_file_id=payload.case_file_id,
        insurance_subtype_id=subtype.id,
        requested_by_user_id=current_user.id,
        input=clean,
        status=PortalRunStatus.QUEUED,
        connector_version=connector.version[:16],
    )
    db.add(run)
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="portal_search.created",
        entity_type=EntityType.PORTAL_SEARCH,
        entity_id=run.id,
        description=f"Búsqueda en {connector.name} ({subtype.code})",
        meta={"portal": connector.key, "subtype": subtype.code, "case_file_id": payload.case_file_id},
    )
    db.commit()
    db.refresh(run)
    _queue(background, run)
    return _run_read(db, run)


@router.get("", response_model=RunPage)
def list_runs(
    case_file_id: int | None = Query(default=None),
    portal: str | None = Query(default=None, max_length=32),
    status_filter: PortalRunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Integrations", "View")),
) -> RunPage:
    """The broker's runs, newest first."""
    stmt = _visible_runs(broker_id, current_user)
    if case_file_id is not None:
        _check_case(db, case_file_id, broker_id=broker_id, user=current_user)
        stmt = stmt.where(PortalSearchRun.case_file_id == case_file_id)
    if portal:
        stmt = stmt.where(PortalSearchRun.portal == portal.strip().lower())
    if status_filter is not None:
        stmt = stmt.where(PortalSearchRun.status == status_filter)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    runs = db.scalars(
        stmt.order_by(PortalSearchRun.created_at.desc(), PortalSearchRun.id.desc()).offset(offset).limit(limit)
    ).all()
    counts: dict[int, int] = {}
    live_keys = _live_keys(db, broker_id) if any(r.status in _RETRYABLE for r in runs) else set()
    if runs:
        counts = dict(
            db.execute(
                select(PortalQuote.run_id, func.count(PortalQuote.id))
                .where(PortalQuote.broker_id == broker_id, PortalQuote.run_id.in_([r.id for r in runs]))
                .group_by(PortalQuote.run_id)
            ).all()
        )
    return RunPage(
        items=[
            RunSummary(**_summary_fields(db, r, quote_count=counts.get(r.id, 0), live_keys=live_keys)) for r in runs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=RunRead)
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Integrations", "View")),
) -> RunRead:
    return _run_read(db, _get_run_or_404(db, run_id, broker_id=broker_id, user=current_user))


@router.post("/{run_id}/retry", response_model=RunRead, status_code=status.HTTP_201_CREATED)
def retry_run(
    run_id: int,
    background: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Integrations", "Submit")),
) -> RunRead:
    """Queue a NEW run with the same portal, subtype, case and input.

    Idempotent per search: when the same search (portal, subtype, case, input)
    is already queued or running for this broker — a double click, two users,
    or a fresh identical search — that run is returned with **200** instead of
    queueing a second portal session with the same credentials (portals tend to
    kill concurrent sessions, which would then read as a wrong password).
    """
    source = _get_run_or_404(db, run_id, broker_id=broker_id, user=current_user)
    _require_feature()  # after the 404: a foreign run stays a 404 by every path
    # Serialise concurrent retries of the same source (row lock on Postgres;
    # SQLite serialises writers anyway).
    db.execute(
        select(PortalSearchRun.id)
        .where(PortalSearchRun.id == source.id, PortalSearchRun.broker_id == broker_id)
        .with_for_update()
    )
    if source.status not in _RETRYABLE:
        raise _unprocessable("not_retryable", MSG_RETRY_STATE)
    key = _search_key(source)
    live_ids = [r.id for r in _live_runs(db, broker_id, portal=source.portal) if _search_key(r) == key]
    if live_ids:
        existing = db.scalar(_visible_runs(broker_id, current_user).where(PortalSearchRun.id.in_(live_ids)))
        if existing is not None:
            db.rollback()  # release the lock; nothing was written
            response.status_code = status.HTTP_200_OK
            return _run_read(db, existing)
    connector = get_connector(source.portal)
    if connector is None:
        raise _unprocessable("unknown_portal", MSG_UNKNOWN_PORTAL)
    _require_available(db, connector, broker_id=broker_id)
    case_file_id = source.case_file_id
    if case_file_id is not None:
        _check_case(db, case_file_id, broker_id=broker_id, user=current_user)
    run = PortalSearchRun(
        broker_id=broker_id,
        portal=source.portal,
        case_file_id=case_file_id,
        insurance_subtype_id=source.insurance_subtype_id,
        requested_by_user_id=current_user.id,
        input=dict(source.input or {}),
        status=PortalRunStatus.QUEUED,
        connector_version=connector.version[:16],
    )
    db.add(run)
    db.flush()
    # The user acted on the failure: its bell entry is done.
    close_for_origin(db, broker_id=broker_id, origin_type=engine.ORIGIN_TYPE, origin_id=source.id)
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="portal_search.retried",
        entity_type=EntityType.PORTAL_SEARCH,
        entity_id=run.id,
        description=f"Reintento de la búsqueda #{source.id} en {connector.name}",
        meta={"portal": source.portal, "retry_of": source.id},
    )
    db.commit()
    db.refresh(run)
    _queue(background, run)
    return _run_read(db, run)


@router.post("/{run_id}/cancel", response_model=RunRead)
def cancel_run(
    run_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Integrations", "Submit")),
) -> RunRead:
    """Cancel a queued (or running) run. A running browser finishes its step,
    and the engine then discards the outcome instead of overwriting ``cancelled``."""
    run = _get_run_or_404(db, run_id, broker_id=broker_id, user=current_user)
    if run.status == PortalRunStatus.CANCELLED:
        return _run_read(db, run)
    if run.status not in _CANCELLABLE:
        raise _unprocessable("not_cancellable", MSG_CANCEL_STATE)
    from app.models.base_class import utcnow  # noqa: PLC0415

    run.status = PortalRunStatus.CANCELLED
    run.finished_at = utcnow()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="portal_search.cancelled",
        entity_type=EntityType.PORTAL_SEARCH,
        entity_id=run.id,
        description=f"Búsqueda #{run.id} cancelada",
        meta={"portal": run.portal},
    )
    db.commit()
    db.refresh(run)
    return _run_read(db, run)
