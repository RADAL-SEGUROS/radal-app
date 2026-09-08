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

**Where the work stands (verified 2026-09-06).** Three passes have landed on `dev` since the v2
rebuild (`ed8b8a1`), all of them **still uncommitted — 189 changed/untracked files**: *v2 case
files* (the expediente, the 29-stage journey machine, the 41-category extraction registry, packs,
the whole post-sale half), *v3 groups & accounts* (`account_group` above the expediente,
`case_file(kind=account)` as the Account = one line × one vigencia × N RUTs, the navigator tree,
`reperiod`/`renew`/`endorsements/batch`), and *v4* (the single tool-using **agent** with typed
READ/WRITE tools and `agent_action` confirm-cards, plus the **Porcelana** UI restyle, the single
context-switching sidebar, the Journey visualization and `/analytics`), and *v5 Signal*
(2026-09-06/07: **Porcelana's execution is deprecated too** — the binding spec is
`docs/v5-signal-ui-spec.md`: Inter, pine-accent primaries, hairline borders, de-mono'd
badges/tables; resizable/collapsible sidebar with the group tree REMOVED from the rail (tree
components deleted — the central account view owns stage navigation); Journey hero mounted on the
account page; group-has-no-RUT relabels (Contratante/Empresas); `/analytics` routed at last, with
an `analytics` i18n namespace, dashboard KPIs + recharts, paginated entity tabs, and new
tenant-scoped `GET /quotes|proposals|policies/summary` backend aggregates). Verified state: **531
backend tests passing in ~101 s**, `tsc` and `npm run build` clean. Read the spec you need rather
than re-deriving it — `docs/v2-architecture.md`, `docs/v2-case-files-spec.md` and its companion
`docs/v2-case-files-as-built.md`, `docs/v2-data-modeling-decisions.md`,
`docs/v3-groups-accounts-spec.md`, `docs/v4-agent-spec.md`, `docs/v4-porcelana-ui-spec.md`,
`docs/deployment.md`, `docs/usuarios-de-prueba.md` — and delegate to the sub-prompts in
`.claude/agents/` (`radal-data-model`, `radal-backend`, `radal-ai`, `radal-frontend`,
`radal-infra`). **Three standing traps:** the dev RDS migration has **not** been run and CI fires on
push to `dev`; nothing was ever uploaded to S3, so run the backend with `MEDIA_BACKEND=local` or
every document 404s; and **Aqua Spectrum is deprecated** — `docs/design-system.md` is historical,
Porcelana is the direction.
