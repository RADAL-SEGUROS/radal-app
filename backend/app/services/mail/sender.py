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
3. sends it — a broker mailbox through :func:`get_transport` (its own
   SMTP), the Radal system sender through :func:`get_system_transport`
   (Amazon SES when ``MAIL_TRANSPORT=ses``, Zoho SMTP otherwise). SES rewrites
   the ``Message-ID``; the id the transport RETURNS is what the row keeps,
   so ``In-Reply-To`` matching of the insurer's reply keeps working;
4. records ONE ``mail_message(direction=outbound)`` — ``skipped`` when
   ``MAIL_ENABLED`` is off (no socket, no boto3 client), ``sent`` / ``failed``
   otherwise.

**The visual copy.** With ``MAIL_SENT_COPY=bcc`` (the default) every mail of
the Radal SYSTEM sender Bcc's ``MAIL_COPY_ADDRESS`` (the Zoho ``contacto@``
mailbox) unless it is already a To/Cc recipient. The Bcc rides only in the
SES envelope (``Destination.BccAddresses``; smtplib strips it the same way) —
never in the raw headers. Broker custom mailboxes never get it.

**Dev allowlist.** ``MAIL_RECIPIENT_ALLOWLIST`` (domains or full addresses,
comma-separated; blank = no restriction) drops To/Cc outside it, for the
system sender AND broker mailboxes. Nothing left => ``skipped`` with
:data:`REASON_NOT_ALLOWLISTED`. ``MAIL_COPY_ADDRESS`` always passes (the Bcc
copy and the inbound forward).

**Reply-To of mail Radal must read the answer of** (status inquiries): the
caller passes :func:`radal_reply_address` — ``RADAL_MAIL_REPLY_TO`` when set
(``respuestas@correo.radalseguros.cl``: SES inbound -> S3 -> ``mail.poll``),
else ``RADAL_MAIL_ADDRESS``.

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
    get_system_transport,
    get_transport,
    radal_smtp_config,
)

logger = logging.getLogger(__name__)

AccountChoice = MailAccount | Literal["auto"] | None

REASON_ACCOUNT_UNUSABLE = "La casilla elegida no está conectada o no tiene el envío habilitado"
REASON_ACCOUNT_FOREIGN = "La casilla elegida no pertenece a esta corredora"
REASON_NO_RECIPIENT = "Sin destinatario"
REASON_NOT_ALLOWLISTED = "Destinatario fuera de la lista permitida de este ambiente"


def _val(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _domain_of(address: str | None) -> str:
    domain = (address or "").rpartition("@")[2].strip()
    return domain or "radalseguros.cl"


def radal_reply_address() -> str:
    """Reply-To of system mail whose answer Radal must process (inquiries)."""
    return (settings.RADAL_MAIL_REPLY_TO or "").strip() or settings.RADAL_MAIL_ADDRESS


def copy_address() -> str:
    """The human mailbox that gets the Bcc copy and the inbound forwards ('' = none)."""
    return (settings.MAIL_COPY_ADDRESS or "").strip()


def allowlist_entries() -> list[str]:
    """``MAIL_RECIPIENT_ALLOWLIST`` normalised (lower-case, ``@`` stripped from domains)."""
    raw = settings.MAIL_RECIPIENT_ALLOWLIST or ""
    entries: list[str] = []
    for item in raw.replace(";", ",").split(","):
        token = item.strip().lower()
        if not token:
            continue
        if token.startswith("@"):
            token = token[1:]
        if token and token not in entries:
            entries.append(token)
    return entries


def recipient_allowed(address: str, entries: list[str] | None = None) -> bool:
    """May this environment send to ``address``? (blank allowlist = yes)."""
    entries = allowlist_entries() if entries is None else entries
    if not entries:
        return True
    addr = (address or "").strip().lower()
    if not addr:
        return False
    if copy_address() and addr == copy_address().lower():
        return True
    domain = addr.rpartition("@")[2]
    for entry in entries:
        if "@" in entry:
            if addr == entry:
                return True
        elif domain == entry:
            return True
    return False


def apply_allowlist(addresses: list[str]) -> tuple[list[str], list[str]]:
    """``(kept, dropped)`` under ``MAIL_RECIPIENT_ALLOWLIST``."""
    entries = allowlist_entries()
    if not entries:
        return list(addresses), []
    kept = [a for a in addresses if recipient_allowed(a, entries)]
    dropped = [a for a in addresses if a not in kept]
    return kept, dropped


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
    bcc: list[str] | None = None,
    attach_rfc822: bytes | None = None,
    attachment_name: str = "mensaje-original.eml",
) -> email.message.EmailMessage:
    """``bcc`` becomes a ``Bcc`` header the transports turn into envelope-only
    recipients (SES ``BccAddresses`` / smtplib) and strip from the raw.
    ``attach_rfc822`` is attached verbatim as a ``message/rfc822`` part."""
    msg = email.message.EmailMessage()
    msg["From"] = formataddr((from_name, from_address))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
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
    if attach_rfc822 is not None:
        attach_original(msg, attach_rfc822, filename=attachment_name)
    return msg


def attach_original(msg: email.message.EmailMessage, raw: bytes, *, filename: str) -> None:
    """Attach ``raw`` (an RFC 822 message) as a ``message/rfc822`` part.

    A 7-bit original (nearly all real mail: QP/base64 bodies) is attached
    byte-for-byte — ``add_attachment`` would re-parse and re-fold it — with
    line endings normalised to CRLF. An 8-bit original cannot travel as a raw
    string through the bytes generator, so it is attached as a parsed
    sub-message (same content, headers possibly re-folded, part ``8bit``;
    RFC 2046 forbids base64 on ``message/rfc822``).
    """
    from email import message_from_bytes  # noqa: PLC0415
    from email import policy as email_policy  # noqa: PLC0415
    from email.mime.message import MIMEMessage  # noqa: PLC0415

    data = (raw or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")
    if not data.endswith(b"\r\n"):
        data += b"\r\n"
    if msg.get_content_maintype() != "multipart" or msg.get_content_subtype() != "mixed":
        msg.make_mixed()
    if data.isascii():
        part = email.message.Message()
        part["Content-Type"] = "message/rfc822"
        part["Content-Transfer-Encoding"] = "7bit"
        part.set_payload(data.decode("ascii"))
    else:
        part = MIMEMessage(message_from_bytes(data, policy=email_policy.compat32))
        part["Content-Transfer-Encoding"] = "8bit"
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)


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
    attach_rfc822: bytes | None = None,
    attachment_name: str = "mensaje-original.eml",
    provider_uid: str | None = None,
    record_subject: str | None = None,
    record_text: str | None = None,
) -> MailMessage:
    """Record (and, when mail is on, send) one outbound mail. Never raises.

    ``skip_reason`` lets a caller record a deliberate non-send (e.g. the ops
    alert without a recipient) through the same row shape.
    ``attach_rfc822`` attaches an original message (the inbound forward).
    ``provider_uid`` tags the row (the forward's idempotency key).
    ``record_subject`` / ``record_text`` replace what the ROW keeps when the
    sent content must not be stored (a forward of unrelated mail, privacy).
    """
    moment = now or utcnow()
    requested_to = _clean(to)
    requested_cc = _clean(cc)
    recipients, dropped_to = apply_allowlist(requested_to)
    cc_list, dropped_cc = apply_allowlist(requested_cc)
    not_allowlisted = bool(dropped_to or dropped_cc) and not (recipients or cc_list)
    if dropped_to or dropped_cc:
        logger.info(
            "mail kind=%s: %d recipient(s) outside MAIL_RECIPIENT_ALLOWLIST dropped",
            kind, len(dropped_to) + len(dropped_cc),
        )
    if not_allowlisted:
        # Keep who it WOULD have gone to on the row, so the log explains itself.
        recipients, cc_list = requested_to, requested_cc

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

    via_system = chosen is None or account_error is not None
    bcc_list: list[str] = []
    copy_to = copy_address()
    if (
        via_system
        and (settings.MAIL_SENT_COPY or "").lower() == "bcc"
        and copy_to
        and copy_to.lower() not in {a.lower() for a in recipients + cc_list}
    ):
        bcc_list = [copy_to]

    message_id = make_msgid(domain=_domain_of(from_address))
    full_subject = (subject or "")[:500]
    row = MailMessage(
        broker_id=broker_id,
        mail_account_id=chosen.id if (chosen is not None and account_error is None) else None,
        direction=MailDirection.OUTBOUND,
        message_id_header=message_id,
        thread_token=(thread_token or None) and thread_token[:32],
        from_address=from_address,
        to_addresses=recipients,
        cc_addresses=cc_list or None,
        subject=(record_subject if record_subject is not None else full_subject)[:500],
        body_text=record_text if record_text is not None else (text or ""),
        occurred_at=moment,
        provider_uid=(provider_uid or None) and provider_uid[:64],
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
    elif not_allowlisted:
        row.status = MailMessageStatus.SKIPPED
        row.error = REASON_NOT_ALLOWLISTED
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
                subject=full_subject,
                text=text or "",
                html=html,
                reply_to=effective_reply_to,
                message_id=message_id,
                kind=kind,
                thread_token=thread_token,
                bcc=bcc_list or None,
                attach_rfc822=attach_rfc822,
                attachment_name=attachment_name,
            )
            if chosen is not None:
                # A broker's own mailbox: always its own SMTP, never SES.
                cfg, transport = account_smtp_config(chosen), get_transport()
            else:
                cfg, transport = radal_smtp_config(), get_system_transport()
            sent_id = transport.send(cfg, msg)
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
    "REASON_NOT_ALLOWLISTED",
    "account_imap_config",
    "account_is_usable",
    "account_password",
    "account_smtp_config",
    "allowlist_entries",
    "apply_allowlist",
    "attach_original",
    "auto_account",
    "broker_label",
    "build_email",
    "copy_address",
    "radal_display_name",
    "radal_reply_address",
    "recipient_allowed",
    "send_mail",
    "text_to_html",
]
