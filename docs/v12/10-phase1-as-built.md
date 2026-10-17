# v12 — phase 1 AS-BUILT (binding for lanes; generated from the phase-1 reports)

Backend suite after phase 1: **949 passed**. Frontend: tsc/locales (30 ns, 3 766 keys)/build green.

Lanes MUST use these exact signatures. Where this file and 01–09 disagree, THIS file wins (it describes the code).

## Phase 1a — data model, migration, seeds, settings, crypto, RBAC

Phase 1a backend foundation for v12 is done, and the full suite passes: 827 tests, the 757 baseline plus 70 new ones.

**Schema.** The data model in docs/v12/01-data-model.md is now in the models: 27 new tables (50 → 77 tables) in 12 new model files, plus the additive columns on 7 existing tables. Every v12 enum lives in `app/models/enums.py` (listed in `V12_ENUMS`). `EntityType` and `DocumentCategory` got their new values without getting any wider (longest values stay 18 and 25), so nothing needs widening. Both FK cycles use `use_alter` with the names from the contract (`fk_collection_plan_mandate`, `fk_payment_evidence`), and the reverse side is mapped with `post_update` so both rows can be written in one flush. Everything is registered in `app/models/__init__.py` and `app/db/base.py`. Every table compiles on both sqlite and mysql with warnings treated as errors.

**Migration.** `scripts/migrate_case_files.py` has the `V12_NEW_TABLES`, `V12_ADDED_COLUMNS` and `V12_WIDENED_COLUMNS` (empty) lists, and `--pass v12`. It also now handles `use_alter` FKs on new tables: MySQL's CREATE TABLE leaves them out, so the plan adds them after all tables exist. SQLite already includes them inline. The offline MySQL v12 plan is 185 statements: 27 CREATE TABLE, 28 ADD COLUMN, 124 CREATE INDEX, 6 ADD FOREIGN KEY, no MODIFY. NOT NULL columns get their DEFAULT ('UF', 'auto', false, '0').

I applied the migration to the local `backend/radal.db` (backup at `/tmp/radal.db.pre-v12.bak`); it now reports zero drift. I also ran `seed_v12 --apply` on it, and a second run reports nothing to do. Nothing touched AWS.

`tests/test_migrate_manifest.py` now checks that the v12 manifest names exist, and runs a rehearsal: it builds a SQLite file without the v12 tables and columns, puts a row in `insurance_line`, migrates it with the planner, and confirms zero drift. Then it runs the seeder twice and confirms the second run changes nothing.

**Seed.** The catalogue CSV is the team's file with the `PRO-05 Robo` row added (18 ramos, 54 subtipos). The Spanish notice templates cover the kinds and levels in 04-mail §5; the inquiry subject carries `[RADAL-{{inquiry_token}}]`. `seed_catalog(db)` upserts ramos by code (reusing a global row with the same name if one exists) and subtipos with the CMF mapping. `seed_collection_defaults(db)` creates missing insurer profiles (`not_contacted`) and missing broker settings. `seed_dev` calls both and is safe to re-run. The CLI is `python -m scripts.seed_v12 [--apply]`, dry run by default.

**Settings.** `config.py` has all the requested settings plus `JOBS_EVENTS_ENABLED` (named in 08-infra). Booleans and ints are validated, and a blank boolean keeps its default instead of failing boot. Mail security must be ssl, starttls or none. The `public_app_url` property falls back to the first CORS origin; `public_api_url` falls back to that.

**Crypto.** `app/core/crypto.py` provides `encrypt_secret` / `decrypt_secret` (Fernet). It uses `CREDENTIALS_KEY`, or a key derived from SECRET_KEY when that is blank. A dict comes back as a dict, a wrong key raises `SecretDecryptionError` without echoing the value, and nothing is logged.

**RBAC.** `roles_config.py` adds the Mail, Integrations and Platform modules for every role, as specified.

**conftest.** The hermetic env block from 09-testing is in place before any app import, with a test-only Fernet key, and there is a new opt-in `catalog` fixture.

**API surface.** No routers were edited. The only behaviour change is RBAC: the executive (and commercial) roles can now edit and create in Collections, which `GET /auth/permissions` reflects. This is a contract change, so I updated one existing test that asserted the executive got 403 on a payment PATCH; it now uses the technician, who is still read-only on Collections.

### Deviations
- case_file.origin collision: the contract asks for `case_file.origin` as enum CaseOrigin{full_journey, quote_comparison, policy_upload, manual_entry, portal_rpa, migration}. But `case_file.origin` already exists as the v3 origin axis: CaseOrigin{new, renewal, period_change}, NOT NULL, used by renewals, the navigator and the schemas, and locked by test_case_origin_has_exactly_the_three_origins. The team's `origen` is therefore implemented as a NEW column `case_file.entry_origin` with a NEW enum `CaseEntryOrigin` carrying the contract's six values. LANE B1 MUST write `case_file.entry_origin` (CaseEntryOrigin.POLICY_UPLOAD / MANUAL_ENTRY …) wherever 02-backend-api §2 says 'create one with origin = policy origin'. `policy.origin` (PolicyOrigin) is as specified.
- MailMessageStatus gained `skipped`. 04-mail §1/§2 records sends as 'skipped' when MAIL_ENABLED=false, but the 01 enum lacked that value.
- All v12 enums are defined in app/models/enums.py rather than in their model modules, so the parallel lanes find them in one place. They are re-exported from app.models.
- The seeder logic lives in app/db/seed_v12.py and scripts/seed_v12.py is a thin CLI that re-exports it. Reason: the Dockerfile copies only app/, and app/db/seed_dev.py has to call it. `from scripts.seed_v12 import seed_catalog` still works. seed_v12 also creates missing collection_settings rows, as 01 §10 says (the task text only named insurer profiles).
- migrate_case_files.py: validate_manifest no longer rejects use_alter FKs on new tables. It now requires them to be named, and both plan builders emit them as deferred ADD CONSTRAINT on MySQL. This was needed for payment.evidence_id. The old test test_no_new_table_declares_a_use_alter_foreign_key was replaced by tests that check the deferred re-add. I also added a validate check that widened columns have a VARCHAR length.
- migrate_case_files.py, latent bug fixed: AddConstraint(...) by default edits the shared model metadata so that FK disappears from every later CREATE TABLE in the same process. All call sites now use `isolate_from_table=False`, with a fallback for older SQLAlchemy 2.0.x. pending_passes() now credits a column to the pass that explicitly lists it, so a DB missing only v12 no longer reports 'case_files' as pending.
- RBAC: broker_commercial is still derived from broker_executive (and test_commercial_is_the_executive_matrix enforces that), so it also gets Collections Create/Edit/Submit, Mail View+Create and Integrations View+Submit. A literal reading of 'every other role: no' would deny these; quoting through portals is commercial work.
- Added setting JOBS_EVENTS_ENABLED (bool, default False). It is named in 08-infra §4 but missing from the task list; added because config.py is frozen after phase 1.
- tests/test_collection_sums.py::test_collections_tenancy_and_permissions asserted that the executive gets 403 on PATCH installment. The contract now grants the executive Collections.Edit, so the read-only check uses broker_technician instead.
- Small choices not fixed by the contract: status_inquiry.insurer_id is ON DELETE RESTRICT (like policy.insurer_id); payment_intent_item.external_installment_id is NOT NULL; collection_settings.cadence_days is NOT NULL JSON with a callable default; ProviderEvent.received_at defaults to utcnow; a few extra composite indexes were added (broker_id+status on payment_intent, payment_mandate, status_inquiry, payment_evidence, portal_search_run and collection_notice; notification user+origin).

### Open issues handed to lanes
- LANE B1 and the orchestrator: use `case_file.entry_origin` (CaseEntryOrigin), not `case_file.origin`, for the team's origen. 01-data-model.md, 02-backend-api.md and docs/technical-reference.md §5 should be updated to match.
- Not done here (outside this task's items 1-7, though 00/03/04/09 list them as phase-1 work): services/collection/status.py and resolver.py, services/payments/providers/base.py (Protocol + FakeProvider), services/mail/transport.py (Protocol + FakeTransport), the services/share_links.py skeleton, tests/fakes_v12.py, and the fake_transport / fake_provider fixtures. A follow-up phase-1 step (1b?) has to write these before the lanes start.
- Frontend `src/lib/permissions.ts` must mirror the new Mail, Integrations and Platform modules and the executive's Collections grants (another agent owns it). Until then the /auth/permissions matrix includes three modules the UI doesn't know about.
- RDS: the reviewable plan is `--dialect mysql --pass v12` (dev RDS carries the v9 schema = every earlier pass), then `python -m scripts.seed_v12 --apply`. Not applied anywhere except the local radal.db.
- docs/technical-reference.md (§5 data model, §4 runbook, §7 RBAC) still has to be updated for v12; that file is outside my ownership.

## Phase 1b — core services, seams, fakes, jobs, router stubs

Phase 1b is done: the backend skeleton and the core shared services are in, and the full suite passes with 949 tests (the 827 from phase 1a plus 122 new). `from app.main import app` imports cleanly. I started uvicorn with `MEDIA_BACKEND=local` and curled `POST /api/v1/internal/jobs/noop` (401 without a token, 200 executed=true, then 200 executed=false for the same slot, 404 for an unknown job) and `POST /events` (200 with a good token, 401 with a bad one, 404 while `JOBS_EVENTS_ENABLED` is unset). The trial `noop` rows were then deleted from the local radal.db. No columns were added, so there was no migration to run. Nothing was committed and AWS was not touched.

PUBLIC SIGNATURES for the lanes:

**app.services.collection.status**
- `InstallmentState(state, flags: frozenset[str], paid_uf, balance_uf, days_late: int, evidence: str|None, paid_on: date|None=None, legacy: bool=False)` (frozen)
- `compute_installment_state(inst, payments, *, today: date, cadence_days: list[int], tolerance_uf: Decimal=Decimal('0.009')) -> InstallmentState` — pure.
- `refresh_installment_cache(db, inst, *, today) -> InstallmentState` — writes status, paid_on and days_late; the caller commits.
- `legacy_status_for(state) -> InstallmentStatus`
- `broker_collection_params(db, broker_id) -> (cadence, tolerance)` — read-only.
- `best_evidence(payments)`, `termination_date(inst)`, `EVIDENCE_ORDER`.

**app.services.collection.resolver**
- `CollectionMode(level, reason, channels: tuple, missing: tuple, insurer_id: int|None=None)`
- `resolve_collection_mode(db, installment, *, broker_id) -> CollectionMode` — raises `LookupError` for another broker's installment.
- `lead_insurer_id(policy) -> int|None`, `active_native_channels(db, insurer_id) -> list[InsurerPaymentChannel]`, `payer_contact_email(policy) -> str|None`, `insurer_contact_email(db, *, broker_id, insurer_id) -> str|None`
- `REASON_RECORD`, `REASON_NATIVE`, `REASON_ASSISTED`.

**app.services.payments.providers**
- `get_provider(name) -> PaymentProvider`, `register_provider(name, factory)`, `ProviderUnavailable`.
- `forpay` loads lazily from `app.services.payments.providers.forpay:ForpayProvider`, so B3 only has to create that class.
- In tests, `_OVERRIDE` makes every name return the fake.

**app.services.payments.providers.base**
- `PaymentProvider` Protocol, exactly as in 03 §4.
- Dataclasses: `PayerData(rut, name, email, last_name='', phone=None, user_id=None)`, `CheckoutItem(installment_id, external_installment_id, amount_uf, due_date, store_commerce_code=None, description='')`, `CheckoutSession(provider_mandate_id, checkout_url, external_id, raw)`, `MandateSession(provider_mandate_id, magic_link_url, external_id, raw)`, `ReceivableIn(...)`, `ProviderEventIn(event_type, dedup_key, provider_installment_id, external_installment_id, state, ...)`.
- Errors: `PaymentProviderError`.
- Constants: `STATE_*`, `DEFAULT_SIGNATURE_FIELDS`, `PAID_SIGNATURE_FIELDS`.
- Helpers: `account_secret(acct) -> dict`, `signature_fields_for(payload, *, event_type='payment', override=None)`, `compute_payload_signature(hmac_key, payload, fields) -> str` (Base64 HMAC-SHA256), `verify_payload_signature(hmac_key, payload, *, event_type='payment', override=None) -> bool` (constant-time, fails closed), `sign_payload(hmac_key, payload, *, event_type='payment', fields=None)`, `parse_installment_webhook(payload, *, event_type='payment') -> ProviderEventIn` (dedup_key = internalId:state.id), `infer_event_type(payload)`.

**app.services.payments.providers.fake**
- `FakePaymentProvider(name='forpay')` with `.calls`, `.calls_to(method)`, `.add_receivable(**kw)`, `.installment_states`, `.fail_next`.
- `FakePaymentProvider.build_webhook_payload(hmac_key, *, internal_id, external_installment_id, state=2, amount=..., fields=None, tamper=False, ...)`.
- Checkout URLs are deterministic: `https://pay.fake-provider.test/checkout/{external_id}`.

**app.services.mail.transport**
- `MailTransport` Protocol: `send(cfg, msg) -> str`, `fetch_since(cfg, cursor, *, limit=200) -> (list[FetchedMail], dict)`, `check(smtp, imap) -> CheckResult`.
- Dataclasses: `SmtpConfig(host, port, security, username, password, timeout=20, from_address='')` and `ImapConfig(..., mailbox='INBOX')` (passwords hidden from repr), `FetchedMail(uid, raw: bytes, uidvalidity=None)` with `.message()`/`.subject`/`.from_address`/`.message_id`/`.in_reply_to`, `CheckResult(smtp_ok, imap_ok, smtp_error, imap_error, checked_at)` with `.ok`.
- `MailTransportError`; `SmtpImapTransport` is a stub that raises `NotImplementedError`.
- `get_transport()`, `radal_smtp_config()`, `radal_imap_config()`.

**app.services.mail.fake**
- `FakeTransport` with `.sent`, `.sent_messages`, `.queue_inbound(msg, *, mailbox=(host, user)|None, uid=None)`, `.check_result`, `.fail_send`, `.uidvalidity`.
- `build_message(*, from_address, to, subject, body, in_reply_to=None, message_id=None)`.

**app.services.mail.ops**
- `notify_ops(db, subject, body) -> MailMessage` — outbound, broker_id NULL, subject prefixed "[Radal ops] ".
- Status is `skipped` when `MAIL_ENABLED` is off or `RADAL_OPS_EMAIL` is blank, `sent` or `failed` otherwise. It never raises; it flushes and the caller commits.

**app.services.share_links**
- `create_share_link(db, *, broker_id, user, entity_type, entity_id, audience, permissions=None, expires_in_days=None, label=None, now=None) -> ShareLink` — raises `ShareTargetNotFound` (a LookupError) for a row outside the broker, and `ValueError` for a bad audience or expiry. Expiry defaults to 60 days, maximum 365.
- `resolve_share_token(db, token, *, now) -> ShareLink|None`
- `record_share_view(link, *, now)`, `revoke_share_link(link, *, now=None)`, `is_link_live(link, *, now)`, `link_permissions(link)`, `normalize_permissions(audience, permissions)`, `resolve_share_entity(db, *, broker_id, entity_type, entity_id)`
- `share_url(token)` returns `{public_app_url}/s/{token}`; `whatsapp_url(text)`.

**app.jobs**
- `JOBS: dict[name, fn(db, now) -> dict]`, `JOB_PERIODS`, `register_job(name, period='day'|'hour'|'minute')`.
- `run_job(db, name, now=None) -> JobRun`, `trigger_job(db, name, now=None) -> JobOutcome(run, executed)`, `UnknownJob`, `slot_for(now, period)`, `load_jobs()` (auto-imports every module in app/jobs), `as_aware_utc`.
- CLI: `python -m app.jobs <name> [--now ISO] | --list`. Exit 0 ok, 1 failed, 2 unknown.

**Routers**
- `internal_jobs.router` (`/internal/jobs`, `POST /{name}`) and `internal_jobs.events_router` (`POST /events`) are fully implemented. The schemas are in `app/schemas/jobs.py`.
- Empty `APIRouter`s, each docstring naming its owning lane: catalog (`/catalog`), notifications, payments, webhooks, platform, mail_accounts (`/mail`), inquiries, share (`/share-links`), public_share (`/public/share`), portal_searches.
- All are registered in `main.py`; `events_router` is mounted without the API prefix.

**Tests**
- `tests/fakes_v12.py`: `make_policy_with_plan(db, tenant, insurer, *, n_installments=3, start=date(2026,1,5), policy_number=None, payment_mode, level_preference, statuses=None, **policy_overrides) -> (policy, plan)`, `make_payment(db, inst, *, amount_uf=None, paid_on, evidence, source, status, receipt_document_id=None)`, `make_provider_account(db, *, owner, insurer_id, status=active, hmac_key, webhook_secret, webhook_key=None, secret_extra=None)` (secrets really encrypted), `webhook_sig(secret, key, provider='forpay')`, `make_native_channel`, `make_collection_settings`, `make_insurer_profile`, `make_broker_override`, `make_party`, `split_amount`, plus re-exports of `seed_catalog`, `FakeTransport`, `FakePaymentProvider` and `build_message`.
- conftest only gained two fixtures: `fake_transport` and `fake_provider`.

### Deviations
- status.py, legacy rows: a cuota with NO payment rows at all whose cached status is paid, paid_late or credited is read as `paid` (legacy=True, evidence declared, flags unconfirmed and no_receipt). A legacy `cancelled` one is read as `voided`. `refresh_installment_cache` leaves these rows untouched. Without this, the daily clock would erase every paid cuota written before v12 or by the legacy PATCH. As soon as one payment row exists, the payments alone decide.
- status.py: termination_risk also fires when `insurer_notice_sent_on` is set on an overdue cuota (an Art. 528 notice means termination is pending), in addition to the contract's `today > due + max(cadence_days)`.
- status.py: the 'plan terminated' rule uses `policy.terminated_on` first, else `plan.terminated_at.date()` when `plan.status == terminated`. A cuota due ON the termination date is not voided (only due AFTER it).
- status.py: `confirmed_by_insurer=True` on a payment counts as at least `insurer_confirmed` evidence. An empty `cadence_days` falls back to the default [-15,-5,0,3,10].
- InstallmentState gained two extra fields with defaults: `paid_on` (the date the running total covered the cuota, which the cache needs) and `legacy`. CollectionMode gained `insurer_id` (the lead insurer, handy for grouping checkouts). Both are additive.
- resolver: when there is no collection_settings row, `native_collection_enabled` is treated as True (the column default) and `templates_unapproved` is reported. `payer_contact` uses payer, then policyholder party emails (respecting notify_collections). Only when there is NO payer or policyholder party does it fall back to client.contact_email, then insured.email. `mail_sender` is reported when default_sender=broker_account and the chosen account is missing, belongs to another broker, is not connected, or has send disabled. `plan` is never emitted, because a cuota always has a plan. Reasons fall back to 'la compañía' when the insurer is missing.
- Signatures follow the task text where it differs from 02 §10: `run_job(db, name, now)`, not `run_job(name, now)`, and `notify_ops(db, subject, body)`, not `notify_ops(subject, body)`. Added `trigger_job(...) -> JobOutcome(run, executed)` so the HTTP routes can report whether the body ran.
- Jobs: a FAILED slot can be re-triggered and reuses its row. A running, succeeded or skipped slot is returned untouched. A body returning `{'skipped': True}` is recorded as SKIPPED. An unknown name raises `UnknownJob` and records nothing (routes return 404, CLI exits 2). Day slots are America/Santiago midnight (UTC fallback if tzdata is missing); hour and minute slots are UTC. A lane that needs e.g. 30-minute idempotency (payments.sync) should register with period='minute'.
- /events returns 404 unless JOBS_TOKEN is set AND `JOBS_EVENTS_ENABLED` is true (that setting's documented meaning from phase 1a). A wrong or missing token is 401 on both doors. The 404 gate is checked BEFORE the body is validated, so a closed door is 404 whatever is sent.
- share_links: every link expires (None means 60 days, valid range 1..365), so a router that omits expiry never mints a permanent public link. Permissions are normalised per audience: an insurer link can never pay or upload, and an insured link can never report status. `create_share_link` also accepts `now=` for tests.
- Fakes live in app/services/*/fake.py and are re-exported from tests/fakes_v12.py; 09-testing places them only in tests/. The fixtures patch a module-level `_OVERRIDE` read by `get_transport()` / `get_provider()`, so they work however the caller imported the getter.
- Webhook signature concatenation joins the field values with no separator (Forpay's exact rule is undocumented). It is configurable per account through the `signature_fields` list or map in the secret JSON. When only the body is known, `infer_event_type(payload)` picks payment or activation.
- My test files are named test_v12_core_*.py (plus the requested test_v12_jobs_framework.py) to stay clear of the lanes' globs (test_v12_share*, test_v12_payments*, …).

### Open issues handed to lanes
- `SmtpImapTransport` is a stub that raises NotImplementedError. Lane B4 must implement it, and route `notify_ops` through its sender if it wants templates and sender selection. Until then, MAIL_ENABLED=true makes `notify_ops` record `failed`.
- `app/services/payments/providers/forpay.py::ForpayProvider` does not exist yet. `get_provider('forpay')` raises ProviderUnavailable until lane B3 writes it. B3 should reuse `verify_payload_signature` and `parse_installment_webhook` from base.py so the fake's signed payloads stay valid for the real provider.
- The empty routers expose no endpoints yet; each lane fills its own file. main.py already includes them all, so no lane needs to touch main.py.
- The legacy `PATCH /collections/{plan}/installments/{n}` still writes status and paid_on directly. Lane B2 must turn it into a `payment(source=manual, evidence=declared)` plus `refresh_installment_cache` (03 §1). Until then the legacy-row rule in status.py keeps those rows consistent.
- Deployment: EventBridge → /events requires both JOBS_TOKEN and JOBS_EVENTS_ENABLED=true in deploy/backend.env (08-infra §4 lists both). If the Lambda image lacks tzdata, day slots fall back to UTC midnight. Python 3.11-slim normally ships /usr/share/zoneinfo, but this has not been verified in the image.
- docs/technical-reference.md (§6 endpoints, the jobs runbook) should mention /api/v1/internal/jobs/{name}, /events, `python -m app.jobs` and the new service seams. That file is outside this task's ownership.

## Phase 1c — frontend scaffold

The phase-1c frontend scaffold is done, and `tsc -b`, the locale check and `npm run build` all pass. I did not open the app in a browser; the 375 px check was done by reading the Tailwind classes only.

1. **Permissions.** `Mail`, `Integrations` and `Platform` are added to the module list in `lib/permissions.ts`.
2. **Translations.** The eight new namespaces (policies, collections, notifications, mail, share, platform, rpa, catalog) are registered, each with real Spanish and English content: titles, descriptions and the platform section labels. New `common.json` keys in both languages: `nav.portalSearch`, `nav.platform`, `nav.notifications`, `nav.overdueInstallments`, `state.noAccess`, `state.noAccessHint`. I also changed `nav.collections` from "Cobranzas" to "Cobranza" (only `App.tsx` used it). Totals: 30 namespaces, 3 766 keys per language.
3. **Routes.** All new routes from 07 §1 are added in `App.tsx` through the existing glob mechanism. `/policies/new` and the two static collections paths come before the `:id` routes. `/s/:token` sits outside the shell. `/platform/*` is wrapped in a new `RequirePermission` guard (Platform.View), which shows a "Sin acceso" card to anyone else. `/` and the fallback route go to a new `HomeRedirect`: `/policies`, or `/platform` for a platform user.
4. **Placeholder pages.** The 12 new pages are placeholders, mostly a page header plus the existing "en construcción" card. The Cobranza page links to its two sub-pages, and the Mantenedor home links to its four sections, so no sub-page is orphaned. The public share page has its own mobile-first frame with a logo that follows the dark theme.
5. **Rail.** `SidebarGlobal` follows the 07 §1 order and each row checks `can(perms, module, 'View')`; the groups block is unchanged. Cobranza shows a red count chip from `GET /collections/summary` (`overdue.count`). It never retries and simply doesn't render on 404/422 or a missing field. Platform users see only Mantenedor: the broker rows and the navigator and summary fetches stay off for them. The collapsed icon rail in `Sidebar.tsx` got the same rows.
6. **Bell and mobile.** The bell placeholder fetches nothing and is mounted in the Topbar. Below `sm`, the Topbar folds the theme and language buttons into the user menu, so the search box keeps about 209 px at 375 px wide. Page side padding is 16 px on phones.
7. **Query keys.** I added the v12 keys to `api/keys.ts` now, since that file freezes after phase 1. They cover collections (installment rows, mode, evidence, notices, settings, templates), catalog, notifications (+count), payments, inquiries, mail, share links, public share, platform, portal searches and agent skills. The rail chip uses `qk.collections.summary()`.

No dynamic `t()` keys were added.

### Deviations
- `/` goes to `HomeRedirect`, not a plain redirect to `/policies`: it sends platform users (`user_type === 'platform'` with Platform.View) to `/platform`, because they have no broker and broker endpoints return 403 for them. Everyone else goes to `/policies`. The fallback route redirects to `/`.
- Platform users get only the Mantenedor row in the rail; the broker rows and their navigator/summary fetches are suppressed. This keys off the server's `user_type` from `/auth/permissions`, not a role, because `platform_admin` is a full role and `can()` alone would show rows whose endpoints 403.
- Configuración keeps its existing `isBrokerAdmin` gate (Settings/Users Manage, from the server) instead of `can(Settings, 'View')`. I kept the current behaviour rather than widen access to the settings page.
- The bell placeholder is a link to `/notifications` (38 px icon button), not an inert button, so it isn't a dead control. It still fetches nothing. F1 should keep the `NotificationBell` export name.
- Added `components/layout/RequirePermission.tsx` (a route-level Platform.View guard plus `HomeRedirect`). It isn't named in 07 §1 but sits inside the layout/* files phase 1 owns.
- Added v12 query-key scopes to `api/keys.ts` now (a phase-1 shared file) so lanes never need to edit it. The plain-list keys under `collections`: `qk.collections.installmentRows(params)` (the cuota-level list), `qk.collections.installmentMode(id)`, `qk.collections.evidence(params)`, `qk.collections.notices(params)`, `qk.collections.settings`, `qk.collections.templates`. The rail chip uses `qk.collections.summary()`, which already existed through `scope()`.
- Renamed `common:nav.collections` from 'Cobranzas' to 'Cobranza' (team vocabulary); only `App.tsx` used it.
- The Mantenedor home lists its four console sections as link cards. The 'channels matrix' F3 is meant to build has no route in 07 §1, so it has no card.

### Open issues handed to lanes
- Until B2 adds `/collections/summary` ahead of the `/collections/{id}` route, a GET for 'summary' is likely to hit the int-id route and return 422. The rail swallows that (no retry, chip hidden), but B2 must register the static path first, or the chip never appears. B2 should also return `overdue: {count, uf}` as 02 §4 specifies: the rail reads only `overdue.count`.
- When lanes F1/F2/F3/F4/F6 replace the placeholder pages and fill the namespace bundles, they should keep the keys the scaffold already uses or update both sides: policies `new.title`/`new.description`; collections `title`, `description`, `evidence.*`, `inquiries.*`; notifications `title`, `description`, `bell.label`; platform `title`, `description`, `nav.*` and the four section blocks; rpa `title`, `description`; share `title`, `description`.
- F2 should publish its summary hook with `qk.collections.summary()` (no params) so it shares a cache entry with the rail chip; if it passes params it's a separate entry, but both still refresh via `qk.collections.all`.
- Not checked in a real browser at 375 px or 1440 px. That's left for the orchestrator's live pass in 09-testing.
- The collapsed 56 px icon rail still has no Portafolio row; it didn't have one before this change either.

## Gate

The phase-1 gate passes and every check is green. I did not need to change any code. The only mismatches I found were in the contract documents, and I updated those to match what was built.

**Step 1: checks**
- Backend full suite: 949 passed, 0 failed.
- Frontend: `tsc -b --noEmit` exits 0, the locale check passes (30 namespaces, 3 766 keys per locale), and `npm run build` succeeds (only the existing chunk-size warning).

**Step 2: audit against the contract.** I dumped the database model for all 27 new tables and the 28 added columns and compared them with 01-data-model.md, table by table and column by column. Everything checked matches:
- **Types:** names, types and lengths are right. UF is Numeric(14,4), amounts are Numeric(16,4), rates Numeric(12,4), scores Numeric(5,2), and PCT is used where specified. Every new DateTime column stores the timezone.
- **Nullability and FK delete rules** match. The `broker_id` rule holds: workspace tables have it NOT NULL with CASCADE and an index, platform tables have none, `mail_account`/`mail_message` have it nullable, and `provider_event.broker_id` is a plain integer.
- **server_default:** every NOT NULL column added to an existing table has one (`insurance_line.sort_order`, `is_skipped`, `has_source_document`, `is_provisional`, both `currency` columns, `level_preference`, `notices_enabled`).
- **Unique constraints:** all 19 from the contract are present.
- **Named deferred FKs:** both exist (`fk_collection_plan_mandate`, `fk_payment_evidence`).
- **Enums:** I compared the values of all ~45 enums against the contract.
- **Existing enums:** the new `EntityType` and `DocumentCategory` values don't widen any column (still 30 and 37 characters).
- **Catalogue seed:** the CSV is the team file plus the `PRO-05` row, and the CMF codes for all 54 subtipos match 01 §1 exactly.

The other contract areas also match:
- **RBAC (02):** I printed the effective grants for every role and they match. `broker_commercial` inheriting the executive's grants is the deviation already reported in phase 1a.
- **conftest:** the test environment block from 09-testing is set before any `app` import.
- **Service signatures (03):** `InstallmentState`, `compute_installment_state`, `refresh_installment_cache` and the legacy status mapping (§1), `CollectionMode` and `resolve_collection_mode` (§2), and the `PaymentProvider` Protocol with its seven methods, `CheckoutItem` and `get_provider` (§4).
- **Internal jobs routes:** a blank `JOBS_TOKEN` returns 404 and a wrong token returns 401.

**Mismatches found:** all were contract-vs-build. Each is an already-reported phase-1 deviation that the docs never picked up, so later lanes reading the docs as binding would have gone wrong:
- (a) The docs said `case_file.origin`; the build uses `case_file.entry_origin` (`CaseEntryOrigin`) because `origin` is the existing new/renewal/period_change column.
- (b) The `MailMessageStatus` enum in the docs was missing `skipped`.
- (c) The docs gave `run_job(name, now)`; the build is `run_job(db, name, now)`, plus `trigger_job`.
- (d) The docs gave `notify_ops(subject, body)`; the build is `notify_ops(db, subject, body)`.

**Step 3: fixes.** I edited the contract docs only:
- 01 §1: `entry_origin` row, with a note that `origin` stays as it is.
- 01 §5: `skipped` added to `MailMessageStatus`.
- 02 §2: the colocación rule now says to write `entry_origin` and create the case file with `origin=new`.
- 02 §10: the `run_job` and `trigger_job` signatures.
- 06: the `notify_ops` signature.

**Step 4: migration.** `--dialect mysql --pass v12` exits 0: 27 CREATE TABLE, 28 ADD COLUMN, no column changes (MODIFY). The NOT NULL columns get `DEFAULT false` / `'UF'` / `'auto'` / `'0'`, and both named FKs are added afterwards with ADD CONSTRAINT. `tests/test_migrate_manifest.py` passes 28 of 28, including the rehearsal that migrates a pre-v12 SQLite database to zero drift.

**Step 5:** I changed no code, so the green runs above are the final ones.

### Deviations
- Edited the contract docs (docs/v12/01, 02, 06) to match the phase-1 build, so the parallel lanes read the right names: case_file.entry_origin/CaseEntryOrigin instead of case_file.origin, MailMessageStatus plus skipped, run_job(db, name, now) plus trigger_job, notify_ops(db, subject, body). These docs are not in the phase-1 file list, but they are the binding contract the lanes read, and the 1a/1b reports asked for this update.
- No new deviations in the code. All 1a/1b/1c deviations already reported were checked and accepted: entry_origin, the skipped status, broker_commercial inheriting the executive grants, JOBS_EVENTS_ENABLED, the legacy-row rule in status.py, the extra fields on InstallmentState and CollectionMode, and HomeRedirect.

### Open issues handed to lanes
- docs/technical-reference.md (§5 data model, §6 endpoints, §7 RBAC, jobs runbook) is still not updated for v12. That file is outside phase-1 ownership and is owed by the orchestrator or the doc step.
- uq_collection_notice_once includes cadence_level, which can be NULL. On both SQLite and MySQL, NULLs never collide, so one-off notices with cadence_level NULL are not deduplicated by the database. B4 must dedupe those in code (or always set a level).
- Carried from 1b, for the lanes: SmtpImapTransport is a stub (B4); ForpayProvider does not exist yet (B3); the legacy PATCH for cuotas still writes status directly (B2 must turn it into a payment); B2 must register /collections/summary before /collections/{id}, or the rail chip shows nothing.
- RDS migration not applied (by design): run `--dialect mysql --pass v12`, take a snapshot, run `--apply`, then `python -m scripts.seed_v12 --apply` at deploy time.
- No browser walk was done at 375 px or 1440 px; that is left to the orchestrator's final live pass.

## Phase 2 shared services (as built)

Built before the lanes, used by B2/B3/B4/B5/B6. Every function **flushes only; the caller commits**. No new columns (no migration).

**app.services.notifications** (the only writer of `notification`)
- `notify(db, *, broker_id, user_ids: list[int] | None, kind, tray, origin_type: str, origin_id: int, params: dict|None=None, link_path: str|None=None, level: str|None=None, now: datetime|None=None, renotify: bool=False) -> list[Notification]`
  - `kind`/`tray` accept the enum or its string; `ValueError` on unknown values or over-long `origin_type` (32) / `level` (16) / `link_path` (255).
  - `user_ids=None` → every active `broker_admin` of the broker. Explicit ids from another broker or inactive users are **dropped silently**; duplicates collapse. `[]` → no rows.
  - One NON-closed row per `(user, origin_type, origin_id)` (scoped by broker). On a repeat call:
    - **material change** (`kind`, `tray` or `level` differs) or `renotify=True` → escalate in place: all fields rewritten, `status=new`, `updated_at=now`;
    - **same alert again** → only `params`/`link_path` refreshed, quietly (status and `updated_at` kept — a daily job does not re-light the bell).
  - **A dismissal sticks:** if there is no open row and the user's latest row for that origin is closed with the same `kind`+`level`, nothing is created (not returned). A new level/kind, or `renotify=True`, opens a fresh row. Callers that need "the same event happened again" (e.g. a second insurer reply) pass `renotify=True`.
  - `params` is made JSON-safe (Decimal→str, date→ISO, Enum→value).
  - Returns the open rows created/updated, in recipient order.
- `close_for_origin(db, *, broker_id, origin_type, origin_id, kinds: list|None=None, now=None) -> int` — closes open rows of every user of the broker; `kinds` narrows.
- `recipients_for_policy(db, policy) -> list[int]` — `[owner]` of `policy.case_file_id` (one `parent_case_file_id` hop for a post-sale child without owner) when that user is active and in the policy's broker; else `broker_admin_ids`.
- `expire_fyi(db, *, now) -> int` — closes `fyi` rows that are `seen`, or whose `updated_at` (last raise) is ≥ 7 days old (`FYI_TTL`). All brokers (it is the `notifications.expire` job body; B2 registers the job).
- Extras: `broker_admin_ids(db, broker_id) -> list[int]`, `mark_seen(db, row)` (new→seen, keeps `updated_at`), `close_notification(db, row, *, now=None)` (idempotent).
- `updated_at` is the bell's "raised at" sort key; status-only writes go through a Core UPDATE that preserves it.

**routers/notifications.py** (`/api/v1/notifications`; schemas in `app/schemas/notifications.py`: `NotificationRead`, `NotificationPage{items,total,limit,offset}`, `NotificationCount`)
- `GET /notifications?tray=&status=&limit=50(≤200)&offset=0` — current user only, `updated_at desc, id desc`. Without `status` → open rows (new+seen); `status=closed` → history. Rows never carry `broker_id`/`user_id`.
- `GET /notifications/count` → `{todo_new, fyi_new}` (status `new`).
- `POST /notifications/{id}/seen`, `POST /notifications/{id}/close` → the row; idempotent; a closed row stays closed.
- Scope: `broker_id` AND `user_id`; another user's or tenant's id → 404 `"Notificación no encontrada"`. No module gate (the bell is personal). Platform users → 403 (`get_current_broker_id`). Anonymous → 401.

**app.services.fx** (`currency_rate`, platform table; never opens a socket)
- `get_uf_rate(db, on: date|datetime) -> Decimal|None`, `get_usd_rate(db, on) -> Decimal|None` — exact date, else the latest PRIOR date carrying that rate within `MAX_LOOKBACK_DAYS = 40`; each rate falls back independently (a UF-only day does not hide yesterday's USD). Older → `None` (refuse to convert).
- `uf_to_clp(amount_uf, rate) -> int` (ROUND_HALF_UP, away from zero on .5); `clp_to_uf(amount_clp, rate) -> Decimal` (4 dp, half-up). Accept Decimal/int/str/float (floats via `str`); `ValueError` on a missing/zero/negative/NaN rate.
- `upsert_rates(db, rows: Iterable[(date, uf|None, usd|None, source)]) -> int` — rows created or changed. Same-date rows merge; a `None` never erases a stored value; non-positive values ignored; values quantised to 4 dp; identical re-run → 0. `source` truncated to 32.
- `parse_mindicador(payload) -> list[RateRow]` sorted by date, source `"mindicador"`: latest payload (`{uf:{fecha,valor}, dolar:{...}}`, other indicators ignored) and series payloads (`{codigo:"uf"|"dolar", serie:[{fecha,valor}]}`). The date is the `YYYY-MM-DD` prefix of `fecha` taken verbatim (mindicador encodes Santiago midnight in UTC, `T03:00Z`/`T04:00Z`; converting would shift a `T00:00Z` value a day back). Bad entries skipped; non-dict → `[]`.
- Not built here (B2): the `fx.daily` job (HTTP fetch of `settings.FX_SOURCE_URL` → `parse_mindicador` → `upsert_rates`) and the `notifications.expire` job registration.

Tests: `tests/test_v12_shared_notifications.py` (18), `tests/test_v12_shared_fx.py` (24).

## Phase 2 as built — API deltas

Lanes B1–B7 and the integration pass are done. This section is **binding for the frontend lanes**: where it and
`02-backend-api.md` differ, this wins (02 now also carries inline **As built:** notes). Every shape named here lives in
`backend/app/schemas/<area>.py`. No column or enum was added in phase 2, so no migration beyond the phase-1 `v12` pass.

### Integration pass (cross-lane wiring)
- **Checkout ⇄ share page share one scope.** `services.payments.checkout` scopes a link with
  `services.share_links.resolve_share_scope` (it used to have its own `_belongs_to_link`), so a `case_file` link (including
  `case.policy_id` and plans filed in a `collection` child case) pays exactly what the page shows with `action:"pay"`.
  `share_links.payable_through_link` is now always `True` (kept as a seam).
- **Public checkout is wired directly** to `create_checkout_for_share` (no lazy import, no 503 "not shipped" path). A service
  answer without a URL is a 502 with a Spanish reason. Refusals keep B3's `status_code` + Spanish `detail`; 5xx commits (the failed
  intent is kept), everything else rolls back.
- **Paid-outside.** `services.collection.ledger.after_installment_change` calls B3's `mark_paid_outside` whenever the cuota ends
  up PAID. Every non-provider payment path goes through it: manual `POST /collections/installments/{id}/payments`, evidence confirm
  (B4, via `ledger.register_payment`), the legacy `PATCH /collections/{plan}/installments/{n}`, and agent `register_payment`.
  Signed webhooks book their own payment and call it with `exclude_intent_id`.
- **One "sin plan" definition.** `services.collection.board.policies_without_plan_stmt` = live policies (computed `active` or
  `upcoming`, via `policy_registry.computed_status_clause`) with **no cuota on any plan** (an empty plan counts as no plan). It
  drives `GET /collections/summary.plans_missing` and the clock's `plan_missing` to-do, and matches `GET /policies/summary.no_plan`
  and `GET /policies?collection=no_plan`. Payer contact for `missing` (B1) and the level/`level_missing` (B2) both come from
  `services.collection.resolver` (`payer_contact_email`, `resolve_collection_mode`).
- **One payment-instructions merge.** `share_links.merged_payment_instructions` and `notices.merged_instructions` delegate to
  `services.payments.insurers.merged_instructions` (profile ⊕ broker override ⊕ legacy `insurer.payment_url`); `INSTRUCTION_KEYS`
  is defined once there.
- **Termination re-derives cuotas.** `PATCH /policies/{id}` changing `terminated_on`, and the extracted collection-status commit
  (`extraction_commit._commit_collection_status`) changing the plan's termination/rehabilitation, call
  `ledger.reconcile_plan_installments` (void cuotas due after the end; reopen them on rehabilitation/clear).
- **Notes on cuotas/payments.** `/notes` accepts `entity_type=installment|payment` (module Collections, tenant-checked → 404).
- **Contract paths.** Added `/collections/evidence`, `/collections/evidence/{id}/confirm|reject`, `/collections/notices`,
  `/collections/notices/preview|send` (alias router `inquiries.collections_router`, mounted in `main.py`; the `/inquiries/*`
  doors stay) and `POST /collections/plans` (same handler as `POST /collections`).
- `requirements.txt`: `httpx` moved to a runtime block (Forpay client, `fx.daily`).
- `tests/test_v12_core_seams.py`: the three phase-1 stub assertions now assert the real Forpay provider and the real SMTP/IMAP
  transport failing closed on the hermetic `127.0.0.1:9`. New `tests/test_v12_integration.py` pins every seam above.

### Final endpoint list (v12 + extended)
| area | endpoints (all `/api/v1`) | gate |
|---|---|---|
| catalogue | `GET /catalog/lines` · `GET /catalog/subtypes/{id}` | authenticated |
| ramo lock | `POST/PUT/DELETE /line-record-schemas[/{id}]` → 403 for broker users; platform writes global rows only | Settings.Manage |
| policies | `GET /policies` · `GET /policies/summary` · `POST /policies` · `POST /policies/upload` · `GET/PATCH/DELETE /policies/{id}` · `PUT /policies/{id}/parties` | Policies.* |
| journey | `POST /case-files` (subtype, route) · `GET /case-files/{id}` (+`stages[]`, `has_policy`, `skip_targets[]`) · `POST /case-files/{id}/skip-to` · `POST /case-files/{id}/activate-stage` | CaseFiles.Create/View/Edit |
| cobranza | `GET /collections/summary` · `GET /collections/installments` · `GET /collections/installments/{id}/mode` · `GET/POST /collections/installments/{id}/payments` · `POST /collections/payments/{id}/reverse|confirm` · `POST /collections/installments/{id}/direct-debit-rejected|promise` · `GET/PUT /collections/settings` · `GET/PUT /collections/templates` · `POST /collections/templates/approve` · `POST /collections/plans` · `PATCH /collections/plans/{id}` · `POST /collections/plans/{id}/start` · legacy `GET/POST /collections`, `/collections/{id:int}[/installments[/{n}]|/status]` | Collections.View/Edit/Create; settings/templates writes Settings.Manage |
| evidence + notices | `GET /collections/evidence` · `POST /collections/evidence/{id}/confirm|reject` · `POST /collections/notices/preview|send` · `GET /collections/notices` (also under `/inquiries/…`) | Collections.View/Edit |
| inquiries | `POST/GET /inquiries` · `GET /inquiries/{id}` · `POST /inquiries/{id}/resend|cancel` | Collections.View/Edit |
| mail | `GET/POST /mail/accounts` · `PATCH/DELETE /mail/accounts/{id}` · `POST /mail/accounts/{id}/test` · `GET /mail/messages` | Mail.View/Create (shared boxes: Settings.Manage); messages Collections.View |
| notifications | `GET /notifications` · `GET /notifications/count` · `POST /notifications/{id}/seen|close` | own rows only |
| payments | `POST/GET /payments/intents` · `GET /payments/intents/{id}` · `POST /payments/intents/{id}/cancel` · `GET /payments/insurers` · `PUT /payments/insurers/{id}/override` | Collections.Edit/View; override Settings.Manage |
| webhook | `POST /webhooks/{provider}?account=&sig=` | public (sig + payload HMAC) |
| platform | `/platform/insurer-profiles[/{insurer_id}]` · `/platform/provider-accounts[/{id}]` (+`/verify`, `/rotate-webhook-secret`) · `GET/PUT /platform/insurer-channels` · `PUT /platform/insurer-channels/{insurer_id}/{channel}` · `GET /platform/events[/{id}]` · `POST /platform/events/{id}/reprocess` · `GET /platform/jobs` | Platform.* AND platform user (broker users 403) |
| share | `POST /share-links` · `GET /share-links?entity_type=&entity_id=` → `{items,total}` · `POST /share-links/{id}/revoke` | View on the target module |
| public | `GET /public/share/{token}` · `GET /public/share/{token}/logo` · `POST /public/share/{token}/report|receipt|checkout` | token |
| RPA | `GET /portal-searches/portals` · `PUT/DELETE /portal-searches/credentials/{portal}` · `POST/GET /portal-searches` · `GET /portal-searches/{id}` · `POST /portal-searches/{id}/retry|cancel` | Integrations.View/Submit; credentials Settings.Manage |
| agent | `GET /ai/skills` · `GET/POST /ai/threads/{id}/skills` | authenticated (owner) |
| jobs | `POST /internal/jobs/{name}` (header token) · `POST /events` (outside `/api/v1`, body token) | `JOBS_TOKEN` |

### Per-lane deltas (from the lane reports; see 02 "As built" notes for the error semantics)
- **B1 policies/catalogue/journey.** Two-mode `POST /policies` (v12 manual when `insurance_subtype_id` or `insured_rut` is sent:
  409 duplicate; legacy otherwise: 422 duplicate). `SIN-N-{id}` placeholder + `has_policy_number`. Computed `?status=` strings.
  Summary keeps the legacy keys; `active_count` is computed. `skip-to` policy targets need a policy (422); `activate-stage` on a
  non-skipped stage 422, with a policy 409. Uploading/registering into an OPEN in-progress folder advances it to `active` (skipped
  events, placement walked by BFS). Rule-5 fallback: same group + ramo + exact vigencia folder is reused and the client added as
  `account_client(insured)`. On-demand folders take `route` from the door (decision 2026-10-17: upload → `digital`, manual → `manual`;
  `policy_registry.route_for_entry`); only `POST /case-files` uses the subtype's `suggested_route`.
  `extraction_commit` stamps `has_source_document`, `origin=reader`, subtype and currency (USD when the payload says dollars).
  Renew/reperiod copy `insurance_subtype_id`, `route`, `entry_origin`.
- **B2 collections.** See 02 §4 "As built". Clock notification origins: `('installment', id)`, `('policy', id)` for `plan_missing`,
  `('insurer', id)` for `native_collection_available`; levels `-15/-5/0/+3/+10`, `term`, `p<date>`, `d<date>`, `a<date>`.
  `fx.daily` records failed (never raises) and is re-runnable; blank `FX_SOURCE_URL` → skipped. Job-run helpers
  `app.jobs.list_job_runs(...)`, `app.jobs.latest_runs(db)`.
- **B3 payments.** See 02 §5 "As built". Webhook: forged-payload events are stored with `dedup_key='invalid:<sha256(body)>'`,
  `ignored`; DRAFT accounts accepted (sandbox). State −99 raises `direct_debit_rejected` only for recurring charges; state 12 raises
  `sensitive_action` with `params.event='chargeback'`. Notifications use `origin_type='installment'`, `link_path=/policies/{id}`.
  Broker-generated checkout return URL is `{public_app_url}/`; share checkouts return to `/s/{token}?pago=retorno`.
- **B4 mail/inquiries.** See 02 §6 "As built". Jobs: `mail.poll` period `minute`; `collection.notices`/`collection.inquiries` `day`.
  Notices job sends only the latest reached cadence step (strict template lookup: with the seed that is −5, 0, +3, +10, not −15);
  promised/paid/voided cuotas skipped; a FAILED step is retried in place. Bounces store no `.eml` and no extraction. Added
  `AITask.CLASSIFY_MAIL`.
- **B5 share.** See 02 §7 "As built". Receipt documents: `entity_type=installment`, `phase='public_share'`, `case_file_id` = the
  plan's case. `score` on evidence stays NULL (B4 defines it).
- **B6 RPA.** See 02 §8 "As built". Ops mail + ERROR log only for connector-side failures (layout_changed, timeout, captcha,
  portal_unavailable, unexpected); broker-fixable ones notify the user only. Maintenance streak per connector key + version across
  brokers; clears on a version bump or after 24 h. Orphaned `running` runs older than 20 min fail with `timeout`. VEH-01 `use` values
  are `particular|commercial`. Failure screenshots are documents without `case_file_id`, reachable via `screenshot_url`.
- **B7 agent.** 26 new tools; write tools reuse the router functions. Read tools fold 6–12 KB of results each.

### Still open after integration
- ~~2b.5 FYI not sent~~ — APPLIED 2026-10-17: `NotificationKind.POLICY_LINKED_INSURED` (21 chars, no widening), origin
  `('policy_link', policy_id)`, tray `fyi`, raised by `policy_registry.notify_policy_linked_insured`.
- `GET /policies?missing=payer_contact` filters in Python after SQL narrowing; needs a denormalised flag if books grow large.
- Forpay: after a chargeback (state 12) a later genuine state 2 on the same charge books nothing (one payment per charge).
  Receivables without `installment.id` match by `internalId` only.
- Mail: SSRF guard is resolve-then-connect (DNS-rebinding window, mail ports only); sender verification is From-header only (no
  DKIM/SPF) — such evidence is only ever `suggested`/`reported`.
- RPA: no per-(broker, portal) serialisation under `RPA_INLINE`; no ops override to clear maintenance (`config.py` was frozen).
- Agent: no tool for `PUT /policies/{id}/parties`; `run_portal_search` relies on the `rpa.run` scheduler; external result URLs
  (share/checkout) must open outside the SPA (F6).
- `docs/technical-reference.md` still needs its v12 sections (§5 data model, §6 endpoints, §7 RBAC, jobs runbook); the table
  above is the source for §6.
- ~~Product decision pending: vía of on-demand colocaciones~~ — APPLIED 2026-10-17: the team spec wins (upload → `digital`,
  Manual → `manual`, full journey → subtype's `suggested_route`).
