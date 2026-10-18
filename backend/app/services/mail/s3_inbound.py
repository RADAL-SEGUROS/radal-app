"""Replies to Radal, received by Amazon SES inbound and read from S3 (v12, tech-ref §19.7).

``contacto@radalseguros.cl`` is a Zoho Free mailbox without IMAP, so the mail
Radal must READ (insurer answers to status inquiries) is addressed to
``RADAL_MAIL_REPLY_TO`` = ``respuestas@correo.radalseguros.cl``. SES inbound
(MX of ``correo.radalseguros.cl``) writes each message as raw RFC 822 MIME to
``s3://{MAIL_INBOUND_S3_BUCKET or S3_BUCKET}/{MAIL_INBOUND_S3_PREFIX}{SES message id}``.

With ``MAIL_INBOUND_SOURCE=s3``, ``mail.poll`` calls :func:`poll_s3_inbound`
for the Radal system mailbox instead of IMAP. Per run, oldest first, at most
``MAIL_INBOUND_S3_MAX_PER_RUN`` objects directly under the prefix (the
``processed/`` sub-prefix is never listed — ``Delimiter="/"``):

1. **dedupe** on ``(system mail_account, provider_uid = the S3 key)`` — the
   same unique key the IMAP path uses;
2. **not mail?** (``AMAZON_SES_SETUP_NOTIFICATION``, or bytes without a From
   and a Date / Message-ID) or **SES virus verdict FAIL** -> one ``ignored``
   row (no content), moved to ``processed/``, never forwarded;
3. otherwise the SAME pipeline as IMAP mail
   (:func:`app.services.mail.inbound.process_message`: prefilter, ``.eml``
   document, AI classification with its ``extraction`` row, token /
   In-Reply-To matching, ``payment_evidence``, inquiry ``replied``, the bell,
   bounces);
4. **forward** a copy to ``MAIL_COPY_ADDRESS`` (the human Zoho mailbox)
   through the system SES transport: ``Fwd: <subject>``, a Spanish note, the
   original attached as ``message/rfc822`` — once per object (an outbound
   ``mail_message`` tagged ``provider_uid = "fwd:" + key`` with status
   ``sent``/``skipped`` marks it done). Radal's own mail (an ``X-Radal-Kind``
   header) is never forwarded (no loop). For mail the prefilter ignored, the
   ROW keeps no subject/body (privacy), though the forward itself is complete;
5. **commit, then move** the object to ``{prefix}processed/{tail}`` (copy +
   delete). Any failure (read, processing, forward, move) is counted and
   logged, never raised, and the object stays for the next run — the dedupe
   row makes the retry resume where it stopped.

Tests install a fake client through :data:`_OVERRIDE` (``fake_s3_inbound``).
"""
from __future__ import annotations

import functools
import hashlib
import logging
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import parseaddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import MailAccountStatus, MailDirection, MailMessageStatus
from app.models.mail import MailAccount, MailMessage
from app.services.mail.transport import FetchedMail

logger = logging.getLogger(__name__)

#: Objects SES writes that are not mail (the rule-set setup probe).
NOT_MAIL_KEYS = frozenset({"AMAZON_SES_SETUP_NOTIFICATION"})
PROCESSED_DIR = "processed/"
FORWARD_KIND = "inbound_forward"
ERROR_NOT_MAIL = "not_an_email"
ERROR_VIRUS = "virus_verdict_fail"
REASON_S3_UNREADABLE = "No se pudo leer el buzón de respuestas de Radal (S3)"
#: S3 keys longer than this are hashed so ``"fwd:" + uid`` fits ``String(64)``.
_UID_MAX = 60
_MAX_PAGES = 20

#: Test seam: when set, :func:`get_s3_client` returns it.
_OVERRIDE: Any | None = None


@functools.lru_cache(maxsize=2)
def _s3_client(region: str, timeout: int):  # noqa: ANN202
    """One lazily-built ``s3`` client (standard boto3 credential chain)."""
    import boto3  # noqa: PLC0415 - lazy: no AWS import on the hermetic path
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        "s3",
        region_name=region,
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def get_s3_client() -> Any:
    if _OVERRIDE is not None:
        return _OVERRIDE
    return _s3_client(settings.S3_REGION, int(settings.MAIL_TIMEOUT_SECONDS))


def inbound_prefix() -> str:
    return settings.MAIL_INBOUND_S3_PREFIX or "inbound-mail/"


def processed_key(key: str, prefix: str | None = None) -> str:
    prefix = inbound_prefix() if prefix is None else prefix
    tail = key[len(prefix):] if key.startswith(prefix) else key.rpartition("/")[2]
    return f"{prefix}{PROCESSED_DIR}{tail}"


def s3_provider_uid(key: str) -> str:
    """``provider_uid`` of an S3 object: the key (hashed when too long)."""
    if len(key) <= _UID_MAX:
        return key
    return "s3:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[: _UID_MAX - 3]


def forward_uid(uid: str) -> str:
    return f"fwd:{uid}"[:64]


def list_pending(client: Any, bucket: str, prefix: str, *, limit: int) -> list[dict[str, Any]]:
    """Objects directly under ``prefix`` (never ``processed/``), oldest first."""
    found: list[dict[str, Any]] = []
    token: str | None = None
    for _page in range(_MAX_PAGES):
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
        if token:
            request["ContinuationToken"] = token
        response = client.list_objects_v2(**request) or {}
        for item in response.get("Contents") or []:
            key = str(item.get("Key") or "")
            if not key or key == prefix or key.endswith("/"):
                continue
            if key.startswith(prefix + PROCESSED_DIR):
                continue  # belt and braces: Delimiter already hides it
            found.append(item)
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
        if not token:
            break
    found.sort(key=lambda o: (str(o.get("LastModified") or ""), str(o.get("Key"))))
    return found[: max(0, int(limit))]


def count_pending(client: Any, bucket: str, prefix: str, *, max_keys: int = 1000) -> int:
    """How many objects wait under ``prefix`` (one page, ``MaxKeys``; preflight)."""
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=max_keys) or {}
    return sum(
        1
        for item in response.get("Contents") or []
        if str(item.get("Key") or "") not in ("", prefix) and not str(item.get("Key")).endswith("/")
    )


def _read(client: Any, bucket: str, key: str) -> bytes:
    body = (client.get_object(Bucket=bucket, Key=key) or {}).get("Body")
    data = body.read() if hasattr(body, "read") else body
    return bytes(data or b"")


def _move(client: Any, bucket: str, key: str, prefix: str) -> None:
    client.copy_object(
        Bucket=bucket, Key=processed_key(key, prefix), CopySource={"Bucket": bucket, "Key": key}
    )
    client.delete_object(Bucket=bucket, Key=key)


def looks_like_mail(tail: str, raw: bytes) -> bool:
    """An RFC 822 message with a From and a Date or Message-ID."""
    if tail in NOT_MAIL_KEYS or not raw or not raw.strip():
        return False
    try:
        msg = message_from_bytes(raw, policy=policy.default)
        sender = parseaddr(str(msg.get("From", "") or ""))[1]
        dated = bool(str(msg.get("Date", "") or "").strip())
        identified = bool(str(msg.get("Message-ID", "") or "").strip())
    except Exception:  # noqa: BLE001 - unparseable = not mail
        return False
    return bool(sender and "@" in sender and (dated or identified))


def virus_flagged(raw: bytes) -> bool:
    try:
        msg = message_from_bytes(raw, policy=policy.default)
        return str(msg.get("X-SES-Virus-Verdict", "") or "").strip().upper() == "FAIL"
    except Exception:  # noqa: BLE001
        return False


def is_radal_mail(raw: bytes) -> bool:
    """Our own mail (it carries ``X-Radal-Kind``): never forwarded (loop guard)."""
    try:
        return bool(message_from_bytes(raw, policy=policy.default).get("X-Radal-Kind"))
    except Exception:  # noqa: BLE001
        return False


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _outcome(row: MailMessage) -> str:
    if _val(row.status) == MailMessageStatus.IGNORED.value:
        return "no corresponde a un correo de Radal; no se guardó en Radal."
    classification = _val(row.classification)
    if classification == "bounce":
        return "rebote de un correo enviado por Radal; se marcó el aviso como rebotado."
    if row.thread_token:
        return f"respuesta a la consulta RADAL-{row.thread_token}; quedó en la bandeja de evidencias."
    return f"asociado a un correo de Radal y clasificado ({classification or 'sin clasificar'})."


def forward_copy(
    db: Session, *, inbound: MailMessage, raw: bytes, uid: str, now: datetime
) -> tuple[MailMessage | None, bool]:
    """Forward ``raw`` to ``MAIL_COPY_ADDRESS`` (once).

    Returns ``(row, attempted)``: ``(None, False)`` = no copy address;
    ``(done_row, False)`` = already forwarded; ``(new_row, True)`` = attempted
    now (``sent`` / ``failed`` / ``skipped``).
    """
    from app.services.mail.sender import copy_address, send_mail  # noqa: PLC0415 - cycle

    to = copy_address()
    if not to:
        return None, False
    done = db.scalar(
        select(MailMessage).where(
            MailMessage.direction == MailDirection.OUTBOUND,
            MailMessage.provider_uid == forward_uid(uid),
            MailMessage.status.in_([MailMessageStatus.SENT, MailMessageStatus.SKIPPED]),
        )
    )
    if done is not None:
        return done, False
    msg = message_from_bytes(raw, policy=policy.default)
    original_subject = str(msg.get("Subject", "") or "").strip()
    original_from = str(msg.get("From", "") or "").strip()
    original_date = str(msg.get("Date", "") or "").strip()
    received_at = (
        (settings.RADAL_MAIL_REPLY_TO or "").strip()
        or parseaddr(str(msg.get("To", "") or ""))[1]
        or "el buzón de respuestas"
    )
    text = (
        f"Recibido en {received_at} y procesado por Radal.\n\n"
        f"De: {original_from or '—'}\n"
        f"Fecha: {original_date or '—'}\n"
        f"Asunto: {original_subject or '(sin asunto)'}\n\n"
        f"Resultado: {_outcome(inbound)}\n\n"
        "El mensaje original va adjunto."
    )
    ignored = _val(inbound.status) == MailMessageStatus.IGNORED.value
    reply_to = parseaddr(original_from)[1] or None
    row = send_mail(
        db,
        broker_id=None,
        kind=FORWARD_KIND,
        to=[to],
        subject=f"Fwd: {original_subject or '(sin asunto)'}",
        text=text,
        account=None,
        reply_to=reply_to,
        now=now,
        attach_rfc822=raw,
        attachment_name="mensaje-original.eml",
        provider_uid=forward_uid(uid),
        # Unrelated mail: the forward is complete, the ROW keeps nothing of it.
        record_subject="Fwd: (correo no relacionado; contenido no guardado)" if ignored else None,
        record_text="" if ignored else None,
    )
    return row, True


def _ignored_row(account: MailAccount, uid: str, error: str, now: datetime) -> MailMessage:
    return MailMessage(
        broker_id=None,
        mail_account_id=account.id,
        direction=MailDirection.INBOUND,
        provider_uid=uid,
        occurred_at=now,
        status=MailMessageStatus.IGNORED,
        error=error,
    )


def poll_s3_inbound(db: Session, account: MailAccount, *, now: datetime) -> dict[str, int]:
    """Read new SES-inbound objects for the Radal system mailbox. Never raises.

    Commits after each object (before moving it), so a crash never leaves a
    moved object whose processing was rolled back.
    """
    from app.services.mail.inbound import _account_failed, process_message  # noqa: PLC0415 - cycle

    stats = {"fetched": 0, "kept": 0, "ignored": 0, "errors": 0, "forwarded": 0, "moved": 0, "not_mail": 0}
    bucket, prefix = settings.mail_inbound_bucket, inbound_prefix()
    limit = max(1, int(settings.MAIL_INBOUND_S3_MAX_PER_RUN or 50))
    try:
        client = get_s3_client()
        objects = list_pending(client, bucket, prefix, limit=limit)
    except Exception:  # noqa: BLE001
        logger.warning("mail.poll s3: cannot list s3://%s/%s", bucket, prefix, exc_info=True)
        stats["errors"] += 1
        _account_failed(db, account, REASON_S3_UNREADABLE, now=now)
        db.flush()
        return stats

    account_id = account.id
    db.commit()  # the (lazily created) system account survives a per-object rollback
    last_error: str | None = None
    for obj in objects:
        key = str(obj["Key"])
        tail = key[len(prefix):]
        uid = s3_provider_uid(key)
        stats["fetched"] += 1
        try:
            row = db.scalar(
                select(MailMessage).where(
                    MailMessage.mail_account_id == account_id, MailMessage.provider_uid == uid
                )
            )
            raw: bytes | None = None
            if row is None:
                raw = _read(client, bucket, key)
                if not looks_like_mail(tail, raw):
                    reason = ERROR_NOT_MAIL
                elif virus_flagged(raw):
                    reason = ERROR_VIRUS
                else:
                    reason = None
                if reason is not None:
                    row = _ignored_row(account, uid, reason, now)
                    db.add(row)
                    db.flush()
                    stats["not_mail"] += 1
                else:
                    with db.begin_nested():
                        row = process_message(
                            db,
                            account,
                            FetchedMail(uid=tail[-40:] or uid, raw=raw, uidvalidity=None),
                            now=now,
                            provider_uid=uid,
                        )
                    is_ignored = _val(row.status) == MailMessageStatus.IGNORED.value
                    stats["ignored" if is_ignored else "kept"] += 1
            if row.error not in (ERROR_NOT_MAIL, ERROR_VIRUS):
                if raw is None:
                    raw = _read(client, bucket, key)
                if not is_radal_mail(raw):
                    forwarded, attempted = forward_copy(db, inbound=row, raw=raw, uid=uid, now=now)
                    status = _val(forwarded.status) if forwarded is not None else None
                    if attempted and status == MailMessageStatus.FAILED.value:
                        db.commit()  # keep the processing; the forward is retried next run
                        stats["errors"] += 1
                        last_error = "No se pudo reenviar una copia a la casilla de contacto"
                        continue
                    if attempted and status == MailMessageStatus.SENT.value:
                        stats["forwarded"] += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one bad object must not block the box
            db.rollback()
            stats["errors"] += 1
            last_error = "No se pudo procesar un correo recibido"
            logger.exception("mail.poll s3: object %s failed; left for the next run", key)
            account = db.get(MailAccount, account_id) or account
            continue
        try:
            _move(client, bucket, key, prefix)
            stats["moved"] += 1
        except Exception:  # noqa: BLE001 - the dedupe row makes the retry a plain move
            stats["errors"] += 1
            last_error = "No se pudo archivar un correo recibido en processed/"
            logger.warning("mail.poll s3: could not move %s to processed/", key, exc_info=True)

    account = db.get(MailAccount, account_id) or account
    account.last_sync_at = now
    account.status = MailAccountStatus.CONNECTED
    account.last_error = last_error
    db.flush()
    return stats


__all__ = [
    "ERROR_NOT_MAIL",
    "ERROR_VIRUS",
    "FORWARD_KIND",
    "NOT_MAIL_KEYS",
    "count_pending",
    "forward_copy",
    "forward_uid",
    "get_s3_client",
    "inbound_prefix",
    "is_radal_mail",
    "list_pending",
    "looks_like_mail",
    "poll_s3_inbound",
    "processed_key",
    "s3_provider_uid",
]
