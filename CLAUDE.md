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

**Where the work stands (v12, 2026-10-17).** v2→v11 are committed and **pushed to `dev`** (CI →
`https://dev.radalseguros.cl`): v9 the broker journey (`docs/handoff-2026-09-09-v9.md`), v10 the
analysis destinations + expediente completo (`docs/handoff-2026-09-10-v10.md`, tech-ref §17), v11
Portafolio/Asegurados (§18 — its rule still applies to every request: a list needs **an origin, a
classifier and an owner**, or it renders permanently empty and reads as a broken app). **v12 is built on
`feat/v12-policies-cobranza` but NOT committed, deployed or migrated** — read
`docs/handoff-2026-10-17-v12.md` (as-built, candid error log, what is owed) and
`docs/technical-reference.md` **§19** (the spec); the build contract is `docs/v12/`. v12 makes
**Pólizas the home** (`/` → `/policies`; journeys are **partial** — a PDF, a manual entry, portal quotes
or the full process — and the account becomes a viewer of the stages that happened; write
`case_file.entry_origin`, never `origin`); a **closed ramo catalogue**; **cobranza in three levels per
cuota** (registro · asistida · nativa) where `collection_installment.status` is a cache and money moves
only through the ledger; a **Forpay framework** configured per insurer by Radal in the **Mantenedor**
(`/platform`, platform users only) behind one generic HMAC webhook; **Zoho mail**
(`contacto@radalseguros.cl`, smtplib/imaplib) + broker SMTP/IMAP, status inquiries to insurers and
suggestion-only evidence; **public share links** `/s/:token` for insureds and insurers; an **RPA**
framework with an **HDI showcase** (fake portal only); and **agent skills** + 26 new tools. Antecedentes
(PDF/Word only, warn-not-block), Comparación (tool calls `extract_budget_proposal` → `submit_comparison`)
and Propuesta (`broker_proposal`) behave as before; extraction runs on **GLM-5.3 tool-calling**;
stage-guard reasons are **Spanish**. **Verified at v12: 1 428 backend tests; `tsc -b`/`build`/
`check:locales` clean (30 namespaces, 5 842 keys/locale; zero dynamic i18n keys); live smoke at 1440/375 px — not the full QA walk.**

**The shell since v10–v12.** The rail is Pólizas · Cobranza · ASEGURADOS · Portafolio · Analítica ·
Agente · Cotizar en portales · Compañías · Configuración (+ Mantenedor for platform users only). `/data`
is **Portafolio** (nine table tabs) and `/analytics` is **Analítica** (visuals, `?by=` from the server
catalog); both share one **scope — grupo · grupo-cuenta · fechas — in the URL**, applied server-side to
lists, summaries and exports (`POST /exports/{entity}`, XLSX + PDF). **"Ver expediente completo"** opens
`/groups/:groupId/accounts/:caseId/expediente`, rendered on demand; absent blocks print a Spanish reason
**derived from the journey**. **Motion lives in the `Tabs` primitive.** Read `README.md` to orient, the
handoffs for the arc, plus `docs/deployment.md` (stale on NAT/CI — see `deploy/v12/README.md` §6) and
`docs/usuarios-de-prueba.md`; delegate to `.claude/agents/`.

**Deploy state / traps.** The dev RDS carries the **v9 schema + blank baseline**. **v12 NEEDS a
migration, before the push:** `migrate_case_files --dialect mysql --pass v12` (review: 27 tables, 28
columns) → snapshot → `--apply` → `python -m scripts.seed_v12 --apply`; then the v12 env keys
(`CREDENTIALS_KEY` first) and the dry-run `deploy/v12/` scripts. **Verified 2026-10-17: the dev backend Lambda has NO
internet egress** (default-VPC public subnets, no NAT/endpoints — dev AI calls die at the 30 s timeout); fix = `fck-nat.sh`
mode C (`deploy/v12/README.md` §0). **Forpay is paused** until the team meets Forpay; Zoho lives on `radal.ai`, not
`radalseguros.cl` (Google MX) — confirm `RADAL_MAIL_ADDRESS` before `MAIL_ENABLED=true`. CI
(`.github/workflows/deploy-backend-dev.yml` / `deploy-frontend-dev.yml`, OIDC) fires on push to `dev`.
**Still owed / watch:** the **full QA walk has never been run**; the backend image builds Chromium;
**money-core extraction is unreliable** under tool-calling; the **vision fallback is stubbed**;
comparison align can run **minutes** (do NOT change the buffered invoke mode, OAC constraint 4); real
HDI / Zoho never exercised. Locally, run the backend with `MEDIA_BACKEND=local` (else
every document 404s) and, for smokes, `AI_API_KEY=` (`backend/.env` holds a live key). **Signal is the
design direction** (Aqua Spectrum + Porcelana deprecated). Traps that fail *silently*: the
**`paramsSerializer` in `src/lib/api.ts`** (repeatable filters stop applying and return plausible wrong
data); **`expose_headers` on CORS in `main.py`** (exports lose filename and row count); group-by
dimensions are **server-driven and not uniform** per entity; the **`{plan_id:int}`** converter on
legacy `/collections` routes. `git remote` still points at the old repo location (GitHub redirect).
