"""Is the Radal system mail ready? (v12 mail via Amazon SES — tech-ref §19.7.)

Checks, in order, and prints NO secret (IMAP shows host + user only):

1. the configuration (``MAIL_ENABLED``, ``MAIL_TRANSPORT``, From, region,
   configuration set, sent-copy mode and folder);
2. **SES** (``MAIL_TRANSPORT=ses``): ``GetAccount`` — sending enabled,
   sandbox vs production, 24 h quota, sent in the last 24 h, max rate — and
   ``GetEmailIdentity`` of the From domain — verified, DKIM, MAIL FROM.
   (With ``MAIL_TRANSPORT=smtp`` the Zoho SMTP login is checked instead.)
3. the visual copy (``MAIL_SENT_COPY`` ``bcc``|``imap_append``|``none``,
   ``MAIL_COPY_ADDRESS``), the Reply-To of inquiries (``RADAL_MAIL_REPLY_TO``)
   and the dev recipient allowlist (``MAIL_RECIPIENT_ALLOWLIST``);
4. **inbound** (``MAIL_INBOUND_SOURCE``): with ``s3``, one
   ``list_objects_v2(MaxKeys)`` on the SES-inbound prefix (objects waiting);
   with ``imap``, the Zoho IMAP login. IMAP is only checked when something
   uses it (``imap`` inbound or ``imap_append`` copy);
5. ``--send-test you@example.com`` sends ONE "[Radal] prueba de correo"
   through the real system path (SES + the Bcc copy / sent copy, Reply-To =
   the reply address). While the SES account is in the sandbox the recipient
   must be a verified address/domain.

Exit codes: ``0`` ready · ``3`` the send side (SES/SMTP) is not ready or the
test send failed · ``4`` IMAP is needed and configured but the login failed ·
``5`` the S3 inbound prefix cannot be listed. IMAP needed but not configured
(no ``RADAL_MAIL_PASSWORD``) is a warning, not a failure.

Run (from backend/, venv active; AWS creds from the standard chain):
    AWS_PROFILE=radal python -m scripts.mail_preflight
    AWS_PROFILE=radal python -m scripts.mail_preflight --send-test you@radalseguros.cl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from email.utils import make_msgid
from typing import Any

from app.core.config import settings
from app.services.mail.sender import (
    allowlist_entries,
    build_email,
    copy_address,
    radal_reply_address,
    recipient_allowed,
)
from app.services.mail.transport import (
    MailTransport,
    SesTransport,
    SmtpImapTransport,
    get_system_transport,
    radal_imap_config,
    radal_smtp_config,
    system_transport_kind,
)

EXIT_OK = 0
EXIT_SEND_NOT_READY = 3
EXIT_IMAP_FAILED = 4
EXIT_INBOUND_FAILED = 5

TEST_SUBJECT = "[Radal] prueba de correo"
TEST_BODY = (
    "Hola:\n\n"
    "Este es un correo de prueba enviado por Radal a través de Amazon SES desde "
    "{address}.\n\n"
    "Si lo recibiste, el envío del sistema funciona. Las respuestas a este correo "
    "llegan a {reply_to}.\n\n"
    "— Radal"
)


def _yes(value: Any) -> str:
    if value is None:
        return "—"
    return "sí" if value else "no"


def run(
    argv: list[str] | None = None,
    *,
    transport: MailTransport | None = None,
    s3_client: Any | None = None,
    out: Callable[[str], None] = print,
) -> tuple[int, dict[str, Any]]:
    """The preflight. Returns ``(exit_code, report)``; never raises."""
    parser = argparse.ArgumentParser(prog="python -m scripts.mail_preflight", description=__doc__.split("\n")[0])
    parser.add_argument("--send-test", metavar="EMAIL", help="send one test mail to EMAIL through the real path")
    parser.add_argument("--json", action="store_true", help="print the report as JSON at the end")
    args = parser.parse_args(argv)

    kind = system_transport_kind()
    address = settings.RADAL_MAIL_ADDRESS
    report: dict[str, Any] = {
        "config": {
            "mail_enabled": bool(settings.MAIL_ENABLED),
            "mail_transport": kind,
            "from_address": address,
            "display_name": settings.RADAL_MAIL_DISPLAY_NAME,
            "ses_region": settings.SES_REGION,
            "ses_configuration_set": settings.SES_CONFIGURATION_SET or None,
            "sent_copy": settings.MAIL_SENT_COPY,
            "sent_folder": settings.MAIL_SENT_FOLDER,
            "copy_address": copy_address() or None,
            "reply_to": settings.RADAL_MAIL_REPLY_TO or None,
            "effective_reply_to": radal_reply_address(),
            "inbound_source": settings.MAIL_INBOUND_SOURCE,
            "effective_inbound_source": settings.mail_inbound_source,
            "inbound_s3": (
                f"s3://{settings.mail_inbound_bucket}/{settings.MAIL_INBOUND_S3_PREFIX}"
                if settings.mail_inbound_source == "s3" else None
            ),
            "allowlist": allowlist_entries(),
        },
        "send": None,
        "imap": None,
        "inbound": None,
        "send_test": None,
    }
    cfg = report["config"]
    out("== Radal mail preflight ==")
    out(f"MAIL_ENABLED={cfg['mail_enabled']}  MAIL_TRANSPORT={kind}  From={address}")
    out(
        f"SES_REGION={cfg['ses_region']}  SES_CONFIGURATION_SET={cfg['ses_configuration_set'] or '(ninguno)'}"
        f"  MAIL_SENT_COPY={cfg['sent_copy']}  MAIL_SENT_FOLDER={cfg['sent_folder']}"
    )
    out(
        f"MAIL_COPY_ADDRESS={cfg['copy_address'] or '(ninguna)'}  "
        f"RADAL_MAIL_REPLY_TO={cfg['reply_to'] or '(vacío ⇒ ' + address + ')'}"
    )
    out(
        f"MAIL_INBOUND_SOURCE={cfg['inbound_source']} (efectivo: {cfg['effective_inbound_source']})"
        + (f"  {cfg['inbound_s3']}" if cfg["inbound_s3"] else "")
    )
    if cfg["allowlist"]:
        out("MAIL_RECIPIENT_ALLOWLIST=" + ", ".join(cfg["allowlist"])
            + " — destinatarios fuera de la lista se omiten (la copia a MAIL_COPY_ADDRESS siempre pasa)")
    else:
        out("MAIL_RECIPIENT_ALLOWLIST=(vacía) — sin restricción de destinatarios")
    if not cfg["mail_enabled"]:
        out("AVISO: MAIL_ENABLED=false — la app registra los correos como 'skipped' y no envía nada.")
    if cfg["sent_copy"] == "bcc" and not cfg["copy_address"]:
        out("AVISO: MAIL_SENT_COPY=bcc sin MAIL_COPY_ADDRESS: no habrá copia visual.")
    if cfg["effective_inbound_source"] == "s3" and not cfg["reply_to"]:
        out("AVISO: MAIL_INBOUND_SOURCE=s3 pero RADAL_MAIL_REPLY_TO está vacío: las consultas piden "
            f"respuesta a {address}, que no llega a S3.")

    try:
        system = transport or get_system_transport()
    except Exception as exc:  # noqa: BLE001
        out(f"ERROR: no se pudo crear el transporte del sistema ({type(exc).__name__})")
        return EXIT_SEND_NOT_READY, report

    # --- send side -----------------------------------------------------------
    send_ok = False
    if kind == "ses":
        out("\n-- Amazon SES --")
        if isinstance(system, SesTransport):
            status = system.ses_status(address)
        else:
            check = system.check(radal_smtp_config(), None)
            status = dict(check.details or {}, ok=bool(check.smtp_ok), error=check.smtp_error)
        report["send"] = status
        account = status.get("account") or {}
        identity = status.get("identity_status") or {}
        if account:
            out(
                f"cuenta: envío habilitado={_yes(account.get('sending_enabled'))}  "
                f"{'SANDBOX' if account.get('sandbox') else 'producción'}  "
                f"enforcement={account.get('enforcement_status') or '—'}"
            )
            out(
                f"cuota 24 h={account.get('max_24h_send')}  enviados últimas 24 h={account.get('sent_last_24h')}"
                f"  tasa máx={account.get('max_send_rate')}/s"
            )
        if identity:
            out(
                f"identidad {status.get('identity')}: verificada={_yes(identity.get('verified_for_sending'))}  "
                f"DKIM={identity.get('dkim_status') or '—'} (firma {_yes(identity.get('dkim_signing_enabled'))})  "
                f"MAIL FROM={identity.get('mail_from_domain') or '—'} ({identity.get('mail_from_status') or '—'})"
            )
        for warning in status.get("warnings") or []:
            out(f"AVISO: {warning}")
        send_ok = bool(status.get("ok"))
        out("SES: listo" if send_ok else f"SES: NO listo — {status.get('error') or 'sin detalle'}")
    else:
        out("\n-- SMTP (Zoho) --")
        smtp = radal_smtp_config()
        check = system.check(smtp, None)
        report["send"] = {"ok": bool(check.smtp_ok), "error": check.smtp_error, "host": smtp.host, "user": smtp.username}
        send_ok = bool(check.smtp_ok)
        out(f"SMTP {smtp.host}:{smtp.port} usuario {smtp.username}: " + ("ok" if send_ok else f"ERROR — {check.smtp_error}"))

    # --- inbound (S3) -----------------------------------------------------------
    inbound_failed = False
    if cfg["effective_inbound_source"] == "s3":
        from app.services.mail.s3_inbound import count_pending, get_s3_client  # noqa: PLC0415

        out("\n-- Respuestas: SES inbound → S3 --")
        bucket, prefix = settings.mail_inbound_bucket, settings.MAIL_INBOUND_S3_PREFIX
        try:
            client = s3_client if s3_client is not None else get_s3_client()
            pending = count_pending(client, bucket, prefix, max_keys=1000)
            report["inbound"] = {"source": "s3", "bucket": bucket, "prefix": prefix, "ok": True, "pending": pending}
            out(f"s3://{bucket}/{prefix}: ok — {pending} objeto(s) por procesar"
                + (" (o más)" if pending >= 1000 else ""))
        except Exception as exc:  # noqa: BLE001
            inbound_failed = True
            report["inbound"] = {"source": "s3", "bucket": bucket, "prefix": prefix, "ok": False,
                                 "error": type(exc).__name__}
            out(f"s3://{bucket}/{prefix}: ERROR — no se pudo listar ({type(exc).__name__}; "
                "revisa s3:ListBucket en el rol)")
    else:
        report["inbound"] = {"source": cfg["effective_inbound_source"]}

    # --- IMAP ----------------------------------------------------------------
    imap = radal_imap_config()
    imap_failed = False
    imap_needed = cfg["effective_inbound_source"] == "imap" or cfg["sent_copy"] == "imap_append"
    if not imap_needed:
        report["imap"] = {"needed": False, "configured": bool(imap.password), "host": imap.host, "user": imap.username}
        out("\n-- IMAP (Zoho) --")
        out("IMAP: no se usa (copia visual por "
            + ("BCC" if cfg["sent_copy"] == "bcc" else "ninguna")
            + ", respuestas por "
            + ("S3" if cfg["effective_inbound_source"] == "s3" else "ninguna vía")
            + ")")
    elif not (imap.host and imap.username and imap.password):
        out("\n-- IMAP (Zoho: respuestas y copia en Enviados) --")
        report["imap"] = {"configured": False, "host": imap.host, "user": imap.username}
        out(
            f"AVISO: IMAP no configurado ({imap.host or 'sin host'}, usuario {imap.username or '—'}, sin contraseña): "
            "no habrá copia en Enviados ni lectura de respuestas."
        )
    else:
        out("\n-- IMAP (Zoho: respuestas y copia en Enviados) --")
        try:
            result = SmtpImapTransport().check(None, imap)
            imap_ok, imap_error = bool(result.imap_ok), result.imap_error
        except Exception as exc:  # noqa: BLE001
            imap_ok, imap_error = False, type(exc).__name__
        report["imap"] = {"configured": True, "host": imap.host, "user": imap.username, "ok": imap_ok, "error": imap_error}
        imap_failed = not imap_ok
        out(f"IMAP {imap.host}:{imap.port} usuario {imap.username}: " + ("ok" if imap_ok else f"ERROR — {imap_error}"))

    # --- test send -------------------------------------------------------------
    test_failed = False
    if args.send_test:
        out(f"\n-- envío de prueba a {args.send_test} --")
        if not send_ok:
            test_failed = True
            report["send_test"] = {"sent": False, "error": "el envío no está listo; no se intentó"}
            out("no se intentó: el lado de envío no está listo")
        else:
            target = args.send_test.strip()
            if not recipient_allowed(target):
                out(f"AVISO: {target} está fuera de MAIL_RECIPIENT_ALLOWLIST; la app lo omitiría "
                    "(la prueba lo envía igual: la pediste explícitamente).")
            copy_to = copy_address()
            bcc = [copy_to] if (cfg["sent_copy"] == "bcc" and copy_to and copy_to.lower() != target.lower()) else None
            reply_to = settings.RADAL_MAIL_REPLY_TO or None
            msg = build_email(
                from_name=settings.RADAL_MAIL_DISPLAY_NAME,
                from_address=address,
                to=[target],
                cc=None,
                subject=TEST_SUBJECT,
                text=TEST_BODY.format(address=address, reply_to=radal_reply_address()),
                html=None,
                reply_to=reply_to,
                message_id=make_msgid(domain=address.rpartition("@")[2] or "radalseguros.cl"),
                kind="preflight",
                bcc=bcc,
            )
            try:
                message_id = system.send(radal_smtp_config(), msg)
                copy_ok = getattr(system, "last_sent_copy_ok", None)
                report["send_test"] = {"sent": True, "message_id": message_id, "sent_copy_ok": copy_ok}
                if bcc:
                    report["send_test"]["bcc"] = bcc
                out(f"enviado: Message-ID {message_id}")
                if bcc:
                    out(f"copia visual: Bcc a {bcc[0]}")
                elif cfg["sent_copy"] == "bcc":
                    out("copia visual: no aplica (el destinatario ya es MAIL_COPY_ADDRESS o no hay dirección)")
                elif copy_ok is None:
                    out("copia en Enviados: no se intentó (MAIL_SENT_COPY=none o IMAP sin credenciales)")
                else:
                    out(f"copia en Enviados ({settings.MAIL_SENT_FOLDER}): " + ("ok" if copy_ok else "FALLÓ (ver log)"))
            except Exception as exc:  # noqa: BLE001
                test_failed = True
                report["send_test"] = {"sent": False, "error": str(exc)[:300]}
                out(f"ERROR: {str(exc)[:300]}")

    if not send_ok or test_failed:
        code = EXIT_SEND_NOT_READY
    elif imap_failed:
        code = EXIT_IMAP_FAILED
    elif inbound_failed:
        code = EXIT_INBOUND_FAILED
    else:
        code = EXIT_OK
    report["exit_code"] = code
    out(f"\nresultado: {'LISTO' if code == EXIT_OK else 'NO LISTO'} (exit {code})")
    if args.json:
        out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return code, report


def main(argv: list[str] | None = None) -> int:
    try:
        code, _report = run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a preflight answers, it never tracebacks
        print(f"ERROR inesperado: {type(exc).__name__}")
        return EXIT_SEND_NOT_READY
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
