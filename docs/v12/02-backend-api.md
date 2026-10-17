# v12 — backend API contract

> **Phase 2 is built.** This file is the contract the lanes were given; every place the build differs is marked
> **As built:** below, and `10-phase1-as-built.md` § "Phase 2 as built — API deltas" lists every delta in one place
> (shapes, extra endpoints, error codes). Frontend lanes code against the "As built" text.

All paths under `/api/v1`. `[B]` = broker-scoped (`get_current_broker_id`; another tenant's row → 404).
`[P]` = platform admin only (`user_type == platform`, gate `require_permission("Platform", ...)`). `[PUB]` = public, token is the
credential (pattern: `offerings.public_router`). `[INT]` = internal (header `X-Radal-Job-Token == settings.JOBS_TOKEN`;
empty token setting ⇒ 404). Pydantic schemas live in `app/schemas/<area>.py` (new files per lane).

## RBAC changes (phase 1, `core/roles_config.py` + `frontend/src/lib/permissions.ts`)
New MODULES: `Mail`, `Integrations`, `Platform`. Every role dict gets the three keys (the matrix is complete by construction —
`tests/test_rbac.py` checks it).
- `platform_admin`: all yes (already `_full_role`).
- `broker_admin`: Mail, Integrations all yes; **Platform all no** (`_full_role("broker", out_of_scope=("Reports","Platform"))`).
- `broker_executive`: `Collections` becomes View, Create, Edit, Comment, Upload, Submit (team: same cobranza actions as admin);
  `Mail` View + Create (own mailbox) ; `Integrations` View + Submit (run portal searches); Platform no.
- `broker_collections`: Collections all yes (unchanged), Mail View+Create, Integrations no.
- every other role: Mail/Integrations/Platform no (insured/insurer roles included).

## 1. Catalogue — lane B1 (`routers/catalog.py`, prefix `/catalog`)
| method | path | gate | returns |
|---|---|---|---|
| GET | `/catalog/lines` | any authenticated | `[{id, code, name, sort_order, subtypes:[{id, code, name, suggested_entry, suggested_route, cmf_codes}]}]` (global rows only, active, ordered) |
| GET | `/catalog/subtypes/{id}` | any authenticated | one subtype |
Locks (B1): `POST/PUT/DELETE /line-record-schemas` (antecedentes `schemas_router`) and any broker `insurance_line` create path return
**403** `{"detail": "Los ramos son un catálogo cerrado de Radal. Pide uno nuevo a soporte."}` for non-platform users. Reads stay.
Account creation (`POST /account-groups/{id}/accounts` or wherever dev creates `case_file(kind=account)`) accepts
`insurance_subtype_id` (and derives `insurance_line_id` from it); when given, it must exist and be active (422 otherwise).
**As built:** accounts are created through `POST /case-files` (`insurance_subtype_id`, optional `route`); every `kind=account`
created there gets `entry_origin=full_journey`. Platform users may still write ramos through `/line-record-schemas`, but only
GLOBAL rows (`broker_id NULL`, `is_template=true`).

## 2. Policies (main view) — lane B1 (`routers/policies.py`)
| method | path | gate | notes |
|---|---|---|---|
| GET | `/policies` | Policies.View [B] | EXTEND: filters `q` (number/insured/RUT), `insurer_id`, `line_code`, `subtype_id`, `status` (computed: `upcoming`/`active`/`expired`/`cancelled`), `collection` (`overdue`/`due_soon`/`no_plan`/`ok`), `origin`, `missing` (`source_document`/`plan`/`payer_contact`/`commission`), `owner=me`, paging. Row adds: `computed_status`, `subtype {code,name}`, `line {code,name}`, `insured_name`, `collection_summary {plan_id, next_due_date, next_due_amount_uf, overdue_count, overdue_uf, level}`, `missing: [keys]`, `has_source_document`, `origin`, `group_id`, `case_file_id`. |
| GET | `/policies/summary` | Policies.View [B] | cards: `active_count`, `active_premium_uf`, `expiring_90`, `no_plan`, `overdue_installments`, `overdue_uf`, `without_source_document`, `provisional` |
| GET | `/policies/{id}` | Policies.View [B] | EXTEND with `parties[]`, `missing[]`, `operability` (feature → enabled + Spanish reason), `collection_summary`, `journey {case_file_id, group_id, entered_stages[], skipped_stages[]}` |
| POST | `/policies` | Policies.Create [B] | **manual entry (vía Manual)**. Body: `client_id` (or `insured_rut`+`insured_name` resolved through the existing client/insured create path), `insurer_id`, `insurance_subtype_id`, `start_date`, `end_date`, optional `policy_number`, money fields, `policyholder {name,rut,email,phone}`, `payer {…}`, `payment_method`, `commission_pct`. Creates/attaches the colocación (rule below), `origin=manual`, `has_source_document=false`. 409 on duplicate `(broker, policy_number)`. |
| PATCH | `/policies/{id}` | Policies.Edit [B] | "Completar" — only fills NULL fields; changing a non-null field requires `correction_reason` and writes an activity with before/after |
| PUT | `/policies/{id}/parties` | Policies.Edit [B] | replace parties; validates exactly one `policyholder`, ≥1 `insured`; payer optional |
| POST | `/policies/upload` | Policies.Create [B] | EXISTING. Change: sets `origin=reader`, `has_source_document=true`, attaches/creates colocación by rule; **also commits the extracted payment schedule** into `collection_plan`+installments (`source=reader`) when the Σ check passes (otherwise leaves a warning, as today). |

**As built (§2):**
- `POST /policies` has two modes. The v12 manual entry (colocación on demand, parties, **409** on a duplicate number) runs when the
  body carries `insurance_subtype_id` or `insured_rut`; without them the legacy register path is kept (422 on duplicate, no
  colocación). A manual policy without a number stores the placeholder `SIN-N-{id}`; rows expose `has_policy_number:false` and
  `missing` contains `policy_number`; PATCH treats the placeholder as empty ("Completar", no reason).
- `GET /policies?status=` takes the computed `upcoming|active|expired|cancelled` plus stored-only `draft|renewed`; unknown values of
  `status`/`collection`/`missing` → 422 (Spanish). `missing=payer_contact` is evaluated in Python after the SQL filters.
- `GET /policies/summary` keeps the legacy keys (`total`, `by_status`, `total_premium_uf`, `expiring_within_60_days`) and adds the
  v12 cards; `active_count` is COMPUTED-active. `no_plan` = live (computed active or upcoming) policies with **no cuota on any plan**
  — the SAME definition as `GET /collections/summary` `plans_missing`, `?collection=no_plan` and the clock's `plan_missing` to-do.
- `PATCH /policies/{id}`: a correction without `correction_reason` → 422 `{code:"correction_reason_required", reason, fields[]}`.
  Correcting `taxable/exempt` premium re-derives net/vat/total. Changing `terminated_on` re-derives every cuota of the policy's plans
  (cuotas due after the date are voided; clearing it reopens them).
- `POST /policies/upload` with no folder needs `insurance_subtype_id` (422 otherwise); the insured comes from `client_id` or the
  extracted RUT (a client is created when the broker lacks it), else 422 `insured_unresolved`. Committed cuotas are always `pending`;
  an existing non-reader plan, or one with payments, is never overwritten (warning).
- `PolicyDetail` adds `parties[]`, `missing[]`, `operability[] {feature, enabled, reason, missing[]}`, `collection_summary
  {plan_id, state, next_due_date, next_due_amount_uf, overdue_count, overdue_uf, level, level_reason, installments_count,
  paid_count}`, `journey`. Shapes: `app/schemas/policy.py` (`PolicyRead`, `PolicyDetail`, `PolicySummary`, `PartiesReplace`).

**Colocación-on-demand rule (team R1/2b.6):** a policy without `case_file_id` gets one: find an open `case_file(kind=account)` of the
same client + subtype (or line) whose period contains `start_date`; else create one with `entry_origin` (`CaseEntryOrigin`; NOT `case_file.origin`, which stays the v3
new/renewal axis — create it with `origin=new`) = policy origin (`policy_upload`/`manual_entry`), `route` from the **door, not the subtipo** (team spec; product decision 2026-10-17): a policy upload (2b, no folder) → `route=digital`, a manual entry → `route=manual` (`policy_registry.route_for_entry`); only the full journey (`POST /case-files`) takes the subtype's `suggested_route`; stage = `active`, and **write skipped stage events**
(`is_skipped=true`) for every stage before `policy_issued` that never happened, then `policy_issued` → `active` events. The group
(account_group) is the client's existing group; if the client has none, create one named after the client (existing helpers).

## 3. Partial journey — lane B1 (`routers/case_files.py`, `services/case_files.py`)
| method | path | gate | notes |
|---|---|---|---|
| POST | `/case-files/{id}/skip-to` | CaseFiles.Edit [B] | body `{target_stage, note?}` — allowed targets for accounts: `quotes_received`, `insured_decision`, `policy_issued`, `active`; writes `is_skipped=true` events for every intermediate stage; 409 with Spanish reason if target is behind current stage |
| POST | `/case-files/{id}/activate-stage` | CaseFiles.Edit [B] | body `{stage}` — re-enter a SKIPPED stage; **allowed only while the case has no policy** (409 "Ya tiene póliza: los cambios se hacen por endoso o corrección") |
| GET | `/case-files/{id}` | existing | EXTEND: `stages: [{stage, state: done|current|skipped|pending, entered_at, skipped}]` (the rail draws only entered + skipped ticks) |

**As built (§3):** `CaseFileDetail` also carries `has_policy` (disable "Activar etapa") and `skip_targets[]` (the "Saltar a…" menu).
`skip-to` → `policy_issued`/`active` additionally requires a policy on the folder (422); `activate-stage` on a non-skipped stage is
422; with a policy it is 409 with the Spanish reason. Skipping also writes `mirror_validation` as skipped.

## 4. Collections core — lane B2 (`routers/collections.py`, `routers/notifications.py`, `routers/internal_jobs.py`)
| method | path | gate | notes |
|---|---|---|---|
| GET | `/collections/installments` | Collections.View [B] | THE Cobranza list. Filters: `bucket` (`upcoming` = due in ≤30d incl. today, `overdue` = vencida/riesgo/en acuerdo, `current` = plans with balance and nothing overdue, `settled`), `insurer_id`, `line_code`, `method`, `level`, `owner=me`, `q`. Rows: installment + policy/insured/insurer labels + `state` + `flags` + `paid_uf` + `balance_uf` + `days_late` + `level` + `level_reason` + `last_note` + `promise_until` |
| GET | `/collections/summary` | Collections.View [B] | cards: `due_7d {count, uf}`, `due_30d`, `overdue {count, uf}`, `collected_month_uf`, `unconfirmed_payments`, `pending_evidence`, `plans_missing` (policies w/o plan) |
| POST | `/collections/installments/{id}/payments` | Collections.Edit [B] | register payment `{amount, currency, paid_on, method?, receipt_document_id?, confirmed_by_insurer?, note?}` → `payment(source=manual, evidence=declared|insurer_confirmed)`; recompute state |
| POST | `/collections/payments/{id}/reverse` | Collections.Edit [B] | `{reason, note}` (never delete) |
| POST | `/collections/payments/{id}/confirm` | Collections.Edit [B] | insurer confirmed (evidence → insurer_confirmed) |
| POST | `/collections/installments/{id}/direct-debit-rejected` | Collections.Edit [B] | `{rejected_on}` |
| POST | `/collections/installments/{id}/promise` | Collections.Edit [B] | `{promise_until, note}` → note (gestión) + `promise_until` |
| GET | `/collections/installments/{id}/mode` | Collections.View [B] | resolver output `{level, reason, channels, missing[]}` |
| PATCH | `/collections/plans/{id}` | Collections.Edit [B] | EXTEND: `notices_enabled`, `level_preference`, `notices_paused_until`, `notices_pause_reason` (**as built:** new path; legacy `PATCH /collections/{id}` accepts the same fields) |
| POST | `/collections/plans/{id}/start` | Collections.Edit [B] | "Iniciar cobranza": `notices_enabled=true` (+ preference) — works for ANY insurer |
| POST | `/collections/plans` | Collections.Create [B] | EXISTING create; accepts `source`, `origin` (**as built:** served at BOTH `POST /collections/plans` and the legacy `POST /collections`, same handler) |
| GET/PUT | `/collections/settings` | GET Collections.View, PUT Settings.Manage [B] | `collection_settings` (lazy-created) |
| GET/PUT | `/collections/templates` · POST `/collections/templates/approve` | View / Settings.Manage [B] | effective templates (broker rows else seed defaults) |
| GET | `/notifications?tray=todo\|fyi&status=` | authenticated [B] | current user's notifications |
| GET | `/notifications/count` | authenticated [B] | `{todo_new, fyi_new}` |
| POST | `/notifications/{id}/seen` · `/notifications/{id}/close` | owner only [B] | |
| POST | `/internal/jobs/{name}` | [INT] | run a job now: body `{now?: iso}`; returns the `job_run` |
| GET | `/platform/jobs` | [P] | last runs per job (lane B3 hosts `/platform/*`; B2 provides the query helper) |

**As built (§4):**
- `GET /collections/summary` keeps the Portafolio generic summary (`entity, total, by_status, total_uf, grouped`, scope and
  `group_by` honoured) and ADDS the v12 cards (`due_7d, due_30d, overdue, termination_risk {count, uf}`, `collected_month_uf`,
  `unconfirmed_payments`, `pending_evidence`, `plans_missing`, `as_of`). The cards are broker-wide and ignore the scope.
- `GET /collections/installments` page: `{items, total, limit, offset, as_of, uf_rate, counts {upcoming, overdue, current, settled}}`;
  rows add `buckets`, `level_missing`, `channels`, `balance_clp`, `last_note {id, body, created_at, follow_up_on, author_id}`.
- Extra: `GET /collections/installments/{id}/payments` (payment ids for reverse/confirm).
- Legacy plan routes use `{plan_id:int}`: a non-numeric literal is 404. Full replace `PUT /collections/{plan}/installments` is 422
  once a cuota has a payment.
- Payments: 422 on a paid/voided cuota, future `paid_on`, non-positive amount, CLP with decimals, missing FX rate; a foreign receipt
  document is 404. `amount_uf` is normalised to the cuota currency (UF, or USD for dollar cuotas). Any payment that leaves the cuota
  PAID also tells the provider ("paid outside", 03 §6.5) so an open Forpay link cannot charge it again.
- `PUT /collections/settings`: `notices_enabled=true` requires approved templates; `default_sender=broker_account` requires a sender
  account (422 Spanish). Editing a template withdraws the approval. `POST .../start` with `level_preference=record` → 422.
- Cuota gestión notes (promise, rejected debit) are `note(entity_type=installment, phase='cobranza')`, readable/writable through
  `/notes` (`installment` and `payment` map to the Collections module).

## 5. Payments / Forpay — lane B3 (`routers/payments.py`, `routers/webhooks.py`, `routers/platform.py`)
| method | path | gate | notes |
|---|---|---|---|
| POST | `/payments/intents` | Collections.Edit [B] | `{installment_ids[], share_link_id?}` → creates intent + Forpay OnePay (only native-level cuotas; 409 with reason otherwise) → `{intent, checkout_url}` |
| GET | `/payments/intents/{id}` · POST `/payments/intents/{id}/cancel` | Collections.View/Edit [B] | |
| GET | `/payments/insurers` | Collections.View [B] | per insurer: `{insurer, level_available: native|assisted|record, channels[], instructions (merged profile+override), collections_email, broker_override}` — **no onboarding status / ETA exposed** |
| PUT | `/payments/insurers/{insurer_id}/override` | Settings.Manage [B] | broker override (`broker_insurer_collection`) |
| POST | `/webhooks/{provider}?account=<webhook_key>&sig=<hex>` | [PUB] | generic webhook (see `03 §5`). 404 unknown account · 401 bad sig · 200 `{status:"ok"}` after storing + processing (idempotent) |
| GET/POST/PUT | `/platform/insurer-profiles[/{insurer_id}]` | [P] | maintainer: onboarding status, instructions, collections_email, sender domains |
| GET/POST/PUT | `/platform/provider-accounts[/{id}]` · POST `/{id}/verify` · POST `/{id}/rotate-webhook-secret` | [P] | secrets write-only; responses show `has_secret`, `webhook_url` (full URL with `account` & `sig`) |
| GET/PUT | `/platform/insurer-channels` · PUT `/platform/insurer-channels/{insurer_id}/{channel}` | [P] | channel status, store code, provider account |
| GET | `/platform/events?status=` | [P] | provider_event inbox (reprocess: POST `/platform/events/{id}/reprocess`) |
| GET | `/platform/jobs` | [P] | job_run list |

Public checkout lives with share links (B5 projects, B3 executes): `POST /public/share/{token}/checkout {installment_ids[]}` →
`services.payments.checkout.create_checkout_for_share(db, link, installment_ids, *, now)` → `{checkout_url}`.

**As built (§5):**
- Checkout and the share page read ONE scope (`services.share_links.resolve_share_scope`): every cuota the page shows with
  `action:"pay"` is accepted by the checkout. Errors: 403 link may not pay · 404 cuota outside the link · 409 paid/voided/no balance,
  different payers ("Paga por separado…"), not native · 422 empty/too many · 502 provider failure (the failed intent is kept).
- Checkout charges each cuota's `balance_uf`; same set behind an open link → that link (`reused:true`); overlapping open links are
  superseded. Spot channels only (`webpay`, `oneclick`, `bank_transfer`).
- Webhook: 413 body > 256 KB; 400 invalid JSON; only a DISABLED account is 404; success `{status:"ok", processing_status}`;
  duplicate `{status:"duplicate"}` (a duplicate whose first processing failed is re-processed).
- Extra: `GET /payments/intents` (list), `GET /platform/events/{id}`, `PUT /platform/insurer-channels` (bulk); `GET /payments/insurers`
  takes `scope=mine|all` and `insurer_id`; `level_available` is `native|assisted` only. Provider-account responses add `secret_keys[]`
  (names only) and `webhook_urls {payment, activation}`. Activating an account requires username + api_key + password + hmac_key +
  public_key_pem (422 "Para activar la cuenta faltan: …"); production requires an https `base_url`.

## 6. Mail + inquiries — lane B4 (`routers/mail_accounts.py`, `routers/inquiries.py`)
| method | path | gate | notes |
|---|---|---|---|
| GET | `/mail/accounts` | Mail.View [B] | broker's accounts (+ a virtual read-only entry describing the Radal system sender: address, `kind: radal_system`) |
| POST | `/mail/accounts` | Mail.Create [B] | custom SMTP/IMAP: `{address, display_name, smtp_host, smtp_port, smtp_security, imap_host, imap_port, imap_security, username, password, send_enabled, receive_enabled, owner: me|shared}`; provider `google`/`microsoft` → 422 "pronto" reason |
| PATCH/DELETE | `/mail/accounts/{id}` | Mail.Create own / Settings.Manage shared [B] | |
| POST | `/mail/accounts/{id}/test` | same | SMTP login + IMAP login (network) → updates status; hermetic tests fake the transport |
| GET | `/mail/messages?direction=&classification=` | Collections.View [B] | log |
| POST | `/inquiries` | Collections.Edit [B] | `{policy_id, installment_ids[], to_address?, message?, send_via: radal|account, mail_account_id?}` → creates `status_inquiry` + insurer share link + sends mail |
| GET | `/inquiries?status=` · GET `/inquiries/{id}` | Collections.View [B] | |
| POST | `/inquiries/{id}/resend` · `/inquiries/{id}/cancel` | Collections.Edit [B] | |
| GET | `/collections/evidence?status=suggested\|needs_review` | Collections.View [B] | the "Respuestas por confirmar" inbox |
| POST | `/collections/evidence/{id}/confirm` | Collections.Edit [B] | `{installment_id?, amount?, paid_on?}` → creates payment (`source=insurer_reply|insurer_share_link`, evidence `reported`, or `insurer_confirmed` when it came from the insurer) |
| POST | `/collections/evidence/{id}/reject` | Collections.Edit [B] | |
| POST | `/collections/notices/preview` · `/collections/notices/send` | Collections.Edit [B] | preview/send a payer reminder for one installment now (`kind`, `channel=email`); whatsapp → 409 "pronto" |
| GET | `/collections/notices?installment_id=` | Collections.View [B] | sent avisos |

**As built (§6):**
- The evidence inbox and the notices are served at the contract paths above **and** at `/inquiries/evidence`,
  `/inquiries/evidence/{id}/confirm|reject`, `/inquiries/notices/preview|send`, `GET /inquiries/notices` — same handlers, gates,
  shapes and errors. Prefer the `/collections/*` paths.
- `GET /mail/accounts` → `{items, providers}`: `items[0]` is the virtual `radal_system` entry (`id: null`, read-only, status
  connected/paused per `MAIL_ENABLED`); `providers` lists `custom` (available) and `google`/`microsoft` (`available:false` + Spanish
  "pronto" reason). PATCH accepts `paused`; changing credentials/servers or un-pausing puts the box back to `draft` (test again).
  A colleague's personal mailbox → 404; a shared one → 403 for non-admins on PATCH/DELETE/test.
- `POST /inquiries`: `send_via` also accepts `auto` (default; follows `collection_settings.default_sender`). Inquiry status follows the
  mail: sent → `sent`, failed/bounced → `failed`, skipped (`MAIL_ENABLED` off) → stays `draft`; the insurer share link works always.
- Evidence confirm: `reported` evidence, except `insurer_statement` uploads or `confirmed_by_insurer:true` → `insurer_confirmed`.
  "Paid" without amount uses the open balance; CLP/USD need a `currency_rate` (422 otherwise). Confirming unpaid/cancelled records
  the decision without moving money. Payments go through the collections ledger (same FX, tolerance, alerts, paid-outside).
- Notices: one-off sends dedupe per (cuota, kind, channel, Santiago business day) → 409; `whatsapp` → 409 "pronto"; no payer contact or
  no template → 422; a refusal writes nothing. The reminder mints a fresh INSURED policy share link with `can_pay` = (level is
  `native`). Shapes: `app/schemas/inquiries.py`, `app/schemas/mail.py`.

## 7. Share links — lane B5 (`routers/share.py` auth'd, `routers/public_share.py` public)
| method | path | gate | notes |
|---|---|---|---|
| POST | `/share-links` | View on the target's module [B] | `{entity_type, entity_id, audience, permissions?, expires_in_days?, label?}` → `{id, token, url, whatsapp_url}` |
| GET | `/share-links?entity_type=&entity_id=` · POST `/share-links/{id}/revoke` | [B] | |
| GET | `/public/share/{token}` | [PUB] | audience projection (insured: broker brand, policy summary, cuotas with state + amounts UF/CLP, per-cuota level → pay button or insurer instructions, upload receipt; insurer: policy id/number/insured name, listed cuotas, report-status form). 404 revoked/expired/unknown. Increments `view_count`. NEVER returns other policies, documents keys, internal notes, commission. |
| POST | `/public/share/{token}/report` | [PUB] insurer audience + `can_report_status` | `{installment_id, status: paid|unpaid|partial|cancelled, amount?, paid_on?, comment?}` → `payment_evidence(source=insurer_share_link)` + notification `evidence_pending` |
| POST | `/public/share/{token}/receipt` | [PUB] insured audience + `can_upload_receipt` | multipart file + installment_id → `document(payment_receipt)` + `payment_evidence(source=payer_upload)` |
| POST | `/public/share/{token}/checkout` | [PUB] insured + `can_pay` | calls B3 service |

**As built (§7):** `POST /share-links` → `ShareLinkRead` (`id, token, url, whatsapp_url, whatsapp_text, permissions, expires_at…`);
`GET /share-links` requires `entity_type`+`entity_id` and returns `{items, total}`. Extra: `GET /public/share/{token}/logo` (bytes;
the projection's `broker.logo_url` points at it). Per-cuota `action` is `pay|instructions|report|none`; instructions live under
`policy.payment_instructions`; `report_statuses` lists the form options; amounts use `amount_uf/paid_uf/balance_uf` (USD when
`currency` says so) and `amount_clp/balance_clp` are null without a rate within 40 days. A `status_inquiry` link is insurer-only (422).
Public writes are throttled per token. Evidence from links carries `reasons=[{code:"share_link", share_link_id, detail}]` and, for
an inquiry link, `status_inquiry_id`. Shapes: `app/schemas/share.py`.

## 8. RPA — lane B6 (`routers/portal_searches.py`)
| method | path | gate | notes |
|---|---|---|---|
| GET | `/portal-searches/portals` | Integrations.View [B] | `[{key:'hdi', name, status: available|maintenance, supported_subtypes[], requires_credentials, has_credentials}]` |
| PUT | `/portal-searches/credentials/{portal}` | Settings.Manage [B] | write-only password |
| POST | `/portal-searches` | Integrations.Submit [B] | `{portal, insurance_subtype_id, case_file_id?, input{…}}` → run queued (executed by job `rpa.run` or inline when `RPA_INLINE=true`) |
| GET | `/portal-searches?case_file_id=` · GET `/portal-searches/{id}` | Integrations.View [B] | run + steps (sanitised) + quotes + `user_message_key` (= error_kind) + `alternatives` |
| POST | `/portal-searches/{id}/retry` · `/{id}/cancel` | Integrations.Submit [B] | |

**As built (§8):** extra `DELETE /portal-searches/credentials/{portal}` (Settings.Manage). Portals add `version, maintenance_reason,
maintenance_code, coming_soon, subtypes[{id,code,name}], credential{…}, input_fields[]`; "pronto" portals come as
`status:"maintenance", coming_soon:true`. 422 bodies are `detail={code, message, …}` (codes: `portal_maintenance`, `rpa_disabled`, …).
With `RPA_ENABLED` off every portal is in maintenance (`rpa_disabled`). Retry creates a NEW run cloned from the failed one.
`user_message_key` ∈ credentials_missing, login_failed, layout_changed, timeout, captcha, no_results, portal_unavailable, unexpected;
alternatives `kind` ∈ retry, upload_quote_pdf, other_portal. Shapes: `app/schemas/portal_searches.py`.

## 9. Agent — lane B7 (`routers/ai.py`, existing thread endpoints)
| method | path | gate | notes |
|---|---|---|---|
| GET | `/ai/skills` | authenticated [B] | skill catalogue `[{key, title, description, tools[]}]` (title/description Spanish — they are LLM/user copy defined in the skills registry) |
| POST | `/ai/threads/{id}/skills` | owner [B] | `{skills: [...]}` set active skills |
Existing message/confirm/discard endpoints unchanged in shape; the tool registry grows (see `05-agent.md`).
**As built (§9):** `GET /ai/skills` rows are `{key, title, description, tools[], triggers[]}`; `GET /ai/threads/{id}/skills` also
exists. Write tools reuse the router functions (same transaction, activity rows and errors). `run_portal_search` confirmations are
queued for the `rpa.run` job.

## 10. Jobs (lane B2 framework; bodies by owning lanes)
Registry `app/jobs/__init__.py`: `JOBS: dict[str, Callable[[Session, datetime], dict]]`. Runner `run_job(db, name, now)` (as built; plus `trigger_job(db, name, now) -> JobOutcome(run, executed)`) writes
`job_run` (unique `(job, scheduled_for)` where `scheduled_for` = `now` truncated to the job's period) and never raises.
CLI: `python -m app.jobs <name> [--now ISO]`. LWA event route: `POST /events` (outside `/api/v1`, body `{"job": name, "token": JOBS_TOKEN}` — EventBridge cannot set
headers, so the token rides in the body) for EventBridge Scheduler → Lambda invoke.
| job | owner | does |
|---|---|---|
| `fx.daily` | B2 | load UF/USD into `currency_rate` (source `settings.FX_SOURCE_URL`, mindicador.cl JSON) |
| `collection.clock` | B2 | refresh cuota state cache; escalate notifications by cadence; promises broken; plans missing |
| `collection.notices` | B4 | payer reminders for plans with `notices_enabled` (and broker master switch + approved templates) |
| `collection.inquiries` | B4 | auto status inquiries when `auto_status_inquiries` and cuota is `inquiry_after_days` overdue |
| `mail.poll` | B4 | IMAP fetch for Radal system box + each receive-enabled broker account |
| `payments.sync` | B3 | Forpay `GET /receivables` reconciliation for pending intents/charges |
| `payments.payouts` | B3 | payouts (`hasPayout=true`) → `provider_payout`, evidence → settled |
| `rpa.run` | B6 | execute queued portal searches |
| `notifications.expire` | B2 | close `fyi` notifications seen/older than 7 days |
