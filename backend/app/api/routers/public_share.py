"""``/public/share/{token}`` — the PUBLIC side of share links (v12, 02 §7).

Owner: lane **B5**. Pattern: ``offerings.public_router`` — the token is the
credential, no auth header, a narrow projection by audience. Unknown, revoked
and expired tokens are all **404** (indistinguishable), and so is a link whose
target row has gone.

What each audience sees (``app.services.share_links.build_public_view``, then
the whitelist schemas in ``app.schemas.share``):

* **insured** — broker brand (name + a token-scoped logo route), the policy
  summary, the cuotas of THAT policy / plan / cuota only with state and amounts
  in UF and CLP (``services.fx``), and per cuota the action: ``pay`` only when
  the cuota resolves to ``native`` and the link ``can_pay``; otherwise the
  insurer's payment instructions (Radal profile ⊕ broker override) and, with
  ``can_upload_receipt``, a comprobante upload;
* **insurer** — policy id/number, insured name, the listed cuotas (for a
  ``status_inquiry`` link ONLY the inquiry's ``installment_ids``) and the
  report-status form when ``can_report_status``.

Never: other policies, document keys, internal notes, commission, payer
contacts, other tenants.

Writes are suggestions, never payments (AI/human rule 6 analogue): a report or
a receipt becomes a ``payment_evidence`` (``status=suggested``) plus an
``evidence_pending`` to-do for the people who own the policy; a broker confirms
it in "Respuestas por confirmar". The online checkout is delegated to lane B3
(``services.payments.checkout.create_checkout_for_share``).

Rate limiting is infra-level (CloudFront/WAF); :func:`_throttle` is only a tiny
in-process guard against a script hammering one token's write endpoints.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routers.clients import record_activity
from app.api.routers.documents import store_generated_document
from app.core.config import settings
from app.models.base_class import utcnow
from app.models.broker import Broker
from app.models.document import DocumentCategory
from app.models.enums import (
    EntityType,
    EvidenceSource,
    EvidenceStatus,
    InquiryStatus,
    NotificationKind,
    NotificationTray,
    PaymentCurrency,
    ReportedStatus,
    ShareAudience,
)
from app.models.mail import PaymentEvidence
from app.models.share_link import ShareLink
from app.schemas.share import (
    PublicCheckoutCreate,
    PublicCheckoutRead,
    PublicReportCreate,
    PublicShareRead,
    PublicWriteResult,
)
from app.services.notifications import notify, recipients_for_policy
from app.services.payments.checkout import create_checkout_for_share
from app.services.payments.providers.base import PaymentProviderError
from app.services.share_links import (
    ShareScope,
    build_public_view,
    business_today,
    evidence_link_id,
    find_link_inquiry,
    link_permissions,
    link_reason,
    record_share_view,
    resolve_share_scope,
    resolve_share_token,
)
from app.services.storage import _media_bytes

router = APIRouter(prefix="/public/share", tags=["public"])

_GONE = "Este enlace no existe, fue revocado o ya expiró."
_NO_CUOTA = "Cuota no encontrada en este enlace."

#: Comprobantes: PDF or an image, ≤ 10 MB.
RECEIPT_MAX_BYTES = 10 * 1024 * 1024
_RECEIPT_TYPES: dict[str, tuple[str, bytes]] = {
    # mime -> (extension, magic prefix)
    "application/pdf": ("pdf", b"%PDF"),
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/jpg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG"),
}

# --- tiny in-process write guard ---------------------------------------------

THROTTLE_WINDOW_SECONDS = 600
THROTTLE_MAX_WRITES = 30
_writes: dict[str, deque[float]] = defaultdict(deque)
_writes_lock = threading.Lock()


def _throttle(token: str) -> None:
    """429 after :data:`THROTTLE_MAX_WRITES` writes per token per window."""
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    moment = time.monotonic()
    with _writes_lock:
        bucket = _writes[key]
        while bucket and moment - bucket[0] > THROTTLE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= THROTTLE_MAX_WRITES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados envíos desde este enlace. Intenta nuevamente en unos minutos.",
            )
        bucket.append(moment)


# --- shared resolution --------------------------------------------------------


def _now() -> datetime:
    return utcnow()


def _live_link_or_404(db: Session, token: str, now: datetime) -> ShareLink:
    link = resolve_share_token(db, token, now=now)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_GONE)
    return link


def _scope_or_404(db: Session, link: ShareLink) -> ShareScope:
    scope = resolve_share_scope(db, link)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_GONE)
    return scope


def _logo_path(db: Session, link: ShareLink) -> str | None:
    broker = db.get(Broker, link.broker_id)
    if broker is None or not broker.logo_key:
        return None
    return f"{settings.API_V1_PREFIX}/public/share/{link.token}/logo"


def _audience(link: ShareLink) -> str:
    return link.audience.value if hasattr(link.audience, "value") else str(link.audience)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _mark_inquiry_replied(db: Session, link: ShareLink, now: datetime) -> int | None:
    """The inquiry this link answers moves to ``replied``; returns its id."""
    inquiry = find_link_inquiry(db, link)
    if inquiry is None:
        return None
    if inquiry.status in (InquiryStatus.DRAFT, InquiryStatus.SENT, InquiryStatus.FAILED):
        inquiry.status = InquiryStatus.REPLIED
    if inquiry.replied_at is None and inquiry.status is InquiryStatus.REPLIED:
        inquiry.replied_at = now
    return inquiry.id


def _notify_evidence(
    db: Session,
    *,
    link: ShareLink,
    scope: ShareScope,
    evidence: PaymentEvidence,
    installment,  # noqa: ANN001
    now: datetime,
) -> None:
    policy = scope.policy_of(installment)
    insurer = getattr(policy, "insurer", None) if policy is not None else None
    notify(
        db,
        broker_id=link.broker_id,
        user_ids=recipients_for_policy(db, policy) if policy is not None else None,
        kind=NotificationKind.EVIDENCE_PENDING,
        tray=NotificationTray.TODO,
        origin_type="payment_evidence",
        origin_id=evidence.id,
        params={
            "source": evidence.source.value if evidence.source else None,
            "reported_status": evidence.reported_status.value if evidence.reported_status else None,
            "policy_number": policy.policy_number if policy is not None else None,
            "insurer": (insurer.trade_name or insurer.legal_name) if insurer is not None else None,
            "installment_number": installment.number,
            "due_date": installment.due_date,
            "amount": evidence.reported_amount,
            "currency": evidence.reported_currency.value if evidence.reported_currency else None,
        },
        link_path="/collections/evidence",
        now=now,
    )


def _check_paid_on(paid_on: date | None, now: datetime) -> None:
    if paid_on is not None and paid_on > business_today(now) + timedelta(days=1):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha de pago no puede estar en el futuro.",
        )


# =============================================================================
# GET — the projection
# =============================================================================


@router.get("/{token}", response_model=PublicShareRead)
def get_shared(token: str, db: Session = Depends(get_db)) -> PublicShareRead:
    """Resolve a share link. UNAUTHENTICATED by design; counts one view."""
    now = _now()
    link = _live_link_or_404(db, token, now)
    view = build_public_view(db, link, now=now, logo_url=_logo_path(db, link))
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_GONE)
    record_share_view(link, now=now)
    db.commit()
    return PublicShareRead.model_validate(view)


@router.get("/{token}/logo")
def get_shared_logo(token: str, db: Session = Depends(get_db)) -> Response:
    """The broker's logo for the branded header — never its storage key."""
    link = _live_link_or_404(db, token, _now())
    broker = db.get(Broker, link.broker_id)
    raw = _media_bytes(broker.logo_key) if broker is not None else None
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sin logo")
    key = broker.logo_key or ""
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else "webp"
    mime = {
        "webp": "image/webp",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
    }.get(ext, "image/webp")
    return Response(content=raw, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


# =============================================================================
# POST /report — the insurer answers "¿está pagada?"
# =============================================================================


@router.post("/{token}/report", response_model=PublicWriteResult, status_code=status.HTTP_201_CREATED)
def report_status(
    token: str,
    payload: PublicReportCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> PublicWriteResult:
    """An insurer reports a cuota's status -> ``payment_evidence`` (suggested)."""
    now = _now()
    link = _live_link_or_404(db, token, now)
    if _audience(link) != ShareAudience.INSURER.value or not link_permissions(link)["can_report_status"]:
        raise _forbidden("Este enlace no permite informar el estado de pago.")
    scope = _scope_or_404(db, link)
    inst = scope.installment(payload.installment_id)
    if inst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_CUOTA)
    _check_paid_on(payload.paid_on, now)
    _throttle(token)

    reported = ReportedStatus(payload.status)
    amount = payload.amount.quantize(Decimal("0.0001")) if payload.amount is not None else None
    currency = payload.currency if amount is not None else None
    comment = (payload.comment or "").strip() or None

    # Idempotent: the same report from the same link, still pending, is not
    # duplicated (and does not re-light the bell).
    for ev in db.scalars(
        select(PaymentEvidence).where(
            PaymentEvidence.broker_id == link.broker_id,
            PaymentEvidence.installment_id == inst.id,
            PaymentEvidence.source == EvidenceSource.INSURER_SHARE_LINK,
            PaymentEvidence.status.in_((EvidenceStatus.SUGGESTED, EvidenceStatus.NEEDS_REVIEW)),
        )
    ):
        if (
            evidence_link_id(ev) == link.id
            and ev.reported_status is reported
            and ev.reported_amount == amount
            and ev.reported_paid_on == payload.paid_on
            and (ev.note or None) == comment
        ):
            response.status_code = status.HTTP_200_OK
            return PublicWriteResult(
                installment_id=inst.id,
                reported_status=reported.value,
                created=False,
                message="Ya habíamos recibido este informe. Gracias.",
            )

    inquiry_id = _mark_inquiry_replied(db, link, now)
    evidence = PaymentEvidence(
        broker_id=link.broker_id,
        installment_id=inst.id,
        source=EvidenceSource.INSURER_SHARE_LINK,
        status_inquiry_id=inquiry_id,
        reported_status=reported,
        reported_amount=amount,
        reported_currency=currency,
        reported_paid_on=payload.paid_on,
        reasons=[link_reason(link, "Informado por la compañía mediante enlace público")],
        status=EvidenceStatus.SUGGESTED,
        note=comment,
    )
    db.add(evidence)
    db.flush()
    _notify_evidence(db, link=link, scope=scope, evidence=evidence, installment=inst, now=now)
    record_activity(
        db,
        broker_id=link.broker_id,
        user=None,
        action="share_link.insurer_report",
        entity_type=EntityType.INSTALLMENT,
        entity_id=inst.id,
        description="La compañía informó el estado de la cuota por enlace público",
        meta={"share_link_id": link.id, "evidence_id": evidence.id, "reported_status": reported.value},
    )
    db.commit()
    return PublicWriteResult(
        installment_id=inst.id,
        reported_status=reported.value,
        message="Gracias. La corredora revisará la información.",
    )


# =============================================================================
# POST /receipt — the payer uploads a comprobante
# =============================================================================


def _parse_amount(raw: str | None) -> Decimal | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Monto inválido."
        ) from exc
    if not value.is_finite() or value <= 0 or value >= Decimal("1e12"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Monto inválido.")
    return value.quantize(Decimal("0.0001"))


def _parse_date(raw: str | None) -> date | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Fecha de pago inválida."
        ) from exc


@router.post("/{token}/receipt", response_model=PublicWriteResult, status_code=status.HTTP_201_CREATED)
def upload_receipt(
    token: str,
    file: UploadFile = File(...),
    installment_id: int = Form(..., gt=0),
    amount: str | None = Form(default=None),
    currency: PaymentCurrency = Form(default=PaymentCurrency.UF),
    paid_on: str | None = Form(default=None),
    comment: str | None = Form(default=None, max_length=2000),
    db: Session = Depends(get_db),
) -> PublicWriteResult:
    """The payer uploads a comprobante -> document + ``payment_evidence`` (suggested)."""
    now = _now()
    link = _live_link_or_404(db, token, now)
    if _audience(link) != ShareAudience.INSURED.value or not link_permissions(link)["can_upload_receipt"]:
        raise _forbidden("Este enlace no permite subir comprobantes.")
    scope = _scope_or_404(db, link)
    inst = scope.installment(installment_id)
    if inst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_CUOTA)

    parsed_amount = _parse_amount(amount)
    parsed_paid_on = _parse_date(paid_on)
    _check_paid_on(parsed_paid_on, now)

    mime = (file.content_type or "").split(";")[0].strip().lower()
    spec = _RECEIPT_TYPES.get(mime)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Sube el comprobante en PDF, JPG o PNG.",
        )
    raw = file.file.read(RECEIPT_MAX_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="El archivo está vacío.")
    if len(raw) > RECEIPT_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="El comprobante supera el máximo de 10 MB.",
        )
    extension, magic = spec
    if not raw.startswith(magic):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="El archivo no corresponde a un PDF, JPG o PNG válido.",
        )
    _throttle(token)

    original = (file.filename or "").strip().replace("/", "_").replace("\\", "_")[:200]
    if not original:
        original = f"comprobante.{extension}"
    if not original.lower().endswith(f".{extension}") and not (
        extension == "jpg" and original.lower().endswith(".jpeg")
    ):
        original = f"{original}.{extension}"

    plan = inst.collection_plan
    document = store_generated_document(
        db,
        broker_id=link.broker_id,
        entity_type=EntityType.INSTALLMENT,
        entity_id=inst.id,
        category=DocumentCategory.PAYMENT_RECEIPT,
        data=raw,
        original_name=original,
        mime_type="image/jpeg" if mime == "image/jpg" else mime,
        phase="public_share",
    )
    if plan is not None and plan.case_file_id is not None:
        document.case_file_id = plan.case_file_id

    clean_comment = (comment or "").strip() or None
    evidence = PaymentEvidence(
        broker_id=link.broker_id,
        installment_id=inst.id,
        source=EvidenceSource.PAYER_UPLOAD,
        document_id=document.id,
        reported_status=ReportedStatus.PAID,
        reported_amount=parsed_amount,
        reported_currency=currency if parsed_amount is not None else None,
        reported_paid_on=parsed_paid_on,
        reasons=[link_reason(link, "Comprobante subido por el pagador mediante enlace público")],
        status=EvidenceStatus.SUGGESTED,
        note=clean_comment,
    )
    db.add(evidence)
    db.flush()
    _notify_evidence(db, link=link, scope=scope, evidence=evidence, installment=inst, now=now)
    record_activity(
        db,
        broker_id=link.broker_id,
        user=None,
        action="share_link.payer_receipt",
        entity_type=EntityType.INSTALLMENT,
        entity_id=inst.id,
        description="El pagador subió un comprobante por enlace público",
        meta={"share_link_id": link.id, "evidence_id": evidence.id, "document_id": document.id},
    )
    db.commit()
    return PublicWriteResult(
        installment_id=inst.id,
        reported_status=ReportedStatus.PAID.value,
        message="Recibimos tu comprobante. La corredora lo revisará y confirmará el pago.",
    )


# =============================================================================
# POST /checkout — native online payment (executed by lane B3)
# =============================================================================

_CHECKOUT_FAILED = "No pudimos abrir el pago en línea. Intenta nuevamente en unos minutos."


def _checkout_service():
    """Lane B3's ``create_checkout_for_share`` (a seam tests monkeypatch)."""
    return create_checkout_for_share


def _checkout_refusal(exc: Exception) -> tuple[int, str] | None:
    """``(status, Spanish detail)`` for a refusal of the checkout service.

    Lane B3 raises ``CheckoutError(status_code, detail)`` (duck-typed, so test
    doubles may raise their own); the generic families map to 404 / 409 / 502.
    """
    code = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if isinstance(code, int) and 400 <= code < 600:
        return code, str(detail or exc) or "No se pudo abrir el pago en línea."
    if isinstance(exc, LookupError):
        return status.HTTP_404_NOT_FOUND, _NO_CUOTA
    if isinstance(exc, PaymentProviderError):
        return (
            status.HTTP_502_BAD_GATEWAY,
            _CHECKOUT_FAILED,
        )
    if isinstance(exc, ValueError):
        return status.HTTP_409_CONFLICT, str(exc) or "Estas cuotas no se pueden pagar en línea."
    return None


def _checkout_url(result: Any) -> str | None:
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("checkout_url")
    return getattr(result, "checkout_url", None)


@router.post("/{token}/checkout", response_model=PublicCheckoutRead)
def checkout(
    token: str,
    payload: PublicCheckoutCreate,
    db: Session = Depends(get_db),
) -> PublicCheckoutRead:
    """Open an online checkout for some of the link's cuotas -> ``{checkout_url}``."""
    now = _now()
    link = _live_link_or_404(db, token, now)
    if _audience(link) != ShareAudience.INSURED.value or not link_permissions(link)["can_pay"]:
        raise _forbidden("Este enlace no permite pagar en línea.")
    scope = _scope_or_404(db, link)
    ids = list(dict.fromkeys(payload.installment_ids))
    if any(scope.installment(i) is None for i in ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_CUOTA)
    service = _checkout_service()
    _throttle(token)
    try:
        result = service(db, link, ids, now=now)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - mapped below, never a bare 500 to a payer
        refusal = _checkout_refusal(exc)
        if refusal is None:
            db.rollback()
            raise
        code, detail = refusal
        if code >= 500:
            # B3 leaves the intent ``failed`` on a provider error: keep that trace.
            db.commit()
        else:
            db.rollback()
        raise HTTPException(status_code=code, detail=detail) from exc
    url = _checkout_url(result)
    if not url:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_CHECKOUT_FAILED)
    db.commit()
    return PublicCheckoutRead(checkout_url=url)


__all__ = ["router", "RECEIPT_MAX_BYTES", "THROTTLE_MAX_WRITES"]
