# 11 · Fast reading — parallel, tiered AI reads (policy + cotización) — build contract

Branch `feat/copilot-home` (worktree `../radal-app-copilot`). Owner decisions 2026-10-18. This file wins over
older docs for the reading pipeline. **No schema change, no migration, no new env key required.**

## Why (measured 2026-10-18, DeepInfra `zai-org/GLM-5.3-Flash`, real demo PDFs in `radal-app/.review-tmp/`)

- One-call POLICY read: TRBF Southbridge **89.5 s** (11 484 output tokens of a 12 000 cap — 96 %, near truncation),
  Coccolino fleet **68.8 s**. Streaming probe: first token at 6.7 s, **reasoning = 71 tokens**; the time is
  OUTPUT (≈150 tok/s): a 36 k-char JSON with 400+ nulls and duplicated fields.
- Same read split into **4 parallel field groups** (same prompt + "devuelve SOLO estas claves; omite nulls"):
  TRBF **28.7 s wall**, Coccolino **21.6 s**, identical item counts (41 sublímites, 20 exclusiones, 16 deducibles,
  10 cuotas, 7 garantías). Core group (identity + money + cuotas) **10–22 s**. Output tokens 11 484 → ~8 000.
- Dev (CloudFront origin timeout **60 s**, Lambda **120 s**): every upload/comparison that took > 60 s was lost by the
  browser; 5 invocations were killed at 120 s. Comparison standardization batches run **sequentially**
  (`ai.py` `for batch in batches`), so *Realinear* exceeds 120 s.
- Field audit: of 48 POLICY fields ~23 are consumed; ~22 are stored and only dumped in the generic
  `PolicyPayloadPanel`; `policy_location` and `coverage_item` are written and never read.

## Decisions

1. **Tiered read.** *Core* (what Confirmar / the policy row / the plan need) runs inside `POST /policies/upload`
   (target ≤ 20 s). *Datos técnicos* run in a **second request** the SPA fires right after the upload succeeds
   (target ≤ 35 s), each request well under the 60 s edge timeout. No async infra, no AWS change.
2. Within each request the groups run **in parallel** (thread pool, one provider call per group, same document
   text — input tokens are cheap and cached by the provider).
3. **Datos técnicos arrive as a SUGGESTION** (rule 6): they are stored on an `extraction` row and shown on the
   policy as "Leído por IA — Revisar y confirmar"; nothing is written to the policy until the broker confirms.
4. **Exclusiones and cláusulas particulares: titles only** (number + title, ≤ 120 chars), never the full text. The
   full wording stays in the PDF; the **agent can search the policy text** and quote paragraphs on demand (new
   read tool `search_document_text`).
5. **Slim schemas:** omit empty values; one verbatim text per row at most; no server-computed fields in the
   schema (`inconsistencies`); no fields nobody reads. Prompts say "omite las claves sin valor".
6. Cotizaciones get the same treatment (parallel groups) and the standardization batches run in parallel.
7. Errors are honest: a provider **timeout** is not "El proveedor de IA no está disponible".

## Policy read — field tiers

**Core group(s)** — run in parallel as two calls: `core_identity_money` and `core_plan`.

| key | notes |
|---|---|
| policy_number, renews_policy_number | renewal link (spec "renueva_a") |
| insurer {legal_name, trade_name, rut, cmf_code} | |
| insured [ {legal_name, rut} ] (one or more), policyholder {legal_name, rut, contact_email, contact_phone} | contratante |
| beneficiaries [ {name, rut, type ∈ hipotecario·prendario·leasing·endosatario·otro, amount_uf} ] | replaces `pledge_creditor` (the spec's acreedor is a beneficiary type) |
| line_of_business (hint only), coverage_modality, is_provisional_certificate | |
| period_start_at, period_end_at, issue_date, currency | |
| total_insured_amount_uf | |
| premium {taxable_premium_uf, exempt_premium_uf, net_premium_uf, vat_uf, total_premium_uf} | |
| commission_pct | |
| payment_mode, installments [ {number, coupon_number, due_date, gross_amount_uf, net_premium_uf, commission_uf} ] | `core_plan` call |
| confidence, extraction_notes (≤ 3 short) | |

Dropped from the core: broker, issue_place, source_proposal_date, source_quote_reference,
mirror_issuance_statement, premium_lines, top-level commission_uf, installment status/paid_on/days_late.

**Technical groups** — four parallel calls (as built; `tech_sublimits` added after a live run packed 41 sublimits into one text).

| group | keys |
|---|---|
| `tech_risk` | items [ {type ∈ ubicación·bien·vehículo·persona, description, address_or_plate, insured_amount_uf} ] (merges locations / vehicles / insured_amounts), coverages [ {code (POL/CAD), name, limit_uf, sublimit_text} ] (**principal coverages only**; no `requested_limit`/`offered_condition`/`is_sublimit`), indemnity_limit, aggregate_limit_uf, valuation_basis, average_rate_permille, cmf_policy_code |
| `tech_sublimits` | sublimits [ {name, limit_uf, sublimit_text} ] — the extensions/sublimits table, one row each, appended to the *Coberturas* section |
| `tech_deductibles` | deductibles [ {peril, pct, min_uf, max_uf, days, text} ] (one literal `text`), warranties [ {code, title, requirement, deadline_days, due_date} ] |
| `tech_clauses` | exclusions [ {number, title} ], particular_clauses [ {number, title} ] — **titles only** |

Dropped: drivers, workers, ap_plans, accumulations, reinstatement, market/additional clause code lists.

## API contract

- `POST /policies/upload` — request gains `insurer_id` (the broker's choice when the PDF names the insurer only by
  name — real policies never print its RUT/CMF; the 422 `insurer_unresolved` carries name-based `suggestions`, and a
  retry within 30 min re-uses the core read). Otherwise unchanged request/response shape; now runs **only the core groups** (parallel) and
  commits as today (core validation, parties, plan). Response gains `technical_read: {status: "pending"}` so the
  SPA knows to fire the next call. The extraction row keeps `kind=policy`, `category=policy`,
  `prompt_version="policy-core-v1"`.
- `POST /policies/{policy_id}/technical-read` — Policies.Create (or Edit — mirror whatever `upload` requires);
  runs the three technical groups in parallel against the policy's source document; writes ONE `extraction`
  row (`kind=policy`, `category=policy`, `prompt_version="policy-technical-v1"`, `status=succeeded|failed`) and
  returns `TechnicalRead`. Idempotent-ish: if a succeeded, unconfirmed technical extraction exists for that
  document, return it unless `?force=true`. 404 for a foreign policy; 409 `no_source_document` when the policy
  has no PDF.
- `GET /policies/{policy_id}/technical-read` → latest `TechnicalRead` or `{status: "none"}`.
- `POST /policies/{policy_id}/technical-read/{extraction_id}/confirm` — body `{sections?: [...]}` (default all);
  commits to the policy: `deductibles` JSON, `indemnity_limit`, `aggregate_limit_uf`,
  `insured_amount_semantics` (valuation_basis), `average_rate_permille`, `cmf_policy_code` (only when empty —
  never overwrite a confirmed core value; differences become warnings), warranties upsert, and merges the
  technical sections into `policy.payload` (so the payload panel shows them). Records a bitácora event and marks
  the extraction confirmed (`record_confirmation`).
- `POST /policies/{policy_id}/technical-read/{extraction_id}/discard` — marks it dismissed (no policy write).
- `TechnicalRead = {status: "none"|"running"|"ready"|"confirmed"|"failed", extraction_id, started_at,
  finished_at, error_code ("ai_timeout"|"ai_unavailable"|"ai_parse"|null), message (Spanish, user-facing),
  sections: {items:[…], coverages:[…], deductibles:[…], warranties:[…], exclusions:[…], particular_clauses:[…],
  limits:{indemnity_limit, aggregate_limit_uf, valuation_basis, average_rate_permille, cmf_policy_code}},
  counts: {items, coverages, deductibles, warranties, exclusions, particular_clauses}}`.
- Agent read tool `search_document_text(document_id, query, max_hits=5)` → paragraphs (± 1 neighbour) from the
  document's extracted text that match the query terms (accent/case-insensitive), each with a page number when
  available. Broker-scoped. Registered in the policy-related skills.

## Comparisons (cotizaciones)

- `extract_budget_proposal`: split into parallel calls — `money_core` (insurer, money, rates, commission, validity,
  dates, quotation_number, document_type) and `facets` split by facet group in 2 parallel calls when the document
  is long. Drop the unused `facet.description` from the guidance. Target: one cotización ≤ 35 s.
- Standardization: run the batches **in parallel** (`ThreadPoolExecutor`), merge deterministically exactly as now.
- Timeouts → `AITimeout` with Spanish copy "La lectura tardó más de lo esperado. Reintenta; si persiste, avísanos."
  The SPA shows that copy instead of "El proveedor de IA no está disponible" for `ai_timeout`.

## Correctness fixes found during the audit (do them)

- `services/policy_validation.py`: `_present` treats a dict of all-null keys as present, so *corredor* and
  *asegurado* can never fail → treat a dict as present only if some leaf is non-empty. The prima check must read
  `premium.*`, not the first `net_premium_uf` anywhere (an installment row can stand in today).
- `services/mirror.py`: premium aliases (`net_taxable*`, `gross_*`) never match the schemas'
  `taxable_premium_uf` / `exempt_premium_uf` / `net_premium_uf` / `total_premium_uf` → add them; read the 07's
  `commission.pct`; stop diffing `confidence`.
- Schema hint for POLICY was 13 591 / 14 000 chars (`MAX_SCHEMA_CHARS`) — the slim schemas must be far below.

## Timeouts (align)

As built: **90 s** per provider call (not 50 s). GLM-5.3-Flash streamed 40–150 tok/s on the same day; the server
finishes a request the 60 s edge already cut (dev logs: entries committed at 100 s) and the SPA recovers it by
re-reading, so a call may use most of the Lambda's 120 s. 429s (per-account concurrency) back off and retry twice.

## Verification

- Hermetic tests (conftest blanks the key): group split, parallel runner (fake client, assert concurrency and
  merge), slim-schema coercion, technical-read endpoints (404 foreign, 409 no PDF, confirm writes, discard
  doesn't), core-validation fixes, mirror alias fixes, agent search tool, comparison parallel batches.
- **Live timing script** (manual, not in the suite): `backend/scripts/ai_timing_probe.py <pdf>…` runs the real tiered
  reads against the provider and prints per-group seconds/tokens — run it on the `.review-tmp` PDFs and record
  results in `10-phase1-as-built.md`.
