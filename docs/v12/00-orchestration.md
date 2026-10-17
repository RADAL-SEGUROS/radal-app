# v12 — orchestration brief (READ FIRST, every agent)

Branch: `feat/v12-policies-cobranza` (off `dev` @ `51ada38`). **Never commit, never push, never touch AWS.**
Baseline before v12: backend `757 passed`; frontend `tsc -b` clean; `check:locales` OK (22 namespaces, 3 719 keys).

This folder is the contract for the v12 build. Files:

| File | What it fixes |
|---|---|
| `00-orchestration.md` | this: product decisions, scope, file ownership, conventions, how to test |
| `01-data-model.md` | every new/altered table, column, enum, constraint — THE schema contract |
| `02-backend-api.md` | every endpoint, its permission gate, request/response shape, error semantics |
| `03-collections-forpay.md` | cuota state, collection levels, Forpay facts (from their real docs), webhook security, flows |
| `04-mail.md` | Radal Zoho mailbox, broker custom SMTP/IMAP, status inquiries to insurers, inbound classification |
| `05-agent.md` | the agentic system: tool registry expansion + skills |
| `06-rpa.md` | portal search RPA framework (Playwright) + the HDI connector + error handling |
| `07-frontend.md` | nav, routes, pages, components, i18n namespaces, responsive rules |
| `08-infra.md` | NAT, webhook ingress, schedulers — docs + scripts only, nothing applied |
| `09-testing.md` | hermetic test env, fixtures, commands, what "done" means |
| `10-phase1-as-built.md` | **what phase 1 actually built** — exact service signatures, fakes, fixtures, deviations. Wins over 01–09 where they differ |
| `team-spec-clic-a-clic-v15.txt` | the team's UX spec as text (Spanish). Source of vocabulary and rules R1–R44 |
| `catalogo_ramos.csv` | the closed ramo/subtipo catalogue (team) |
| `../v12-cobranza-y-viaje-parcial.html` | the analysis that preceded this build (Forpay research, regulatory notes) |

## 1. What the product owner decided (verbatim intent, condensed)

1. **Policies are the main view.** Journeys are *partial*: a broker may start from a policy PDF, portal quotes, or the full
   market process. The group/account (`account_group` → `case_file(kind=account)`) stays, but works **more like a viewer than a
   creator**: it shows only the stages that actually happened and the important files.
2. **Closed ramo catalogue.** Ramos and subtipos are a global Radal catalogue (team CSV, 18 ramos, 53+1 subtipos). **Brokers can no
   longer create ramos** (reason: we must be able to trace which ramos are processed most).
3. **Collections in three levels per cuota** — `record` (registro), `assisted` (asistida), `native` (Forpay). A broker may start
   assisted collection on ANY policy at ANY time, whatever the insurer's Forpay status. When an insurer goes native, open cuotas
   upgrade automatically. The broker never needs to know whether an insurer is "on its way" to Forpay.
4. **Forpay is configured manually per insurer by RADAL** (after meetings with the insurer: "Forpay + RADAL is a collection").
   Build a **maintainer console for RADAL platform admins** AND let **broker admins** hold per-insurer overrides (contact email,
   instructions, opt-out) — both, behind one framework that can switch the full workflow on per insurer/channel.
5. **One generic webhook** used across all provider accounts, secured with **query params + HMAC** (account key + signature in the
   query string, plus Forpay's own payload HMAC).
6. **We cannot read transfer comprobantes**: payments land in the *insurer's* account/mailbox, which the broker doesn't own. So the
   assisted level does NOT match comprobantes. Instead: **intelligent mail that ASKS the insurer for payment status**
   (status inquiries), detects insurer replies/notices arriving in the broker's or Radal's mailbox, and turns them into
   *suggestions* a human confirms. The insurer can also answer through a **public share link** (no login).
7. **Roles:** broker = the only real app user. Insurers (partners/external) and insureds will NOT log in for weeks/months →
   **public share links** (WhatsApp-ready) are the priority; formal viewer roles stay as they are (already in RBAC), no new work.
8. **Mail:** RADAL official mail = **Zoho** account `contacto@radalseguros.cl` via **smtplib (SMTP) + imaplib (IMAP)**. Brokers may
   connect a **custom mail server** (GoDaddy & similar) via SMTP/IMAP. **Google/Microsoft OAuth are NOT built now** — show them as
   visibly disabled "pronto" options with a reason (no dead buttons).
9. **Agentic system matters a lot:** native function calling, **skills**, tools to create, edit, analyse, search — partial
   journeys, checking collections, checking files.
10. **RPA portal search:** Playwright, start with **HDI Seguros only** as a showcase. Scripts break when portals change → robust
    error taxonomy, **notify the RADAL team** and **explain to the user**, suggest other portals/manual path.
11. **WhatsApp:** open door only (enum value + disabled "pronto" chip; Meta approval pending). No sending.
12. **Deployment:** NAT or split servers — whatever gives protection + internet egress, cheapest first. Document; do not apply.
13. **Responsive** web app (brokers check progress from phones). No native mobile app.

## 2. Scope boundaries (do NOT build)

- No Alembic. Migrations go through `backend/scripts/migrate_case_files.py` (new `v12` pass) — non-negotiable #10.
- No AWS changes, no real network calls in tests, no real Forpay/Zoho/HDI calls anywhere in CI.
- No Google/Microsoft OAuth flows, no WhatsApp sending, no comprobante (bank transfer email) matching.
- No per-broker `insured` refactor (the R33 leak from plan-v12 §1.1) — out of scope, listed in the handoff as still owed.
- Don't delete legacy tables/routes (placement, quote_request, offering…). Don't rename existing routes.

## 3. Global conventions (beyond CLAUDE.md non-negotiables — they all still apply)

- English identifiers everywhere. Spanish only in `frontend/src/locales/es/*.json`, LLM prompts, and **data seed files**
  (`backend/app/db/seed_assets/*.json|csv`, e.g. default notice templates). Server-side "reason" strings shown to users
  (`reason`, `detail` meant for display) are Spanish, exactly like the existing stage-guard reasons.
- Every new workspace table carries `broker_id` (indexed) and every query filters it; another tenant's row → **404**.
  Platform-level tables (catalogue, provider accounts, provider events, payouts, job runs, currency rates,
  insurer collection profiles) carry no `broker_id` and are **never** listed through a broker endpoint without a broker filter.
- New columns on EXISTING tables must be nullable or have a `server_default` (SQLite/MySQL `ADD COLUMN` on populated tables).
- Enums: `sql_enum(...)` (VARCHAR, `native_enum=False`). Computed length = longest value + 12. **Adding a value longer than the
  current longest WIDENS the column** → it must be listed in the migration's widened-columns manifest (see `01-data-model.md §9`).
- Money: UF `Numeric(14,4)` (`app.models.types.UF`). CLP amounts are integers. Never float.
- Secrets (SMTP/IMAP passwords, Forpay keys, portal passwords) are stored **encrypted** (`app/core/crypto.py`, Fernet) in a
  `*_ciphertext` column, are **write-only** in the API (responses expose `has_secret: bool` only) and never logged.
- AI: suggest → human confirm → commit. Every AI attempt persists an `extraction` row. A signed provider webhook is NOT AI and
  commits directly (evidence `provider_confirmed`).
- Time: business dates are `America/Santiago`. Jobs take `today`/`now` as an argument (testable, deterministic).
- Follow existing patterns: routers use `get_current_broker_id`, `require_permission(module, action)`, `record_activity(...)`.
  Public endpoints follow `offerings.public_router` (token is the credential, narrow projection, no auth header).

## 4. File ownership (parallel agents MUST stay inside their lane)

Phase 1 (sequential, foundation) owns every SHARED file. After phase 1, shared files are FROZEN except where a lane below lists
them. If a lane needs a change in someone else's file, it must NOT edit it — it reports it in its final summary instead.

Shared files created/edited ONLY in phase 1: `backend/app/models/*` (all model files incl. new ones), `backend/app/models/__init__.py`,
`backend/app/models/enums.py`, `backend/app/db/base.py`, `backend/app/core/roles_config.py`, `backend/app/core/config.py`,
`backend/app/core/crypto.py`, `backend/app/main.py`, `backend/tests/conftest.py`, `backend/scripts/migrate_case_files.py`,
`backend/app/db/seed_assets/*`, `frontend/src/App.tsx`, `frontend/src/components/layout/*`, `frontend/src/lib/permissions.ts`,
`frontend/src/i18n/index.ts`, `frontend/src/api/keys.ts`.

| Lane | Owns (create/edit) |
|---|---|
| **B1 policies+catalogue+journey** | `routers/policies.py`, new `routers/catalog.py`, `routers/case_files.py`, `services/case_files.py`, `routers/antecedentes.py` (ramo-create lock only), `routers/account_groups.py` (subtype on account create only), `services/policy_registry.py` (new), `services/extraction_commit.py` (commit cuotas only), tests `tests/test_v12_policies*.py`, `tests/test_v12_catalog*.py`, `tests/test_v12_journey*.py` |
| **B2 collections core** | `services/collection/*` (status/resolver are written in phase 1 — B2 may extend, not change signatures), `routers/collections.py`, new `routers/notifications.py`, new `services/notifications.py`, `jobs/*` except job bodies owned by other lanes, new `routers/internal_jobs.py`, new `services/fx.py`, tests `tests/test_v12_collections*.py`, `tests/test_v12_jobs*.py` |
| **B3 payments/Forpay** | `services/payments/*`, new `routers/payments.py`, new `routers/webhooks.py`, new `routers/platform.py`, new `jobs/payments.py`, tests `tests/test_v12_payments*.py`, `tests/test_v12_webhooks*.py`, `tests/test_v12_platform*.py` |
| **B4 mail+inquiries** | `services/mail/*`, new `routers/mail_accounts.py`, new `routers/inquiries.py`, new `services/notices.py`, new `jobs/mail.py`, `jobs/notices.py`, tests `tests/test_v12_mail*.py`, `tests/test_v12_inquiries*.py`, `tests/test_v12_notices*.py` |
| **B5 share links / public** | `services/share_links.py` (phase-1 skeleton; B5 completes projections), new `routers/share.py` (auth'd create/revoke) and `routers/public_share.py` (public), tests `tests/test_v12_share*.py` |
| **B6 RPA** | `services/rpa/*`, new `routers/portal_searches.py`, new `jobs/rpa.py`, `tests/test_v12_rpa*.py`, `tests/fixtures/rpa/*` |
| **B7 agent** (runs AFTER B1–B6) | `services/agent_tools.py`, new `services/agent_skills.py`, agent parts of `services/ai.py` (system prompt + loop only), `routers/ai.py` (skills endpoints only), `tests/test_v12_agent*.py` |
| **F1 shell+policies** | `pages/policies/*`, `components/policies/*`, `components/layout/Topbar.tsx` (bell mount only — the bell component itself is F1's `components/notifications/*`), `api/policies.ts`, `api/notifications.ts`, `api/catalog.ts`, `locales/*/policies.json`, `locales/*/notifications.json` |
| **F2 cobranza** | `pages/collections/*`, `components/collections/*`, `api/collections.ts`, `api/payments.ts`, `api/inquiries.ts`, `locales/*/collections.json` |
| **F3 settings+platform** | `pages/settings/*` (new sections only: mail, collections, insurers), `pages/platform/*`, `api/mail.ts`, `api/platform.ts`, `locales/*/mail.json`, `locales/*/platform.json`, `locales/*/settings.json` (additive keys only) |
| **F4 share/public** | `pages/public/share.tsx`, `components/share/*`, `api/share.ts`, `locales/*/share.json` |
| **F5 catalogue+journey** | `pages/groups/*`, `components/groups/*`, `api/accountGroups.ts`, `api/caseFiles.ts`, `locales/*/accounts.json`, `locales/*/catalog.json` |
| **F6 agent+rpa** | `pages/agent/*`, `components/agent/*`, `api/ai.ts` (skills only), `pages/portal-search/*`, `api/rpa.ts`, `locales/*/rpa.json`, agent keys in its existing namespace |

`api/types.ts` is NOT shared for v12: each lane puts its types in its own `api/<lane>.ts` (export them from there).

## 5. Commands

```bash
# backend (from backend/)
MEDIA_BACKEND=local .venv/bin/python -m pytest tests -q -p no:cacheprovider          # full suite (~3 min)
MEDIA_BACKEND=local .venv/bin/python -m pytest tests/test_v12_payments.py -q          # one lane
.venv/bin/python -m scripts.migrate_case_files --dialect mysql --pass v12              # offline RDS plan
# frontend (from frontend/)
npx tsc -b --noEmit && node scripts/check-locales.mjs && npm run build
```
There is no `timeout` binary on this Mac (don't use it). Python is `backend/.venv/bin/python`.

## 6. Definition of done (per lane)

1. Implements exactly the contract in `01`/`02` (+ its topic file). If the contract is wrong/insufficient, implement the minimal
   sensible fix AND state it explicitly in the final report (the orchestrator reconciles).
2. New tests: happy path, tenant isolation (B's row → 404), permission denial, and the lane's edge cases. Hermetic (no network).
3. The lane's tests pass AND the full backend suite still passes (or frontend `tsc`/locales/build pass).
4. Final report: files touched, endpoints/components added, deviations from the contract, known gaps. No prose padding.
