"""``/platform`` — RADAL's maintainer console (v12, 02 §5). Platform admins only.

Owner: lane **B3**. Every route is gated ``require_permission("Platform", …)``
AND requires ``user_type == platform`` (a broker admin is 403 by both). The
tables here are platform-level (no ``broker_id``) and are never listed through
a broker endpoint.

* insurer cobranza profiles (onboarding status, instructions, collections
  e-mail, sender domains) — the ONLY place the onboarding status is visible;
* provider accounts (Forpay credential sets): secrets are write-only and
  encrypted; responses carry ``has_secret``, the names of the keys set and the
  full ``webhook_url``; ``verify`` attempts a provider login;
  ``rotate-webhook-secret`` keeps the previous secret valid for 30 days;
* insurer payment channels (status, Transbank store code, provider account);
* the provider-event inbox (+ reprocess) and job runs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission, resolve_user_type
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.jobs import load_jobs
from app.models.base_class import utcnow
from app.models.enums import (
    ChannelStatus,
    EventProcessing,
    JobStatus,
    OnboardingStatus,
    PaymentChannel,
    ProviderAccountStatus,
    ProviderOwner,
)
from app.models.insurer import Insurer
from app.models.insurer_collection import InsurerCollectionProfile, InsurerPaymentChannel
from app.models.jobs import JobRun
from app.models.provider import ProviderAccount, ProviderEvent
from app.models.user import User
from app.schemas.jobs import JobRunRead
from app.schemas.platform import (
    ChannelBulkItem,
    ChannelRead,
    ChannelUpsert,
    EventDetail,
    EventPage,
    EventRead,
    InsurerProfileCreate,
    InsurerProfileRead,
    InsurerProfileUpdate,
    JobPage,
    ProviderAccountCreate,
    ProviderAccountRead,
    ProviderAccountUpdate,
    VerifyResult,
)
from app.services.collection.resolver import provider_account_usable, verify_status
from app.services.payments.accounts import (
    WEBHOOK_KINDS,
    WRITABLE_SECRET_KEYS,
    has_secret,
    merge_secret_bundle,
    new_webhook_key,
    new_webhook_secret,
    previous_secret_until,
    rotate_webhook_secret,
    secret_keys_set,
    webhook_url,
)
from app.services.payments.insurers import clean_instructions
from app.services.payments.providers import get_provider
from app.services.payments.providers.base import PaymentProviderError
from app.services.payments.webhooks import run_processing

router = APIRouter(prefix="/platform", tags=["platform"])

#: Keys an account needs before it may be ``active`` (login + webhook HMAC).
REQUIRED_FOR_ACTIVE: tuple[str, ...] = ("api_key", "password", "hmac_key", "public_key_pem")


def platform_gate(action: str):
    """``Platform.<action>`` AND a platform user (no broker)."""
    permission = require_permission("Platform", action)

    def _guard(user: User = Depends(permission)) -> User:
        if resolve_user_type(user) != "platform" or getattr(user, "broker_id", None) is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Not permitted to {action} on Platform")
        return user

    return _guard


def _insurer_or_404(db: Session, insurer_id: int) -> Insurer:
    insurer = db.get(Insurer, insurer_id)
    if insurer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compañía no encontrada")
    return insurer


def _name(insurer: Insurer | None) -> str | None:
    if insurer is None:
        return None
    return insurer.trade_name or insurer.legal_name


def _val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


# --- projections -------------------------------------------------------------------

def channel_read(db: Session, row: InsurerPaymentChannel) -> ChannelRead:
    acct = db.get(ProviderAccount, row.provider_account_id) if row.provider_account_id else None
    return ChannelRead(
        id=row.id,
        insurer_id=row.insurer_id,
        insurer_name=_name(db.get(Insurer, row.insurer_id)),
        channel=row.channel,
        provider_account_id=row.provider_account_id,
        provider_account_label=acct.label if acct else None,
        store_commerce_code=row.store_commerce_code,
        status=row.status,
        fee_model=row.fee_model,
        activated_at=row.activated_at,
        updated_at=row.updated_at,
    )


def profile_read(db: Session, insurer: Insurer, profile: InsurerCollectionProfile | None) -> InsurerProfileRead:
    channels = db.scalars(
        select(InsurerPaymentChannel)
        .where(InsurerPaymentChannel.insurer_id == insurer.id)
        .order_by(InsurerPaymentChannel.id)
    ).all()
    return InsurerProfileRead(
        insurer_id=insurer.id,
        insurer_name=_name(insurer) or "",
        insurer_rut=insurer.rut,
        has_profile=profile is not None,
        onboarding_status=profile.onboarding_status if profile else OnboardingStatus.NOT_CONTACTED,
        status_note=profile.status_note if profile else None,
        expected_live_on=profile.expected_live_on if profile else None,
        payment_instructions=clean_instructions(profile.payment_instructions if profile else None),
        collections_email=profile.collections_email if profile else None,
        sender_domains=[str(d) for d in (profile.sender_domains or [])] if profile else [],
        updated_by_user_id=profile.updated_by_user_id if profile else None,
        updated_at=profile.updated_at if profile else None,
        channels=[channel_read(db, c) for c in channels],
    )


#: Shown next to the webhook URLs when ``PUBLIC_API_URL`` is not configured.
WEBHOOK_FALLBACK_NOTE = (
    "PUBLIC_API_URL no está configurado: la URL usa el origen de esta API. "
    "Configúralo con el dominio público del backend antes de entregarla al proveedor."
)


def request_api_base(request: Request) -> str:
    """The origin THIS request reached — the backend's, never the SPA's."""
    return str(request.base_url).rstrip("/")


def account_read(
    db: Session, acct: ProviderAccount, *, api_base: str | None = None
) -> ProviderAccountRead:
    urls = {
        kind: url for kind in WEBHOOK_KINDS if (url := webhook_url(acct, kind, api_base=api_base))
    }
    until = previous_secret_until(acct)
    return ProviderAccountRead(
        id=acct.id,
        provider=acct.provider,
        label=acct.label,
        owner=acct.owner,
        insurer_id=acct.insurer_id,
        insurer_name=_name(db.get(Insurer, acct.insurer_id)) if acct.insurer_id else None,
        environment=acct.environment,
        base_url=acct.base_url,
        commerce=acct.commerce,
        channel_header=acct.channel_header,
        process_id=acct.process_id,
        username=acct.username,
        sales_executive_id=acct.sales_executive_id,
        status=acct.status,
        has_secret=has_secret(acct),
        secret_keys=secret_keys_set(acct),
        webhook_key=acct.webhook_key,
        webhook_url=urls.get("payment"),
        webhook_urls=urls,
        previous_webhook_secret_valid_until=until if until and until > utcnow() else None,
        verify_status=verify_status(acct),
        last_verified_at=acct.last_verified_at,
        last_error=acct.last_error,
        usable=provider_account_usable(acct),
        webhook_url_note=(
            None if (settings.PUBLIC_API_URL or "").strip() else WEBHOOK_FALLBACK_NOTE
        ),
        created_at=acct.created_at,
        updated_at=acct.updated_at,
    )


# --- insurer profiles ----------------------------------------------------------------

def _apply_profile(row: InsurerCollectionProfile, fields: dict[str, Any], user: User) -> None:
    for key in ("onboarding_status", "status_note", "expected_live_on", "sender_domains"):
        if key in fields:
            setattr(row, key, fields[key])
    if "collections_email" in fields:
        row.collections_email = str(fields["collections_email"]) if fields["collections_email"] else None
    if "payment_instructions" in fields:
        row.payment_instructions = clean_instructions(fields["payment_instructions"]) or None
    if row.onboarding_status is None:
        row.onboarding_status = OnboardingStatus.NOT_CONTACTED
    row.updated_by_user_id = user.id


@router.get("/insurer-profiles", response_model=list[InsurerProfileRead])
def list_insurer_profiles(
    q: str | None = Query(default=None, max_length=120),
    onboarding_status: OnboardingStatus | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("View")),
) -> list[InsurerProfileRead]:
    """Every canonical insurer with its profile (a missing profile reads as ``not_contacted``)."""
    stmt = select(Insurer, InsurerCollectionProfile).outerjoin(
        InsurerCollectionProfile, InsurerCollectionProfile.insurer_id == Insurer.id
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Insurer.legal_name.ilike(like), Insurer.trade_name.ilike(like), Insurer.rut.ilike(like)))
    if onboarding_status is not None:
        if onboarding_status == OnboardingStatus.NOT_CONTACTED:
            stmt = stmt.where(
                or_(
                    InsurerCollectionProfile.insurer_id.is_(None),
                    InsurerCollectionProfile.onboarding_status == onboarding_status,
                )
            )
        else:
            stmt = stmt.where(InsurerCollectionProfile.onboarding_status == onboarding_status)
    rows = db.execute(stmt.order_by(Insurer.legal_name.asc())).all()
    return [profile_read(db, insurer, profile) for insurer, profile in rows]


@router.get("/insurer-profiles/{insurer_id}", response_model=InsurerProfileRead)
def get_insurer_profile(
    insurer_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("View")),
) -> InsurerProfileRead:
    insurer = _insurer_or_404(db, insurer_id)
    return profile_read(db, insurer, db.get(InsurerCollectionProfile, insurer.id))


@router.post("/insurer-profiles", response_model=InsurerProfileRead, status_code=status.HTTP_201_CREATED)
def create_insurer_profile(
    payload: InsurerProfileCreate,
    db: Session = Depends(get_db),
    user: User = Depends(platform_gate("Create")),
) -> InsurerProfileRead:
    insurer = _insurer_or_404(db, payload.insurer_id)
    if db.get(InsurerCollectionProfile, insurer.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="La compañía ya tiene perfil de cobranza")
    row = InsurerCollectionProfile(insurer_id=insurer.id, onboarding_status=OnboardingStatus.NOT_CONTACTED)
    db.add(row)
    _apply_profile(row, payload.model_dump(exclude_unset=True, exclude={"insurer_id"}), user)
    db.commit()
    return profile_read(db, insurer, row)


@router.put("/insurer-profiles/{insurer_id}", response_model=InsurerProfileRead)
def update_insurer_profile(
    insurer_id: int,
    payload: InsurerProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(platform_gate("Edit")),
) -> InsurerProfileRead:
    """Upsert (the seed creates profiles lazily; a missing one is created here)."""
    insurer = _insurer_or_404(db, insurer_id)
    row = db.get(InsurerCollectionProfile, insurer.id)
    if row is None:
        row = InsurerCollectionProfile(insurer_id=insurer.id, onboarding_status=OnboardingStatus.NOT_CONTACTED)
        db.add(row)
    _apply_profile(row, payload.model_dump(exclude_unset=True), user)
    db.commit()
    return profile_read(db, insurer, row)


# --- provider accounts ------------------------------------------------------------------

def _account_or_404(db: Session, account_id: int) -> ProviderAccount:
    acct = db.get(ProviderAccount, account_id)
    if acct is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta de proveedor no encontrada")
    return acct


def _validate_account(db: Session, acct: ProviderAccount) -> None:
    if not (acct.base_url or "").startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="La URL base debe comenzar con https://")
    if _val(acct.environment) == "production" and not acct.base_url.startswith("https://"):
        raise HTTPException(status_code=422, detail="En producción la URL base debe usar https://")
    if acct.owner == ProviderOwner.INSURER and acct.insurer_id is None:
        raise HTTPException(status_code=422, detail="Una cuenta de compañía necesita la compañía (insurer_id)")
    if acct.insurer_id is not None and db.get(Insurer, acct.insurer_id) is None:
        raise HTTPException(status_code=422, detail="La compañía indicada no existe")
    if acct.status == ProviderAccountStatus.ACTIVE:
        present = set(secret_keys_set(acct))
        missing = [k for k in REQUIRED_FOR_ACTIVE if k not in present]
        if not acct.username:
            missing.insert(0, "username")
        if missing:
            raise HTTPException(
                status_code=422, detail=f"Para activar la cuenta faltan: {', '.join(missing)}"
            )


def _secret_updates(payload: Any) -> dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    return {k: data[k] for k in WRITABLE_SECRET_KEYS if k in data}


@router.get("/provider-accounts", response_model=list[ProviderAccountRead])
def list_provider_accounts(
    db: Session = Depends(get_db),
    api_base: str = Depends(request_api_base),
    _user: User = Depends(platform_gate("View")),
) -> list[ProviderAccountRead]:
    rows = db.scalars(select(ProviderAccount).order_by(ProviderAccount.id)).all()
    return [account_read(db, r, api_base=api_base) for r in rows]


@router.get("/provider-accounts/{account_id}", response_model=ProviderAccountRead)
def get_provider_account(
    account_id: int,
    db: Session = Depends(get_db),
    api_base: str = Depends(request_api_base),
    _user: User = Depends(platform_gate("View")),
) -> ProviderAccountRead:
    return account_read(db, _account_or_404(db, account_id), api_base=api_base)


@router.post("/provider-accounts", response_model=ProviderAccountRead, status_code=status.HTTP_201_CREATED)
def create_provider_account(
    payload: ProviderAccountCreate,
    db: Session = Depends(get_db),
    api_base: str = Depends(request_api_base),
    _user: User = Depends(platform_gate("Create")),
) -> ProviderAccountRead:
    acct = ProviderAccount(
        provider=payload.provider,
        label=payload.label.strip(),
        owner=payload.owner,
        insurer_id=payload.insurer_id,
        environment=payload.environment,
        base_url=(payload.base_url or settings.FORPAY_DEFAULT_BASE_URL).strip().rstrip("/"),
        commerce=payload.commerce,
        channel_header=payload.channel_header,
        process_id=payload.process_id or "WHITE_LABEL",
        username=payload.username,
        sales_executive_id=payload.sales_executive_id,
        status=payload.status,
        webhook_key=new_webhook_key(),
        webhook_secret_ciphertext=encrypt_secret(new_webhook_secret()),
    )
    merge_secret_bundle(acct, _secret_updates(payload))
    _validate_account(db, acct)
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return account_read(db, acct, api_base=api_base)


@router.put("/provider-accounts/{account_id}", response_model=ProviderAccountRead)
def update_provider_account(
    account_id: int,
    payload: ProviderAccountUpdate,
    db: Session = Depends(get_db),
    api_base: str = Depends(request_api_base),
    _user: User = Depends(platform_gate("Edit")),
) -> ProviderAccountRead:
    acct = _account_or_404(db, account_id)
    fields = payload.model_dump(exclude_unset=True)
    for key in (
        "label",
        "owner",
        "insurer_id",
        "environment",
        "commerce",
        "channel_header",
        "process_id",
        "username",
        "sales_executive_id",
        "status",
    ):
        if key in fields and (fields[key] is not None or key == "insurer_id"):
            setattr(acct, key, fields[key])
    if fields.get("base_url"):
        acct.base_url = fields["base_url"].strip().rstrip("/")
    merge_secret_bundle(acct, _secret_updates(payload))
    _validate_account(db, acct)
    db.commit()
    db.refresh(acct)
    return account_read(db, acct, api_base=api_base)


@router.post("/provider-accounts/{account_id}/verify", response_model=VerifyResult)
def verify_provider_account(
    account_id: int,
    db: Session = Depends(get_db),
    api_base: str = Depends(request_api_base),
    _user: User = Depends(platform_gate("Manage")),
) -> VerifyResult:
    """A login attempt against the provider; records ``last_verified_at`` / ``last_error``."""
    acct = _account_or_404(db, account_id)
    error: str | None = None
    try:
        provider = get_provider(_val(acct.provider))
        verify = getattr(provider, "verify_credentials", None)
        if callable(verify):
            verify(acct)
        else:  # providers without a login probe: one authenticated read
            provider.search_receivables(acct, externalInstallmentId="rdl-verify", pageSize=1)
    except PaymentProviderError as exc:
        error = str(exc)[:2000] or "No se pudo verificar la cuenta"
    if error is None:
        acct.last_verified_at = utcnow()
        acct.last_error = None
    else:
        # ``last_verified_at`` keeps the last SUCCESS; ``last_error`` marks the
        # LAST result as failed, so the account stops serving native collection
        # (resolver.provider_account_verified) and the card reads "falló".
        acct.last_error = error
        db.flush()
        _close_native_notices_for(db, acct)
    db.commit()
    db.refresh(acct)
    return VerifyResult(ok=error is None, error=error, account=account_read(db, acct, api_base=api_base))


def _close_native_notices_for(db: Session, acct: ProviderAccount) -> int:
    """A failed verify: every broker's "X ya cobra en línea" for the insurers this
    account served is closed now, unless another usable account still serves them."""
    from app.models.enums import NotificationKind, NotificationStatus  # noqa: PLC0415
    from app.models.notification import Notification  # noqa: PLC0415
    from app.services.collection.resolver import active_native_channels  # noqa: PLC0415

    insurer_ids = set(
        db.scalars(
            select(InsurerPaymentChannel.insurer_id).where(
                InsurerPaymentChannel.provider_account_id == acct.id
            )
        )
    )
    closed = 0
    moment = utcnow()
    for insurer_id in sorted(i for i in insurer_ids if i is not None):
        if active_native_channels(db, insurer_id):
            continue
        for row in db.scalars(
            select(Notification).where(
                Notification.origin_type == "insurer",
                Notification.origin_id == insurer_id,
                Notification.kind == NotificationKind.NATIVE_COLLECTION_AVAILABLE,
                Notification.status != NotificationStatus.CLOSED,
            )
        ):
            row.status = NotificationStatus.CLOSED
            row.closed_at = moment
            closed += 1
    return closed


@router.post("/provider-accounts/{account_id}/rotate-webhook-secret", response_model=ProviderAccountRead)
def rotate_account_webhook_secret(
    account_id: int,
    db: Session = Depends(get_db),
    api_base: str = Depends(request_api_base),
    _user: User = Depends(platform_gate("Manage")),
) -> ProviderAccountRead:
    """New ``sig`` secret for NEW mandates; the previous one stays valid for 30 days."""
    acct = _account_or_404(db, account_id)
    rotate_webhook_secret(acct)
    db.commit()
    db.refresh(acct)
    return account_read(db, acct, api_base=api_base)


# --- insurer channels -----------------------------------------------------------------------

def _upsert_channel(
    db: Session, insurer: Insurer, channel: PaymentChannel, fields: dict[str, Any]
) -> InsurerPaymentChannel:
    row = db.scalar(
        select(InsurerPaymentChannel).where(
            InsurerPaymentChannel.insurer_id == insurer.id, InsurerPaymentChannel.channel == channel
        )
    )
    if row is None:
        row = InsurerPaymentChannel(insurer_id=insurer.id, channel=channel, status=ChannelStatus.PENDING)
        db.add(row)
    if "provider_account_id" in fields:
        account_id = fields["provider_account_id"]
        if account_id is not None and db.get(ProviderAccount, account_id) is None:
            raise HTTPException(status_code=422, detail="La cuenta de proveedor indicada no existe")
        row.provider_account_id = account_id
    if "store_commerce_code" in fields:
        row.store_commerce_code = (fields["store_commerce_code"] or "").strip() or None
    if "fee_model" in fields:
        row.fee_model = fields["fee_model"]
    if fields.get("status") is not None:
        new_status = ChannelStatus(fields["status"])
        if new_status == ChannelStatus.ACTIVE and row.status != ChannelStatus.ACTIVE:
            row.activated_at = utcnow()
        row.status = new_status
    if row.status == ChannelStatus.ACTIVE and row.provider_account_id is None:
        raise HTTPException(status_code=422, detail="Un canal activo necesita una cuenta de proveedor")
    db.flush()
    return row


@router.get("/insurer-channels", response_model=list[ChannelRead])
def list_insurer_channels(
    insurer_id: int | None = Query(default=None),
    channel_status: ChannelStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("View")),
) -> list[ChannelRead]:
    stmt = select(InsurerPaymentChannel)
    if insurer_id is not None:
        stmt = stmt.where(InsurerPaymentChannel.insurer_id == insurer_id)
    if channel_status is not None:
        stmt = stmt.where(InsurerPaymentChannel.status == channel_status)
    rows = db.scalars(stmt.order_by(InsurerPaymentChannel.insurer_id, InsurerPaymentChannel.id)).all()
    return [channel_read(db, r) for r in rows]


@router.put("/insurer-channels", response_model=list[ChannelRead])
def bulk_upsert_insurer_channels(
    payload: list[ChannelBulkItem],
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("Edit")),
) -> list[ChannelRead]:
    """The channels matrix in one save (all-or-nothing)."""
    rows = []
    for item in payload:
        insurer = _insurer_or_404(db, item.insurer_id)
        rows.append(
            _upsert_channel(db, insurer, item.channel, item.model_dump(exclude_unset=True, exclude={"insurer_id", "channel"}))
        )
    db.commit()
    return [channel_read(db, r) for r in rows]


@router.put("/insurer-channels/{insurer_id}/{channel}", response_model=ChannelRead)
def upsert_insurer_channel(
    insurer_id: int,
    channel: PaymentChannel,
    payload: ChannelUpsert,
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("Edit")),
) -> ChannelRead:
    insurer = _insurer_or_404(db, insurer_id)
    row = _upsert_channel(db, insurer, channel, payload.model_dump(exclude_unset=True))
    db.commit()
    return channel_read(db, row)


# --- provider events ----------------------------------------------------------------------------

@router.get("/events", response_model=EventPage)
def list_events(
    status_filter: EventProcessing | None = Query(default=None, alias="status"),
    provider_account_id: int | None = Query(default=None),
    signature_valid: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("View")),
) -> EventPage:
    stmt = select(ProviderEvent)
    if status_filter is not None:
        stmt = stmt.where(ProviderEvent.processing_status == status_filter)
    if provider_account_id is not None:
        stmt = stmt.where(ProviderEvent.provider_account_id == provider_account_id)
    if signature_valid is not None:
        stmt = stmt.where(ProviderEvent.signature_valid.is_(signature_valid))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ProviderEvent.received_at.desc(), ProviderEvent.id.desc()).offset(offset).limit(limit)
    ).all()
    return EventPage(
        items=[EventRead.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def _event_or_404(db: Session, event_id: int) -> ProviderEvent:
    row = db.get(ProviderEvent, event_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")
    return row


@router.get("/events/{event_id}", response_model=EventDetail)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("View")),
) -> EventDetail:
    return EventDetail.model_validate(_event_or_404(db, event_id), from_attributes=True)


@router.post("/events/{event_id}/reprocess", response_model=EventDetail)
def reprocess_event(
    event_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("Manage")),
) -> EventDetail:
    """Run a stored, signature-valid event again (idempotent: never a second payment)."""
    event = _event_or_404(db, event_id)
    if not event.signature_valid:
        raise HTTPException(status_code=409, detail="Solo se reprocesan eventos con firma válida")
    acct = db.get(ProviderAccount, event.provider_account_id) if event.provider_account_id else None
    if acct is None:
        raise HTTPException(status_code=409, detail="El evento no tiene una cuenta de proveedor vigente")
    run_processing(db, event, acct)
    return EventDetail.model_validate(_event_or_404(db, event_id), from_attributes=True)


# --- job runs ----------------------------------------------------------------------------------------

@router.get("/jobs", response_model=JobPage)
def list_job_runs(
    job: str | None = Query(default=None, max_length=64),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(platform_gate("View")),
) -> JobPage:
    stmt = select(JobRun)
    if job:
        stmt = stmt.where(JobRun.job == job)
    if job_status is not None:
        stmt = stmt.where(JobRun.status == job_status)
    if since is not None:
        stmt = stmt.where(JobRun.scheduled_for >= since)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(JobRun.scheduled_for.desc(), JobRun.id.desc()).offset(offset).limit(limit)
    ).all()
    return JobPage(
        items=[JobRunRead.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
        registered=sorted(load_jobs()),
    )
