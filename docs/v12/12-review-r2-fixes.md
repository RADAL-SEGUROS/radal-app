# 12 · Review round 2 fixes — build contract

Branch `fix/review-r2` (worktree `../radal-app-copilot`), from `dev` @ `14ba4ca`. Source: the round-2 computer-use
review on dev (`.review-tmp/r2-report.md` + screenshots `.review-tmp/r2-shots/`, git-ignored, Spanish). Owner
decisions 2026-10-19 are marked **[D1–D3]**. This file wins over older docs for these points.

Rules that still apply: English identifiers, Spanish only in locale values / LLM prompts / user-facing server
`reason`/`message` strings; tenant 404s; no dead buttons; explicit i18n maps (no dynamic keys); suggest → confirm;
money only through `services/collection/ledger.py`; **prefer no schema change** — if a column is unavoidable it
is additive and goes through `backend/scripts/migrate_case_files.py` (say so in the report).

Structured error shape for every new/changed 4xx: `{"detail": {"code": "<snake>", "reason": "<Spanish>", …}}`.

## A · Comparisons → Propuesta (backend agent A)

A1. **Columns lose insurer + premium on dev** (critical). On dev `/comparisons/26` the three columns show
"Cotización 1/2/3", Prima total / neta "UF —", tasa "—"; only commission shows. Locally the parallel read
(`services/budget_read.py`) returned insurer + totals. Find where the split read's `money_core` keys are lost
between `extract_budget_proposal` (ai.py) → the `extraction.parsed` stored → `_premium_core` / column header
(`routers/comparisons.py` ~L203, ~L793) and fix it. You may read dev through its API (read-only, demo tenant):
`https://dev.radalseguros.cl/api/v1`, login `admin@demo.cl` / `radal1234` (authorised test credentials), header
`X-Radal-Token: <jwt>`; every request needs `x-amz-content-sha256: <sha256 hex of the exact body>` (empty body =
`e3b0c442…b855`). Never write to dev.

A2. **A read that failed/timed out must not become a normal-looking column** (Mapfre came back "No indicada" in every
row after the SPA's "recovered" path). A failed read → the entry is visibly failed (`status`/`error_code` on the
entry read) with retry; the standardizer skips failed/empty readings.

A3. **Recommendation** must name insurers ("Southbridge", "HDI", "Mapfre"), never "columna N" or ids, and compare
**prima total** (not exenta). Fix the recommend + submit prompts; pass the insurer names in the input.

A4. **Standardization quality:** exclusion rows: `present=true` means "the offer includes this exclusion"
consistently across columns; values as numbers in UF/% (no "475256" strings for UF — numeric `value` + `unit`
when the schema allows, else keep numeric); dedupe the same concept across group slices by canonical key.

A5. **Promover a propuesta [D3]:** if the account has no placement, **create it on demand** (same machinery as the
v12 colocación-on-demand for policies / `case_file.entry_origin`), never the English `no_placement` error. The
insurer is chosen like in Subir póliza: `ComparisonPromote` accepts `insurer_id` (catalogue choice, visibility as
`GET /insurers`), and the entry read exposes `insurer_id` (when resolved) + `insurer_suggestions: [{id, name, rut,
cmf_code}]` (reuse `extraction_commit.insurer_name_suggestions` on the read `insurer_name`). Typed RUT/CMF stays as
a fallback field. All errors Spanish + structured.

A6. **Journey:** adding cotizaciones / aligning must advance the account stage as the journey defines
(Cotizaciones recibidas → Comparación); "Se necesita al menos una cotización confirmada" must not show when 3
exist. Check the machine (`services/case_files`) and wire what is missing.

## B · Policies, cobranza, copilot, agent, documents (backend agent B)

B1. **Duplicate upload [D2]:** `POST /policies/upload` for a policy that already exists (same broker, same
`policy_number`, and same insurer when it is resolvable — when the read insurer is name-only, the same number +
an insurer whose name suggestion matches is enough) → **409** `{"code":"policy_duplicate","reason":"Esta póliza ya
existe: N° {n} de {insurer}.","policy_id":…, "policy_number":…, "insurer_name":…}` **with no side effects**: no
notifications, no plan, no technical re-read, confirmed Datos técnicos untouched, and the document of this request
is removed if it is an orphan just uploaded by the same user (or not linked to the policy). Check it right after the
core read (before any commit work). Tests.

B2. **Past cuotas at upload [D1]:** when a plan is created from an uploaded/manual policy and some cuotas are
already past due (Chile date), those cuotas are **pending review**: no `termination_risk`/overdue alerts and not
counted in overdue KPIs/board totals until the broker decides. Prefer no schema change (e.g. the decision recorded
as a bitácora event on the plan, and "pending" = cuota due before the plan's creation date and no decision event).
- Upload response gains `past_installments: {plan_id, count, total_uf, first_due, last_due} | null`.
- `POST /collections/plans/{plan_id}/past-installments` body `{"decision": "paid" | "unpaid"}`:
  - `paid` → a full payment per past cuota through the ledger (`paid_on = due_date`, method from the plan, a note
    "Declarado pagado al cargar la póliza");
  - `unpaid` → alerts resume normally.
  - Both write the event; the call is idempotent; it returns the updated counts.
- Policy detail / plan read exposes `past_installments_pending` so the UI can show a banner.
- Make the existing `collection.clock` / `collection.notices` / `alerts.py` respect it. Tests.

B3. **Technical read coverages (vehicles 12/26):** in `schemas/extraction/policy_tiers.py` `tech_risk.coverages` =
**every** coverage row of the policy's coverage table (in auto policies asistencia, vehículo de reemplazo, daños
en el extranjero… ARE coverages); `tech_sublimits` = only an explicit table of *extensiones / sublímites* (empty
when the policy has none). Re-run live once: `MEDIA_BACKEND=local .venv/bin/python -m scripts.ai_timing_probe
<both .review-tmp policy PDFs> --tier technical` (key: `AI_API_KEY` from `../radal-app/backend/.env`, never print
it). Target: Coccolino ≈26 coverages, TRBF 1 + ~41.

B4. **Copilot:** `expiring_policies_30` must link to a list that really filters by expiry — add
`expiring_within_days` (int) to `GET /policies` (active policies whose end date is within N days) and link
`/policies?expiring=30` (+ `&mine=1` for mine). `todo_notifications` for an admin (scope `all`) = the broker's
open to-dos (*Todas*), matching Notificaciones → Todas.

B5. **KPI cards honor "Mías":** `GET /policies` summary cards and the Cobranza summary cards accept the same owner
filter as their lists (`mine=1` / `owner=me`) so cards and list agree for the executive.

B6. **Agent output hygiene:** the system prompt forbids tool names, enum values and ids in answers (speak in
Spanish business terms: "cuota 1 de 10", "transferencia"); proposed write actions carry a server-built
`display: [{label, value}]` (Spanish labels, UF/CLP formatted Chilean style, dates dd-mm-aaaa, the cuota NUMBER and
policy number, never the internal id) that the confirm card renders; the result line is Spanish ("Pago registrado:
declarado, cuota pagada").

B7. **Documents:** presigned URLs set `ResponseContentType` = the document's mime and
`ResponseContentDisposition` = `inline` for preview / `attachment` for download with `filename*=UTF-8''<original
name>` (the download kept the internal category name; PDFs downloaded instead of opening). Add a `mode` query param
(`preview|download`, default `download`) to `GET /documents/{id}/download`.

B8. **Server copy:** the `insured_unresolved` reason without "client_id" ("El RUT del asegurado leído no está en tu
cartera: créalo o elige el asegurado."). Grep for other English/technical `reason` strings the upload / technical
read / collections routes can return to the UI and make them Spanish.

## C · Frontend (agent C) — builds against A/B contracts above

C1. **Agent Markdown:** render assistant messages as Markdown (bold, headings, lists, GFM tables, links) safely —
`react-markdown` + `remark-gfm`, no raw HTML; tables scroll horizontally on mobile. Old sessions too.

C2. **Comparison page:** after each entry finishes (or is recovered), refetch so the new column appears and
**Realinear** enables without reload; Realinear shows progress while running; a failed entry (A2) shows as failed
with *Reintentar*; disabled Realinear says why; values formatted (UF with thousands separator "UF 15.000",
percentages "25 %"); the sticky label column has an opaque background (values overlapped labels at ~546 px).

C3. **Promover dialog [D3]:** pick the insurer from the catalogue with `insurer_suggestions` first (same UX as the
upload's company step); RUT/CMF typing only as "¿No está en la lista?" fallback; Spanish server errors shown.

C4. **Upload dialog:** 409 `policy_duplicate` → "Esta póliza ya existe…" + **Ver póliza** link (no technical read
fired). After 201 with `past_installments.count > 0` → step "Esta póliza tiene N cuotas con vencimiento pasado
(UF X). ¿Ya están pagadas?" → **Sí, marcar pagadas** / **No, quedan vencidas** / **Decidir después**; the policy
page shows a banner with the same choice while `past_installments_pending`.

C5. **Policy page:** Datos técnicos card times in Chile local time (use the app's existing date helpers); empty
cells "—" not "UF —"; hide the "Código" column when every row is empty; the confirmed **deductibles** render as a
readable list ("Incendio: 10 % de la pérdida, mín. UF 10") not a `pct: … peril: …` dump; **Contenido extraído**
uses an explicit Spanish label map for known payload keys, Chilean number/date formatting, and hides unknown keys
(no raw English keys ever).

C6. **Agent confirm card:** render `display` (B6) when present; never raw enum/ids.

C7. **Copilot / small:** `/policies?expiring=30` read by the Pólizas page (B4); archive *Deshacer* toast ~8 s;
"Aseguradoras" page title → "Compañías"; Portafolio "Nuevo cliente"/"Clientes totales" → "Asegurado(s)" wording.

## Verification (all agents)

Backend: `cd backend && MEDIA_BACKEND=local .venv/bin/python -m pytest tests -q -p no:cacheprovider` green
(baseline 1 567). Frontend: `cd frontend && npx tsc -b --noEmit && node scripts/check-locales.mjs && npm run build`
clean. Add tests for every behaviour change. Docs: a short "§19.19 Review round 2 fixes" in
`docs/technical-reference.md` (append your part; re-read before editing — others write there too).
