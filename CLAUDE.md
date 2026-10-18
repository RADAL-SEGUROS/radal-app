# Radal — context for Claude sessions

**Radal is the platform** — the software provider for insurance distribution in Chile — and the
**broker (`corredora`) is the tenant**. Radal is never itself a broker. The product exists because
the placement cycle lives in email, WhatsApp and spreadsheets today; Radal makes the **expediente**
the folder the broker works in and normalises every insurer offer into one comparable shape. The
shape of the domain is `account_group → case_file(kind=account) → placement → quote_request →
proposal → policy`, with post-sale work (endoso, cobranza, siniestro, renovación) hanging off the
policy as versioned child case files. **This file is orientation only — at most three paragraphs.
The full technical specification is `docs/technical-reference.md`** (setup, data model, every
endpoint, the RBAC matrix, the AI registry, the frontend map, the changelog and the traps); put new
detail there, not here. Reference paths are written plain on purpose: **never `@`-prefix them**, or
they are pasted into every session and cost ~170k tokens before a word is exchanged.

**The broker journey & key terms (learn this vocabulary — the whole app speaks it).** The broker
works top-down and left-to-right:

```
grupo (account_group — shown to the user as **Asegurado** since v11) →
   grupo-cuenta (account: ramo × vigencia — shown as **Cuenta**) →
   Antecedentes → Bases Técnicas → Comparación → Propuesta → Pólizas
```

- **grupo / group** (`account_group`, UI label **Asegurado**) — the broker-private folder grouping the *empresas* (RUTs), the
  *vigencias* and the *ramos* of one commercial account. Has an icon; carries no money and no stage.
- **grupo-cuenta / account / expediente** (`case_file(kind=account)`) — **one ramo × one vigencia × N
  RUTs**, under a group. THE unit of work; it carries the journey stage. "Expediente" = the folder;
  every milestone below produces/updates an expediente.
- **ramo** — the class of insurance (Incendio/Property, RC/Ingeniería/Transporte, …). In Radal a ramo
  is an **advisory recommended-files template + AI context, NOT a field/section schema**; since v12 it
  comes from a **closed Radal catalogue** (18 ramos × 54 subtipos) — brokers can no longer create ramos.
- **vigencia** — the coverage period (e.g. `2026-2027`); a *label over per-account full dates*, not a
  shared range.
- **Antecedentes** — the **intake** stage: the ramo's recommended files + free uploads the insured/
  broker gathers, each AI-extracted per document. Free upload; **PDF/Word only** (Excel → print to PDF;
  vision fallback for scans is flagged-not-done). Excludes cotizaciones.
- **Bases Técnicas** — the completed antecedentes **consolidated into a structured, branded document**
  (PDF + UI view) — "the slip" of terms & conditions the broker sends to the market. It is itself an
  expediente; warn-not-block (always renders, incomplete = warning).
- **cotización / cotizaciones** — an **insurer's inbound offer/quote** against the bases técnicas. In
  code this is a `proposal` (read dynamically as a `budget_proposal`); it is uploaded into the
  Comparación. **⚠ Terminology trap:** in the codebase `proposal` = the insurer's INBOUND cotización.
- **Comparación** — the expediente that **dynamically compares the cotizaciones** side by side
  (standardized table + verbatim + an AI recommendation). This IS the cotizaciones step; there is no
  separate "Cotizaciones" journey tab.
- **Propuesta** — the broker's **OUTBOUND artifact sent to the insurer**, built from the comparison
  winner (`broker_proposal`, a validated core + tail). **Never confuse it with `proposal`/cotización
  (the insurer's inbound offer) — they are opposites.**
- **Póliza / policy** (`policy`) — the issued policy; **uploaded, validate-then-dynamic** (a fixed core
  check confirms it's a policy, then the full parse is kept in `policy.payload`).
- **corredora / broker** = the tenant; **asegurado / insured**, **aseguradora / insurer** = canonical,
  cross-broker.

Account **tabs** = the journey: Resumen · Antecedentes · Bases Técnicas · Comparación · Propuesta ·
Pólizas · Renovación · Notas · Bitácora. The **Journey rail lives only on Resumen**; the sidebar
**vigencia expands** into that journey.

**The non-negotiables — do not simplify these away.** (1) Every identifier is **English**; Spanish
belongs only in `locales/*/**.json` values and inside LLM prompts, because the source documents are
Spanish. (2) Every workspace table carries `broker_id` and every query filters it; `insured`,
`insurer` and `cmf_line` are canonical and reached only through the broker's own rows; another
tenant's row is a **404, never a 403**. (3) **No dead buttons** — a control is wired or visibly
disabled with a "pronto" chip and the server's reason; the nav is filtered by `GET /auth/permissions`,
never by hardcoded role checks. (4) i18n is English keys, Spanish values, `es` complete and `en`
mirroring; beware dynamic keys, which compile and then render raw on screen. (5) Money:
`net = taxable + exempt`, `vat = 0.19 × taxable` (**not** on net — earthquake cover is VAT-exempt),
`total = net + vat`; UF everywhere, and deductible bases differ by peril. (6) **AI is suggest →
human confirm → commit** — nothing auto-writes, and every attempt persists an `extraction` row.
(7) RUT is validated mod-11 and stored `BODY-DV`; insurers dedup by **RUT + CMF code, never by
name**; a broker is never blocked creating a client. (8) The `document` table is the **only** place
an S3 key lives. (9) `tests/conftest.py` blanks `AI_API_KEY` and points `AI_BASE_URL` at an
unroutable port **before** any `app.*` import — delete those two lines and the suite makes live
calls and hangs. (10) There is **no Alembic**: `create_all` never ALTERs, so any new column must go
through `backend/scripts/migrate_case_files.py` before the branch is pushed.

**Where the work stands (v12, 2026-10-17) — read before working.** v2→v11 shipped earlier (handoffs
`docs/handoff-2026-09-0{8,9}*.md`, `-09-10-v10.md`; tech-ref §17–§18 — the v11 rule still applies: a list needs
**an origin, a classifier and an owner** or it renders empty and reads as broken). **v12 is on `dev` and deployed**
(`5447c7b` build · `826f78c` deploy fixes · `ba47ee6` SES mail): spec = tech-ref **§19**, as-built + error log =
`docs/handoff-2026-10-17-v12.md`, build contract = `docs/v12/` (`00`–`10`, `api_lookup.py` over `openapi-v12.json`),
review fixes = `docs/handoff-2026-10-17-v12-review-fixes.md`. What v12 is: **Pólizas is the home** (`/`→`/policies`);
journeys are **partial** (PDF upload, manual entry, portal quotes or the full process; skipped stages are *omitida*;
a colocación is created on demand — write `case_file.entry_origin`, never `origin`; no walking back once *Vigente*);
a **closed ramo catalogue** (18 × 54, CMF-mapped); **cobranza in three levels per cuota** — registro · asistida ·
en línea — where `collection_installment.status` is a cache, money moves only through `services/collection/ledger.py`
(never above the balance), evidence from insurers is a **suggestion a human confirms**, and *En línea* needs an
active **and verified** Forpay account (**Forpay is paused** by decision — no sandbox until the team meets Forpay);
the **Mantenedor** (`/platform`, platform users only) configures insurers/provider accounts; one generic HMAC webhook;
**public share links** `/s/:token` (insured: cuotas, instructions, receipt upload; insurer: report status); the bell
(Por hacer / Para saber; admins get Mías/Todas; to-dos go to the account **owner**); per-template approved
reminders; status inquiries to insurers; an **RPA** framework (HDI showcase, `selectors/hdi.json`, off in dev);
**agent skills** (`load_skill`) + 26 tools, confirm-before-write. **WhatsApp = open door only; Google/Microsoft OAuth
deferred.** **Verified: 1 476 backend tests; `tsc -b`/`build`/`check:locales` clean (30 ns, 5 968 keys; zero
dynamic i18n keys — explicit maps in `frontend/src/i18n/labels.ts`); live smoke of dev at 1440/375 px.** The
**full QA walk has never been run** — the next one is `docs/v12/computer-use-dev-review-prompt.md` (computer use on
dev); the local equivalent is `./review-v12.sh` + `docs/v12/chrome-review-prompt.md`.

**The shell.** Rail: Pólizas · Cobranza (overdue count) · ASEGURADOS · Portafolio · Analítica · Agente · Cotizar en
portales · Compañías · Configuración (+ Mantenedor). `/data` = Portafolio, `/analytics` = Analítica; both share the
URL **scope** (grupo · grupo-cuenta · fechas) applied server-side. "Ver expediente completo" →
`/groups/:groupId/accounts/:caseId/expediente`. Motion lives in `Tabs`. **Signal** is the design direction.

**Deploy & infra (dev, verified with `aws --profile radal`).** CI (`deploy-backend-dev.yml` /
`deploy-frontend-dev.yml`, OIDC) fires on push to `dev` and syncs the Lambda env from S3 `deploy/backend.env`
(backups `backend.env.bak-2026-10-17-pre-v12` and `backend.env.v12-keys-backup-2026-10-17` — **never lose
`CREDENTIALS_KEY`**). **RDS stays private — never open it**; migrations/seeds run **inside the VPC** through the
token-gated jobs `db.migrate` / `db.seed_catalog` / `db.seed_demo` (`app/jobs/maintenance.py`, also needs
`ALLOW_DB_MAINTENANCE=true` in the env for the window only); pre-v12 snapshot `radal-dev-db-pre-v12-20261017`. The
backend Lambda egresses via **fck-nat** (EIP 3.230.124.80, private subnets 172.31.96–97.0/24, S3 gateway endpoint);
timeout 120 s, CloudFront origin 60 s; hashed `/assets/*` are cached a year at the edge. EventBridge (`radal-dev`)
runs `fx.daily`, `collection.clock`, `collection.notices`, `collection.inquiries`, `notifications.expire` — **Lambda Web
Adapter 0.7.0 only accepts HTTP-shaped events**, so schedules send an HTTP v2 envelope to `POST /events`. The
account's **Lambda concurrency limit is 10** (increase to 1000 requested). Everything applied is logged in
`deploy/v12/README.md` §0b; `docs/deployment.md` is partly stale. Demo data on dev: broker *Corredora Demo SpA*
(`admin@demo.cl` / `ejecutivo@demo.cl` / platform `admin@radal.cl`, all `radal1234`).

**Mail transition (2026-10-17).** `radalseguros.cl` has **four Google Workspace mailboxes (MX → Google)** and one
**Zoho Free** mailbox, `contacto@`. Radal's system mail **sends through Amazon SES v2** (`MAIL_TRANSPORT=ses`, domain
verified with Easy DKIM + MAIL FROM `mail.radalseguros.cl`; root MX/SPF/DMARC untouched); **Google Workspace routes
`contacto@` to Zoho** (Gmail *Default routing*, non-recognized addresses → host `mx.zoho.com`, TLS) — verified: an
SES test landed in Zoho. Broker custom mailboxes keep their own SMTP/IMAP. `python -m scripts.mail_preflight
[--send-test]` checks readiness. **`MAIL_ENABLED=false` on dev** until the next steps below. Zoho Free has **no
IMAP**, so the "Sent copy" (`MAIL_SENT_COPY=imap_append`) and IMAP reply reading cannot work on `contacto@`.

**Next session — open work, in order.** (1) Mail: BCC `contacto@` on every system mail (visual copy) instead of the
IMAP append; receive replies on an SES-inbound subdomain (e.g. `respuestas@correo.radalseguros.cl` → S3 → processed
by Radal, a copy forwarded to `contacto@`); request **SES production access** (sandbox: 200/day, verified recipients
only); then set `MAIL_ENABLED=true`. (2) Run the computer-use review on dev and fix what it finds. (3) Watch the
Lambda concurrency request. (4) Owed, unchanged: Forpay (after the team's meeting), HDI real credentials/selectors,
the **R33** per-broker insured (canonical insured still leaks), money-core extraction reliability, the vision
fallback, `fx.daily` (mindicador.cl unreliable), long comparisons vs the 60 s edge timeout. Locally: run the backend
with `MEDIA_BACKEND=local` and, for smokes, `AI_API_KEY=` (`backend/.env` holds a live key). Silent traps: the
`paramsSerializer` in `src/lib/api.ts`, `expose_headers` on CORS in `main.py`, server-driven group-by dimensions, the
`{plan_id:int}` converter on legacy `/collections` routes. `git remote` points at the old repo URL (GitHub redirect to
`RADAL-SEGUROS/radal-app`).
