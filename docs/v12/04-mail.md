# v12 — mail (Radal system sender via Amazon SES + Zoho mailbox, broker custom servers, inquiries)

> **2026-10-17 change:** the Radal system sender no longer sends through Zoho SMTP. It sends through
> **Amazon SES v2** from `contacto@radalseguros.cl`. Zoho Free has **no IMAP**, so the human copy is a
> **Bcc to `contacto@`** and the replies Radal must read go to **`respuestas@correo.radalseguros.cl`**
> (SES inbound → S3 → `mail.poll`, each one forwarded to `contacto@`). See §6–§8. Broker custom mailboxes
> are unchanged.

## 1. Settings (phase 1 adds to `core/config.py`; all optional; tests blank them)
```
MAIL_ENABLED: bool = False                 # master switch; False => every send is recorded as status "skipped" (no network, no boto3 client)
MAIL_TRANSPORT: str = "ses"                # Radal system sender: "ses" (Amazon SES v2) | "smtp" (Zoho SMTP fallback); validated
SES_REGION: str = "us-east-1"
SES_CONFIGURATION_SET: str = ""            # sent as ConfigurationSetName only when set
MAIL_SENT_COPY: str = "bcc"                # "bcc" (Bcc MAIL_COPY_ADDRESS, envelope only) | "imap_append" (APPEND to MAIL_SENT_FOLDER) | "none"
MAIL_SENT_FOLDER: str = "Sent"
MAIL_COPY_ADDRESS: str = "contacto@radalseguros.cl"   # the human mailbox: Bcc copy + forwards of SES-inbound mail
RADAL_MAIL_REPLY_TO: str = ""              # Reply-To of inquiries; blank => RADAL_MAIL_ADDRESS. Dev: respuestas@correo.radalseguros.cl
MAIL_INBOUND_SOURCE: str = "auto"          # system box from "s3" | "imap" | "none"; auto = imap with RADAL_MAIL_PASSWORD, else none
MAIL_INBOUND_S3_PREFIX: str = "inbound-mail/"
MAIL_INBOUND_S3_BUCKET: str = ""           # blank => S3_BUCKET
MAIL_INBOUND_S3_MAX_PER_RUN: int = 50
MAIL_RECIPIENT_ALLOWLIST: str = ""         # dev safety: "radalseguros.cl,ben@x.cl"; To/Cc outside dropped; none left => skipped
RADAL_MAIL_ADDRESS: str = "contacto@radalseguros.cl"
RADAL_MAIL_DISPLAY_NAME: str = "Radal"
RADAL_MAIL_USERNAME: str = ""              # Zoho login (usually the address)
RADAL_MAIL_PASSWORD: str = ""              # IMAP app password (imap_append / MAIL_INBOUND_SOURCE=imap only) — env/S3 only, never committed
RADAL_MAIL_SMTP_HOST: str = "smtp.zoho.com";  RADAL_MAIL_SMTP_PORT: int = 465;  RADAL_MAIL_SMTP_SECURITY: str = "ssl"   # MAIL_TRANSPORT=smtp only
RADAL_MAIL_IMAP_HOST: str = "imap.zoho.com";  RADAL_MAIL_IMAP_PORT: int = 993;  RADAL_MAIL_IMAP_SECURITY: str = "ssl"
RADAL_OPS_EMAIL: str = ""                  # where RPA/job failures are reported to the RADAL team
MAIL_TIMEOUT_SECONDS: int = 20
```
(Zoho: smtp.zoho.com 465 SSL / 587 STARTTLS; imap.zoho.com 993 SSL. Region-specific hosts like smtp.zoho.eu exist — keep as settings.)

## 2. Transport seam — `app/services/mail/transport.py` (phase 1: Protocol + FakeTransport; B4: real impl)
```python
class MailTransport(Protocol):
    def send(self, cfg: SmtpConfig, msg: email.message.EmailMessage) -> str: ...        # returns Message-ID
    def fetch_since(self, cfg: ImapConfig, cursor: dict | None, *, limit: int = 200) -> tuple[list[FetchedMail], dict]: ...
    def check(self, smtp: SmtpConfig | None, imap: ImapConfig | None) -> CheckResult: ...
def get_transport() -> MailTransport         # SmtpImapTransport (smtplib/imaplib): broker mailboxes + all inbound IMAP
def get_system_transport() -> MailTransport  # Radal system sender: SesTransport (MAIL_TRANSPORT=ses) | SmtpImapTransport (smtp)
# tests monkeypatch a FakeTransport into _OVERRIDE, which both doors honour
```
- Real: `smtplib.SMTP_SSL` / `SMTP` + `starttls()`, `imaplib.IMAP4_SSL`, `SELECT INBOX` (readonly), `UID SEARCH UID {last+1}:*`,
  fetch `RFC822`, cursor `{uidvalidity, last_uid}` (UIDVALIDITY change ⇒ reset to "since 2 days ago" with `SINCE`).
- Build messages with `email.message.EmailMessage`, `make_msgid(domain=...)`, plain-text + simple HTML alternative.
- Sender selection: `collection_settings.default_sender == broker_account` and the account is `connected` & `send_enabled`
  → send through it; else the Radal system mailbox with `From: "{broker trade_name} vía Radal" <contacto@radalseguros.cl>` and
  `Reply-To: <broker contact email or broker account address>` (so replies reach the broker) **plus** a Cc/Bcc-free design:
  inquiry replies must come back to a mailbox Radal reads → for inquiries sent through Radal (and their resends), `Reply-To`
  is `RADAL_MAIL_REPLY_TO` (else the Radal address) and the subject carries the token; the reply is routed to the broker by token.
- System mail Bcc's `MAIL_COPY_ADDRESS` (`MAIL_SENT_COPY=bcc`); `MAIL_RECIPIENT_ALLOWLIST` filters To/Cc for every sender
  (none left ⇒ `skipped`, "Destinatario fuera de la lista permitida de este ambiente").
- Every outbound mail = a `mail_message(direction=outbound)` row (status queued → sent/failed/skipped). Body stored (it is ours).

## 3. Broker custom accounts
Created through `POST /mail/accounts`; password encrypted with `app/core/crypto.py`; `POST /mail/accounts/{id}/test` logs in
to SMTP and IMAP and sets `status`. Google/Microsoft: not creatable (422 with Spanish reason "Pronto: conexión con Google /
Microsoft 365 mediante OAuth"), and the UI shows them disabled with a "pronto" chip.

## 4. Inbound processing — `jobs/mail.py` `mail.poll`
1. For the Radal system box (row `mail_account` with `broker_id NULL, provider radal_system`, created lazily) — from **S3**
   (§8), IMAP or nowhere per `MAIL_INBOUND_SOURCE` — and each broker account with `receive_enabled` and `status=connected`
   (always IMAP): fetch new mail since cursor.
2. **Prefilter before storing anything** (privacy, Ley 21.719): keep a message only if (a) it carries a `RADAL-INQ-` token in
   subject/body or `In-Reply-To` matching one of our outbound `message_id_header`s, or (b) the sender domain is in some insurer's
   `sender_domains` AND the mailbox is a broker account, or (c) it is a bounce (`mailer-daemon`, `postmaster`, DSN content type)
   for one of our outbound messages. Otherwise store only `{provider_uid, occurred_at, status=ignored}` (no from/subject/body).
3. Kept messages: save the `.eml` as a `document(category=email_message, entity_type=status_inquiry|policy)` via the existing
   document storage helper (rule 8), set `classification` via AI (`classify_inbound_mail` task, small tier, persists an
   `extraction`), then extract `{reported_status, installment_numbers[], amount, currency, paid_on, comment}` for
   `insurer_status_reply|insurer_statement|cancellation_notice` → create `payment_evidence` rows (one per referenced cuota)
   with `score`/`reasons`; inquiry → `replied`; notification `inquiry_reply`/`evidence_pending` to the inquiry creator.
4. Bounce → `collection_notice.status = bounced` (+ todo notification `mail_account_error` if the account itself fails).
5. AI is hermetic in tests (fake the chat model as existing tests do).

## 5. Default templates — `backend/app/db/seed_assets/notice_templates_es.json` (phase 1 writes it)
Kinds `payer_reminder` (levels −5, 0), `payer_overdue` (+3, +10), `payment_link`, `insurer_status_inquiry`. Variables:
`{{broker_name}} {{insured_name}} {{policy_number}} {{insurer_name}} {{installment_number}} {{installment_count}} {{due_date}}
{{amount_uf}} {{amount_clp}} {{share_url}} {{insurer_instructions}} {{inquiry_token}} {{installments_table}}`.
Rendering: a tiny `{{var}}` replacer (no Jinja), unknown vars → empty string, values escaped in the HTML part.

## 6. The Radal system sender — Amazon SES v2 (send) + Zoho (the human mailbox)

**Why.** `radalseguros.cl` has four Google Workspace mailboxes (root MX → Google) and one **Zoho Free**
mailbox, `contacto@radalseguros.cl`. Sending through Zoho SMTP would fail SPF/DMARC (the root SPF only
includes Google) and Radal must never send as, or touch, the Google users. SES sends with its own DKIM and
MAIL FROM, so nothing at the root changes.

```
send_mail ──► get_system_transport() ──► SesTransport.send(cfg, msg)
                                            │ sesv2.send_email(FromEmailAddress=contacto@…,
                                            │   Destination={To,Cc,Bcc}, Content={"Raw":{"Data": msg CRLF}},
                                            │   [ConfigurationSetName])
                                            │ ◄── MessageId  →  "<{MessageId}@email.amazonses.com>"
                                            │     (written back into msg; returned; stored as message_id_header)
                                            ├─► Bcc contacto@ (MAIL_SENT_COPY=bcc: Destination.BccAddresses only)
                                            └─► append_sent_copy(IMAP, raw, "Sent")   only with MAIL_SENT_COPY=imap_append
mail.poll ──► MAIL_INBOUND_SOURCE=s3:   s3_inbound.poll_s3_inbound()   (§8)
          └─► MAIL_INBOUND_SOURCE=imap: get_transport().fetch_since(radal_imap_config())
broker mailbox ──► get_transport().send(account_smtp_config(...))  (unchanged: the broker's own SMTP, never SES, no Bcc)
```

- **From** is always `RADAL_MAIL_ADDRESS` (`"{broker} vía Radal" <contacto@radalseguros.cl>` for broker mail,
  `Radal` for platform mail); **Reply-To** exactly as before (§2).
- **Message-ID.** SES replaces ours; the SES one is what the row keeps, so `In-Reply-To` of an insurer's reply
  matches `mail_message.message_id_header`. A failed or skipped send keeps our own `make_msgid` id.
- **Errors.** `ClientError` & co. become a Spanish category (`ERR_SES_*`) on the `failed` row; `send_mail` never
  raises. In the sandbox an unverified recipient reads "Amazon SES está en modo sandbox y el destinatario no
  está verificado".
- **Visual copy.** Default `MAIL_SENT_COPY=bcc`: every system mail Bcc's `MAIL_COPY_ADDRESS` unless it is
  already To/Cc; the Bcc header is removed before the raw is built, so recipients never see it. Option
  `imap_append`: after an SES success, APPEND the same bytes (with the SES `Message-ID`) to the IMAP `Sent`
  folder — needs an IMAP login, which Zoho Free does not offer.
- **Readiness.** `SesTransport.check()` and `python -m scripts.mail_preflight` (GetAccount, GetEmailIdentity,
  copy/reply/inbound/allowlist settings, the S3 inbound count, IMAP only when used, optional `--send-test`).
  Exit `0` / `3` (SES not ready) / `4` (IMAP needed and refused) / `5` (S3 inbound prefix unlistable).
- **Hermetic tests** (`tests/test_v12_mail_ses.py`): fake `sesv2` client + fake `imaplib`/`smtplib`; conftest pins
  `MAIL_TRANSPORT=ses`, points `AWS_ENDPOINT_URL_SESV2` at the discard port and an autouse fixture makes the
  real client factory refuse — no test can reach AWS.
- **DNS / IAM / sandbox / the open MX question:** `deploy/v12/README.md` "Mail via SES".

## 7. The final mail architecture (dev, 2026-10-17)

```
OUT    Radal <contacto@radalseguros.cl> ──SES──► To/Cc (allowlist)  +  Bcc contacto@ ──► Google ──► Zoho (human copy)
REPLY  insurer ──► respuestas@correo.radalseguros.cl ──MX correo.──► SES receipt rule ──► s3://radal-dev-185011028331/inbound-mail/<id>
       mail.poll ──► process_message (same pipeline as IMAP) ──► Fwd: … to contacto@ (rfc822 attached) ──► inbound-mail/processed/<id>
```

Only mail that EXPECTS a Radal-processed answer (insurer status inquiries + resends) carries
`Reply-To: respuestas@correo.radalseguros.cl`. Payer reminders reply to the broker's contact. Ops alerts and
forwards are Radal's own. Nobody's Google mailbox is touched; Zoho only receives.

## 8. Inbound from S3 — `services/mail/s3_inbound.py`

`MAIL_INBOUND_SOURCE=s3` ⇒ `mail.poll` runs `poll_s3_inbound(db, system_account)` for the Radal system box:

1. `list_objects_v2(Bucket, Prefix=MAIL_INBOUND_S3_PREFIX, Delimiter="/")`, paginated, oldest first, at most
   `MAIL_INBOUND_S3_MAX_PER_RUN`; `processed/` is never listed.
2. Dedupe on `(system mail_account, provider_uid = S3 key)` (hashed when over 60 chars).
3. `AMAZON_SES_SETUP_NOTIFICATION`, anything without a From + (Date or Message-ID), or `X-SES-Virus-Verdict: FAIL`
   ⇒ one `ignored` row (`error=not_an_email|virus_verdict_fail`, no content), archived, not forwarded.
4. Otherwise `inbound.process_message(..., provider_uid=key)` — the identical prefilter / `.eml` / AI / evidence /
   inquiry / bell / bounce pipeline.
5. Forward to `MAIL_COPY_ADDRESS` through the system SES transport: `Fwd: <subject>`, Spanish note with the original
   From/Date/Subject and what Radal did, `Reply-To` = original sender, original as `message/rfc822`; an outbound
   `mail_message` with `provider_uid = "fwd:" + key` (status `sent`/`skipped` = done, so never twice). Prefilter-ignored
   mail is forwarded but its row stores no subject/body. Mail with `X-Radal-Kind` is never forwarded.
6. Commit, then move to `processed/<tail>` (copy + delete). Failures are counted, logged, never raised; the object
   stays and the next run resumes from the dedupe row.

Tests: `tests/test_v12_mail_inbound_s3.py` (a fake `s3` client via `s3_inbound._OVERRIDE`; the conftest guard refuses
a real one).
