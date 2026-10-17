"""The mail transport seam (v12, docs/v12/04-mail.md §2).

Phase 1 wrote the CONTRACT: the :class:`MailTransport` protocol, its config
and result value objects, the Radal system-mailbox configs read from settings,
and :func:`get_transport`. Lane B4 implemented :class:`SmtpImapTransport`
(``smtplib`` + ``imaplib``). Tests install
:class:`app.services.mail.fake.FakeTransport` with the ``fake_transport``
fixture, which sets :data:`_OVERRIDE` so every ``get_transport()`` call returns
the fake, however the caller imported it.

Passwords travel only inside :class:`SmtpConfig` / :class:`ImapConfig` (their
``repr`` hides them) — never log a config, never echo it in an error.

**Network guard (SSRF).** A broker types its own SMTP/IMAP host, and the
backend connects from inside the VPC. So a broker mailbox config carries
``restrict_network=True`` and, right before connecting, :func:`check_mail_host`
refuses a port outside :data:`SMTP_PORTS` / :data:`IMAP_PORTS` and any host that
is (or resolves to) a loopback, private, link-local, reserved, shared or
multicast address. The API runs the same check without DNS on create/update
for a fast 422. Errors shown to the broker are a generic Spanish CATEGORY
(:func:`_describe`: authentication / refused / timeout / TLS / host not
found), never the server's banner; the detail goes only to the server log.
"""
from __future__ import annotations

import email.message
import imaplib as _imaplib  # its error class only (tests swap ``imaplib.IMAP4``)
import ipaddress
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings

logger = logging.getLogger(__name__)

#: ``imaplib.IMAP4.error`` captured at import (tests swap ``imaplib.IMAP4``).
_IMAP_ERROR: type[BaseException] = _imaplib.IMAP4.error

#: Ports a broker mailbox may use (anything else is refused: no port scanning).
SMTP_PORTS = frozenset({25, 465, 587, 2525})
IMAP_PORTS = frozenset({143, 993})

REASON_HOST_INVALID = "El nombre del servidor de correo no es válido"
REASON_HOST_INTERNAL = (
    "El servidor de correo apunta a una dirección interna o reservada; "
    "usa el servidor público de tu proveedor de correo"
)
REASON_SMTP_PORT = "Puerto SMTP no permitido: usa 465, 587, 25 o 2525"
REASON_IMAP_PORT = "Puerto IMAP no permitido: usa 993 o 143"
ERR_AUTH = "Autenticación rechazada: revisa el usuario y la contraseña"
ERR_REFUSED = "El servidor rechazó la conexión (revisa el host y el puerto)"
ERR_TIMEOUT = "El servidor de correo no respondió a tiempo"
ERR_NOT_FOUND = "No se encontró el servidor de correo (revisa el nombre del host)"
ERR_TLS = "Falló la conexión segura (revisa el tipo de seguridad y el puerto)"
ERR_RECIPIENTS = "El servidor rechazó a los destinatarios"
ERR_SENDER = "El servidor rechazó al remitente"
ERR_SMTP = "El servidor SMTP rechazó la operación"
ERR_IMAP = "El servidor IMAP rechazó la operación"
ERR_GENERIC = "No se pudo conectar con el servidor de correo"

_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home.arpa",
    ".arpa",
)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str  # ssl | starttls | none
    username: str
    password: str = field(repr=False)
    timeout: int = 20
    #: The envelope sender (``MAIL FROM``); defaults to ``username`` when blank.
    from_address: str = ""
    #: Broker-typed server: enforce :func:`check_mail_host` before connecting.
    restrict_network: bool = False


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    security: str  # ssl | starttls | none
    username: str
    password: str = field(repr=False)
    timeout: int = 20
    mailbox: str = "INBOX"
    #: Broker-typed server: enforce :func:`check_mail_host` before connecting.
    restrict_network: bool = False


@dataclass(frozen=True)
class FetchedMail:
    """One raw message fetched over IMAP. ``raw`` is the RFC 822 bytes."""

    uid: str
    raw: bytes = field(repr=False)
    uidvalidity: str | None = None

    def message(self) -> email.message.EmailMessage:
        """Parse ``raw`` (modern policy => ``EmailMessage``)."""
        return message_from_bytes(self.raw, policy=policy.default)  # type: ignore[return-value]

    @property
    def subject(self) -> str:
        return str(self.message().get("Subject", "") or "")

    @property
    def from_address(self) -> str:
        return str(self.message().get("From", "") or "")

    @property
    def message_id(self) -> str:
        return str(self.message().get("Message-ID", "") or "")

    @property
    def in_reply_to(self) -> str:
        return str(self.message().get("In-Reply-To", "") or "")


@dataclass(frozen=True)
class CheckResult:
    """Outcome of logging in to SMTP and/or IMAP. ``None`` = not checked."""

    smtp_ok: bool | None = None
    imap_ok: bool | None = None
    smtp_error: str | None = None
    imap_error: str | None = None
    checked_at: datetime | None = None

    @property
    def ok(self) -> bool:
        checked = [v for v in (self.smtp_ok, self.imap_ok) if v is not None]
        return bool(checked) and all(checked)


class MailTransportError(RuntimeError):
    """A send/fetch/check failed. The message is a generic Spanish category:
    never credentials, never the remote server's own text."""


class MailHostRejected(MailTransportError):
    """The host/port of a broker mailbox is not allowed (SSRF guard)."""


def _public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            ip = mapped
    return bool(ip.is_global) and not ip.is_multicast


def check_mail_host(host: str | None, port: int | None, *, kind: str, resolve: bool) -> None:
    """Refuse a broker mail server that is not a public host on a mail port.

    ``kind`` is ``smtp`` | ``imap``. Without ``resolve`` only the literal is
    checked (the API, hermetic); with it every address the name resolves to
    must be public (the transport, right before connecting). Raises
    :class:`MailHostRejected` (Spanish ``str``).
    """
    allowed = SMTP_PORTS if kind == "smtp" else IMAP_PORTS
    if port is not None and int(port) not in allowed:
        raise MailHostRejected(REASON_SMTP_PORT if kind == "smtp" else REASON_IMAP_PORT)
    name = (host or "").strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if not name:
        raise MailHostRejected(REASON_HOST_INVALID)
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    if literal is not None:
        if not _public_ip(literal):
            raise MailHostRejected(REASON_HOST_INTERNAL)
        return
    if not _HOST_RE.match(name):
        # Single-label names ("rds", "localhost") resolve through the VPC's
        # search domains: refuse them along with anything malformed.
        raise MailHostRejected(REASON_HOST_INTERNAL if "." not in name else REASON_HOST_INVALID)
    if name == "localhost" or name.endswith(_BLOCKED_SUFFIXES):
        raise MailHostRejected(REASON_HOST_INTERNAL)
    last_label = name.rsplit(".", 1)[-1]
    if last_label.isdigit() or last_label.startswith("0x"):
        # "127.1", "10.0x1": inet_aton shorthands for addresses, not names.
        raise MailHostRejected(REASON_HOST_INTERNAL)
    if not resolve:
        return
    import socket  # noqa: PLC0415

    try:
        infos = socket.getaddrinfo(name, int(port or 0) or None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise MailTransportError(ERR_NOT_FOUND) from None
    except OSError:
        raise MailTransportError(ERR_GENERIC) from None
    if not infos:
        raise MailTransportError(ERR_NOT_FOUND)
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise MailHostRejected(REASON_HOST_INTERNAL) from None
        if not _public_ip(ip):
            raise MailHostRejected(REASON_HOST_INTERNAL)


@runtime_checkable
class MailTransport(Protocol):
    def send(self, cfg: SmtpConfig, msg: email.message.EmailMessage) -> str:
        """Send ``msg``; return its ``Message-ID``."""
        ...

    def fetch_since(
        self, cfg: ImapConfig, cursor: dict[str, Any] | None, *, limit: int = 200
    ) -> tuple[list[FetchedMail], dict[str, Any]]:
        """New mail after ``cursor`` (``{uidvalidity, last_uid}``) and the new cursor."""
        ...

    def check(self, smtp: SmtpConfig | None, imap: ImapConfig | None) -> CheckResult: ...


class SmtpImapTransport:
    """The real transport: ``smtplib`` for sending, ``imaplib`` for reading.

    * ``security``: ``ssl`` -> ``SMTP_SSL`` / ``IMAP4_SSL``; ``starttls`` -> plain
      connection upgraded with ``starttls()``; ``none`` -> plain (local relays).
    * Reading is READ-ONLY and never flags a message ``\\Seen``: the mailbox is
      selected ``readonly`` and bodies are fetched with ``BODY.PEEK[]``.
    * The cursor is ``{uidvalidity, last_uid}``. New mail = ``UID SEARCH UID
      {last_uid+1}:*``. A changed (or missing) UIDVALIDITY invalidates every UID,
      so the window resets to "since 2 days ago" (``SEARCH SINCE``).
    * Every failure is raised as :class:`MailTransportError` carrying a generic
      Spanish category (:func:`_describe`) — never the credentials, never the
      remote server's text (that goes to the server log only).
    """

    #: How far back a cursor reset reads (04-mail §2).
    RESET_WINDOW_DAYS = 2

    # --- SMTP ------------------------------------------------------------------
    @staticmethod
    def _smtp(cfg: SmtpConfig):  # noqa: ANN205
        import smtplib  # noqa: PLC0415
        import ssl  # noqa: PLC0415

        if cfg.restrict_network:
            check_mail_host(cfg.host, cfg.port, kind="smtp", resolve=True)
        security = (cfg.security or "ssl").lower()
        if security == "ssl":
            server = smtplib.SMTP_SSL(
                cfg.host, int(cfg.port), timeout=cfg.timeout, context=ssl.create_default_context()
            )
        else:
            server = smtplib.SMTP(cfg.host, int(cfg.port), timeout=cfg.timeout)
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
        if cfg.username and cfg.password:
            try:
                server.login(cfg.username, cfg.password)
            except smtplib.SMTPAuthenticationError:
                try:
                    server.close()
                except Exception:  # noqa: BLE001
                    pass
                raise MailTransportError(ERR_AUTH) from None
        return server

    def send(self, cfg: SmtpConfig, msg: email.message.EmailMessage) -> str:
        if not msg.get("Message-ID"):
            from email.utils import make_msgid  # noqa: PLC0415

            domain = (cfg.from_address or cfg.username or "radalseguros.cl").rpartition("@")[2]
            msg["Message-ID"] = make_msgid(domain=domain or "radalseguros.cl")
        try:
            server = self._smtp(cfg)
            try:
                server.send_message(msg, from_addr=cfg.from_address or cfg.username or None)
            finally:
                try:
                    server.quit()
                except Exception:  # noqa: BLE001 - the mail is already accepted
                    pass
        except MailTransportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MailTransportError(_describe(exc, cfg.password)) from None
        return str(msg["Message-ID"])

    # --- IMAP ------------------------------------------------------------------
    @staticmethod
    def _imap(cfg: ImapConfig):  # noqa: ANN205
        import imaplib  # noqa: PLC0415
        import ssl  # noqa: PLC0415

        if cfg.restrict_network:
            check_mail_host(cfg.host, cfg.port, kind="imap", resolve=True)
        security = (cfg.security or "ssl").lower()
        if security == "ssl":
            conn = imaplib.IMAP4_SSL(
                cfg.host, int(cfg.port), ssl_context=ssl.create_default_context(), timeout=cfg.timeout
            )
        else:
            conn = imaplib.IMAP4(cfg.host, int(cfg.port), timeout=cfg.timeout)
            if security == "starttls":
                conn.starttls(ssl_context=ssl.create_default_context())
        try:
            conn.login(cfg.username, cfg.password)
        except _IMAP_ERROR:
            try:
                conn.shutdown()
            except Exception:  # noqa: BLE001
                pass
            raise MailTransportError(ERR_AUTH) from None
        return conn

    @staticmethod
    def _untagged(conn, name: str) -> str | None:  # noqa: ANN001
        try:
            _typ, data = conn.response(name)
        except Exception:  # noqa: BLE001
            return None
        for item in data or []:
            if item is None:
                continue
            text = item.decode() if isinstance(item, bytes) else str(item)
            digits = "".join(ch for ch in text if ch.isdigit())
            if digits:
                return digits
        return None

    @staticmethod
    def _uids(data) -> list[int]:  # noqa: ANN001
        uids: list[int] = []
        for chunk in data or []:
            if not chunk:
                continue
            text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
            uids.extend(int(part) for part in text.split() if part.isdigit())
        return sorted(set(uids))

    @staticmethod
    def _raw_from_fetch(data) -> bytes | None:  # noqa: ANN001
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return None

    def fetch_since(
        self, cfg: ImapConfig, cursor: dict[str, Any] | None, *, limit: int = 200
    ) -> tuple[list[FetchedMail], dict[str, Any]]:
        from datetime import date, timedelta  # noqa: PLC0415

        try:
            conn = self._imap(cfg)
        except Exception as exc:  # noqa: BLE001
            raise MailTransportError(_describe(exc, cfg.password)) from None
        try:
            typ, _ = conn.select(cfg.mailbox or "INBOX", readonly=True)
            if typ != "OK":
                raise MailTransportError(f"No se pudo abrir la carpeta {cfg.mailbox or 'INBOX'} del correo")
            uidvalidity = self._untagged(conn, "UIDVALIDITY")
            uidnext = self._untagged(conn, "UIDNEXT")
            last_uid = 0
            same_epoch = bool(
                cursor
                and uidvalidity is not None
                and str(cursor.get("uidvalidity")) == str(uidvalidity)
            )
            if same_epoch:
                last_uid = int(cursor.get("last_uid") or 0)  # type: ignore[union-attr]
                typ, data = conn.uid("SEARCH", None, f"UID {last_uid + 1}:*")
            else:
                since = (date.today() - timedelta(days=self.RESET_WINDOW_DAYS)).strftime("%d-%b-%Y")
                typ, data = conn.uid("SEARCH", None, f"SINCE {since}")
            if typ != "OK":
                raise MailTransportError("El servidor IMAP rechazó la búsqueda de mensajes")
            # ``UID n:*`` always answers the newest message even when its UID < n.
            uids = [uid for uid in self._uids(data) if uid > last_uid][: max(0, int(limit))]
            fetched: list[FetchedMail] = []
            for uid in uids:
                typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                raw = self._raw_from_fetch(data) if typ == "OK" else None
                if raw is None:
                    continue
                fetched.append(FetchedMail(uid=str(uid), raw=raw, uidvalidity=uidvalidity))
            new_last = max([last_uid, *uids]) if uids else last_uid
            if not uids and not same_epoch and uidnext and uidnext.isdigit():
                # Nothing in the reset window: start after the current newest UID.
                new_last = max(new_last, int(uidnext) - 1)
            return fetched, {"uidvalidity": uidvalidity, "last_uid": new_last}
        except MailTransportError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MailTransportError(_describe(exc, cfg.password)) from None
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    # --- check -----------------------------------------------------------------
    def check(self, smtp: SmtpConfig | None, imap: ImapConfig | None) -> CheckResult:
        from datetime import timezone  # noqa: PLC0415

        smtp_ok = imap_ok = None
        smtp_error = imap_error = None
        if smtp is not None:
            try:
                server = self._smtp(smtp)
                try:
                    server.noop()
                finally:
                    try:
                        server.quit()
                    except Exception:  # noqa: BLE001
                        pass
                smtp_ok = True
            except Exception as exc:  # noqa: BLE001
                smtp_ok, smtp_error = False, _describe(exc, smtp.password)
        if imap is not None:
            try:
                conn = self._imap(imap)
                try:
                    conn.select(imap.mailbox or "INBOX", readonly=True)
                finally:
                    try:
                        conn.logout()
                    except Exception:  # noqa: BLE001
                        pass
                imap_ok = True
            except Exception as exc:  # noqa: BLE001
                imap_ok, imap_error = False, _describe(exc, imap.password)
        return CheckResult(
            smtp_ok=smtp_ok,
            imap_ok=imap_ok,
            smtp_error=smtp_error,
            imap_error=imap_error,
            checked_at=datetime.now(timezone.utc),
        )


def _category(exc: BaseException) -> str:
    import smtplib  # noqa: PLC0415
    import socket  # noqa: PLC0415
    import ssl  # noqa: PLC0415

    if isinstance(exc, MailTransportError):
        return str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ERR_AUTH
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return ERR_RECIPIENTS
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return ERR_SENDER
    if isinstance(exc, ssl.SSLError):
        return ERR_TLS
    if isinstance(exc, socket.gaierror):
        return ERR_NOT_FOUND
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ERR_TIMEOUT
    if isinstance(exc, ConnectionRefusedError):
        return ERR_REFUSED
    if isinstance(exc, _IMAP_ERROR):
        return ERR_IMAP
    if isinstance(exc, smtplib.SMTPException):
        return ERR_SMTP
    return ERR_GENERIC


def _describe(exc: BaseException, password: str | None = None) -> str:
    """The broker-facing text of a failure: a generic Spanish category.

    The detail (class + server reply, password scrubbed) is logged server-side
    only — a server's banner is never echoed to the API (port-scan oracle).
    """
    detail = f"{type(exc).__name__}: {exc}"
    if password:
        detail = detail.replace(password, "***")
    logger.info("mail transport failure: %s", detail[:500])
    text = _category(exc)
    if password:
        text = text.replace(password, "***")
    return text[:500]


#: Test seam: when set, every ``get_transport`` call returns it.
_OVERRIDE: MailTransport | None = None


def get_transport() -> MailTransport:
    """The transport in use: the override when installed, else the real one."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    return SmtpImapTransport()


def radal_smtp_config() -> SmtpConfig:
    """The Radal system mailbox (Zoho) SMTP config, from settings."""
    return SmtpConfig(
        host=settings.RADAL_MAIL_SMTP_HOST,
        port=int(settings.RADAL_MAIL_SMTP_PORT),
        security=settings.RADAL_MAIL_SMTP_SECURITY,
        username=settings.RADAL_MAIL_USERNAME or settings.RADAL_MAIL_ADDRESS,
        password=settings.RADAL_MAIL_PASSWORD,
        timeout=int(settings.MAIL_TIMEOUT_SECONDS),
        from_address=settings.RADAL_MAIL_ADDRESS,
    )


def radal_imap_config() -> ImapConfig:
    """The Radal system mailbox (Zoho) IMAP config, from settings."""
    return ImapConfig(
        host=settings.RADAL_MAIL_IMAP_HOST,
        port=int(settings.RADAL_MAIL_IMAP_PORT),
        security=settings.RADAL_MAIL_IMAP_SECURITY,
        username=settings.RADAL_MAIL_USERNAME or settings.RADAL_MAIL_ADDRESS,
        password=settings.RADAL_MAIL_PASSWORD,
        timeout=int(settings.MAIL_TIMEOUT_SECONDS),
    )


__all__ = [
    "CheckResult",
    "FetchedMail",
    "ImapConfig",
    "IMAP_PORTS",
    "MailHostRejected",
    "MailTransport",
    "MailTransportError",
    "SMTP_PORTS",
    "check_mail_host",
    "SmtpConfig",
    "SmtpImapTransport",
    "get_transport",
    "radal_imap_config",
    "radal_smtp_config",
]
