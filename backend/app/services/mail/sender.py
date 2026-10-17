"""Outbound mail with sender selection (v12, docs/v12/04-mail.md §2).

:func:`send_mail` is the ONE door every outbound mail goes through (payer
notices, status inquiries, account tests, ops alerts). It:

1. chooses the sender —
   * ``account`` is a :class:`MailAccount` -> that mailbox (it must be the
     broker's, ``connected`` and ``send_enabled``; otherwise the row is
     recorded ``failed`` with the reason, nothing is silently re-routed);
   * ``account == "auto"`` -> the broker's ``collection_settings``: when
     ``default_sender == broker_account`` and the chosen account is usable,
     that mailbox; else the Radal system mailbox;
   * ``account is None`` -> the Radal system mailbox.
   Through Radal the ``From`` is ``"{broker} vía Radal" <contacto@…>`` and the
   ``Reply-To`` is ``reply_to`` when given (inquiries pass the Radal address so
   the reply comes back to a mailbox Radal polls) else the broker's contact
   e-mail, so ordinary replies reach the broker;
2. builds a plain-text + simple HTML ``EmailMessage`` with our own
   ``Message-ID`` (kept on the row even when nothing is sent, so a reply's
   ``In-Reply-To`` can be matched);
3. records ONE ``mail_message(direction=outbound)`` — ``skipped`` when
   ``MAIL_ENABLED`` is off (no socket), ``sent`` / ``failed`` otherwise.

It NEVER raises: a failed send is a ``failed`` row with ``error``. The row is
added and flushed; the CALLER commits.
"""
from __future__ import annotations

import email.message
import html as html_lib
import logging
from datetime import datetime
from email.utils import formataddr, make_msgid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.base_class import utcnow
from app.models.broker import Broker
from app.models.collection_settings import CollectionSettings
from app.models.enums import MailAccountStatus, MailDirection, MailMessageStatus, MailSender
from app.models.mail import MailAccount, MailMessage
from app.services.mail.transport import (
    ImapConfig,
    MailTransportError,
    SmtpConfig,
    get_transport,
    radal_smtp_config,
)

logger = logging.getLogger(__name__)

AccountChoice = MailAccount | Literal["auto"] | None

REASON_ACCOUNT_UNUSABLE = "La casilla elegida no está conectada o no tiene el envío habilitado"
REASON_ACCOUNT_FOREIGN = "La casilla elegida no pertenece a esta corredora"
REASON_NO_RECIPIENT = "Sin destinatario"


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _domain_of(address: str | None) -> str:
    domain = (address or "").rpartition("@")[2].strip()
    return domain or "radalseguros.cl"


def account_password(account: MailAccount) -> str:
    """The decrypted password of a broker mailbox ('' when none is stored)."""
    if not account.secret_ciphertext:
        return ""
    value = decrypt_secret(account.secret_ciphertext)
    if isinstance(value, dict):
        return str(value.get("password") or "")
    return str(value or "")


def account_smtp_config(account: MailAccount) -> SmtpConfig:
    return SmtpConfig(
        host=account.smtp_host or "",
        port=int(account.smtp_port or 465),
        security=_val(account.smtp_security) or "ssl",
        username=account.username or account.address,
        password=account_password(account),
        timeout=int(settings.MAIL_TIMEOUT_SECONDS),
        from_address=account.address,
        restrict_network=True,  # broker-typed host: SSRF guard at connect time
    )


def account_imap_config(account: MailAccount) -> ImapConfig:
    return ImapConfig(
        host=account.imap_host or "",
        port=int(account.imap_port or 993),
        security=_val(account.imap_security) or "ssl",
        username=account.username or account.address,
        password=account_password(account),
        timeout=int(settings.MAIL_TIMEOUT_SECONDS),
        restrict_network=True,  # broker-typed host: SSRF guard at connect time
    )


def account_is_usable(account: MailAccount | None, broker_id: int | None) -> bool:
    """A broker mailbox we may send through right now."""
    return bool(
        account is not None
        and broker_id is not None
        and account.broker_id == broker_id
        and _val(account.status) == MailAccountStatus.CONNECTED.value
        and account.send_enabled
    )


def auto_account(db: Session, broker_id: int | None) -> MailAccount | None:
    """The broker account ``collection_settings`` asks for, when usable; else None."""
    if broker_id is None:
        return None
    row = db.scalar(select(CollectionSettings).where(CollectionSettings.broker_id == broker_id))
    if row is None or _val(row.default_sender) != MailSender.BROKER_ACCOUNT.value:
        return None
    if not row.sender_mail_account_id:
        return None
    account = db.get(MailAccount, row.sender_mail_account_id)
    return account if account_is_usable(account, broker_id) else None


def broker_label(db: Session, broker_id: int | None) -> str:
    broker = db.get(Broker, broker_id) if broker_id is not None else None
    if broker is None:
        return settings.RADAL_MAIL_DISPLAY_NAME
    return broker.trade_name or broker.legal_name


def radal_display_name(db: Session, broker_id: int | None) -> str:
    """``"{broker} vía Radal"`` (or just ``Radal`` for platform mail)."""
    if broker_id is None:
        return settings.RADAL_MAIL_DISPLAY_NAME
    return f"{broker_label(db, broker_id)} vía {settings.RADAL_MAIL_DISPLAY_NAME}"


def text_to_html(text: str) -> str:
    """A minimal, escaped HTML rendering of a plain-text body."""
    paragraphs = [p for p in (text or "").split("\n\n")]
    body = "".join(
        "<p>" + html_lib.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs if p.strip()
    )
    return (
        '<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:14px;line-height:1.5;color:#1f2937">' + body + "</body></html>"
    )


def build_email(
    *,
    from_name: str,
    from_address: str,
    to: list[str],
    cc: list[str] | None,
    subject: str,
    text: str,
    html: str | None,
    reply_to: str | None,
    message_id: str,
    kind: str | None = None,
    thread_token: str | None = None,
) -> email.message.EmailMessage:
    msg = email.message.EmailMessage()
    msg["From"] = formataddr((from_name, from_address))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if kind:
        msg["X-Radal-Kind"] = kind
    if thread_token:
        msg["X-Radal-Token"] = thread_token
    msg.set_content(text or "")
    msg.add_alternative(html if html is not None else text_to_html(text or ""), subtype="html")
    return msg


def _clean(addresses: list[str] | str | None) -> list[str]:
    if not addresses:
        return []
    if isinstance(addresses, str):
        addresses = [addresses]
    return [a.strip() for a in addresses if a and a.strip()]


def send_mail(
    db: Session,
    *,
    broker_id: int | None,
    kind: str,
    to: list[str] | str,
    cc: list[str] | str | None = None,
    subject: str,
    text: str,
    html: str | None = None,
    account: AccountChoice = None,
    reply_to: str | None = None,
    thread_token: str | None = None,
    now: datetime | None = None,
    skip_reason: str | None = None,
) -> MailMessage:
    """Record (and, when mail is on, send) one outbound mail. Never raises.

    ``skip_reason`` lets a caller record a deliberate non-send (e.g. the ops
    alert without a recipient) through the same row shape.
    """
    moment = now or utcnow()
    recipients = _clean(to)
    cc_list = _clean(cc)

    chosen: MailAccount | None = None
    account_error: str | None = None
    if isinstance(account, MailAccount):
        if broker_id is not None and account.broker_id != broker_id:
            account_error = REASON_ACCOUNT_FOREIGN
        elif not account_is_usable(account, broker_id):
            account_error = REASON_ACCOUNT_UNUSABLE
        chosen = account
    elif account == "auto":
        chosen = auto_account(db, broker_id)

    if chosen is not None and account_error is None:
        from_address = chosen.address
        from_name = chosen.display_name or broker_label(db, broker_id)
        effective_reply_to = reply_to
    else:
        from_address = settings.RADAL_MAIL_ADDRESS
        from_name = radal_display_name(db, broker_id)
        effective_reply_to = reply_to
        if effective_reply_to is None and broker_id is not None:
            broker = db.get(Broker, broker_id)
            effective_reply_to = (broker.email if broker is not None else None) or None

    message_id = make_msgid(domain=_domain_of(from_address))
    row = MailMessage(
        broker_id=broker_id,
        mail_account_id=chosen.id if (chosen is not None and account_error is None) else None,
        direction=MailDirection.OUTBOUND,
        message_id_header=message_id,
        thread_token=(thread_token or None) and thread_token[:32],
        from_address=from_address,
        to_addresses=recipients,
        cc_addresses=cc_list or None,
        subject=(subject or "")[:500],
        body_text=text or "",
        occurred_at=moment,
        status=MailMessageStatus.QUEUED,
    )

    if skip_reason:
        row.status = MailMessageStatus.SKIPPED
        row.error = skip_reason
    elif account_error:
        row.status = MailMessageStatus.FAILED
        row.error = account_error
    elif not settings.MAIL_ENABLED:
        row.status = MailMessageStatus.SKIPPED
    elif not recipients:
        row.status = MailMessageStatus.FAILED
        row.error = REASON_NO_RECIPIENT
    else:
        try:
            msg = build_email(
                from_name=from_name,
                from_address=from_address,
                to=recipients,
                cc=cc_list,
                subject=row.subject or "",
                text=text or "",
                html=html,
                reply_to=effective_reply_to,
                message_id=message_id,
                kind=kind,
                thread_token=thread_token,
            )
            cfg = account_smtp_config(chosen) if chosen is not None else radal_smtp_config()
            sent_id = get_transport().send(cfg, msg)
            row.message_id_header = (sent_id or message_id)[:255]
            row.status = MailMessageStatus.SENT
        except Exception as exc:  # noqa: BLE001 - a send never raises
            row.status = MailMessageStatus.FAILED
            # MailTransportError already reads "Class: message" (credentials scrubbed).
            text = str(exc) if isinstance(exc, MailTransportError) else f"{type(exc).__name__}: {exc}"
            row.error = text[:2000]
    try:
        db.add(row)
        db.flush()
    except Exception:  # noqa: BLE001 - never let the log break the caller
        logger.exception("send_mail: could not record the mail_message")
    logger.info("mail %s kind=%s status=%s", row.id, kind, _val(row.status))
    return row


__all__ = [
    "account_imap_config",
    "account_is_usable",
    "account_password",
    "account_smtp_config",
    "auto_account",
    "broker_label",
    "build_email",
    "radal_display_name",
    "send_mail",
    "text_to_html",
]
