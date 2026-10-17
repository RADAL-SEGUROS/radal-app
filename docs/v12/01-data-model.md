# v12 — data model contract

Implemented in PHASE 1 by one agent. Everything below is binding for every later lane: names, types, nullability, enums.
Conventions: `Base, TimestampMixin` from `app.models.base_class`; `sql_enum` from `app.models.enums`; `UF`, `PCT`, `JSONType`
from `app.models.types`. `[ws]` = workspace table (has `broker_id` FK `broker.id` ON DELETE CASCADE, indexed, NOT NULL).
`[pl]` = platform table (no broker_id). `FK→x (SET NULL)` = nullable FK with `ondelete="SET NULL"`, indexed.
Every ALTERed column on a pre-existing table is nullable **or** carries a `server_default`.

New model modules (one file each, registered in `app/models/__init__.py` and therefore in `app.db.base`):
`catalog.py`, `policy_party.py` (or inside `policy.py`), `payment.py`, `collection_settings.py`, `notification.py`,
`jobs.py`, `currency.py`, `insurer_collection.py`, `provider.py`, `mail.py`, `share_link.py`, `rpa.py`.

---

## 1. Catalogue (closed ramo list)

### `insurance_line` — ALTER (pre-existing)
| column | type | notes |
|---|---|---|
| `code` | `String(8)`, nullable, indexed | ramo code from the CSV (`VEH`, `PRO`, …). Set ONLY on global rows (`broker_id IS NULL`). |
| `sort_order` | `Integer`, NOT NULL, `server_default="0"` | display order |

The 18 ramos are **global** `insurance_line` rows (`broker_id NULL`), upserted by `code` from the catalogue seed.
Broker-owned `insurance_line` rows stay (legacy) but no endpoint may create new ones any more.

### `insurance_subtype` — NEW `[pl]`
| column | type |
|---|---|
| `id` | PK |
| `insurance_line_id` | FK→`insurance_line.id` ON DELETE CASCADE, NOT NULL, indexed |
| `code` | `String(12)`, NOT NULL, **unique** (e.g. `PRO-02`) |
| `name` | `String(160)`, NOT NULL (Spanish display data from the CSV — data, not code) |
| `suggested_entry` | enum `SuggestedEntry{market, portal}` NOT NULL (CSV `mercado`→market, `portal`→portal) |
| `suggested_route` | enum `PlacementRoute{traditional, digital, manual}` NOT NULL (market→traditional, portal→digital) |
| `cmf_codes` | `JSONType` nullable — list of CMF ramo codes (strings) this subtipo reports under |
| `is_active` | `Boolean` NOT NULL default True |
| `sort_order` | `Integer` NOT NULL default 0 |

Seed source: `backend/app/db/seed_assets/catalogo_ramos.csv` = team CSV (`docs/v12/catalogo_ramos.csv`) **plus one row the team's
HTML has and the CSV lacks**: `PRO,Property,PRO-05,Robo,mercado`. CMF mapping (CMF generales codes, see analysis):
PRO-01→[1,3], PRO-02→[1,3,4,6], PRO-03→[2,5], PRO-04→[7], PRO-05→[8]; VEH-*→[10,16]; CAS-01→[11]; CAS-02→[12];
RC-01→[13]; RC-02,RC-03,RC-07→[14]; RC-04,RC-05,RC-06,RC-08→[15]; TRA-01→[17]; TRA-02→[18,19]; ING-01,ING-03→[21]; ING-02→[20];
ING-04→[22]; ING-05→[23]; GYC-01→[24]; GYC-02→[25]; GYC-03→[27]; GYC-04→[28]; OTR-02→[26]; AP-*→[31]; SAL-*→[30]; SOA-01→[32];
OTR-01→[33]; OTR-03→[34]; AGR-01→[35]; ASV-*→[36]; HOG-01,CON-01,PYM-01→[1,3,4,8,9,13]; CIB-01→[50]; OTR-04→[50]; VID-*→[] (life
list not seeded). Seeder: `backend/scripts/seed_v12.py` (idempotent upsert by code; also callable from tests and `seed_dev`).

### `case_file` — ALTER
| column | type | notes |
|---|---|---|
| `insurance_subtype_id` | FK→`insurance_subtype.id` (SET NULL) | the colocación's subtipo |
| `entry_origin` | enum `CaseEntryOrigin{full_journey, quote_comparison, policy_upload, manual_entry, portal_rpa, migration}` nullable | where it was born (team `origen`). **As built (phase-1 gate):** NOT `origin` — `case_file.origin` already exists as the v3 axis `CaseOrigin{new, renewal, period_change}` (NOT NULL, used by renewals) and is unchanged. |
| `route` | enum `PlacementRoute{traditional, digital, manual}` nullable | how it is worked (team `via_prevista`) |

### `case_file_stage_event` — ALTER
| column | type | notes |
|---|---|---|
| `is_skipped` | `Boolean` NOT NULL `server_default="0"` (use `sa.false()`), default False | a stage marked *omitida* by the system |

---

## 2. Policy (the main view)

### `policy` — ALTER
| column | type | notes |
|---|---|---|
| `insurance_subtype_id` | FK→`insurance_subtype.id` (SET NULL) | |
| `origin` | enum `PolicyOrigin{reader, manual, journey, portal_rpa, migration}` nullable | how it entered Radal |
| `has_source_document` | `Boolean` NOT NULL server_default false | team *respaldo* (false ⇒ "Sin respaldo") |
| `is_provisional` | `Boolean` NOT NULL server_default false | team *Condicionada* (certificado de cobertura) |
| `currency` | enum `PolicyCurrency{UF, USD}` NOT NULL server_default `'UF'` | |
| `payment_method` | enum `PaymentMode` (existing) nullable | plan's method mirrored for listing |
| `terminated_on` | `Date` nullable | cancellation effective date |
| `termination_reason` | enum `TerminationReason{non_payment, insured_request, insurer_request, other}` nullable | |

### `policy_party` — NEW `[ws]`
| column | type |
|---|---|
| `id` | PK |
| `policy_id` | FK→`policy.id` ON DELETE CASCADE NOT NULL indexed |
| `role` | enum `PartyRole{insured, policyholder, payer, beneficiary, additional_insured}` NOT NULL |
| `client_id` | FK→`client.id` (SET NULL) — set when the RUT is one of the broker's clients |
| `name` | `String(255)` NOT NULL |
| `rut` | `String(20)` nullable (normalised `BODY-DV`) |
| `email` | `String(255)` nullable |
| `phone` | `String(64)` nullable |
| `beneficiary_kind` | `String(32)` nullable (hipotecario/prendario/leasing/endosatario/otro — free data) |
| `share_pct` | `PCT` nullable |
| `is_primary` | `Boolean` NOT NULL default False |
| `notify_collections` | `Boolean` NOT NULL default True |
Index `(broker_id, policy_id)`. Business rule: payer defaults to policyholder; policyholder defaults to insured.

---

## 3. Collections core

### `collection_plan` — ALTER
| column | type |
|---|---|
| `origin` | enum `CollectionPlanOrigin{policy, endorsement, extension}` nullable |
| `source` | enum `CollectionPlanSource{reader, manual}` nullable |
| `source_document_id` | FK→`document.id` (SET NULL) |
| `mandate_id` | FK→`payment_mandate.id` (SET NULL, `use_alter=True`, name `fk_collection_plan_mandate`) |
| `level_preference` | enum `CollectionLevelPreference{auto, assisted, record}` NOT NULL server_default `'auto'` |
| `notices_enabled` | `Boolean` NOT NULL server_default false — broker switched payer reminders ON for this plan ("Iniciar cobranza") |
| `notices_paused_until` | `Date` nullable |
| `notices_pause_reason` | `Text` nullable |

### `collection_installment` — ALTER
| column | type |
|---|---|
| `currency` | enum `PolicyCurrency` NOT NULL server_default `'UF'` |
| `direct_debit_rejected_at` | `Date` nullable |
| `voided_by_endorsement_id` | FK→`endorsement.id` (SET NULL) |
| `insurer_notice_sent_on` | `Date` nullable (art. 528 notice; termination = +15 days, computed) |
| `promise_until` | `Date` nullable (latest *Promete pagar el*) |
`status` stays as a **cache** written by `services/collection/status.py` on every payment write and by the daily clock.

### `payment` — NEW `[ws]`
| column | type |
|---|---|
| `id` | PK |
| `installment_id` | FK→`collection_installment.id` ON DELETE CASCADE NOT NULL indexed |
| `amount` | `Numeric(16,4)` NOT NULL |
| `currency` | enum `PaymentCurrency{UF, USD, CLP}` NOT NULL |
| `uf_rate` | `Numeric(12,4)` nullable (UF value used when currency=CLP) |
| `amount_uf` | `UF` NOT NULL (normalised; what the state machine sums) |
| `paid_on` | `Date` NOT NULL |
| `method` | enum `PaymentMode` nullable |
| `source` | enum `PaymentSource{manual, insurer_statement, insurer_reply, insurer_share_link, provider, payer_upload, agent}` NOT NULL |
| `evidence_level` | enum `EvidenceLevel{declared, reported, insurer_confirmed, provider_confirmed, settled}` NOT NULL |
| `status` | enum `PaymentStatus{active, reversed}` NOT NULL default active |
| `reversal_reason` | enum `ReversalReason{error, direct_debit_rejected, chargeback, other}` nullable |
| `reversal_note` | `Text` nullable |
| `reversed_at` | `DateTime(tz)` nullable · `reversed_by_user_id` FK→user (SET NULL) |
| `confirmed_by_insurer` | `Boolean` NOT NULL default False · `confirmed_at` DateTime(tz) · `confirmed_by_user_id` FK→user (SET NULL) |
| `receipt_document_id` | FK→`document.id` (SET NULL) |
| `provider_charge_id` | FK→`provider_charge.id` (SET NULL), **unique** |
| `evidence_id` | FK→`payment_evidence.id` (SET NULL, `use_alter=True`, name `fk_payment_evidence`) |
| `registered_by_user_id` | FK→user (SET NULL) |
| `note` | `Text` nullable |

### `collection_settings` — NEW `[ws]` (one row per broker; created lazily with defaults)
| column | type / default |
|---|---|
| `broker_id` | **unique** |
| `cadence_days` | JSON, default `[-15, -5, 0, 3, 10]` |
| `followup_days` | Integer default 5 |
| `tolerance_uf` | UF default `0.0090` |
| `tolerance_pct` | PCT default `1.000` |
| `tolerance_clp` | Integer default 1000 |
| `notices_enabled` | Boolean default False (broker-wide master switch) |
| `notices_approved_at` | DateTime(tz) nullable · `notices_approved_by_user_id` FK→user (SET NULL) |
| `default_sender` | enum `MailSender{radal, broker_account}` default radal |
| `sender_mail_account_id` | FK→`mail_account.id` (SET NULL) |
| `native_collection_enabled` | Boolean default True |
| `auto_status_inquiries` | Boolean default False |
| `inquiry_after_days` | Integer default 3 (days past due before auto-asking the insurer) |

### `notice_template` — NEW `[ws]`
| column | type |
|---|---|
| `kind` | enum `NoticeKind{payer_reminder, payer_overdue, payment_link, insurer_status_inquiry}` NOT NULL |
| `cadence_level` | Integer nullable |
| `channel` | enum `NoticeChannel{email, whatsapp}` NOT NULL default email (`whatsapp` = open door, never sent) |
| `subject` | `String(255)` NOT NULL |
| `body` | `Text` NOT NULL (Spanish, `{{variable}}` placeholders) |
| `is_active` | Boolean default True |
| `approved_by_user_id` / `approved_at` | nullable |
Defaults (when a broker has none) come from `backend/app/db/seed_assets/notice_templates_es.json` (data).

### `collection_notice` — NEW `[ws]` (one row per outbound reminder/aviso)
| column | type |
|---|---|
| `installment_id` | FK→`collection_installment.id` (SET NULL) |
| `kind` | `NoticeKind` NOT NULL · `cadence_level` Integer nullable · `channel` `NoticeChannel` NOT NULL |
| `recipient_party_id` | FK→`policy_party.id` (SET NULL) |
| `to_address` | `String(255)` NOT NULL · `cc` JSON nullable · `subject` `String(255)` |
| `mail_message_id` | FK→`mail_message.id` (SET NULL) |
| `share_link_id` | FK→`share_link.id` (SET NULL) |
| `payment_intent_id` | FK→`payment_intent.id` (SET NULL) |
| `status` | enum `NoticeStatus{queued, sent, failed, bounced, skipped}` NOT NULL |
| `sent_at` | DateTime(tz) · `error` Text |
UniqueConstraint `(installment_id, kind, cadence_level, channel)` name `uq_collection_notice_once`.

### `notification` — NEW `[ws]` (the bell)
| column | type |
|---|---|
| `user_id` | FK→`user.id` ON DELETE CASCADE NOT NULL indexed |
| `tray` | enum `NotificationTray{todo, fyi}` NOT NULL |
| `kind` | enum `NotificationKind{installment_due, installment_overdue, termination_risk, direct_debit_rejected, promise_broken, plan_missing, inquiry_reply, evidence_pending, payment_received, native_collection_available, portal_search_failed, portal_search_done, mail_account_error, sensitive_action}` NOT NULL |
| `level` | `String(16)` nullable (e.g. `-5`, `+10`, `term`) |
| `origin_type` | `String(32)` NOT NULL · `origin_id` Integer NOT NULL |
| `params` | JSON nullable (render data: policy number, insurer, amount, due date…) |
| `link_path` | `String(255)` nullable (SPA path to open) |
| `status` | enum `NotificationStatus{new, seen, closed}` NOT NULL default new |
| `closed_at` | DateTime(tz) nullable |
Index `(broker_id, user_id, status)`. Rule: at most ONE non-closed row per `(user_id, origin_type, origin_id)` — escalation
updates the row (kind/level/tray/params) and resets it to `new` (enforced in `services/notifications.py`).

### `job_run` — NEW `[pl]`
`job String(64)` NOT NULL · `scheduled_for DateTime(tz)` NOT NULL · `status` enum `JobStatus{running, succeeded, failed, skipped}` ·
`started_at`, `finished_at` DateTime(tz) · `stats` JSON · `error` Text. UniqueConstraint `(job, scheduled_for)`.

### `currency_rate` — NEW `[pl]`
`rate_date Date` **unique** NOT NULL · `uf UF` nullable · `usd_observed Numeric(12,4)` nullable · `source String(32)`.

---

## 4. Insurer capability & payment provider (Forpay)

### `insurer_collection_profile` — NEW `[pl]` (canonical, written only by platform admins)
| column | type |
|---|---|
| `insurer_id` | PK, FK→`insurer.id` ON DELETE CASCADE |
| `onboarding_status` | enum `OnboardingStatus{not_contacted, in_conversation, onboarding, sandbox, active, suspended}` NOT NULL default not_contacted |
| `status_note` | Text (internal, never shown to brokers) |
| `expected_live_on` | Date nullable (internal; brokers are NOT shown "on its way" — product decision §1.3) |
| `payment_instructions` | JSON nullable `{portal_url, bank_name, account_type, account_number, account_holder, account_rut, transfer_email, receipt_email, notes}` |
| `collections_email` | `String(255)` nullable — where status inquiries go by default |
| `sender_domains` | JSON nullable — list like `["hdi.cl"]`, used to classify inbound mail |
| `updated_by_user_id` | FK→user (SET NULL) |
`insurer.payment_url` stays (legacy); the profile's `payment_instructions.portal_url` wins when present.

### `broker_insurer_collection` — NEW `[ws]` (broker-admin overrides)
`insurer_id` FK→insurer CASCADE NOT NULL · `collections_email String(255)` · `contact_name String(160)` ·
`payment_instructions` JSON (same shape; overrides field-by-field) · `native_enabled Boolean default True` · `notes Text`.
UniqueConstraint `(broker_id, insurer_id)`.

### `provider_account` — NEW `[pl]` (one per Forpay "company" credential set)
| column | type |
|---|---|
| `provider` | enum `PaymentProviderName{forpay}` NOT NULL |
| `label` | `String(120)` NOT NULL |
| `owner` | enum `ProviderOwner{radal_mall, insurer}` NOT NULL |
| `insurer_id` | FK→insurer (SET NULL) — when owner=insurer |
| `environment` | enum `ProviderEnvironment{sandbox, production}` NOT NULL default sandbox |
| `base_url` | `String(255)` NOT NULL (sandbox `https://devapi.forpayservices.cl/white/label`) |
| `commerce` | `String(64)` · `channel_header` `String(64)` · `process_id` `String(16)` default `WHITE_LABEL` · `username` `String(128)` · `sales_executive_id` `String(64)` |
| `secret_ciphertext` | Text nullable — encrypted JSON `{api_key, password, hmac_key, public_key_pem}` |
| `webhook_key` | `String(48)` NOT NULL **unique** (public id, goes in the webhook query string) |
| `webhook_secret_ciphertext` | Text NOT NULL (encrypted random secret used for the `sig` query param) |
| `status` | enum `ProviderAccountStatus{draft, active, disabled}` default draft |
| `last_verified_at` DateTime(tz) · `last_error` Text |

### `insurer_payment_channel` — NEW `[pl]`
`insurer_id` FK→insurer CASCADE NOT NULL · `channel` enum `PaymentChannel{webpay, oneclick, bank_transfer, direct_debit}` NOT NULL ·
`provider_account_id` FK→provider_account (SET NULL) · `store_commerce_code String(32)` (Transbank child store) ·
`status` enum `ChannelStatus{pending, sandbox, active, suspended}` default pending · `fee_model` JSON · `activated_at` DateTime(tz).
UniqueConstraint `(insurer_id, channel)`.

### `payment_intent` — NEW `[ws]` (one checkout = one Forpay OnePay mandate)
`external_id String(64)` **unique** (`rdl-intent-<hex>`) · `share_link_id` FK→share_link (SET NULL) · `payer_party_id` FK→policy_party (SET NULL) ·
`provider_account_id` FK→provider_account (SET NULL) · `provider_mandate_id String(64)` · `checkout_url String(1024)` ·
`status` enum `IntentStatus{created, pending, paid, partially_paid, failed, expired, cancelled}` default created ·
`expires_at` DateTime(tz) · `created_by_user_id` FK→user (SET NULL).

### `payment_intent_item` — NEW `[ws]`
`intent_id` FK→payment_intent CASCADE NOT NULL · `installment_id` FK→collection_installment CASCADE NOT NULL ·
`insurer_id` FK→insurer (SET NULL) · `amount_uf UF` NOT NULL · `store_commerce_code String(32)` ·
`external_installment_id String(64)` **unique** (`rdl-cuota-<installment_id>-<intent_hex>`).

### `provider_charge` — NEW `[ws]` (one row per Forpay installment we learn about)
`provider_account_id` FK→provider_account (SET NULL) · `intent_id` FK→payment_intent (SET NULL) · `mandate_id` FK→payment_mandate (SET NULL) ·
`installment_id` FK→collection_installment (SET NULL) · `provider_mandate_id String(64)` · `provider_installment_id String(64)` NOT NULL ·
`external_installment_id String(64)` · `state Integer` · `state_name String(64)` · `amount Numeric(16,4)` · `currency String(8)` ·
`amount_clp Integer` · `authorization_code String(64)` · `payment_method String(64)` · `paid_at DateTime(tz)` ·
`payout_id` FK→provider_payout (SET NULL) · `raw` JSON.
UniqueConstraint `(provider_account_id, provider_installment_id)`.

### `provider_payout` — NEW `[pl]`
`provider_account_id` FK CASCADE NOT NULL · `dispersion_id String(64)` NOT NULL · `insurer_id` FK (SET NULL) · `credited_amount Numeric(16,4)` ·
`deposit_date Date` · `destination_account_masked String(64)` · `bank_tx_number String(64)` · `status_name String(64)` · `raw` JSON.
UniqueConstraint `(provider_account_id, dispersion_id)`.

### `provider_event` — NEW `[pl]` (webhook inbox)
`provider` `PaymentProviderName` NOT NULL · `provider_account_id` FK (SET NULL) · `dedup_key String(128)` NOT NULL · `event_type String(32)` ·
`signature_valid Boolean` NOT NULL · `payload` JSON · `received_at` DateTime(tz) NOT NULL · `processing_status` enum
`EventProcessing{received, processed, ignored, failed}` · `attempts Integer default 0` · `error Text` · `broker_id Integer` nullable (no FK needed; resolved).
UniqueConstraint `(provider, dedup_key)`.

### `payment_mandate` — NEW `[ws]` (PAC / Oneclick recurring, Forpay DYNAMIC mandate)
`plan_id` FK→collection_plan CASCADE NOT NULL · `payer_party_id` FK→policy_party (SET NULL) · `insurer_id` FK (SET NULL) ·
`provider_account_id` FK (SET NULL) · `channel PaymentChannel` · `provider_mandate_id String(64)` · `magic_link_url String(1024)` ·
`consent_document_id` FK→document (SET NULL) · `status` enum `MandateStatus{pending, bank_confirmation, active, revoked, failed}` ·
`bank_name String(120)` · `account_last4 String(8)` · `activated_at`, `revoked_at` DateTime(tz).

---

## 5. Mail

### `mail_account` — NEW (broker_id NULLABLE: NULL = the Radal system mailbox row used for IMAP cursor storage)
| column | type |
|---|---|
| `broker_id` | FK→broker CASCADE **nullable** indexed |
| `owner_user_id` | FK→user (SET NULL) — null = shared broker mailbox |
| `provider` | enum `MailProvider{radal_system, custom, google, microsoft}` NOT NULL (google/microsoft reserved — not creatable now) |
| `address` | `String(255)` NOT NULL · `display_name String(160)` |
| `smtp_host String(255)` · `smtp_port Integer` · `smtp_security` enum `MailSecurity{ssl, starttls, none}` | |
| `imap_host String(255)` · `imap_port Integer` · `imap_security MailSecurity` | |
| `username` | `String(255)` |
| `secret_ciphertext` | Text nullable (encrypted password) |
| `send_enabled` | Boolean default True · `receive_enabled` Boolean default True |
| `sync_cursor` | JSON nullable `{uidvalidity, last_uid}` |
| `status` | enum `MailAccountStatus{draft, connected, error, paused}` default draft |
| `last_sync_at` DateTime(tz) · `last_error` Text |
UniqueConstraint `(broker_id, address)`.

### `mail_message` — NEW (broker_id nullable for unmatched system-inbox mail)
`broker_id` FK nullable indexed · `mail_account_id` FK (SET NULL) · `direction` enum `MailDirection{outbound, inbound}` ·
`message_id_header String(255)` indexed · `in_reply_to String(255)` · `thread_token String(32)` indexed (our `RADAL-XXXXXX`) ·
`from_address String(255)` · `to_addresses` JSON · `cc_addresses` JSON · `subject String(500)` · `body_text Text` (outbound: full;
inbound: only when classified as related) · `occurred_at DateTime(tz)` · `provider_uid String(64)` ·
`classification` enum `InboundClass{insurer_status_reply, insurer_statement, cancellation_notice, payer_reply, bounce, unrelated}` nullable ·
`extraction_id` FK→extraction (SET NULL) · `document_id` FK→document (SET NULL) (the .eml) ·
`status` enum `MailMessageStatus{queued, sent, failed, skipped, received, processed, ignored}` (`skipped` = `MAIL_ENABLED=false`, see 04 §1) · `error Text`.
UniqueConstraint `(mail_account_id, provider_uid)`.

### `status_inquiry` — NEW `[ws]` (Radal asks an insurer "¿está pagada la cuota…?")
`insurer_id` FK NOT NULL · `policy_id` FK→policy CASCADE NOT NULL · `token String(16)` **unique** (`INQ-` + 6 base32) ·
`installment_ids` JSON NOT NULL · `to_address String(255)` · `outbound_message_id` FK→mail_message (SET NULL) ·
`share_link_id` FK→share_link (SET NULL) (the insurer can answer through a public link) ·
`status` enum `InquiryStatus{draft, sent, replied, resolved, failed, cancelled}` · `sent_at`, `replied_at`, `resolved_at` DateTime(tz) ·
`created_by_user_id` FK (SET NULL) · `created_by_job` Boolean default False.

### `payment_evidence` — NEW `[ws]` (a suggestion that a cuota's status changed; human confirms)
`installment_id` FK→collection_installment (SET NULL) · `source` enum `EvidenceSource{insurer_reply, insurer_share_link, insurer_statement, payer_upload}` ·
`mail_message_id` FK (SET NULL) · `status_inquiry_id` FK (SET NULL) · `document_id` FK (SET NULL) · `extraction_id` FK (SET NULL) ·
`reported_status` enum `ReportedStatus{paid, unpaid, partial, cancelled, unknown}` · `reported_amount Numeric(16,4)` ·
`reported_currency` `PaymentCurrency` nullable · `reported_paid_on Date` · `score Numeric(5,2)` · `reasons` JSON ·
`status` enum `EvidenceStatus{suggested, needs_review, confirmed, rejected, superseded}` default suggested ·
`decided_by_user_id` FK (SET NULL) · `decided_at` DateTime(tz) · `payment_id` FK→payment (SET NULL) · `note Text`.

---

## 6. Share links (public, WhatsApp-ready)

### `share_link` — NEW `[ws]`
| column | type |
|---|---|
| `token` | `String(64)` NOT NULL **unique** (`secrets.token_urlsafe(24)`) |
| `entity_type` | enum `ShareEntity{policy, collection_plan, installment, case_file, status_inquiry}` NOT NULL |
| `entity_id` | Integer NOT NULL |
| `audience` | enum `ShareAudience{insured, insurer}` NOT NULL |
| `permissions` | JSON nullable `{can_pay, can_upload_receipt, can_report_status, show_documents}` |
| `label` | `String(160)` nullable |
| `expires_at` | DateTime(tz) nullable · `revoked_at` DateTime(tz) nullable |
| `created_by_user_id` | FK (SET NULL) |
| `view_count` | Integer NOT NULL default 0 · `last_viewed_at` DateTime(tz) |

---

## 7. RPA (portal search)

### `portal_credential` — NEW `[ws]`
`portal String(32)` NOT NULL (`hdi`) · `username String(255)` · `secret_ciphertext Text` · `status` enum `CredentialStatus{unverified, valid, invalid}` ·
`last_verified_at` DateTime(tz) · `last_error Text`. UniqueConstraint `(broker_id, portal)`.

### `portal_search_run` — NEW `[ws]`
`portal String(32)` NOT NULL · `case_file_id` FK (SET NULL) · `insurance_subtype_id` FK (SET NULL) · `requested_by_user_id` FK (SET NULL) ·
`input` JSON NOT NULL · `status` enum `PortalRunStatus{queued, running, succeeded, failed, cancelled}` default queued ·
`error_kind` enum `PortalErrorKind{credentials_missing, login_failed, layout_changed, timeout, captcha, no_results, portal_unavailable, unexpected}` nullable ·
`error_detail Text` (technical, internal) · `steps` JSON (step log) · `screenshot_document_id` FK→document (SET NULL) ·
`connector_version String(16)` · `started_at`, `finished_at` DateTime(tz).

### `portal_quote` — NEW `[ws]`
`run_id` FK→portal_search_run CASCADE NOT NULL · `insurer_id` FK (SET NULL) · `plan_name String(255)` · `premium_uf UF` ·
`premium_clp Integer` · `deductible String(255)` · `coverages` JSON · `raw` JSON · `proposal_id` FK→proposal (SET NULL).

---

## 8. Agent

### `agent_thread` — ALTER
`active_skills` JSON nullable — list of skill keys loaded in this thread.

---

## 9. Enum additions to EXISTING enums (watch the VARCHAR width)

| enum | add | width impact |
|---|---|---|
| `EntityType` (max today `inspection_request`=18) | `payment`, `installment`, `share_link`, `status_inquiry`, `mail_account`, `portal_search`, `insurance_subtype`, `payment_intent` | none if every value ≤ 18 chars (`insurance_subtype`=17 ✓) |
| `DocumentCategory` (max today 25) | `payment_receipt`, `email_message`, `cancellation_letter`, `payment_consent`, `portal_screenshot`, `insurer_statement_upload` | none (≤25) |
| `CaseStage` | none | — |
| `PaymentMode` | none | — |

If any new value of an EXISTING enum is longer than its current max, the column widens: add it to the v12 pass's
`WIDENED_COLUMNS` manifest in `migrate_case_files.py` (same mechanism as `document.category` 29→37 in the case-files pass).

## 10. Migration (`backend/scripts/migrate_case_files.py`, new `--pass v12`)

Add `V12_NEW_TABLES` (all NEW tables above), `V12_ADDED_COLUMNS` (all ALTER columns above, per table) and `V12_WIDENED_COLUMNS`
(probably empty) and wire `--pass v12` into the offline `--dialect mysql` plan exactly like `--pass groups`. Extend
`validate_manifest()` coverage. `tests/test_migrate_manifest.py` must assert the v12 manifest names exist in the models AND a
rehearsal test: build a SQLite DB with the PRE-v12 schema (drop the v12 columns/tables from a metadata copy, or create_all then
`ALTER TABLE ... DROP COLUMN` is not portable — instead create tables from a metadata filtered to exclude the v12 manifest),
run the planner/apply, assert zero drift. A data step (`backend/scripts/seed_v12.py`) upserts the catalogue and creates missing
`collection_settings`/`insurer_collection_profile` rows; it is idempotent and safe to run twice.
