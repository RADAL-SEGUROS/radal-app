# v6 — The Antecedentes Expediente (demo, binding spec)

Status: **active build** (2026-09-07). The first of several *expedientes*. The broker gathers
everything the insured has communicated for one **account** (a `case_file` of `kind=account`, one
`insurance_line`/ramo × one vigencia), Radal's AI **consolidates** it into a **per-ramo schema**,
the broker **validates and completes** it (nothing auto-writes), **registers** it, and can then
**view it in-app** and **export a branded PDF** (Radal + the broker's own logo). Doctrine extends
CLAUDE.md rule 6: **extract → human validates & completes → register → view + PDF**.

Demo target: `usuario11@fuenzalidasr.cl` (broker_admin, broker_id=3) → group 6 GRUPO VIÑA INDOMITA
→ account `case_file id=4` (Viña Santa Alicia, `insurance_line_id=1` "Incendio y Sismo (Property)",
2026-2027), at `/groups/6/accounts/4`. Its 7 antecedentes docs already exist as real bytes under
`backend/media/documents/case_file/4/`, so **live GLM extraction works today** with
`MEDIA_BACKEND=local`. Broker 3 already has a `logo.webp` on disk, so the broker-logo requirement is
met without switching tenants; we additionally seed **Ossa Covarrubias (broker 1)** the real logo.

## AI model note (GLM-5.3-Flash is a reasoning model)

`AI_MODEL=zai-org/GLM-5.3-Flash` (DeepInfra). It returns `reasoning_content` separately and can
leave `message.content` **empty** when the output `max_tokens` is too small (the budget is spent on
reasoning first). The extraction call path MUST be hardened (see Backend §B).

## A. Data model (new tables via the model-driven migrate script)

The migrate script (`backend/scripts/migrate_case_files.py`) **creates new tables from metadata** —
define the models, register them in `app/models/__init__.py`, add a manifest, dry-run, then
`--apply`. No Alembic. Run `--apply` before pushing to `dev` (CI fires on push; dev RDS not migrated).

- **`line_record_schema`** (new `app/models/line_record_schema.py`) — the admin-maintained per-ramo
  antecedentes schema. `id` PK; `broker_id` FK→broker CASCADE **nullable** (NULL = global Radal seed,
  mirrors `insurance_line`); `insurance_line_id` FK→insurance_line NOT NULL; `name` String(160);
  `version` Integer default 1; `is_active` Boolean default True; `definition` JSON NOT NULL;
  `created_by_id` FK→user SET NULL; TimestampMixin. `UniqueConstraint(broker_id, insurance_line_id,
  version)`, `Index(broker_id, insurance_line_id, is_active)`. Resolution:
  `or_(broker_id==caller, broker_id.is_(None))` preferring the broker's own active row.
  - **`definition` JSON shape** (English `key`s = identifiers per rule 1; `label`/`description`/
    `options` are broker-authored data, Spanish allowed):
    `{sections:[{key, label, fields:[{key, label, type, required, description, options?, unit?,
    repeatable?, fields?}]}]}` where `type ∈ text|number|integer|boolean|date|money_uf|percent|
    select|list|group`. `list`/`group` carry nested `fields`.
- **`record_expediente`** (new `app/models/record_expediente.py`) — the registered instance, one per
  account. `id` PK; `broker_id` FK NOT NULL; `case_file_id` FK→case_file NOT NULL (the account);
  `insurance_line_id` FK SET NULL; `line_record_schema_id` FK SET NULL; `schema_version` Integer|None;
  `status` enum `RecordExpedienteStatus` default DRAFT; `payload` JSON|None (human-validated data);
  `source_extraction_id` FK→extraction SET NULL; `pdf_document_id` FK→document SET NULL (rule 8:
  the PDF is a Document row); `ai_confidence` PCT|None; `registered_at`/`registered_by_id`;
  TimestampMixin. `UniqueConstraint(broker_id, case_file_id)`. Plain FKs (no cycle — same topology as
  CasePack).
- **`RecordExpedienteStatus`** (new StrEnum in `app/models/enums.py`, add to `__all__`):
  `draft | processing | review | registered`.
- **`DocumentCategory.ANTECEDENTES_PACK = "antecedentes_pack"`** (add to the generated-packs block in
  `app/models/document.py`; stays <25 chars). The generated PDF is a Document (category
  ANTECEDENTES_PACK, entity_type CASE_FILE, id in `record_expediente.pdf_document_id`).
- Register `LineRecordSchema`, `RecordExpediente`, `RecordExpedienteStatus` in
  `app/models/__init__.py`; add an `ANTECEDENTES_MANIFEST` (new_tables=[line_record_schema,
  record_expediente]) to `MANIFESTS` and `OFFLINE_PASSES` in the migrate script.
- **No new CaseSection or CaseStage.** Antecedentes = the `RECORD_FOLDERS` view spanning
  `ROOT_PROSPECT + SUBMISSION`. Candidate docs = `Document` where `case_file_id==account.id AND
  category IN (flattened RECORD_FOLDERS categories)`, filtered by `broker_id`.

## B. Backend — extraction/consolidation + hardening

- **Reasoning-model hardening (load-bearing) in `app/services/ai.py`:** add
  `AI_EXTRACTION_MAX_TOKENS` to `config.py` (default ~12000) and pass it into `_chat_model` inside
  `_invoke_structured` (currently passes none) and into `_run_completion`. Make `_message_text`
  (ai.py:1017) fall back to `additional_kwargs['reasoning_content']`/response metadata when `.content`
  is empty; add a `_strip_reasoning` that removes `<think>…</think>` before `_extract_json_object`.
  Keep the empty-content guard as the LAST resort after these fallbacks. This fixes every existing
  extraction too, not just the new one.
- **Consolidation service** `consolidate_antecedentes(db, *, case_file_id, broker_id, user)`:
  (1) resolve the account case_file (broker-filtered → 404); (2) select its antecedentes documents
  (`section IN (root_prospect, submission)`, broker-filtered); (3) `load_document_text()` per doc
  (reuse as-is; respects `MAX_DOCUMENT_CHARS=45_000` each — budget/truncate the aggregate);
  (4) resolve the ramo schema via `line_record_schema` (broker-or-global, active) and **build a
  Pydantic model dynamically** with `pydantic.create_model` from `definition` using the `common.py`
  Annotated helpers (Str/Int/UF/Pct/FlexDate + Row/AmountRow lists) on `RootExtraction`;
  (5) assemble a multi-doc prompt (new sibling of `_extraction_messages`, labels each source doc,
  appends the schema hint via `_schema_hint`) and call the LLM (`_invoke_structured` pattern
  generalised, or `_run_completion(json_mode=True, larger max_tokens)` + `_extract_json_object` +
  `_coerce_payload`); (6) **persist exactly one `Extraction` row on every outcome** (rule 6),
  suggest-only — never auto-write the expediente.
- **Endpoints** (new router or extend `ai.py`; follow the extract/confirm skeleton + `_ai_http_error`
  502/503/504/422 mapping, never 500):
  - `POST /case-files/{id}/antecedentes/process` → runs consolidation; upserts a `record_expediente`
    row to status `review` holding the suggested payload + `source_extraction_id`; returns the schema
    + suggested payload + confidence + warnings. Gated `CaseFiles.Submit` (or Documents.Upload).
  - `GET /case-files/{id}/antecedentes` → the registered/in-progress expediente (schema + payload +
    status). Broker-filtered → 404.
  - `POST /case-files/{id}/antecedentes/register` → validates the human-completed payload against the
    dynamic model, sets status `registered` + `registered_at/by`. Gated appropriately.
  - `GET /case-files/{id}/antecedentes/pdf` → generates (or returns) the branded PDF Document;
    stores via `store_generated_document(category=ANTECEDENTES_PACK)`, sets `pdf_document_id`.
- **Ramo-schema maintainer CRUD** (admin, `Settings.Manage` / broker_admin):
  `GET/POST/PUT /line-record-schemas` (+ `GET /line-record-schemas/{insurance_line_id}` resolution).
  broker_id-scoped writes; reads include the global seed via `or_(broker_id==caller, is_(None))`.
- **Tenancy:** reuse `get_document`/`get_extraction` (broker-filtered → None → 404). Every query on
  both new tables filters `broker_id`; the PDF Document is reached only through the owned expediente.

## C. Media / broker logo

- Read raw bytes both backends with `app.services.packs._media_bytes(key) -> bytes|None` (never
  raises). **Refactor it into `app/services/storage.py`** (shared by packs + the new pdf service);
  keep the None-on-missing contract. Source doc bytes via `_document_bytes`.
- Broker logo path is deterministic: `broker.logo_key = 'media/broker/{id}/logo.webp'` (set
  unconditionally by the importer; may point at a non-existent file). PDF service: read via
  `_media_bytes(broker.logo_key)`; when None, render Radal-only branding (mirror packs.py). Embed as
  `data:image/webp;base64,…`.
- **Seed Ossa (broker 1) logo:** convert `backend/app/db/seed_assets/ossa-covarrubias-logo.jpeg`
  (898×177 wordmark) to WEBP **preserving aspect ratio** — do NOT use `_to_webp_square` /
  `process_and_store_avatar` (they center-crop square and would ruin the wordmark). Write to
  `backend/media/media/broker/1/logo.webp` (local) and S3 key `media/broker/1/logo.webp` when
  uploads enabled. No DB change (logo_key already points there). Add this to `import_fixtures.py`
  after broker import (guard: only if the seed asset exists).

## D. PDF service (Playwright + HTML/CSS)

- **New `app/services/pdf.py`** — `async render_html_to_pdf(html: str) -> bytes` using the **async**
  Playwright api (`from playwright.async_api import async_playwright`; the skill's `sync_playwright`
  would block/raise inside the FastAPI event loop). ONE shared `Browser` launched via a module-level
  `ensure_browser()` (lazy) and from the app lifespan; per render:
  `page = await browser.new_page(); await page.set_content(html, wait_until="networkidle");
  await page.wait_for_function("document.fonts.ready.then(()=>true)", timeout=4500);
  await page.wait_for_timeout(300); await page.emulate_media(media="print");
  data = await page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
  margin={"top":"0","right":"0","bottom":"0","left":"0"}); await page.close()`. Launch args
  `["--no-sandbox","--disable-gpu","--disable-dev-shm-usage"]`. Guard a missing `playwright` import
  with a clear `PDFGenerationError` (mirror `packs.py`'s `PackGenerationError`), never a raw
  ImportError at request time.
- **Lifecycle in `main.py`**: launch the shared browser at startup, close it at shutdown (add an
  async startup/shutdown or convert to a `lifespan` asynccontextmanager; keep the existing
  `create_all`). Lazy-launch-on-first-render is the fallback so the app still boots where Chromium
  isn't installed (tests/dev).
- **New generic template `app/services/pdf_templates.py`** —
  `render_expediente_html(*, title, branding, sections) -> str`. `branding = {radal_wordmark,
  broker_logo_datauri|None, broker_name, broker_rut, cmf_code}`. `sections: list[Section]` where a
  Section is `{kind: "fields"|"table"|"callout"|"stats", heading, ...}`. Every empty value renders an
  explicit missing marker: a `.muted` em-dash `—` for optional-empty and a `.badge.warn` **"falta"**
  chip for required-but-missing (so the human-validate step is visible on the page). GENERIC on
  purpose — antecedentes is one caller (cover + insured identity + per-ramo schema sections); later
  expedientes reuse it.
- **Pages** compose as skill "sheets": SHEET 1 cover (no runframe) = "Radal." wordmark lockup +
  broker logo (data URI) + `.eyebrow` "Expediente · Antecedentes" + `.display` title + `.cover-meta`
  grid (razón social, RUT BODY-DV, ramo, vigencia). Following sheets carry `.runhead` ("Radal." +
  broker left, "Antecedentes" right) / `.runfoot` (broker legal name + page n/N). Body = insured
  identity block, then per-ramo schema as `.seclabel`-numbered sections of grouped fields +
  repeatable `table.data` blocks.
- **CSS**: vendor a TRIMMED subset of the skill CSS **inlined in the template head** (no runtime
  dependency on the skill files; no charts/echarts/dark-beats needed). Retune tokens to Radal Signal:
  `--accent:#0e7c70` (pine; near-identical to the existing pack teal #0F766E), lighter `--accent-2`,
  `--paper:#fff`, `--ink` dark slate, `--line` hairline (~#e5e7eb) — **hairline borders, not
  shadows**; `--font-body:"Inter"`; NO mono chips in tables (design-direction memory rejects them).
  Copy `@page{size:A4;margin:0}`, `.sheet{width:210mm;height:297mm}`, `print-color-adjust:exact`
  verbatim. **Embed Inter woff2 as `data:` URIs** in an `@font-face` block (do NOT depend on the
  Google Fonts CDN — server-side render may be offline; `networkidle` must resolve instantly). Logos
  are `data:` URIs too. **Never emit an http(s) img src** (it hangs `networkidle` offline).
- **Storage**: refactor `packs._media_bytes` into `app/services/storage.py` (shared by packs + pdf;
  keep the None-on-missing contract) OR import it; read the broker logo via `_media_bytes(broker.logo_key)`.
- **requirements.txt**: add `playwright>=1.47,<2.0` under a new HTML→PDF block (reportlab stays).
  **Provisioning is a NEW prerequisite** (not in the repo today): `playwright install chromium`
  (+ `--with-deps` on Linux/CI). Install it into `backend/.venv` for the demo and document it in
  `docs/deployment.md` + backend README.
- **pytest stays Chromium-free**: `render_expediente_html` is a pure string builder — unit-test it
  (assert the `—`/"falta" markers, broker identity, and that NO http(s) img src leaks). For
  `render_html_to_pdf`, monkeypatch it to a small bytes stub in endpoint tests, or gate the one true
  render behind `@pytest.mark.pdf_render` (skipped unless `RUN_PDF_RENDER=1`). Never touch conftest.

## E. Frontend

- **Entry point** — add a **"Procesar antecedentes"** primary CTA in `RecordsTab` (account.tsx),
  distinct from the per-document "Analizar" (that stays). Gated `useCan("Documents","Upload")` (or
  `CaseFiles.Submit`), with `DisabledHint` when the account's ramo has no registered schema. Wires to
  `useProcessAntecedentes({case_file_id})` (suggest step; writes only an extraction row).
- **New `components/common/AntecedentesReview.tsx`** (do NOT extend `SuggestionForm` — it is a flat
  scalar renderer that dumps lists/objects into raw JSON textareas). Iterate the schema `sections`
  (each a Signal `Section`/Card with `divide-y`), switch per field `type`
  (text/number/date/select-with-options/boolean/nested-group/**repeatable table** with add/remove
  rows). Use `react-hook-form` `useFieldArray` for the repeatable arrays. Reuse SuggestionForm's
  provenance card, the edited/override highlighting, and confirm-disabled-with-reason idioms. Confirm
  posts the assembled sectioned payload to the register endpoint.
- **Registered view** — `components/expedientes/ExpedienteView.tsx` (read-only, grouped by section,
  Signal inline `KeyValue` rows, no card-in-card). Mount as a new **`?tab=antecedentes`** in
  account.tsx's `TABS` (add key + `TabsContent` + `accounts:account.tabs.antecedentes`). Header
  carries a **"Descargar PDF"** button → `useExpedientePdf(caseId)` two-step download (mirror
  DocumentsTab's `DownloadButton`: fetch short-lived URL then `window.open`), gated
  `useCan("CaseFiles","View")`. No dead button.
- **Schema maintainer** — `pages/settings/components/RamoSchemasTab.tsx`, registered as a 4th
  underline tab in `pages/settings/index.tsx` (`settings:tabs.schemas`), gated
  `useCan("Settings","Manage")` (the `isBrokerAdmin` gate). Built with the settings kit (`SectionCard`,
  `Field`, `GuardedButton`, `Dialog`, `DataTable`): list per-ramo schemas + create/edit/delete; the
  editor is a nested authoring form (sections → fields with key/label/type/required/options/repeatable/
  nested). No new route (Settings is one page).
- **API** — new `api/antecedentes.ts` (+ types in `api/types.ts`: `RamoSchema {sections:[{key,label,
  fields:[{key,label,type,required,description,options?,repeatable?,fields?}]}]}`, suggestion +
  confirm shapes). `keys.ts` adds `expedientes: scope("antecedentes-expedientes")` (+ `detail(caseId)`)
  and `ramoSchemas: scope("ramo-schemas")` (+ `detail(ramo)`). Hooks copy the ai.ts idiom:
  `useProcessAntecedentes()`, `useRegisterAntecedentes(caseId)` (invalidate `qk.caseFiles.all` +
  `qk.expedientes.all`), `useExpediente(caseId)`, `useExpedientePdf(caseId)`; ramo CRUD
  `useRamoSchemas/useRamoSchema/useCreateRamoSchema/useUpdateRamoSchema/useDeleteRamoSchema`.
- **i18n** — new **`antecedentes`** namespace (register in `NAMESPACES` in `i18n/index.ts` + create
  `locales/es/antecedentes.json` authoritative + `locales/en/antecedentes.json` exact mirror). Key
  groups: `process.*`, `review.*`, `view.*`, `sections.*`/`fields.*`. Add `tabs.schemas` + a
  `schemas.*` group to the `settings` namespace, and `account.tabs.antecedentes` to `accounts`. Run
  `npm run check:locales` + `npx tsc -b --noEmit` before returning.

## F. Run / seed / evaluate

- Backend: `cd backend && MEDIA_BACKEND=local .venv/bin/uvicorn app.main:app --reload --port 8000`.
- Seed (fixtures FIRST, then expedientes): `MEDIA_BACKEND=local .venv/bin/python -m
  app.db.import_fixtures --reset --no-upload` then `… -m app.db.import_expedientes --source
  "~/Downloads/EXPEDIENTES DEMO" --no-upload`. Both mirror bytes into `backend/media/`.
  (Current DB already has the demo data on disk — a reset is only needed if ids drift.)
- Frontend: `cd frontend && npm run dev` (:5500).
- **Migration before push:** `python -m scripts.migrate_case_files` (dry-run, expect CREATE TABLE
  line_record_schema + record_expediente) then `--apply`, confirm "verify: zero drift".
- **Hermetic pytest:** monkeypatch `app.services.ai._chat_model` to a `_FakeChatModel`
  (`with_structured_output(method='json_mode', include_raw=True)`); reuse the fakes in
  `tests/test_ai_streaming.py`. Local-media fixture: set `settings.MEDIA_BACKEND='local'`,
  `MEDIA_LOCAL_DIR=tmp_path`. Mock the PDF render (don't require Chromium in the suite). Never touch
  the two `conftest.py` AI-blanking lines. Baseline ~531 tests green.
- **E2E eval checklist:** login usuario11 → `/groups/6/accounts/4` → Antecedentes tab → "Procesar
  antecedentes" (processing state; one extraction row per attempt; nothing auto-committed) → review
  shows the ramo schema pre-filled → edit a field + fill a deliberately-missing field → Register
  (writes the expediente only on confirm) → reopen shows persisted values → Descargar PDF (Radal +
  broker 3 logo; aggregates all insured data) → tenant check (usuario1/broker 1 gets 404 on the
  expediente/PDF route) → maintainer tab visible only to broker_admin, can add a ramo schema →
  pytest green + frontend tsc/build/check:locales clean.
