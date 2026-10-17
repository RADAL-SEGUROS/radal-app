"""``/mail`` — broker mailboxes (custom SMTP/IMAP) and the mail log (v12, 02 §6, 04 §3).

Owner: lane **B4** (mail + inquiries).

* ``GET /mail/accounts`` (Mail.View) — the broker's mailboxes the caller may
  see (shared ones + their own; a Settings.Manage holder sees all), led by a
  virtual read-only entry describing the Radal system sender (with
  ``transport`` ``ses``|``smtp`` and a Spanish ``transport_label``), plus the
  provider picker (Google / Microsoft visibly unavailable — "pronto").
* ``POST /mail/accounts`` (Mail.Create) — custom SMTP/IMAP only; ``google`` /
  ``microsoft`` -> 422 with the Spanish "pronto" reason. ``owner=shared`` needs
  Settings.Manage. The password is ENCRYPTED (``app.core.crypto``) and
  write-only: responses only say ``has_secret``.
* ``PATCH`` / ``DELETE /mail/accounts/{id}`` — your own mailbox with
  Mail.Create; a shared (or someone else's) one with Settings.Manage.
* ``POST /mail/accounts/{id}/test`` — SMTP + IMAP login through the transport;
  sets ``status`` connected / error. Errors are a generic Spanish category,
  never the server's reply; hosts must be public and ports mail ports
  (SMTP 25/465/587/2525, IMAP 143/993) — SSRF guard, 422 on create/update.
* ``GET /mail/messages`` (Collections.View) — the broker's mail log.

Tenant scope everywhere: another broker's mailbox — or a colleague's personal
one you may not manage — is a 404, never a 403.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_broker_id, get_db
from app.api.routers.clients import record_activity
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.permissions import has_permission, require_permission, resolve_role
from app.models.collection_settings import CollectionSettings
from app.models.enums import (
    EntityType,
    InboundClass,
    MailAccountStatus,
    MailDirection,
    MailProvider,
    MailSender,
)
from app.models.mail import MailAccount, MailMessage
from app.models.user import User
from app.schemas.mail import (
    MailAccountCreate,
    MailAccountList,
    MailAccountRead,
    MailAccountTestResult,
    MailAccountUpdate,
    MailCheckRead,
    MailMessagePage,
    MailMessageRead,
    MailProviderOption,
)
from app.services.mail.sender import (
    account_imap_config,
    account_smtp_config,
    radal_display_name,
)
from app.services.mail.transport import (
    MailHostRejected,
    check_mail_host,
    get_transport,
    system_transport_kind,
    system_transport_label,
)

router = APIRouter(prefix="/mail", tags=["mail"])

PRONTO_OAUTH = "Pronto: conexión con Google / Microsoft 365 mediante OAuth"
REASON_RADAL_SYSTEM = "La casilla de Radal ya está disponible para todas las corredoras; no se crea"
REASON_SHARED_NEEDS_ADMIN = "Solo un administrador puede conectar o modificar una casilla compartida"
REASON_DUPLICATE = "Ya hay una casilla conectada con ese correo"
REASON_SMTP_REQUIRED = "Indica el servidor SMTP (host y puerto) para enviar desde esta casilla"
REASON_IMAP_REQUIRED = "Indica el servidor IMAP (host y puerto) para leer esta casilla"
NOT_FOUND = "Casilla no encontrada"

_CONNECTION_FIELDS = (
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "imap_host",
    "imap_port",
    "imap_security",
    "username",
)


def _val(value):  # noqa: ANN001, ANN202
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _is_admin(user: User) -> bool:
    return has_permission(resolve_role(user), "Settings", "Manage")


def _visible_stmt(stmt, *, broker_id: int, user: User):  # noqa: ANN001, ANN202
    stmt = stmt.where(MailAccount.broker_id == broker_id)
    if not _is_admin(user):
        stmt = stmt.where(
            or_(MailAccount.owner_user_id.is_(None), MailAccount.owner_user_id == user.id)
        )
    return stmt


def _get_visible_or_404(db: Session, account_id: int, *, broker_id: int, user: User) -> MailAccount:
    row = db.scalar(
        _visible_stmt(select(MailAccount), broker_id=broker_id, user=user).where(
            MailAccount.id == account_id
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return row


def _can_manage(account: MailAccount, user: User) -> bool:
    if account.owner_user_id is not None and account.owner_user_id == user.id:
        return True
    return _is_admin(user)


def _require_manage(account: MailAccount, user: User) -> None:
    if not _can_manage(account, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REASON_SHARED_NEEDS_ADMIN)


def _owner_label(account: MailAccount, user: User) -> str:
    if account.owner_user_id is None:
        return "shared"
    return "me" if account.owner_user_id == user.id else "other"


def _read(account: MailAccount, user: User) -> MailAccountRead:
    return MailAccountRead(
        id=account.id,
        kind=_val(account.provider) or "custom",  # type: ignore[arg-type]
        provider=account.provider,
        address=account.address,
        display_name=account.display_name,
        owner=_owner_label(account, user),  # type: ignore[arg-type]
        owner_user_id=account.owner_user_id,
        smtp_host=account.smtp_host,
        smtp_port=account.smtp_port,
        smtp_security=account.smtp_security,
        imap_host=account.imap_host,
        imap_port=account.imap_port,
        imap_security=account.imap_security,
        username=account.username,
        has_secret=bool(account.secret_ciphertext),
        send_enabled=account.send_enabled,
        receive_enabled=account.receive_enabled,
        status=account.status,
        last_sync_at=account.last_sync_at,
        last_error=account.last_error,
        read_only=False,
        mail_enabled=bool(settings.MAIL_ENABLED),
        can_manage=_can_manage(account, user),
    )


def _radal_entry(db: Session, broker_id: int) -> MailAccountRead:
    return MailAccountRead(
        id=None,
        kind="radal_system",
        provider=MailProvider.RADAL_SYSTEM,
        address=settings.RADAL_MAIL_ADDRESS,
        display_name=radal_display_name(db, broker_id),
        owner="radal",
        has_secret=False,
        send_enabled=True,
        receive_enabled=True,
        status=MailAccountStatus.CONNECTED if settings.MAIL_ENABLED else MailAccountStatus.PAUSED,
        read_only=True,
        mail_enabled=bool(settings.MAIL_ENABLED),
        can_manage=False,
        transport=system_transport_kind(),  # type: ignore[arg-type]
        transport_label=system_transport_label(),
    )


def _providers() -> list[MailProviderOption]:
    return [
        MailProviderOption(provider=MailProvider.CUSTOM, available=True),
        MailProviderOption(provider=MailProvider.GOOGLE, available=False, reason=PRONTO_OAUTH),
        MailProviderOption(provider=MailProvider.MICROSOFT, available=False, reason=PRONTO_OAUTH),
    ]


def _validate_servers(account: MailAccount) -> None:
    if account.send_enabled and not (account.smtp_host and account.smtp_port):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=REASON_SMTP_REQUIRED)
    if account.receive_enabled and not (account.imap_host and account.imap_port):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=REASON_IMAP_REQUIRED)
    # SSRF guard: a public host on a mail port only. No DNS here (the transport
    # resolves and re-checks every address right before it connects).
    try:
        if account.smtp_host or account.smtp_port is not None:
            check_mail_host(account.smtp_host, account.smtp_port, kind="smtp", resolve=False)
        if account.imap_host or account.imap_port is not None:
            check_mail_host(account.imap_host, account.imap_port, kind="imap", resolve=False)
    except MailHostRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None


@router.get("/accounts", response_model=MailAccountList)
def list_mail_accounts(
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Mail", "View")),
) -> MailAccountList:
    rows = db.scalars(
        _visible_stmt(select(MailAccount), broker_id=broker_id, user=current_user).order_by(
            MailAccount.id
        )
    ).all()
    return MailAccountList(
        items=[_radal_entry(db, broker_id), *(_read(row, current_user) for row in rows)],
        providers=_providers(),
    )


@router.post("/accounts", response_model=MailAccountRead, status_code=status.HTTP_201_CREATED)
def create_mail_account(
    payload: MailAccountCreate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Mail", "Create")),
) -> MailAccountRead:
    provider = _val(payload.provider)
    if provider in (MailProvider.GOOGLE.value, MailProvider.MICROSOFT.value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=PRONTO_OAUTH)
    if provider == MailProvider.RADAL_SYSTEM.value:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=REASON_RADAL_SYSTEM)
    if payload.owner == "shared" and not _is_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=REASON_SHARED_NEEDS_ADMIN)
    duplicate = db.scalar(
        select(MailAccount.id).where(
            MailAccount.broker_id == broker_id, MailAccount.address == payload.address
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=REASON_DUPLICATE)
    account = MailAccount(
        broker_id=broker_id,
        owner_user_id=current_user.id if payload.owner == "me" else None,
        provider=MailProvider.CUSTOM,
        address=payload.address,
        display_name=payload.display_name,
        smtp_host=(payload.smtp_host or "").strip() or None,
        smtp_port=payload.smtp_port,
        smtp_security=payload.smtp_security,
        imap_host=(payload.imap_host or "").strip() or None,
        imap_port=payload.imap_port,
        imap_security=payload.imap_security,
        username=(payload.username or "").strip() or None,
        secret_ciphertext=encrypt_secret(payload.password) if payload.password else None,
        send_enabled=payload.send_enabled,
        receive_enabled=payload.receive_enabled,
        status=MailAccountStatus.DRAFT,
    )
    _validate_servers(account)
    db.add(account)
    db.flush()
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="mail_account.created",
        entity_type=EntityType.MAIL_ACCOUNT,
        entity_id=account.id,
        description=f"Casilla {account.address} conectada",
        meta={"owner": payload.owner},
    )
    db.commit()
    db.refresh(account)
    return _read(account, current_user)


@router.patch("/accounts/{account_id}", response_model=MailAccountRead)
def update_mail_account(
    account_id: int,
    payload: MailAccountUpdate,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Mail", "Create")),
) -> MailAccountRead:
    account = _get_visible_or_404(db, account_id, broker_id=broker_id, user=current_user)
    _require_manage(account, current_user)
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    paused = data.pop("paused", None)
    reconnect = False
    for key, value in data.items():
        if key in ("smtp_host", "imap_host", "username") and isinstance(value, str):
            value = value.strip() or None
        if key in _CONNECTION_FIELDS and getattr(account, key) != value:
            reconnect = True
        setattr(account, key, value)
    if password:
        account.secret_ciphertext = encrypt_secret(password)
        reconnect = True
    _validate_servers(account)
    if paused is True:
        account.status = MailAccountStatus.PAUSED
    elif reconnect or paused is False:
        # New credentials/servers (or un-pausing) must be tested again.
        account.status = MailAccountStatus.DRAFT
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="mail_account.updated",
        entity_type=EntityType.MAIL_ACCOUNT,
        entity_id=account.id,
        description=f"Casilla {account.address} actualizada",
        meta={"fields": sorted([*data.keys(), *(["password"] if password else [])])},
    )
    db.commit()
    db.refresh(account)
    return _read(account, current_user)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mail_account(
    account_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Mail", "Create")),
) -> Response:
    account = _get_visible_or_404(db, account_id, broker_id=broker_id, user=current_user)
    _require_manage(account, current_user)
    for row in db.scalars(
        select(CollectionSettings).where(
            CollectionSettings.broker_id == broker_id,
            CollectionSettings.sender_mail_account_id == account.id,
        )
    ):
        row.sender_mail_account_id = None
        row.default_sender = MailSender.RADAL
    # The log keeps its rows (FK SET NULL); detach explicitly for SQLite.
    for message in db.scalars(select(MailMessage).where(MailMessage.mail_account_id == account.id)):
        message.mail_account_id = None
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="mail_account.deleted",
        entity_type=EntityType.MAIL_ACCOUNT,
        entity_id=account.id,
        description=f"Casilla {account.address} desconectada",
    )
    db.delete(account)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/accounts/{account_id}/test", response_model=MailAccountTestResult)
def test_mail_account(
    account_id: int,
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    current_user: User = Depends(require_permission("Mail", "Create")),
) -> MailAccountTestResult:
    account = _get_visible_or_404(db, account_id, broker_id=broker_id, user=current_user)
    _require_manage(account, current_user)
    smtp = account_smtp_config(account) if account.send_enabled else None
    imap = account_imap_config(account) if account.receive_enabled else None
    try:
        result = get_transport().check(smtp, imap)
        check = MailCheckRead(
            smtp_ok=result.smtp_ok,
            imap_ok=result.imap_ok,
            smtp_error=result.smtp_error,
            imap_error=result.imap_error,
            checked_at=result.checked_at or datetime.now(timezone.utc),
            ok=result.ok,
        )
    except Exception:  # noqa: BLE001 - a test answers, it never 500s
        generic = "No se pudo verificar la casilla"
        check = MailCheckRead(
            smtp_ok=False if smtp else None,
            imap_ok=False if imap else None,
            smtp_error=generic if smtp else None,
            imap_error=generic if imap else None,
            checked_at=datetime.now(timezone.utc),
            ok=False,
        )
    if check.ok:
        account.status = MailAccountStatus.CONNECTED
        account.last_error = None
    else:
        account.status = MailAccountStatus.ERROR
        parts = []
        if check.smtp_ok is False:
            parts.append(f"SMTP: {check.smtp_error or 'falló la conexión'}")
        if check.imap_ok is False:
            parts.append(f"IMAP: {check.imap_error or 'falló la conexión'}")
        account.last_error = " · ".join(parts) or "No se pudo verificar la casilla"
    record_activity(
        db,
        broker_id=broker_id,
        user=current_user,
        action="mail_account.tested",
        entity_type=EntityType.MAIL_ACCOUNT,
        entity_id=account.id,
        description=f"Prueba de la casilla {account.address}: {'conectada' if check.ok else 'error'}",
    )
    db.commit()
    db.refresh(account)
    return MailAccountTestResult(account=_read(account, current_user), check=check)


@router.get("/messages", response_model=MailMessagePage)
def list_mail_messages(
    direction: MailDirection | None = Query(default=None),
    classification: InboundClass | None = Query(default=None),
    thread_token: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    broker_id: int = Depends(get_current_broker_id),
    _user: User = Depends(require_permission("Collections", "View")),
) -> MailMessagePage:
    stmt = select(MailMessage).where(MailMessage.broker_id == broker_id)
    if direction is not None:
        stmt = stmt.where(MailMessage.direction == direction)
    if classification is not None:
        stmt = stmt.where(MailMessage.classification == classification)
    if thread_token:
        stmt = stmt.where(MailMessage.thread_token == thread_token.strip().upper())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(MailMessage.created_at.desc(), MailMessage.id.desc()).offset(offset).limit(limit)
    ).all()
    return MailMessagePage(
        items=[MailMessageRead.model_validate(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )
