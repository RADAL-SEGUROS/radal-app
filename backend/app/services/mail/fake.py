"""An in-memory :class:`MailTransport` for tests (v12). No sockets.

* :attr:`FakeTransport.sent` records every ``(cfg, message)`` sent;
* :meth:`FakeTransport.queue_inbound` scripts inbound mail per mailbox
  (keyed by ``(host, username)``; ``None`` = any mailbox), served by
  :meth:`fetch_since` in UID order after the cursor's ``last_uid``;
* :attr:`FakeTransport.check_result` is what :meth:`check` answers;
* :attr:`FakeTransport.fail_send` makes the next send raise.

Installed by the ``fake_transport`` fixture (tests/conftest.py).
"""
from __future__ import annotations

import email.message
from email.utils import make_msgid
from typing import Any

from app.services.mail.transport import (
    CheckResult,
    FetchedMail,
    ImapConfig,
    MailTransportError,
    SmtpConfig,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[SmtpConfig, email.message.EmailMessage]] = []
        self.checks: list[tuple[SmtpConfig | None, ImapConfig | None]] = []
        self.fetches: list[tuple[ImapConfig, dict[str, Any] | None]] = []
        self.inbox: dict[tuple[str, str] | None, list[FetchedMail]] = {}
        self.uidvalidity = "1"
        self.check_result = CheckResult(smtp_ok=True, imap_ok=True)
        #: Set to an exception (or True) to make the NEXT send raise.
        self.fail_send: Exception | bool | None = None
        self._next_uid = 1

    # --- protocol ------------------------------------------------------------
    def send(self, cfg: SmtpConfig, msg: email.message.EmailMessage) -> str:
        if self.fail_send:
            exc = self.fail_send
            self.fail_send = None
            raise exc if isinstance(exc, Exception) else MailTransportError("fake send failure")
        if not msg.get("Message-ID"):
            msg["Message-ID"] = make_msgid(domain="fake.radal.test")
        self.sent.append((cfg, msg))
        return str(msg["Message-ID"])

    def fetch_since(
        self, cfg: ImapConfig, cursor: dict[str, Any] | None, *, limit: int = 200
    ) -> tuple[list[FetchedMail], dict[str, Any]]:
        self.fetches.append((cfg, cursor))
        last = 0
        if cursor and str(cursor.get("uidvalidity")) == self.uidvalidity:
            last = int(cursor.get("last_uid") or 0)
        pool = self.inbox.get((cfg.host, cfg.username), []) + self.inbox.get(None, [])
        fresh = sorted((m for m in pool if int(m.uid) > last), key=lambda m: int(m.uid))[:limit]
        new_last = max([last, *(int(m.uid) for m in fresh)])
        return fresh, {"uidvalidity": self.uidvalidity, "last_uid": new_last}

    def check(self, smtp: SmtpConfig | None, imap: ImapConfig | None) -> CheckResult:
        self.checks.append((smtp, imap))
        return self.check_result

    # --- helpers -------------------------------------------------------------
    def queue_inbound(
        self,
        message: email.message.EmailMessage | bytes | str,
        *,
        mailbox: tuple[str, str] | None = None,
        uid: int | None = None,
    ) -> FetchedMail:
        """Script one inbound message (``mailbox=(host, username)``, None = any)."""
        if isinstance(message, email.message.Message):
            raw = message.as_bytes()
        elif isinstance(message, str):
            raw = message.encode("utf-8")
        else:
            raw = message
        if uid is None:
            uid = self._next_uid
        self._next_uid = max(self._next_uid, uid) + 1
        fetched = FetchedMail(uid=str(uid), raw=raw, uidvalidity=self.uidvalidity)
        self.inbox.setdefault(mailbox, []).append(fetched)
        return fetched

    @property
    def sent_messages(self) -> list[email.message.EmailMessage]:
        return [msg for _cfg, msg in self.sent]


def build_message(
    *,
    from_address: str,
    to: str | list[str],
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    message_id: str | None = None,
) -> email.message.EmailMessage:
    """A plain-text ``EmailMessage`` (handy for scripting inbound mail)."""
    msg = email.message.EmailMessage()
    msg["From"] = from_address
    msg["To"] = ", ".join(to) if isinstance(to, list) else to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or make_msgid(domain="fake.radal.test")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg.set_content(body)
    return msg


__all__ = ["FakeTransport", "build_message"]
