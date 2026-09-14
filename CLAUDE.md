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
  is an **advisory recommended-files template + AI context, NOT a field/section schema**; it is chosen
  when creating the grupo-cuenta (dropdown or "Crear ramo" drawer), never managed in Configuración.
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

**Where the work stands (v11, 2026-09-14).** The v2→v10 arc is committed (`0c60de6`) and **pushed to
`dev`**, which fires CI to `https://dev.radalseguros.cl`. **v9** built the broker journey (see
`docs/handoff-2026-09-09-v9.md`); **v10** built the two analysis destinations and the account
super-overview (see `docs/handoff-2026-09-10-v10.md` — as-built AND a candid error log;
`docs/technical-reference.md` §17 is the spec); **v11** is the first **issue-driven** pass — the
Portafolio/Asegurados rename and the nine-tab Portafolio (§18), from team issues #1 and #2. §18.5
carries the rule that killed the two tabs that were asked for but NOT built (Renovaciones,
Inspecciones): a list needs **an origin, a classifier and an owner**, or it renders permanently empty
and reads as a broken app — reuse that test on the next feature request. A **ramo** is an **advisory recommended-files template
+ AI context — NOT a field/section schema**: chosen when creating the grupo-cuenta via a **dropdown**
or a **"Crear ramo" drawer**, never managed in Configuración. **Antecedentes** shows the ramo's
recommended-file slots + free upload, two subtabs (Archivos / Extracción), **cotizaciones scoped out**,
**PDF/Word only** (Excel rejected; vision fallback flagged-not-done), warn-not-block. **Comparación**
is a dynamic incremental comparison via **tool calls** (`extract_budget_proposal` →
`submit_comparison`: standardized table + a named AI recommendation, block-on-provider-down);
**Propuesta** is `broker_proposal`; **Pólizas** is validate-then-dynamic upload with account-aware
insurer resolution. Extraction is context-aware on **GLM-5.3 tool-calling** with a model-tiering
registry; stage-guard reasons are **Spanish**.

**What v10/v11 changed.** The **MÁS nav section is gone**; the rail is ASEGURADOS + five plain rows —
**Portafolio · Analítica · Agente · Compañías · Configuración** (no ADMINISTRACIÓN header).
`/analytics` split into **`/data` (Portafolio) and `/analytics` (Analítica — visuals only, with a
`?by=` dimension read from the server catalog)**. **v11** renamed Datos → **Portafolio** and Grupos →
**Asegurados** in every user-facing string (routes and identifiers stay English) and cut Portafolio's
eleven table tabs to **nine** in the broker's own order — Asegurados · Pipeline · Cotizaciones ·
Propuestas · Pólizas · Endosos · Cobranza · Siniestros · Documentos. The old entity routes still resolve for group deep links;
they just lost their rail entries. Both destinations share one **scope — grupo · grupo-cuenta ·
fechas — held in the URL**, applied server-side to lists, summaries and exports alike; comparing
several groups is deliberately NOT built (that is the agent's job). **"Ver expediente completo"** now
opens `/groups/:groupId/accounts/:caseId/expediente` — the account super-overview (identidad, the
journey with real completion dates, antecedentes, comparación, propuesta, pólizas, montos, documents)
plus a branded PDF, **rendered on demand so it is always current and needed no migration**; absent
blocks print a Spanish reason **derived from the journey**, never a blank. New: `POST /exports/{entity}`
(XLSX + PDF over every matching row) and `GET /exports/entities`. **Motion lives in the `Tabs`
primitive** so every tab bar inherits the sliding marker. **Verified at v11: 757 backend tests;
`tsc -b`/`build`/`check:locales` clean (22 namespaces, 3 719 keys/locale); the v10 surface driven
live in a real browser.** Read `README.md` to orient, the handoffs for the arc, the technical reference
for detail, plus `docs/deployment.md`, `docs/usuarios-de-prueba.md`; delegate to `.claude/agents/`.

**Deploy state / traps.** The dev RDS carries the **v9 schema + blank baseline**; **neither v10 nor
v11 added a column or an enum value, so neither needs a migration**. S3 `deploy/backend.env` carries the AI/media
keys (`MEDIA_BACKEND=s3`); CI (`.github/workflows/dev-deploy.yml`, OIDC) fires on push to `dev` →
`https://dev.radalseguros.cl`. **Still owed / watch:** the **full sequential QA walk has never been run**
(`docs/qa-handoff-chrome.md` — v10 verified only its own changes); the backend image builds Chromium
(slowest/riskiest CI step); **money-core extraction is
unreliable** under tool-calling (needs a retry or a stronger tier — not fixed); the **vision fallback
is stubbed**; comparison align can run **minutes** (buffered Lambda/CloudFront may time out — do NOT
change the invoke mode, OAC constraint 4). Locally, run the backend with `MEDIA_BACKEND=local` or every
document 404s. There is **no Alembic** — future column changes go through
`backend/scripts/migrate_case_files.py`. **Aqua Spectrum + Porcelana are deprecated — Signal is the
direction.** Three v10 traps that fail *silently*: the **`paramsSerializer` in `src/lib/api.ts`**
(without it every repeatable filter — `kind`, `stage`, `status` — stops applying and returns plausible
wrong data); **`expose_headers` on CORS in `main.py`** (without it an export loses the server filename
and row count while still downloading); and the **group-by dimensions are server-driven and NOT
uniform** per entity, so never hardcode them. `git remote` still points at the old repo location and
works only via GitHub's redirect.
