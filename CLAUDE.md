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

**Where the work stands (verified 2026-09-08).** Every pass since the v2 rebuild (`ed8b8a1`) is
**still uncommitted on `dev`**: *v2 case files* (the expediente, the 29-stage journey machine, the
extraction registry, packs, the post-sale half), *v3 groups & accounts* (`account_group` above the
expediente, `case_file(kind=account)` as the Account), *v4* (the tool-using **agent**; the deprecated
Porcelana restyle), *v5 Signal* UI (`docs/v5-signal-ui-spec.md`: Inter, pine-accent primaries,
hairline borders; group tree removed from the rail; `/analytics`), *v6/v7* (antecedentes → Bases
Técnicas; broker-defined lines), and now **v8 — the broker-journey redesign** (this session). v8
reframes **ramos as advisory antecedentes templates** (`line_record_schema` gains `recommended_files`
+ `explanation`, `insurance_line_id` is now nullable; antecedentes is **warn-not-block** — Bases
Técnicas always renders, `GET …/pdf` no longer 409s) and makes the account milestones **antecedentes
→ bases técnicas → comparación → propuesta → pólizas**: a new **incremental, dynamically-extracted
`comparison`** expedient (`extract_budget_proposal` with a fixed money core + open facets + wrong-file
detection; monotonic `align_comparison` with a deterministic fallback), the outbound **`broker_proposal`**
(propuesta) built from the comparison, and **validate-then-dynamic policy** upload (`validate_policy_core`
hard-gates the core, `override` forces it, the full parse is kept in `policy.payload`) with a policies
overview. Extraction is **context-aware** (`_account_extraction_context` prepends prior antecedentes/
proposal for policies). Frontend: the enlarged **overview-altitude 7-node Journey** + pending-actions +
default **Summary** tab, group **icons** (emoji/glyph/image, styled-name fallback), the **vigencia
shortcut**, the comparison board `/comparisons/:caseId`, and the public insured-decision page `/o/:token`.
New tables `comparison`/`comparison_entry`/`comparison_source`, `broker_proposal`; new columns on
`policy`, `offering`, `account_group`; routers reuse existing RBAC modules (no matrix change). Verified
state: **596 backend tests passing**, `tsc -b` and `npm run build` clean, `check:locales` 22 namespaces
(es==en). Read the spec you need rather than re-deriving it — `docs/handoff-2026-09-08-v8.md` is the v8
companion; also `docs/v2-architecture.md`, `docs/v2-case-files-as-built.md`,
`docs/v2-data-modeling-decisions.md`, `docs/v3-groups-accounts-spec.md`, `docs/v5-signal-ui-spec.md`,
`docs/v6-antecedentes-expediente-spec.md`, `docs/v7-lines-and-bases-tecnicas-spec.md`,
`docs/deployment.md`, `docs/usuarios-de-prueba.md` — and delegate to the sub-prompts in `.claude/agents/`.
**Standing pre-push traps:** the dev RDS migration has **not** been run — run
`backend/scripts/migrate_case_files.py --apply` **and** by-hand `ALTER TABLE line_record_schema MODIFY
COLUMN insurance_line_id BIGINT NULL` (the script never alters nullability), before pushing (CI fires on
push to `dev`); nothing was ever uploaded to S3, so run the backend with `MEDIA_BACKEND=local` or every
document 404s; **corpus extraction has not been run through the new dynamic comparison/policy paths** and
**browser QA is not done**; and **Aqua Spectrum + Porcelana are deprecated** — Signal is the direction.
