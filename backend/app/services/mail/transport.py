"""The mail transport seam (v12, docs/v12/04-mail.md §2).

Phase 1 wrote the CONTRACT: the :class:`MailTransport` protocol, its config
and result value objects, the Radal system-mailbox configs read from settings,
and :func:`get_transport`. Lane B4 implemented :class:`SmtpImapTransport`
(``smtplib`` + ``imaplib``). Tests install
:class:`app.services.mail.fake.FakeTransport` with the ``fake_transport``
fixture, which sets :data:`_OVERRIDE` so every ``get_transport()`` call returns
the fake, however the caller imported it.

**The Radal system sender** (``contacto@radalseguros.cl``) sends through
:class:`SesTransport` (Amazon SES v2, ``send_email`` with ``Content.Raw``) when
``MAIL_TRANSPORT == "ses"`` — the default — and through Zoho SMTP otherwise;
:func:`get_system_transport` picks it. SES rewrites ``Message-ID`` to
``<{MessageId}@email.amazonses.com>``, so that is what ``send`` returns and what
the sender stores (``In-Reply-To`` of a reply must match it). After a
the visual copy for humans is, by default, a **Bcc** to ``MAIL_COPY_ADDRESS``
(added by the sender; SES envelope only). ``MAIL_SENT_COPY=imap_append``
instead APPENDs the exact raw message to the Zoho ``Sent`` folder
(:func:`append_sent_copy`, best-effort — needs IMAP, which Zoho Free lacks).
Replies are read either from S3 (SES inbound, :mod:`.s3_inbound`) or over
IMAP (``fetch_since``), per ``MAIL_INBOUND_SOURCE``. Broker custom mailboxes
always use :func:`get_transport` (their own SMTP/IMAP), never SES.

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
import functools
import imaplib as _imaplib  # its error class only (tests swap ``imaplib.IMAP4``)
import ipaddress
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
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
ERR_SES_AUTH = "Amazon SES rechazó las credenciales o los permisos (revisa el rol IAM)"
ERR_SES_IDENTITY = "El remitente no está verificado en Amazon SES"
ERR_SES_SANDBOX = (
    "Amazon SES está en modo sandbox y el destinatario no está verificado"
)
ERR_SES_REJECTED = "Amazon SES rechazó el mensaje"
ERR_SES_PAUSED = "El envío está pausado o suspendido en la cuenta de Amazon SES"
ERR_SES_THROTTLED = "Amazon SES limitó el envío (cuota o tasa); reintenta más tarde"
ERR_SES_UNREACHABLE = "No se pudo contactar a Amazon SES"
ERR_SES_GENERIC = "Falló la operación en Amazon SES"
ERR_SES_SENDING_DISABLED = "El envío está deshabilitado en la cuenta de Amazon SES"

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
    """Outcome of checking the SEND side and/or IMAP. ``None`` = not checked.

    ``smtp_ok`` / ``smtp_error`` describe the send side whatever carries it:
    an SMTP login, or — for :class:`SesTransport` — "SES account can send and
    the From identity is verified". ``transport`` says which (``smtp``|``ses``);
    ``details`` carries the SES account/identity facts (no secrets).
    """

    smtp_ok: bool | None = None
    imap_ok: bool | None = None
    smtp_error: str | None = None
    imap_error: str | None = None
    checked_at: datetime | None = None
    transport: str | None = None
    details: dict[str, Any] | None = None

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


# =============================================================================
# Amazon SES v2 — the Radal system sender
# =============================================================================

#: SES v2 error codes, grouped into the Spanish categories shown to people.
_SES_AUTH_CODES = frozenset({
    "AccessDenied",
    "AccessDeniedException",
    "UnrecognizedClientException",
    "InvalidClientTokenId",
    "InvalidSignatureException",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "ExpiredTokenException",
})
_SES_IDENTITY_CODES = frozenset({"MailFromDomainNotVerifiedException", "NotFoundException"})
_SES_PAUSED_CODES = frozenset({"AccountSuspendedException", "SendingPausedException"})
_SES_THROTTLE_CODES = frozenset({
    "TooManyRequestsException",
    "LimitExceededException",
    "Throttling",
    "ThrottlingException",
})


@functools.lru_cache(maxsize=4)
def _ses_client(region: str, timeout: int):  # noqa: ANN202
    """One lazily-built ``sesv2`` client per (region, timeout).

    Credentials come from the standard boto3 chain (the Lambda role in AWS,
    the local profile on a laptop). Never called while ``MAIL_ENABLED`` is off:
    the sender short-circuits first. Tests replace this function.
    """
    import boto3  # noqa: PLC0415 - lazy: no AWS import on the hermetic path
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        "sesv2",
        region_name=region,
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def ses_message_id(ses_id: str, region: str) -> str:
    """The ``Message-ID`` header SES writes for ``MessageId`` ``ses_id``.

    us-east-1 uses ``email.amazonses.com``; other regions ``{region}.amazonses.com``.
    """
    region = (region or "us-east-1").strip().lower()
    domain = "email.amazonses.com" if region == "us-east-1" else f"{region}.amazonses.com"
    return f"<{ses_id}@{domain}>"


def _ses_error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


def _ses_category(exc: BaseException) -> str:
    if isinstance(exc, MailTransportError):
        return str(exc)
    code = _ses_error_code(exc)
    name = type(exc).__name__
    if code in _SES_AUTH_CODES or name in ("NoCredentialsError", "PartialCredentialsError", "NoRegionError"):
        return ERR_SES_AUTH
    if code == "MessageRejected":
        text = str(exc).lower()
        if "not verified" in text and "identit" in text:
            return ERR_SES_SANDBOX
        return ERR_SES_REJECTED
    if code in _SES_IDENTITY_CODES:
        return ERR_SES_IDENTITY
    if code in _SES_PAUSED_CODES:
        return ERR_SES_PAUSED
    if code in _SES_THROTTLE_CODES:
        return ERR_SES_THROTTLED
    if code == "BadRequestException":
        return ERR_SES_REJECTED
    if name in (
        "EndpointConnectionError",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "ConnectionClosedError",
        "ProxyConnectionError",
    ) or isinstance(exc, (TimeoutError, ConnectionError)):
        return ERR_SES_UNREACHABLE
    return ERR_SES_GENERIC


def _describe_ses(exc: BaseException) -> str:
    """Spanish category for an SES failure; the detail goes to the log only."""
    code = _ses_error_code(exc)
    logger.info("ses failure: %s%s: %s", type(exc).__name__, f" [{code}]" if code else "", str(exc)[:500])
    return _ses_category(exc)[:500]


def raw_message_bytes(msg: email.message.EmailMessage) -> bytes:
    """``msg`` serialised with CRLF line endings (RFC 5322; SES Raw + IMAP APPEND)."""
    pol = msg.policy if isinstance(msg.policy, policy.EmailPolicy) else policy.default
    return msg.as_bytes(policy=pol.clone(linesep="\r\n"))


def _quote_mailbox(name: str) -> str:
    """An IMAP quoted-string (imaplib does not quote ``APPEND`` arguments)."""
    escaped = (name or "INBOX").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def append_sent_copy(cfg: ImapConfig, raw: bytes, folder: str, *, timeout: float | None = None) -> bool:
    """APPEND ``raw`` to ``folder`` flagged ``\\Seen``. Best-effort: never raises.

    Runs in a daemon thread joined for at most ``timeout`` seconds (default
    ``cfg.timeout``), so a slow IMAP server can never hold a send hostage. Any
    failure is logged (no credentials) and answered ``False``.
    """
    limit = float(timeout if timeout is not None else (cfg.timeout or 20))
    outcome: dict[str, Any] = {}

    def _work() -> None:
        try:
            conn = SmtpImapTransport._imap(cfg)
            try:
                typ, data = conn.append(
                    _quote_mailbox(folder),
                    r"(\Seen)",
                    _imaplib.Time2Internaldate(time.time()),
                    raw,
                )
                outcome["ok"] = typ == "OK"
                if typ != "OK":
                    outcome["detail"] = f"APPEND answered {typ}"
            finally:
                try:
                    conn.logout()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001 - best-effort by contract
            outcome["ok"] = False
            outcome["detail"] = _describe(exc, cfg.password)

    worker = threading.Thread(target=_work, name="radal-mail-sent-copy", daemon=True)
    worker.start()
    worker.join(limit)
    if worker.is_alive():
        logger.warning("mail sent copy: IMAP APPEND to %r timed out after %.0fs", folder, limit)
        return False
    if not outcome.get("ok"):
        logger.warning("mail sent copy: IMAP APPEND to %r failed: %s", folder, outcome.get("detail") or "unknown")
        return False
    return True


class SesTransport:
    """The Radal system sender over Amazon SES v2 (``send_email`` + ``Content.Raw``).

    * ``send`` posts the exact MIME message we built; ``FromEmailAddress`` is
      ``cfg.from_address`` (the verified ``RADAL_MAIL_ADDRESS``), the envelope
      recipients are the message's To/Cc/Bcc (a ``Bcc`` header is stripped
      from the raw). ``ConfigurationSetName`` is sent only when configured.
    * SES rewrites ``Message-ID``: ``send`` returns ``<{MessageId}@email.amazonses.com>``
      and writes it back into ``msg`` so the sent copy carries the same id.
    * After a successful send, when ``sent_copy`` (an :class:`ImapConfig`) is
      set, the raw message is APPENDed to ``sent_folder`` (\\Seen) — never
      failing the send.
    * Inbound (``fetch_since``) is plain IMAP, delegated to :class:`SmtpImapTransport`.
    * The client is lazy, cached per region (:func:`_ses_client`) and injectable.
    """

    def __init__(
        self,
        *,
        region: str | None = None,
        configuration_set: str | None = None,
        client: Any | None = None,
        sent_copy: ImapConfig | None = None,
        sent_folder: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.region = (region or settings.SES_REGION or "us-east-1").strip()
        self.configuration_set = (
            settings.SES_CONFIGURATION_SET if configuration_set is None else configuration_set
        ).strip()
        self.sent_copy = sent_copy
        self.sent_folder = sent_folder or settings.MAIL_SENT_FOLDER or "Sent"
        self.timeout = int(timeout if timeout is not None else settings.MAIL_TIMEOUT_SECONDS)
        self._injected = client
        #: Outcome of the last sent copy: True/False, None = not attempted.
        self.last_sent_copy_ok: bool | None = None

    @classmethod
    def from_settings(cls) -> "SesTransport":
        sent_copy = None
        if (settings.MAIL_SENT_COPY or "").lower() == "imap_append":
            imap = radal_imap_config()
            if imap.host and imap.username and imap.password:
                sent_copy = imap
        return cls(sent_copy=sent_copy)

    @property
    def client(self) -> Any:
        if self._injected is None:
            self._injected = _ses_client(self.region, self.timeout)
        return self._injected

    # --- send ------------------------------------------------------------------
    def send(self, cfg: SmtpConfig, msg: email.message.EmailMessage) -> str:
        from_address = (cfg.from_address or parseaddr(str(msg.get("From", "") or ""))[1] or "").strip()
        to_list = [a for _n, a in getaddresses(msg.get_all("To", []) or []) if a]
        cc_list = [a for _n, a in getaddresses(msg.get_all("Cc", []) or []) if a]
        bcc_list = [a for _n, a in getaddresses(msg.get_all("Bcc", []) or []) if a]
        if "Bcc" in msg:
            del msg["Bcc"]
        if not (to_list or cc_list or bcc_list):
            raise MailTransportError(ERR_RECIPIENTS)
        if not msg.get("Message-ID"):
            msg["Message-ID"] = make_msgid(domain=from_address.rpartition("@")[2] or "radalseguros.cl")
        if not msg.get("Date"):
            msg["Date"] = formatdate(usegmt=True)
        destination: dict[str, list[str]] = {}
        if to_list:
            destination["ToAddresses"] = to_list
        if cc_list:
            destination["CcAddresses"] = cc_list
        if bcc_list:
            destination["BccAddresses"] = bcc_list
        request: dict[str, Any] = {
            "FromEmailAddress": from_address,
            "Destination": destination,
            "Content": {"Raw": {"Data": raw_message_bytes(msg)}},
        }
        if self.configuration_set:
            request["ConfigurationSetName"] = self.configuration_set
        try:
            response = self.client.send_email(**request)
        except Exception as exc:  # noqa: BLE001
            raise MailTransportError(_describe_ses(exc)) from None
        ses_id = str((response or {}).get("MessageId") or "").strip()
        header = ses_message_id(ses_id, self.region) if ses_id else str(msg["Message-ID"])
        msg.replace_header("Message-ID", header)
        logger.info("ses sent message_id=%s recipients=%d", header, len(to_list) + len(cc_list) + len(bcc_list))
        self.last_sent_copy_ok = None
        if self.sent_copy is not None:
            try:
                self.last_sent_copy_ok = append_sent_copy(
                    self.sent_copy, raw_message_bytes(msg), self.sent_folder, timeout=self.timeout
                )
            except Exception:  # noqa: BLE001 - belt and braces: the mail is already out
                self.last_sent_copy_ok = False
                logger.warning("mail sent copy: unexpected failure", exc_info=True)
        return header

    # --- inbound (IMAP, unchanged) ---------------------------------------------
    def fetch_since(
        self, cfg: ImapConfig, cursor: dict[str, Any] | None, *, limit: int = 200
    ) -> tuple[list[FetchedMail], dict[str, Any]]:
        return SmtpImapTransport().fetch_since(cfg, cursor, limit=limit)

    # --- readiness -------------------------------------------------------------
    def ses_status(self, from_address: str | None = None) -> dict[str, Any]:
        """SES account + From-identity facts. Never raises; no secrets."""
        address = (from_address or settings.RADAL_MAIL_ADDRESS or "").strip()
        domain = address.rpartition("@")[2].lower()
        report: dict[str, Any] = {
            "region": self.region,
            "from_address": address,
            "identity": domain or address,
            "configuration_set": self.configuration_set or None,
            "account": None,
            "identity_status": None,
            "ok": False,
            "error": None,
            "warnings": [],
        }
        try:
            account = self.client.get_account() or {}
        except Exception as exc:  # noqa: BLE001
            report["error"] = _describe_ses(exc)
            return report
        quota = account.get("SendQuota") or {}
        production = bool(account.get("ProductionAccessEnabled"))
        report["account"] = {
            "sending_enabled": bool(account.get("SendingEnabled")),
            "production_access": production,
            "sandbox": not production,
            "enforcement_status": account.get("EnforcementStatus"),
            "max_24h_send": quota.get("Max24HourSend"),
            "max_send_rate": quota.get("MaxSendRate"),
            "sent_last_24h": quota.get("SentLast24Hours"),
        }
        if not production:
            report["warnings"].append(
                "Cuenta SES en sandbox: solo se puede enviar a direcciones o dominios verificados"
            )
        identity = None
        for candidate in [c for c in (domain, address.lower()) if c]:
            try:
                identity = self.client.get_email_identity(EmailIdentity=candidate) or {}
                report["identity"] = candidate
                break
            except Exception as exc:  # noqa: BLE001
                if _ses_error_code(exc) == "NotFoundException":
                    continue
                report["error"] = _describe_ses(exc)
                return report
        if identity is None:
            report["error"] = ERR_SES_IDENTITY
            return report
        dkim = identity.get("DkimAttributes") or {}
        mail_from = identity.get("MailFromAttributes") or {}
        report["identity_status"] = {
            "identity_type": identity.get("IdentityType"),
            "verified_for_sending": bool(identity.get("VerifiedForSendingStatus")),
            "verification_status": identity.get("VerificationStatus"),
            "dkim_signing_enabled": bool(dkim.get("SigningEnabled")),
            "dkim_status": dkim.get("Status"),
            "mail_from_domain": mail_from.get("MailFromDomain"),
            "mail_from_status": mail_from.get("MailFromDomainStatus"),
            "mail_from_behavior_on_mx_failure": mail_from.get("BehaviorOnMxFailure"),
        }
        if dkim.get("Status") not in (None, "SUCCESS"):
            report["warnings"].append(f"DKIM de {report['identity']} en estado {dkim.get('Status')}")
        if mail_from.get("MailFromDomain") and mail_from.get("MailFromDomainStatus") != "SUCCESS":
            report["warnings"].append(
                f"MAIL FROM {mail_from.get('MailFromDomain')} en estado {mail_from.get('MailFromDomainStatus')}"
            )
        if not report["account"]["sending_enabled"]:
            report["error"] = ERR_SES_SENDING_DISABLED
        elif not report["identity_status"]["verified_for_sending"]:
            report["error"] = ERR_SES_IDENTITY
        else:
            report["ok"] = True
        return report

    def check(self, smtp: SmtpConfig | None, imap: ImapConfig | None) -> CheckResult:
        from datetime import timezone  # noqa: PLC0415

        details: dict[str, Any] | None = None
        send_ok = send_error = None
        if smtp is not None:
            try:
                details = self.ses_status(smtp.from_address or smtp.username)
            except Exception as exc:  # noqa: BLE001 - check never raises
                details = {"ok": False, "error": _describe_ses(exc)}
            send_ok, send_error = bool(details.get("ok")), details.get("error")
        imap_ok = imap_error = None
        if imap is not None:
            try:
                result = SmtpImapTransport().check(None, imap)
                imap_ok, imap_error = result.imap_ok, result.imap_error
            except Exception as exc:  # noqa: BLE001
                imap_ok, imap_error = False, _describe(exc, imap.password)
        return CheckResult(
            smtp_ok=send_ok,
            imap_ok=imap_ok,
            smtp_error=send_error,
            imap_error=imap_error,
            checked_at=datetime.now(timezone.utc),
            transport="ses",
            details=details,
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
    """The SMTP/IMAP transport (broker mailboxes, inbound polling): the override
    when installed, else the real one."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    return SmtpImapTransport()


def system_transport_kind() -> str:
    """``ses`` | ``smtp`` — how the Radal system sender sends (``MAIL_TRANSPORT``)."""
    return "smtp" if (settings.MAIL_TRANSPORT or "ses").strip().lower() == "smtp" else "ses"


def system_transport_label() -> str:
    """The Spanish one-liner shown on the Radal sender card."""
    address = settings.RADAL_MAIL_ADDRESS
    if system_transport_kind() == "ses":
        reply_to = (settings.RADAL_MAIL_REPLY_TO or "").strip()
        if reply_to and reply_to.lower() != address.lower():
            return f"Amazon SES · respuestas a {reply_to} (procesadas por Radal)"
        return f"Amazon SES · respuestas a {address} (Zoho)"
    return f"SMTP de Zoho · {address}"


def get_system_transport() -> MailTransport:
    """The Radal SYSTEM sender: the override when installed, else
    :class:`SesTransport` (``MAIL_TRANSPORT=ses``) or :class:`SmtpImapTransport`.

    Building it opens nothing: the SES client is created on the first send.
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    if system_transport_kind() == "ses":
        return SesTransport.from_settings()
    return SmtpImapTransport()


def radal_smtp_config() -> SmtpConfig:
    """The Radal system sender's config, from settings.

    With ``MAIL_TRANSPORT=smtp`` it is the Zoho SMTP login; with SES only
    ``from_address`` (the verified ``RADAL_MAIL_ADDRESS``) is read."""
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
    "SesTransport",
    "SmtpImapTransport",
    "append_sent_copy",
    "get_system_transport",
    "get_transport",
    "radal_imap_config",
    "radal_smtp_config",
    "raw_message_bytes",
    "ses_message_id",
    "system_transport_kind",
    "system_transport_label",
]
