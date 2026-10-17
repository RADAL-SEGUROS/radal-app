"""``mail.poll`` — read the Radal system mailbox and every receive-enabled broker mailbox (v12, 04 §4).

Owner: lane **B4**. The body is :func:`app.services.mail.inbound.run_mail_poll`
(prefilter before storing anything, classify, suggest evidence). Idempotency
slot: one run per minute; the IMAP cursor + the unique
``(mail_account_id, provider_uid)`` make re-reads harmless anyway. Skipped
(``{"skipped": true}``) while ``MAIL_ENABLED`` is off.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.jobs import register_job


@register_job("mail.poll", period="minute")
def mail_poll(db: Session, now: datetime) -> dict[str, Any]:
    from app.services.mail.inbound import run_mail_poll  # noqa: PLC0415

    return run_mail_poll(db, now)
