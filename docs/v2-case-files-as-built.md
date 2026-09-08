# Radal v2 — Case Files: AS-BUILT

> **This is the resume-here document.** `v2-case-files-spec.md` is the *design* spec, written
> before the code; this file is what actually exists on disk. Where the two disagree, **this file
> wins** and §2 says why.
>
> **Built:** 2026-08-19 / 2026-08-20, one multi-agent pass on branch `dev`, plus a fix round.
> **State: entirely UNCOMMITTED** — 108 changed/untracked files in the working tree.

---

## 0. What the pass delivered, and what is verified

The pass turned the v2 broker workspace into an **expediente-centric** application: a `case_file`
(the folder the broker actually thinks in) wrapping the existing `placement`, a 29-value journey
stage machine with server-side guards, a 41-member document-category registry with 25 Pydantic
extraction schemas behind a generic AI extract → review → commit pipeline, the whole post-sale
half of the domain (policy, endorsement, collection plan + instalments, warranty, claim + claim
items, mirror-diff), generated PDF/ZIP packs, a lead pipeline, notes with follow-up dates, an SSE
agent chat, and 23 new frontend pages/components across `cases`, `leads`, `agent`, `policies`,
`endorsements`, `collections` and `claims`.

Verified on 2026-08-20 (re-verified while writing this document):

| Check | Result |
|---|---|
| `python -m pytest tests/ -q` | **315 passed**, 3 warnings, **51.6 s** (was 159 before the pass) |
| `npx tsc --noEmit && npm run build` | clean; `✓ built in 3.44s` (one >500 kB chunk warning, pre-existing) |
| `import_expedientes --dry-run` | **168 corpus files classified, 0 landing in `other`** |
| demo DB (`backend/radal.db`) | **16 `case_file` rows** = 7 account + 4 endorsement + 2 collection + 2 claim + 1 renewal |
| documents persisted | **89** of 168 attached to a case (`--full` → 111); 157 `document` rows total incl. fixtures + packs |
| post-sale rows | 2 policies · 4 endorsements · 2 collection plans · 25 instalments · 19 warranties · 2 claims · 11 claim items · 8 packs · 45 notes · 88 stage events |
| git | **nothing committed**; branch `dev`, 108 files `M`/`??` |

---

## 1. How to run it right now

### Backend — `:8000`

```bash
cd backend && source .venv/bin/activate

# The DB and the corpus bytes are ALREADY on disk from the 2026-08-20 import:
#   backend/radal.db          16 case files, 89 case documents
#   backend/media/            172 files, 6.4 MB (documents/ media/ broker/)
#
# backend/.env says MEDIA_BACKEND=s3, but NOTHING was ever uploaded to S3 — both
# importers ran with --no-upload and mirrored the bytes locally instead. So you MUST
# override the backend, or every download/extract/pack-zip 404s:
MEDIA_BACKEND=local uvicorn app.main:app --reload --port 8000
```

`AI_API_KEY` in `backend/.env` is a live DeepInfra key, so `/ai/*` works out of the box. Nothing
in the normal demo path needs it — the LA FAVORITA 07/08 extraction pair is seeded pre-confirmed
by the importer, so `GET /policies/{id}/mirror-diff` renders without ever calling a model.

### Frontend — `:5500`

```bash
cd frontend && npm install && npm run dev
```

Login with any user from `docs/usuarios-de-prueba.md`, password `radal1234`.
`usuario1@ossacovarrubias.cl` (Ossa Covarrubias) owns 5 of the 7 account expedientes including
LA FAVORITA, the richest one. `usuario11@fuenzalidasr.cl` owns the other two.

### Rebuilding the demo data from scratch

```bash
cd backend && source .venv/bin/activate
python -m app.db.import_fixtures --reset --no-upload
python -m app.db.import_expedientes --source ~/Downloads/"EXPEDIENTES DEMO" --no-upload
```

Order matters — `import_expedientes` refuses to run before `import_fixtures` (it needs the
tenants, the 25-company CMF catalog and the insurance lines). Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | classify all 168 corpus files, print the tally, write nothing |
| `--reset-cases` | delete only what this importer created, leave the fixture world alone |
| `--full` | also import the lead-stage expediente's 22 files → 111 case documents |
| `--extract` | run one real LLM extraction per category (needs `AI_API_KEY`) |
| `--no-local-copy` | do **not** mirror bytes into `MEDIA_LOCAL_DIR` (you almost never want this) |

The corpus lives at `~/Downloads/EXPEDIENTES DEMO` (169 files on disk, 168 after the ignore list).
It is **not** in the repo and must not be committed.

### Tests

```bash
cd backend && .venv/bin/python -m pytest tests/ -q          # 315 passed in ~52 s
cd frontend && npx tsc --noEmit && npm run build            # clean
```

---

## 2. As-built delta table — spec said X, we built Y

Rows are ordered roughly by how much they would surprise someone reading the spec cold.

| # | Spec said | Built | Why |
|---|---|---|---|
| 1 | §3.1 prefill targets read as if every category writes to an entity (e.g. `declination` → a `proposal` with `status=rejected`; `risk_engineering_plan` → `warranty` rows) | **14 categories commit, 10 are informational-only.** `_COMMITTERS` in `app/services/extraction_commit.py:1855` covers `prospect_request, technical_brief, submission_letter, resubmission_letter, inspection_report, endorsement_proposal, endorsement, payment_plan, collection_status, claim_notice, claim_preliminary_report, claim_final_report, compliance_notice, policy`. `_INFORMATIONAL` (line 1873) covers `business_questionnaire, insured_values_schedule, loss_history, risk_engineering_plan, declination, conditional_pronouncement, quote_comparison, issuance_proposal, technical_recommendation, broker_closing_note`, each with a Spanish-facing reason string returned to the UI | Rule 6 (suggest → confirm → commit) does not license *fabricating* rows. A declination is an insurer that **did not quote** — writing a `proposal` row for it would corrupt the comparator. Master data (00B, 00C) is edited on its own screen. A confirmed 07 (`issuance_proposal`) **is** the mirror baseline — it is consumed by `mirror.py`, not copied into a table |
| 2 | §4 / §8: earlier drafts put pack generation on `CaseFiles.Manage` | **`CaseFiles.Submit`** on all three pack endpoints (`packs.py:138,155,172`) and on `POST /packs/{id}/summary/confirm` | Generating a pack is *moving the case forward*, not a settings action. Same grant that gates stage transitions, so the executives and technicians who run an expediente can produce its deliverables without being admins. The spec text in the repo already carries the corrected wording |
| 3 | §3: `proposal` is "kept and deprecated" | **Kept, and it is a genuine second key in `CATEGORY_REGISTRY`** (26 entries = 25 distinct categories + the `proposal` alias), pointing at the same `InsurerQuotationExtraction` schema and at prompt version **`proposal-extract-v1`**, not `insurer_quotation-v1` | Holding the legacy prompt version stable is what keeps the pre-existing proposal-upload tests and `pages/proposals/upload.tsx` byte-identical in behaviour. New code writes `insurer_quotation`, which shares the same prompt version on purpose |
| 4 | §2.1: table named `sales_lead`, not `lead` | Built exactly so, and the model docstring records the check against `sqlalchemy.dialects.mysql.base.RESERVED_WORDS_MYSQL` | `LEAD` is a MySQL 8 reserved word (the window function). A bare `lead` table would depend on the dialect quoting it |
| 5 | §9.1: brokers matched by name, never by the corpus RUT | Built: `BROKER_KEY_BY_NAME` in `app/db/expediente_mappings.py:312` matches on the accent-folded legal + trade name | The letterheads print Ossa Covarrubias `77.245.318-9` and Fuenzalida SR `76.114.982-3`; the fixtures carry `78.069.390-8` and `77.290.425-8`. **Both corpus RUTs fail mod-11** — they are facts about a document, not tenant identity. Matching on them would fork every tenant in two |
| 6 | §7.6: the mirror-diff compares the two **confirmed** payloads | Compares confirmed-first, and **falls back to the newest `succeeded` extraction** when nothing is confirmed, tagging the side as `latest_succeeded` vs `confirmed` in the API response (`app/services/mirror.py:268-337`) | Otherwise the panel is permanently empty until a human confirms both documents — a dead screen during review. The tag lets the UI label the diff provisional instead of lying about it |
| 7 | §9.3: "7 cases along the journey" | **7 account cases + 9 post-sale children = 16 `case_file` rows.** The demo import truncates each expediente to its journey stage (`CaseSpec.sections`), so **89 of 168** documents persist; `--full` un-skips the lead-stage expediente's 22 files → **111** | A case parked at `intake` must not contain the quotations it has not received yet, or the stage machine and every guard become theatre |
| 8 | §5.2: forward skips are conditional, "e.g. `intake → technical_basis` when `insurance_line.requires_inspection == False`" | A static, documented **`CASE_STAGE_SKIPS`** table (`app/services/case_files.py:112`). `requires_inspection(db, case)` exists at line 676 but **nothing consumes it** — `intake → technical_basis` is always offered. Extra skips beyond the spec: `proposal_issued → policy_issued` (carrier issues without a separate ratification), `claim_adjusting → claim_final` (small loss, no pre-informe), and the whole `collection_suspended` side-stage web | A conditional edge that can't be precomputed makes `GET /transitions` inconsistent with the machine. Each skip is a path the corpus really takes; the guards, not the topology, are what refuse a premature move |
| 9 | §6.4: `POST /policies/{id}/from-proposal` | **`POST /policies/from-proposal`** — collection-level, `proposal_id` in the body | There is no policy yet at that point; the path parameter had nothing to name |
| 10 | §6.5 lists 7 pack routes | All 7 built, plus an extra **`GET /packs/{pack_id}`** (single pack read) the UI needed for polling after generation | |
| 11 | §2.10: `DocumentCategory` VARCHAR grows **29 → 36** | **29 → 37.** `document.category` is `VARCHAR(37)`; `extraction.kind` `22 → 25`; `document.section` is `VARCHAR(27)` | Arithmetic correction only — the computed length comes from `sql_enum`, and the longest member is `conditional_pronouncement` (25) / `claim_preliminary_report` (24). `backend/scripts/migrate_case_files.py:110` and `docs/deployment.md` both carry the corrected 29→37 |
| 12 | §3.1 gives multi-code hints (`payment_plan` = "09/plan", `claim_notice` = "10/11") | `CategorySpec.code` is a **single canonical hint** (`payment_plan` = `09`, `collection_status` = `10`, `claim_notice` = `11`, `claim_preliminary_report` = `12`, `claim_final_report` = `13`). The many-to-one mapping lives in **`CODE_TO_CATEGORY`** (35 codes → 25 categories) | One code per spec keeps the registry a clean dataclass; the ambiguity belongs in the lookup table the importer and upload auto-detect actually use |
| 13 | §4 role matrix: `broker_executive` Claims = `V C E Cm U` | Built with **`Submit` as well** | An executive who can file a claim must be able to send it to the carrier; withholding `Submit` made `POST /claims/{id}` useful and the claim un-submittable |
| 14 | §10.1: post-sale pages imported normally in `App.tsx` | Post-sale routes are resolved through **`import.meta.glob`** with a lazy `React.lazy` + `UnderConstruction` placeholder (`frontend/src/App.tsx:48-73`) | The route contract had to be published *before* the postsale pass wrote its pages; a static import of a not-yet-existing module breaks the build for everyone. A missing page renders "en construcción", never a blank screen |
| 15 | §12 acceptance 1: "≥ 159 passing" | **315 passing** — 7 new test modules (`test_case_file_model, test_case_files, test_post_sale, test_packs, test_collection_sums, test_extraction_registry, test_ai_streaming`) | |
| 16 | §2.2: `case_file.reference` "unique per broker" | `UniqueConstraint(broker_id, reference)` built, but the column is **nullable** | A case can be created before the reference is minted; the unique constraint tolerates NULLs on both engines |

Everything else in the spec was built as written. The 9 new tables, all 33 endorsement columns,
all 22 warranty columns, the 18 new enums, the 38 additive columns on 9 pre-existing tables, the
seven stage guards, the `sequence_no`/`version` sub-funnel, the pack contract (`packs.py` never
touches S3 or the LLM directly), and the OAC-frozen `frontend/src/lib/api.ts` all match §2–§11.

### Endpoint surface, as built

190 routes total. The case-files pass added:

```
GET    /case-files/summary · /case-files · /case-files/{id} · /{id}/transitions
       /{id}/timeline · /{id}/documents · /{id}/packs · /{id}/recipients
POST   /case-files · /{id}/transition · /{id}/versions
       /{id}/packs/{submission|comparison|proposal}
PATCH  /case-files/{id}        DELETE /case-files/{id}
GET    /policies/{id}/case-files                      (the post-sale sub-funnel)

GET    /leads/summary · /leads · /leads/{id}          POST /leads · /leads/{id}/convert
PATCH  /leads/{id}                                    DELETE /leads/{id}

GET    /notes · /notes/{id} · /activities             POST /notes
PATCH  /notes/{id}                                    DELETE /notes/{id}

GET    /policies · /policies/{id} · /{id}/mirror-diff · /{id}/warranties
POST   /policies · /policies/from-proposal · /{id}/mirror-diff/queue · /{id}/warranties
PATCH  /policies/{id}   DELETE /policies/{id}   GET/PATCH /warranties/{id}

GET    /endorsements · /endorsements/{id}   POST /endorsements · /{id}/issue
PATCH/DELETE /endorsements/{id}

GET    /collections · /{id} · /{id}/installments · /{id}/status
POST   /collections   PUT /{id}/installments   PATCH /{id} · /{id}/installments/{number}

GET    /claims · /{id} · /{id}/items   POST /claims · /{id}/close
PATCH  /claims/{id}   PUT /claims/{id}/items

GET    /packs/{id} · /packs/{id}/download   POST /packs/{id}/summary/confirm

GET    /ai/categories                        POST /ai/documents/extract
POST   /ai/documents/{extraction_id}/confirm · /ai/proposals/{id}/summary
POST   /ai/case-files/{id}/summary · /ai/threads/{id}/stream
```

Permissions are as in spec §4 except delta #2 and #13. `/notes` and `/activities` resolve their
gate dynamically: `MODULE_BY_ENTITY[entity_type]` + `View`/`Comment`, with edit/delete allowed to
the author or to `<module>.Manage`; `/activities` is gated on `Dashboard.View`.

---

## 3. KNOWN GAPS / NEXT STEPS

Honest list. Each one is real, each one has a file path.

### 1. The dev RDS migration has NOT been run — **blocker before the first push to `dev`**

There is no Alembic in this repo. Startup runs `Base.metadata.create_all()`, which creates
**missing tables** but never **ALTERs** existing ones. This branch adds 9 tables (fine on boot)
**plus 38 additive columns on 9 pre-existing tables** and widens two enum-backed VARCHARs
(`document.category` 29→37, `extraction.kind` 22→25). Pushed as-is, `radal-dev-db` keeps its old
schema and nearly every query 500s.

- Tool: **`backend/scripts/migrate_case_files.py`** — idempotent, dry-run first, and it compiles
  every column spec from the live SQLAlchemy metadata with the target dialect, so it cannot drift
  from the models. `--dialect mysql` prints the full offline RDS plan without connecting.
- Procedure: **`docs/deployment.md` § "Schema migration — case-files pass"** (line 109) —
  temporarily make RDS public, add your IP to `sg-0da8dd955985aaa32`, dry-run, apply, re-run to
  confirm "nothing to do", then **revert both** SG and public-access changes.
- `.github/workflows/dev-deploy.yml` fires on push to `dev`. Migrate **first**.

### 2. Nothing has been uploaded to S3

Both importers ran with `--no-upload`; the 172 corpus/fixture files live in `backend/media/`
(6.4 MB) and nowhere else. `backend/.env` still says `MEDIA_BACKEND=s3`, so a run without the
`MEDIA_BACKEND=local` override serves 404s for every document, every AI extraction and every pack
ZIP. Before a cloud demo, re-run both importers with `--profile radal` (no `--no-upload`) so the
bytes land under `documents/` in `s3://radal-dev-185011028331`.

### 3. `SuggestionForm` is duplicated

`frontend/src/components/common/SuggestionForm.tsx` (362 lines, registry-driven via
`GET /ai/categories`, consumed by `pages/cases/detail.tsx`) and a **second, local**
`function SuggestionForm(...)` at `frontend/src/pages/proposals/upload.tsx:670`, used at line 495.
The duplication was deliberate during the pass — `upload.tsx` and its tests had to stay untouched
— but the two will drift the moment either the registry field-rendering rules or the confidence
badge behaviour changes. Fold `upload.tsx` onto the common component (passing
`category="insurer_quotation"`) and delete the local copy.

### 4. `renewal` is the only case kind with no corpus documents

`case_file` id 16, `EXP-2026-0005-RN1`, stage `renewal_review` — seeded from the LA FAVORITA
closing note (14) and carrying **0 documents**. Every other kind has real files (account 72,
endorsement 8, claim 7, collection 2). The renewal chain exists end-to-end in the machine
(`CASE_STAGE_FLOW[RENEWAL]` re-enters `intake` and runs the full account path) and the stage is
reachable, but **renewal is not demoable end to end** — there is nothing to open, nothing to
compare, no pack to generate. The corpus has no renewal folder. Either build a renewal document
set, or keep the "renovar" control disabled with the "pronto" chip as it is today.

### 5. The pack builder's summarize step commits mid-build

`app/services/packs.py::_maybe_summarize` (line 732) and `_maybe_summarize_proposals` (line 756)
call `app.services.ai.summarize_case_pack` / `summarize_proposal`, and **both of those end in
`db.commit()`** (`app/services/ai.py:2385`, `:2257`). They run inside `_guarded(...)`, before
`_finish(...)` stores the PDF/ZIP documents. So a failure *after* summarization — a render error,
an oversized ZIP (413), a storage error — leaves the already-committed `case_pack` row and its
summary/extraction persisted, and `_guarded`'s `status=FAILED` write is the only cleanup. The
promise in spec §8.4 ("never a half-written document row") holds for the *document* rows but not
for the pack + summary state. Fix by having the summarizers `flush()` and let the caller own the
transaction, matching the `extraction_commit.py` contract which already does exactly that.

### 6. SSE streaming will not stream in production

`POST /ai/threads/{id}/stream` is a real `StreamingResponse` and locally it genuinely streams
token by token. Behind CloudFront it will not: the OAC setup requires **both** the Lambda Function
URL invoke mode **and** the LWA env to be `buffered` (`docs/deployment.md`, OAC constraint 4), so
the whole body arrives in one chunk at the end. **Do not "fix" this by changing the invoke mode —
that regresses the OAC contract and costs another debugging cycle.** The frontend already handles
it: `frontend/src/pages/agent/index.tsx` sets `FALLBACK_MS = 5000` and, if no token has arrived in
5 s, aborts the stream and replays the turn on the non-streaming `POST /ai/threads/{id}/messages`.
Expect the cloud demo to show a 5-second pause and then the full answer.

### 7. Test hermeticity to AI is fragile — **do not delete these three lines**

`backend/tests/conftest.py` lines 34-36, **before any `app.*` import**:

```python
os.environ["AI_API_KEY"] = ""
os.environ["AI_BASE_URL"] = "http://127.0.0.1:9/no-provider-in-tests"
```

They are assignments, not `setdefault`, on purpose: they must **shadow** `backend/.env`, which
carries a live DeepInfra key. Without them, the best-effort `_maybe_summarize` inside every pack
test reaches the real provider — the suite slows to a crawl and then **hangs on a socket that
never returns**. This cost roughly an hour to diagnose during the fix round; the symptom is a
green-looking suite that simply stops in `test_packs.py`. If you refactor `conftest.py`, keep the
blanking above the `from app...` imports (`app.db.session` builds its engine at import time, and
`settings` is read at import time too).

### Smaller things worth knowing

- `case_files.requires_inspection()` (`app/services/case_files.py:676`) is dead code — see delta #8.
- `extraction` currently holds only 3 rows in the demo DB: the 2 seeded LA FAVORITA confirmations
  plus 1 `failed` summary. `--extract` was never run against the corpus.
- **5 of the 8 `case_pack` rows in the demo DB are seeded, not generated.** The importer writes
  `status=generated` rows whose `pdf_document_id` points straight at the corpus 06 / 07 file
  (`app/db/import_expedientes.py:2291, 2444, 2636, 3318, …`), which is why their
  `zip_document_id` is NULL. Do not mistake a seeded row for evidence that a builder ran.
  **Verified 2026-09-04 — all three builders work end to end**, on a live server against the real
  corpus:
  - `submission` on `EXP-2026-0001` (Viña Santa Alicia, case 4) → pack 8, documents **156** (PDF)
    + **157** (ZIP); the ZIP holds the cover plus all 7 real sub-expediente-1 files.
  - `comparison` on `EXP-2026-0004` (Coccolino Multirriesgo, case 5) → pack 2, document **158**,
    a valid 2-page PDF and **no ZIP** — correct, §8.2 makes the comparison pack PDF-only.
  - `proposal` on the same case → pack 3, documents **159** (PDF) + **160** (ZIP), the ZIP holding
    the cover and the real corpus `07 Propuesta de Emisión … .docx`.
  Re-running the importer resets these to seeded rows again. *(An earlier revision of this bullet
  attributed the submission pack to LA FAVORITA — it is Viña Santa Alicia; documents 156/157 hang
  off case 4.)*
- `RADAL_Viaje_del_Riesgo.html` at the repo root is untracked and is the source artwork for the
  journey rail. Decide whether it belongs in `docs/` before committing.
- The frontend build emits one 1.37 MB chunk. Pre-existing, not a case-files regression, but it
  will get worse as post-sale grows.

---

## 4. If you are picking this up cold, read in this order

1. **`CLAUDE.md`** — the seven non-negotiable rules. Nothing below overrides them.
2. **This file** — what exists, what is broken, what must happen before the next deploy.
3. **`docs/v2-case-files-spec.md` §0–§5** — the idea, the sections, the tables, the stage machine.
   Read §2 of *this* file alongside §3.1 and §5.2 of the spec, which are the two sections the
   build moved furthest.
4. **`backend/app/models/case_file.py`** — 273 lines, and the docstrings explain
   `sequence_no` vs `version` better than any prose here can.
5. **`backend/app/services/case_files.py`** — `CASE_STAGE_FLOW`, `CASE_STAGE_SKIPS`,
   `STAGE_TO_PLACEMENT_STATUS`, the seven guards. This is the spine.
6. **`backend/app/schemas/extraction/registry.py`** — the 26-entry registry; every AI screen in
   the app is generated from it.
7. **`backend/app/services/extraction_commit.py:1855-1935`** — the three dispatch tables
   (`_COMMITTERS`, `_INFORMATIONAL`, `COMMIT_PERMISSIONS`). Read these before adding a category.
8. **`frontend/src/pages/cases/detail.tsx`** — the seven-tab expediente page; it consumes almost
   every endpoint the pass added.
9. **`docs/deployment.md` § "Schema migration — case-files pass"** — before you push anything.
10. **`docs/v2-architecture.md`** and **`docs/v2-data-modeling-decisions.md`** — still the
    authority on actors, tenancy, money and the column-vs-JSON-vs-child-table rule.
