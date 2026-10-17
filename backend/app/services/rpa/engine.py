"""Execute portal searches end to end (06-rpa §1, §3).

:func:`execute_run` is the ONLY place a ``portal_search_run`` moves from
``queued`` to a terminal state. The job ``rpa.run`` and the dev inline path
(``RPA_INLINE`` -> FastAPI ``BackgroundTasks``) both call it.

Unlike most services this one COMMITS: it claims the run (``queued ->
running``, an atomic conditional UPDATE, so the job and an inline task never
execute the same run twice) and commits that before the browser starts, then
commits the outcome. It never raises: a failure is a ``failed`` run.

On failure:

* the error maps 1:1 to ``error_kind`` (:mod:`.errors`); anything else is
  ``unexpected``; ``error_detail`` is technical, scrubbed, internal;
* a screenshot of the page (when a browser was running) becomes a
  ``document(category=portal_screenshot, entity_type=portal_search)``;
* the requesting user gets a ``portal_search_failed`` fyi notification with
  ``params.error_kind`` (the frontend explains it);
* connector defects (everything except the broker-actionable kinds — missing
  credentials, wrong password, no plans) page the RADAL team through
  ``services.mail.ops.notify_ops`` and an ERROR log line with the connector
  key, version and error kind;
* ``login_failed`` marks the stored credential ``invalid``.

On success: ``portal_quote`` rows, the credential becomes ``valid``, and a
``portal_search_done`` fyi notification.

Maintenance (:func:`portal_availability`): a portal reads ``maintenance`` when
RPA is switched off, when its base URL is blank ("Conector en configuración"),
or on a ``layout_changed`` streak of the CURRENT connector version in the last
24 h (:func:`layout_maintenance_scope`):

* **global** — the last two portal-side outcomes (any broker) were both
  ``layout_changed`` AND came from at least two DISTINCT brokers. One tenant's
  own portal account (a forced password change, a locked-account page) can
  therefore never lock the portal for everybody;
* **broker** — that broker's own last two portal-side outcomes were both
  ``layout_changed``: only that broker is held back.

Either flag self-heals: once ``MAINTENANCE_PROBE_AFTER`` has passed since the
streak's latest failure, one probe run is let through (while it is queued or
running everyone else in that scope still reads maintenance). A probe that
succeeds breaks the streak; one that fails restarts the cool-down. Bumping the
map's ``version`` after a fix clears the flag immediately.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import SecretDecryptionError, decrypt_secret
from app.models.base_class import utcnow
from app.models.enums import (
    CredentialStatus,
    EntityType,
    NotificationKind,
    NotificationTray,
    PortalErrorKind,
    PortalRunStatus,
)
from app.models.insurer import Insurer
from app.models.rpa import PortalCredential, PortalQuote, PortalSearchRun
from app.services.rpa.base import (
    ERROR_DETAIL_MAX,
    BrowserFactory,
    PortalConnector,
    PortalResult,
    RunContext,
    scrub,
)
from app.services.rpa.errors import (
    USER_ACTIONABLE_KINDS,
    CredentialsMissing,
    PortalError,
    PortalUnavailable,
    kind_of,
)
from app.services.rpa.registry import get_connector

logger = logging.getLogger(__name__)

#: How far back the maintenance streak looks.
MAINTENANCE_WINDOW = timedelta(hours=24)
#: Consecutive ``layout_changed`` outcomes that flip a portal to maintenance.
MAINTENANCE_STREAK = 2
#: Distinct brokers a streak must span to hold the portal back for EVERY broker.
MAINTENANCE_MIN_BROKERS = 2
#: Cool-down after the streak's latest failure before one probe run is allowed.
MAINTENANCE_PROBE_AFTER = timedelta(hours=2)
#: Max runs one ``rpa.run`` invocation executes (06 §3).
JOB_BATCH = 3
#: A ``running`` run older than this was orphaned (process died) and is failed.
STALE_AFTER = timedelta(minutes=20)

ORIGIN_TYPE = "portal_search"

# --- Spanish, user-facing -------------------------------------------------------
REASON_DISABLED = "La búsqueda en portales no está habilitada en este ambiente."
REASON_NOT_CONFIGURED = "Conector en configuración"
REASON_LAYOUT = (
    "El portal cambió y el equipo Radal está actualizando el conector. "
    "Mientras tanto, cotiza directo en el portal o sube la cotización en PDF."
)
REASON_LAYOUT_BROKER = (
    "Las últimas búsquedas de tu corredora en este portal no encontraron la página esperada. "
    "Revisa tu cuenta en el portal (cambio de clave, bloqueo o aviso pendiente); el equipo Radal "
    "ya fue notificado. Mientras tanto, cotiza directo en el portal o sube la cotización en PDF."
)
LOGIN_REJECTED = "{portal} rechazó el usuario o la clave."
STALE_DETAIL = "La búsqueda quedó interrumpida y se dio por fallida."
#: Step-log line when the failure happened outside the step map (launch, config).
GENERIC_FAILURE: dict[PortalErrorKind, str] = {
    PortalErrorKind.PORTAL_UNAVAILABLE: "El portal no está disponible.",
    PortalErrorKind.TIMEOUT: "El portal no respondió a tiempo.",
    PortalErrorKind.UNEXPECTED: "Ocurrió un error inesperado al ejecutar la búsqueda.",
}


@dataclass(frozen=True)
class PortalAvailability:
    status: str  # "available" | "maintenance"
    reason: str | None = None
    #: machine code: rpa_disabled | not_configured | layout_changed | None
    code: str | None = None
    #: for ``layout_changed``: ``global`` (every broker) | ``broker`` (the caller only)
    scope: str | None = None

    @property
    def available(self) -> bool:
        return self.status == "available"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


# =============================================================================
# Browser
# =============================================================================

def _launch_args() -> list[str]:
    try:  # reuse the PDF engine's container-safe flags (Lambda needs extra ones)
        from app.services.pdf import _launch_args as pdf_launch_args  # noqa: PLC0415

        return pdf_launch_args()
    except Exception:  # noqa: BLE001
        return ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]


@contextmanager
def default_browser_factory() -> Iterator[Any]:
    """A fresh headless (``settings.RPA_HEADLESS``) Chromium page via sync Playwright.

    Sync API on purpose: runs execute in a worker thread (job, BackgroundTasks),
    never inside an event loop. One browser per run — portals keep cookies and
    a run must never inherit another broker's session.
    """
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=bool(settings.RPA_HEADLESS), args=_launch_args())
        try:
            context = browser.new_context(locale="es-CL", viewport={"width": 1280, "height": 900})
            yield context.new_page()
        finally:
            browser.close()


# =============================================================================
# Availability / maintenance
# =============================================================================

SCOPE_GLOBAL = "global"
SCOPE_BROKER = "broker"


def _layout_streak(db: Session, connector: PortalConnector, *, moment: datetime, broker_id: int | None) -> list[Any]:
    """The leading run of ``layout_changed`` failures of the current connector
    version among the latest portal-side outcomes (last 24 h), newest first.

    Broker-actionable failures (missing credentials, wrong password, no plans)
    say nothing about the portal's layout and are skipped; a success, any other
    portal-side failure or an older connector version ends the streak.
    """
    stmt = (
        select(
            PortalSearchRun.status,
            PortalSearchRun.error_kind,
            PortalSearchRun.connector_version,
            PortalSearchRun.broker_id,
            PortalSearchRun.finished_at,
        )
        .where(
            PortalSearchRun.portal == connector.key,
            PortalSearchRun.status.in_([PortalRunStatus.SUCCEEDED, PortalRunStatus.FAILED]),
            PortalSearchRun.finished_at.is_not(None),
            PortalSearchRun.finished_at >= moment - MAINTENANCE_WINDOW,
        )
        .order_by(PortalSearchRun.finished_at.desc(), PortalSearchRun.id.desc())
        .limit(50)
    )
    if broker_id is not None:
        stmt = stmt.where(PortalSearchRun.broker_id == broker_id)
    streak: list[Any] = []
    for row in db.execute(stmt).all():
        if row.status == PortalRunStatus.FAILED and row.error_kind in USER_ACTIONABLE_KINDS:
            continue
        if (
            row.status == PortalRunStatus.FAILED
            and row.error_kind == PortalErrorKind.LAYOUT_CHANGED
            and (row.connector_version or "") == connector.version
        ):
            streak.append(row)
            continue
        break
    return streak


def _probe_in_flight(db: Session, connector: PortalConnector, *, broker_id: int | None) -> bool:
    stmt = select(PortalSearchRun.id).where(
        PortalSearchRun.portal == connector.key,
        PortalSearchRun.status.in_([PortalRunStatus.QUEUED, PortalRunStatus.RUNNING]),
    )
    if broker_id is not None:
        stmt = stmt.where(PortalSearchRun.broker_id == broker_id)
    return db.scalar(stmt.limit(1)) is not None


def _streak_holds(db: Session, connector: PortalConnector, streak: list[Any], *, moment: datetime,
                  broker_id: int | None) -> bool:
    """A qualifying streak holds unless its cool-down passed and no probe is in flight."""
    latest = _aware(streak[0].finished_at)
    if latest is not None and moment - latest < MAINTENANCE_PROBE_AFTER:
        return True
    return _probe_in_flight(db, connector, broker_id=broker_id)


def layout_maintenance_scope(
    db: Session, connector: PortalConnector, *, broker_id: int | None = None, now: datetime | None = None
) -> str | None:
    """``"global"`` | ``"broker"`` | ``None`` — see the module docstring.

    Without ``broker_id`` only the global flag is evaluated.
    """
    moment = _aware(now) or utcnow()
    overall = _layout_streak(db, connector, moment=moment, broker_id=None)
    if (
        len(overall) >= MAINTENANCE_STREAK
        and len({r.broker_id for r in overall}) >= MAINTENANCE_MIN_BROKERS
        and _streak_holds(db, connector, overall, moment=moment, broker_id=None)
    ):
        return SCOPE_GLOBAL
    if broker_id is None:
        return None
    own = _layout_streak(db, connector, moment=moment, broker_id=broker_id)
    if len(own) >= MAINTENANCE_STREAK and _streak_holds(db, connector, own, moment=moment, broker_id=broker_id):
        return SCOPE_BROKER
    return None


def layout_changed_streak(
    db: Session, connector: PortalConnector, *, broker_id: int | None = None, now: datetime | None = None
) -> bool:
    """True when a ``layout_changed`` maintenance flag holds for ``broker_id``
    (or, without one, for every broker)."""
    return layout_maintenance_scope(db, connector, broker_id=broker_id, now=now) is not None


def portal_availability(
    db: Session, connector: PortalConnector, *, broker_id: int | None = None, now: datetime | None = None
) -> PortalAvailability:
    """Availability of ``connector`` as seen by ``broker_id`` (pass it from every
    request path; without it only the global flags apply)."""
    if not settings.RPA_ENABLED:
        return PortalAvailability("maintenance", REASON_DISABLED, "rpa_disabled")
    if not connector.base_url:
        return PortalAvailability("maintenance", REASON_NOT_CONFIGURED, "not_configured")
    scope = layout_maintenance_scope(db, connector, broker_id=broker_id, now=now)
    if scope == SCOPE_GLOBAL:
        return PortalAvailability("maintenance", REASON_LAYOUT, "layout_changed", SCOPE_GLOBAL)
    if scope == SCOPE_BROKER:
        return PortalAvailability("maintenance", REASON_LAYOUT_BROKER, "layout_changed", SCOPE_BROKER)
    return PortalAvailability("available")


# =============================================================================
# Credentials
# =============================================================================

def get_credential(db: Session, *, broker_id: int, portal: str) -> PortalCredential | None:
    return db.scalar(
        select(PortalCredential).where(
            PortalCredential.broker_id == broker_id, PortalCredential.portal == portal
        )
    )


def has_usable_credential(row: PortalCredential | None) -> bool:
    return bool(row is not None and (row.username or "").strip() and row.secret_ciphertext)


def load_credentials(db: Session, *, broker_id: int, portal: str) -> dict[str, str] | None:
    """``{username, password}`` decrypted, or ``None`` when absent/undecryptable."""
    row = get_credential(db, broker_id=broker_id, portal=portal)
    if not has_usable_credential(row):
        return None
    try:
        secret = decrypt_secret(row.secret_ciphertext)  # type: ignore[arg-type]
    except SecretDecryptionError:
        return None
    password = secret if isinstance(secret, str) else str(secret.get("password") or "")
    if not password:
        return None
    return {"username": row.username.strip(), "password": password}  # type: ignore[union-attr]


# =============================================================================
# Execution
# =============================================================================

def claim_run(db: Session, run: PortalSearchRun, *, now: datetime | None = None) -> bool:
    """Atomically move ``queued -> running``. False when someone else got it."""
    result = db.execute(
        update(PortalSearchRun)
        .where(PortalSearchRun.id == run.id, PortalSearchRun.status == PortalRunStatus.QUEUED)
        .values(status=PortalRunStatus.RUNNING, started_at=_aware(now) or utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    return result.rowcount == 1


def _resolve_insurer_id(db: Session, connector: PortalConnector) -> int | None:
    """Link quotes to the canonical insurer by RUT / CMF code — never by name."""
    try:
        ident = connector.insurer_identity()
    except Exception:  # noqa: BLE001
        return None
    rut = (ident.get("rut") or "").strip()
    cmf = (ident.get("cmf_code") or "").strip()
    if rut:
        from app.services.identifiers import InvalidRut, normalize_rut  # noqa: PLC0415

        try:
            rut = normalize_rut(rut)
        except InvalidRut:
            rut = ""
    if rut:
        found = db.scalar(select(Insurer.id).where(Insurer.rut == rut))
        if found is not None:
            return found
    if cmf:
        return db.scalar(select(Insurer.id).where(Insurer.cmf_code == cmf))
    return None


def _store_screenshot(db: Session, run: PortalSearchRun, data: bytes) -> int | None:
    try:
        from app.api.routers.documents import store_generated_document  # noqa: PLC0415  (import cycle)
        from app.models.document import DocumentCategory  # noqa: PLC0415

        doc = store_generated_document(
            db,
            broker_id=run.broker_id,
            entity_type=EntityType.PORTAL_SEARCH,
            entity_id=run.id,
            category=DocumentCategory.PORTAL_SCREENSHOT,
            data=data,
            original_name=f"portal-{run.portal}-busqueda-{run.id}.png",
            mime_type="image/png",
            phase="portal_search",
            uploaded_by_id=run.requested_by_user_id,
        )
        return doc.id
    except Exception:  # noqa: BLE001 - evidence must never mask the real failure
        logger.exception("rpa: could not store the failure screenshot of run %s", run.id)
        return None


def _recipients(run: PortalSearchRun) -> list[int] | None:
    return [run.requested_by_user_id] if run.requested_by_user_id else None


def _notify_user(db: Session, run: PortalSearchRun, connector_name: str, *, ok: bool, quote_count: int = 0) -> None:
    from app.services.notifications import notify  # noqa: PLC0415

    params: dict[str, Any] = {"portal": run.portal, "portal_name": connector_name, "run_id": run.id}
    if ok:
        params["quote_count"] = quote_count
    else:
        params["error_kind"] = run.error_kind.value if run.error_kind else PortalErrorKind.UNEXPECTED.value
    try:
        notify(
            db,
            broker_id=run.broker_id,
            user_ids=_recipients(run),
            kind=NotificationKind.PORTAL_SEARCH_DONE if ok else NotificationKind.PORTAL_SEARCH_FAILED,
            tray=NotificationTray.FYI,
            origin_type=ORIGIN_TYPE,
            origin_id=run.id,
            params=params,
            link_path=f"/portal-search?run={run.id}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("rpa: could not notify the user of run %s", run.id)


def _notify_ops(db: Session, run: PortalSearchRun, connector: PortalConnector | None, *, maintenance: str | None) -> None:
    key = run.portal
    version = run.connector_version or (connector.version if connector else "?")
    kind = run.error_kind.value if run.error_kind else "unexpected"
    failed_step = next((s.get("name") for s in reversed(run.steps or []) if not s.get("ok")), None)
    subject = f"RPA {key} v{version}: {kind} (búsqueda #{run.id})"
    if maintenance == SCOPE_GLOBAL:
        subject += " — conector en mantención"
    elif maintenance == SCOPE_BROKER:
        subject += f" — en mantención para la corredora #{run.broker_id}"
    body = "\n".join(
        [
            f"Conector: {key} (versión {version})",
            f"Búsqueda: #{run.id} · corredora #{run.broker_id}",
            f"Tipo de error: {kind}",
            f"Paso fallido: {failed_step or '-'}",
            f"Detalle técnico: {run.error_detail or '-'}",
            f"Captura: documento #{run.screenshot_document_id}" if run.screenshot_document_id else "Captura: -",
            {
                SCOPE_GLOBAL: "Portal marcado en mantención para todas las corredoras.",
                SCOPE_BROKER: (
                    f"Portal marcado en mantención solo para la corredora #{run.broker_id} "
                    "(posible problema de su cuenta en el portal)."
                ),
            }.get(maintenance or "", ""),
        ]
    ).strip()
    try:
        from app.services.mail.ops import notify_ops  # noqa: PLC0415

        notify_ops(db, subject, body)
    except Exception:  # noqa: BLE001 - an alert never raises
        logger.exception("rpa: could not record the ops mail for run %s", run.id)


def _mark_credential(db: Session, run: PortalSearchRun, *, status: CredentialStatus, error: str | None, now: datetime) -> None:
    row = get_credential(db, broker_id=run.broker_id, portal=run.portal)
    if row is None:
        return
    row.status = status
    row.last_error = error
    if status == CredentialStatus.VALID:
        row.last_verified_at = now


def _finish_success(
    db: Session, run: PortalSearchRun, connector: PortalConnector, result: PortalResult, ctx: RunContext, now: datetime
) -> None:
    insurer_id = _resolve_insurer_id(db, connector)
    for quote in result.quotes:
        db.add(
            PortalQuote(
                broker_id=run.broker_id,
                run_id=run.id,
                insurer_id=insurer_id,
                plan_name=(quote.plan_name or None) and quote.plan_name[:255],
                premium_uf=quote.premium_uf,
                premium_clp=quote.premium_clp,
                deductible=(quote.deductible or None) and quote.deductible[:255],
                coverages=quote.coverages,
                raw=quote.raw,
            )
        )
    run.status = PortalRunStatus.SUCCEEDED
    run.error_kind = None
    run.error_detail = None
    run.steps = ctx.steps
    run.finished_at = now
    _mark_credential(db, run, status=CredentialStatus.VALID, error=None, now=now)
    db.flush()
    _notify_user(db, run, connector.name, ok=True, quote_count=len(result.quotes))
    logger.info("rpa: run %s succeeded connector=%s version=%s quotes=%d", run.id, run.portal, run.connector_version, len(result.quotes))


def _finish_failure(
    db: Session,
    run: PortalSearchRun,
    connector: PortalConnector | None,
    exc: BaseException,
    ctx: RunContext,
    shot: bytes | None,
    now: datetime,
) -> None:
    kind = kind_of(exc)
    raw_detail = exc.detail if isinstance(exc, PortalError) else f"{type(exc).__name__}: {exc}"
    step = getattr(exc, "step", None)
    if step:
        raw_detail = f"[{step}] {raw_detail}"
    run.status = PortalRunStatus.FAILED
    run.error_kind = kind
    run.error_detail = scrub(raw_detail, ctx.secrets, limit=ERROR_DETAIL_MAX) or kind.value
    run.steps = ctx.steps
    run.finished_at = now
    if shot:
        run.screenshot_document_id = _store_screenshot(db, run, shot)
    if kind == PortalErrorKind.LOGIN_FAILED:
        name = connector.name if connector else run.portal
        _mark_credential(db, run, status=CredentialStatus.INVALID, error=LOGIN_REJECTED.format(portal=name), now=now)
    db.flush()
    _notify_user(db, run, connector.name if connector else run.portal, ok=False)
    if kind in USER_ACTIONABLE_KINDS:
        logger.info("rpa: run %s failed connector=%s version=%s error_kind=%s", run.id, run.portal, run.connector_version, kind.value)
        return
    maintenance = (
        layout_maintenance_scope(db, connector, broker_id=run.broker_id, now=now)
        if connector is not None and kind == PortalErrorKind.LAYOUT_CHANGED
        else None
    )
    logger.error(
        "rpa: run %s failed connector=%s version=%s error_kind=%s maintenance=%s",
        run.id, run.portal, run.connector_version, kind.value, maintenance or "none",
    )
    _notify_ops(db, run, connector, maintenance=maintenance)


def execute_run(
    db: Session,
    run: PortalSearchRun,
    *,
    connector: PortalConnector | None = None,
    browser_factory: BrowserFactory | None = None,
    now: datetime | None = None,
    step_timeouts: dict[str, int] | None = None,
) -> PortalSearchRun:
    """Run one queued search to a terminal state. Never raises; commits."""
    try:
        if run.status != PortalRunStatus.QUEUED or not claim_run(db, run, now=now):
            return run
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("rpa: could not claim run %s", getattr(run, "id", None))
        return run

    connector = connector or get_connector(run.portal)
    version = connector.version[:16] if connector is not None else run.connector_version
    ctx = RunContext(page=None, input=dict(run.input or {}), credentials={}, step_timeouts=dict(step_timeouts or {}))
    result: PortalResult | None = None
    error: BaseException | None = None
    shot: bytes | None = None
    try:
        if connector is None:
            raise PortalUnavailable(f"no connector registered for portal {run.portal!r}")
        if not connector.base_url:
            raise PortalUnavailable("connector base URL is not configured")
        if connector.requires_credentials:
            creds = load_credentials(db, broker_id=run.broker_id, portal=run.portal)
            if creds is None:
                ctx.log_step("credentials", False, "No hay credenciales configuradas para este portal.", op="check")
                raise CredentialsMissing("no usable credentials stored for this broker", step="credentials")
            ctx.credentials = creds
        ctx.base_url = connector.base_url
        factory = browser_factory or default_browser_factory
        with factory() as page:
            ctx.page = page
            try:
                result = connector.run(ctx)
            except Exception:
                shot = ctx.screenshot("failure")
                raise
    except Exception as exc:  # noqa: BLE001 - every failure is a typed run outcome
        error = exc
        if not any(not step.get("ok") for step in ctx.steps):
            ctx.log_step("run", False, GENERIC_FAILURE.get(kind_of(exc), GENERIC_FAILURE[PortalErrorKind.UNEXPECTED]), op="run")

    finished = _aware(now) or utcnow()
    if now is not None and run.started_at is not None:
        # Injected clock (tests/jobs): keep finished >= started.
        finished = max(finished, _aware(run.started_at))  # type: ignore[type-var]
    try:
        db.refresh(run)
        run.connector_version = version
        if run.status == PortalRunStatus.CANCELLED:
            run.steps = ctx.steps
            run.finished_at = run.finished_at or finished
            db.commit()
            return run
        if error is None and result is not None:
            _finish_success(db, run, connector, result, ctx, finished)  # type: ignore[arg-type]
        else:
            _finish_failure(db, run, connector, error or RuntimeError("no result"), ctx, shot, finished)
        db.commit()
    except Exception:  # noqa: BLE001 - last resort: never leave a run "running"
        db.rollback()
        logger.exception("rpa: could not record the outcome of run %s", run.id)
        try:
            db.execute(
                update(PortalSearchRun)
                .where(PortalSearchRun.id == run.id)
                .values(
                    status=PortalRunStatus.FAILED,
                    error_kind=PortalErrorKind.UNEXPECTED,
                    error_detail="outcome could not be recorded",
                    finished_at=finished,
                )
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    try:
        db.refresh(run)
    except Exception:  # noqa: BLE001
        pass
    return run


def execute_run_by_id(run_id: int, **kwargs: Any) -> None:
    """``BackgroundTasks`` entry point (``RPA_INLINE``): own session, never raises."""
    from app.db.session import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        run = db.get(PortalSearchRun, run_id)
        if run is not None:
            execute_run(db, run, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("rpa: inline execution of run %s crashed", run_id)
    finally:
        db.close()


def reap_stale_runs(db: Session, *, now: datetime) -> int:
    """Fail ``running`` runs orphaned by a dead process (flushes)."""
    cutoff = (_aware(now) or utcnow()) - STALE_AFTER
    rows = db.scalars(
        select(PortalSearchRun).where(
            PortalSearchRun.status == PortalRunStatus.RUNNING,
            or_(PortalSearchRun.started_at.is_(None), PortalSearchRun.started_at < cutoff),
        )
    ).all()
    for run in rows:
        run.status = PortalRunStatus.FAILED
        run.error_kind = PortalErrorKind.TIMEOUT
        run.error_detail = "stale: the run was still 'running' after the stale window"
        run.steps = [*(run.steps or []), {"name": "stale", "op": None, "ok": False, "detail": STALE_DETAIL,
                                          "at": (_aware(now) or utcnow()).isoformat(timespec="seconds"), "elapsed_ms": None}]
        run.finished_at = now
        known = get_connector(run.portal)
        _notify_user(db, run, known.name if known else run.portal, ok=False)
    db.flush()
    return len(rows)


def run_queued(db: Session, *, now: datetime, limit: int = JOB_BATCH, **kwargs: Any) -> dict[str, Any]:
    """The ``rpa.run`` job body: reap orphans, then execute the oldest queued runs."""
    reaped = reap_stale_runs(db, now=now)
    db.commit()
    ids = db.scalars(
        select(PortalSearchRun.id)
        .where(PortalSearchRun.status == PortalRunStatus.QUEUED)
        .order_by(PortalSearchRun.created_at, PortalSearchRun.id)
        .limit(limit)
    ).all()
    stats = {"executed": 0, "succeeded": 0, "failed": 0, "reaped": reaped}
    for run_id in ids:
        run = db.get(PortalSearchRun, run_id)
        if run is None:
            continue
        run = execute_run(db, run, now=now, **kwargs)
        stats["executed"] += 1
        if run.status == PortalRunStatus.SUCCEEDED:
            stats["succeeded"] += 1
        elif run.status == PortalRunStatus.FAILED:
            stats["failed"] += 1
    return stats


__all__ = [
    "JOB_BATCH",
    "MAINTENANCE_MIN_BROKERS",
    "MAINTENANCE_PROBE_AFTER",
    "MAINTENANCE_STREAK",
    "MAINTENANCE_WINDOW",
    "ORIGIN_TYPE",
    "PortalAvailability",
    "REASON_DISABLED",
    "REASON_LAYOUT",
    "REASON_LAYOUT_BROKER",
    "SCOPE_BROKER",
    "SCOPE_GLOBAL",
    "REASON_NOT_CONFIGURED",
    "claim_run",
    "default_browser_factory",
    "execute_run",
    "execute_run_by_id",
    "get_credential",
    "has_usable_credential",
    "layout_changed_streak",
    "layout_maintenance_scope",
    "load_credentials",
    "portal_availability",
    "reap_stale_runs",
    "run_queued",
]
