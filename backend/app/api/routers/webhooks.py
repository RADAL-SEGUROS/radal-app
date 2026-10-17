"""``/webhooks/{provider}`` — the ONE generic provider webhook (v12, 02 §5, 03 §5).

Owner: lane **B3**. PUBLIC (no auth header): security is the ``account`` +
``sig`` query params plus the provider's own payload HMAC. Also reachable
through the API Gateway webhook ingress (08-infra §2) — same code.

Handler order (binding, 03 §5):

1. ``account`` → the provider account by ``webhook_key`` → **404** when unknown,
   of another provider, or disabled;
2. ``sig`` (constant-time; the rotated-out secret is accepted for 30 days) →
   **401**;
3. raw body ≤ 256 KB (**413** above) and a JSON object (**400** otherwise);
4. the provider's payload HMAC — the event is STORED either way
   (``signature_valid``); an invalid one then answers **401**. An invalid event
   never takes a genuine event's dedup key;
5. insert into the ``provider_event`` inbox with the dedup key — built from
   SIGNED fields only, so a replay with a mutated unsigned field is the same
   event → a duplicate answers 200 ``{"status": "duplicate"}`` (a duplicate
   whose processing FAILED is processed again, and so is another body of an
   event that was IGNORED — a mutated variant arriving first cannot block the
   genuine delivery);
6. process in the same request; any failure is recorded as
   ``processing_status=failed`` and STILL answers 200 — the provider must not
   hammer retries for our bugs; ``payments.sync`` reconciles and the
   maintainer console can reprocess.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.base_class import utcnow
from app.models.enums import EventProcessing, PaymentProviderName, ProviderAccountStatus
from app.models.provider import ProviderAccount, ProviderEvent
from app.services.payments.accounts import WEBHOOK_KINDS, verify_sig
from app.services.payments.providers import get_provider
from app.services.payments.providers.base import PaymentProviderError, infer_event_type
from app.services.payments.webhooks import dedup_key_for, run_processing, signed_digest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_BODY_BYTES = 256 * 1024


def _provider_name(provider: str) -> PaymentProviderName:
    try:
        return PaymentProviderName(provider)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from None


def _authenticate(db: Session, name: PaymentProviderName, account: str, sig: str) -> int:
    """Steps (1)-(2): the account by ``webhook_key`` (404) and the query ``sig`` (401)."""
    acct = (
        db.scalar(
            select(ProviderAccount).where(
                ProviderAccount.webhook_key == account, ProviderAccount.provider == name
            )
        )
        if account and len(account) <= 48
        else None
    )
    if acct is None or acct.status == ProviderAccountStatus.DISABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not verify_sig(acct, sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    return acct.id


async def _read_body(request: Request) -> bytes:
    """Step (3a): the raw body, capped at :data:`MAX_BODY_BYTES` (413 above)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload too large")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload too large")
    return raw


def _canonical(payload: object) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(payload)


def _ingest(db: Session, name: PaymentProviderName, acct_id: int, raw: bytes, kind: str | None) -> dict[str, str]:
    """Steps (3b)-(6): parse, verify the payload HMAC, store, dedupe, process."""
    acct = db.get(ProviderAccount, acct_id)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    # (4) the provider's payload HMAC
    impl = get_provider(name.value)
    try:
        valid = bool(impl.verify_webhook(acct, payload))
    except Exception:  # noqa: BLE001 - a verifier crash is an invalid signature
        logger.exception("webhook: verifier failed for account %s", acct_id)
        valid = False
    event_type = kind if kind in WEBHOOK_KINDS else infer_event_type(payload)
    parsed = None
    if valid:
        try:
            parsed = impl.parse_webhook(acct, payload)
        except (PaymentProviderError, ValueError, TypeError, KeyError):
            parsed = None
    signed = signed_digest(acct, payload) if valid else None
    dedup = dedup_key_for(parsed, raw=raw, event_type=event_type, valid=valid, signed=signed)
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid payload signature")

    # (5) the inbox
    existing = db.scalar(
        select(ProviderEvent).where(ProviderEvent.provider == name, ProviderEvent.dedup_key == dedup)
    )
    if existing is not None:
        if not valid:
            raise invalid
        if existing.processing_status == EventProcessing.IGNORED and _canonical(existing.payload) != _canonical(payload):
            # Same SIGNED content, another body: the key is signed-only, so a
            # variant whose UNSIGNED fields were mutated (e.g. a swapped
            # internalId) that got here first and was ignored must not block
            # this delivery. Process this body instead (still idempotent).
            existing.payload = payload
            existing.error = None
        elif existing.processing_status != EventProcessing.FAILED:
            return {"status": "duplicate"}
        result = run_processing(db, existing, acct)
        return {"status": "ok", "processing_status": result.value}

    event = ProviderEvent(
        provider=name,
        provider_account_id=acct.id,
        dedup_key=dedup,
        event_type=event_type,
        signature_valid=valid,
        payload=payload,
        received_at=utcnow(),
        processing_status=EventProcessing.RECEIVED if valid else EventProcessing.IGNORED,
        attempts=0,
        error=None if valid else "firma del proveedor inválida",
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:  # a concurrent delivery won the dedup key
        db.rollback()
        if not valid:
            raise invalid from None
        return {"status": "duplicate"}
    if not valid:
        raise invalid

    # (6) process in-request
    result = run_processing(db, event, acct)
    return {"status": "ok", "processing_status": result.value}


@router.post("/{provider}")
async def receive_webhook(
    provider: str,
    request: Request,
    account: str = Query(default=""),
    sig: str = Query(default=""),
    kind: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """The generic webhook. The DB work runs in the threadpool (sync session)."""
    name = _provider_name(provider)
    acct_id = await run_in_threadpool(_authenticate, db, name, account, sig)
    raw = await _read_body(request)
    return await run_in_threadpool(_ingest, db, name, acct_id, raw, kind)
