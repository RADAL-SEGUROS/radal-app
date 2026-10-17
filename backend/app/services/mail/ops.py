"""Tell the RADAL team something broke (v12, 06-rpa §1, 04-mail §1).

``notify_ops(db, subject, body)`` is the phase-1 helper the RPA engine and the
jobs call on failure. It ALWAYS records an outbound ``mail_message`` (broker
``NULL`` — this is Radal's own mail, not a tenant's) and only touches the
network when ``MAIL_ENABLED`` is on AND ``RADAL_OPS_EMAIL`` is set:

* ``MAIL_ENABLED = false``   -> status ``skipped`` (no socket; the test default);
* no ``RADAL_OPS_EMAIL``     -> status ``skipped`` with the reason in ``error``;
* send succeeded             -> status ``sent`` + ``message_id_header``;
* send raised                -> status ``failed`` + the error class/message.

It never raises: an ops alert must not turn one failure into two. The row is
added and flushed; the CALLER commits (same rule as ``record_activity``).
Lane B4 routed this through :mod:`app.services.mail.sender` without changing
the signature.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.mail import MailMessage
from app.services.mail.sender import send_mail

logger = logging.getLogger(__name__)

_SUBJECT_PREFIX = "[Radal ops] "


def notify_ops(db: Session, subject: str, body: str) -> MailMessage:
    """Record (and, when mail is on, send) an alert to ``RADAL_OPS_EMAIL``.

    Routed through :func:`app.services.mail.sender.send_mail` (Radal system
    mailbox, ``broker_id NULL``), which records the row and never raises.
    """
    to = (settings.RADAL_OPS_EMAIL or "").strip()
    full_subject = (_SUBJECT_PREFIX + (subject or "")).strip()[:500]
    row = send_mail(
        db,
        broker_id=None,
        kind="ops",
        to=[to] if to else [],
        subject=full_subject,
        text=body or "",
        account=None,
        skip_reason=None if (to or not settings.MAIL_ENABLED) else "RADAL_OPS_EMAIL is not configured",
    )
    logger.warning("ops notification %s: %s", row.status, full_subject)
    return row


__all__ = ["notify_ops"]
