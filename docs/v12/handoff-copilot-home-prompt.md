# Handoff prompt — land the copilot home, fix the dev-review blockers, redeploy, re-review

Paste everything between the `=====` lines into a fresh Claude Code session opened in
`/Users/bgg/Documents/repos/radal/radal-app`.

=====

You are continuing Radal v12 (read `CLAUDE.md` first; the spec is `docs/technical-reference.md` §19, with
**§19.17** for this pass). Three things happened in parallel on 2026-10-17/18:

1. A **computer-use review** walked the deployed build at https://dev.radalseguros.cl and found three serious
   problems (below). It may still be running, and its full report (Spanish) will arrive from the owner.
2. A **copilot pass + fast-reading pass** were built on the branch **`feat/copilot-home`** (git worktree
   `../radal-app-copilot`, on top of `dev` @ `8502b84`). It is **not merged and not deployed**.
3. `dev` may have new commits from the debugging session. Check `git log dev` before you start.

Standing rules: never open RDS (migrations/seeds run in the VPC through the token-gated jobs); never print
secrets or lose `CREDENTIALS_KEY`; run smokes with `AI_API_KEY=` blank; commit only on `dev` or feature branches,
and pushing `dev` deploys (CI), so push only when the owner asks you to deploy.

## A. What `feat/copilot-home` contains (product decisions by the owner)

- **The broker's home is the agent copilot.** `/` → `HomeRedirect` → `/agent` (platform users → `/platform`, and a
  broker role without `Dashboard.View` → `/policies`). The welcome shows "Hola, {nombre}", **up to five floating
  indicator cards** from `GET /ai/copilot/home` (overdue cuotas, evidence to confirm, to-dos, policies expiring in
  30 days, active policies; each one links to the list it counts, scope *mías* for executives and *todas* for
  admins), plus **common queries** (≤ 6 ranked by the data, "Ver más" for the catalogue). A query creates a
  thread, loads its skill and sends the prompt.
- **Chat sessions are stored and listed in a sub-sidebar**, the same pattern as the group sub-sidebar:
  - *Nueva conversación*, search, and buckets Hoy / Ayer / Últimos 7 días / Anteriores;
  - inline rename, archive with *Deshacer*, and *Archivadas*.
  - Backend: `GET/PATCH/DELETE /ai/threads[/{id}]` (soft delete, a 404 for any thread that isn't the caller's).
    `ThreadRead` gains a `title` that is never null, `message_count` and `preview`. Auto-title comes from the
    first message.
  - **Bug fixed:** the SSE path dropped `title`/`last_message_at` (the DB session was closed before the streamed
    body was written).
- **Portal search ("Cotizar en portales") is coming soon.** Insurer portals need each insurer's user + password
  to apply filters.
  - `PORTAL_SEARCH_ENABLED=false` (the default) and `GET /meta/features` carries the reason. Creating or
    retrying a run returns 409 `feature_disabled`, `rpa.run` skips, and the agent hides the portal tools and
    skill.
  - UI: the rail entry is disabled with a "Pronto" chip plus the reason. `/portal-search` shows a coming-soon
    page, and in Integraciones the button is a `SoonButton`.
  - The RPA code is kept.
- **No schema change, no migration.** No new env keys are needed on dev (the flag defaults to off).
- Verified in the worktree:
  - backend: full suite green (see the commit message for the count) and `tests/test_copilot_home_sessions.py`;
  - frontend: `tsc -b`, `check-locales` (30 ns, 6 096 keys, explicit maps in `pages/agent/catalog.ts`) and
    `build` all clean;
  - a Playwright smoke at 1440/375 px against a local seed.

## B. The dev-review blockers — FIXED on the same branch (verify them on dev)

The branch also carries the **fast-reading pass** (tech-ref §19.18, contract `docs/v12/11-fast-reading.md`), built
after reading the dev Lambda logs and timing the real PDFs against DeepInfra:

1. **Downloads** — `lib/download.ts` asks `GET /documents/{id}/download` first and opens presigned S3 URLs directly
   (no axios); `…/content` only for the local backend. Every document opener uses it.
2. **Subir póliza ~60 s → error** — the single 11 k-token read (69–90 s) is now **parallel field groups**: the upload
   reads only the core (5–15 s live), *Datos técnicos* are a second request (10–36 s) that lands on the ficha as
   *Leído por IA — Revisar y confirmar*. Exclusions/clauses are titles only; the agent has `search_document_text`.
   **Also fixed:** real policy PDFs never print the insurer's RUT/CMF, so "Subir póliza" from Pólizas failed with
   422 `insurer_unresolved` for every real policy (that was the reviewer's 422 at 98 s) — the dialog now asks
   *¿Qué compañía emitió esta póliza?* with name-based suggestions (dev's catalogue has the 25 general insurers),
   and the retry re-uses the read.
3. **Comparisons** — each cotización reads in 5 parallel calls; the comparison standardizes **by facet group** in
   parallel; timeouts say "La lectura tardó más de lo esperado…" (`ai_timeout`), and the SPA re-reads the
   comparison when the 60 s edge cuts an answer the Lambda finishes. Live with the provider at only 40–45 tok/s:
   42–51 s per cotización, 78.7 s for the 3-way comparison (recovered by polling). **This is the weakest spot**:
   GLM-5.3-Flash throughput swings 40–150 tok/s. If it stays slow on dev, the options are (a) CloudFront origin
   timeout 60 → 180 s (Service Quotas request, AWS change — ask the owner), (b) a faster model with a guard for
   Chilean thousands ("10.000" → 10 was seen on DeepSeek-V4.1-Flash), (c) true async jobs.
4. Then work through the reviewer's full report when the owner pastes it, severity first, each fix with a test.

## C. Land the copilot branch

1. Start from an up-to-date `dev`, then `git merge feat/copilot-home` (or rebase the branch first).
2. Expect conflicts:
   - `frontend/src/App.tsx`, `components/layout/Sidebar.tsx` / `SidebarGlobal.tsx`, `pages/agent/index.tsx`;
   - the locale JSONs (`common`, `dashboard`, `rpa`, `settings`);
   - `CLAUDE.md` and `docs/technical-reference.md` §19.
   - Keep both sides, and keep the locale files key-sorted with es/en parity.
3. Re-run everything after merging:
   - `cd backend && MEDIA_BACKEND=local .venv/bin/python -m pytest tests -q -p no:cacheprovider`;
   - `cd frontend && npx tsc -b --noEmit && node scripts/check-locales.mjs && npm run build`;
   - `./review-v12.sh`, then a quick local look at `/agent` (welcome, a query, the sessions sub-sidebar,
     rename/archive/undo) and `/portal-search` (Pronto).
4. **When the owner says deploy:** push `dev` and watch both GitHub Actions runs (`gh run watch`) until green.
   - No migration is needed for either pass (no new column, no new env key). Anything that adds a column later goes
     through `scripts/migrate_case_files.py` and the `db.migrate` job.
   - Smoke dev at 1440 and 375 px:
     - `/` lands on `/agent` for `admin@demo.cl`;
     - the indicators are right for the demo world (cross-check Cobranza and Pólizas);
     - a common query answers;
     - a session survives a reload and shows in the sub-sidebar;
     - `/portal-search` shows Pronto;
     - a document downloads;
     - Pólizas → Subir póliza with `.review-tmp/08 Poliza N 0020119904 TRBF…` → company step suggests Southbridge →
       policy page in ~10–20 s → the *Datos técnicos* card turns ready (41 sublímites as rows) → Confirmar;
     - a comparison with the three `.review-tmp` cotizaciones completes (possibly after the SPA's re-read).

## D. Update the reviewer prompt for the next walk

Edit `docs/v12/computer-use-dev-review-prompt.md`:

1. **"What v12 is":** the home is the copilot, not Pólizas. Add the sessions sub-sidebar, and portal search is
   "Pronto" (replace the "no se ejecutará / en configuración" expectation).
2. **New walk step 0 — Copilot (as `admin@demo.cl`, then as `ejecutivo@demo.cl`):**
   - The welcome greets the user by first name.
   - The floating indicators match the Cobranza and Pólizas screens, with *mías* for the executive and
     *todas* for the admin. Each card opens the list it counts.
   - Run three common queries plus one from "Ver más". Check that the answers are correct against the
     screens and how long they take.
   - Sessions: they are auto-titled, they persist after a reload and after logging out and back in, and the
     search finds them by message text. Rename, archive and *Deshacer*, then *Archivadas* → restore.
   - An executive must never see the admin's sessions.
   - Motion must be calm and must stop with "reduce motion"; at 375 px the sub-sidebar must be usable.
3. **"Already known":** add whatever B left unfixed, verbatim.
4. Mark in the **Walk** which findings of the previous report were fixed, so the reviewer re-tests those first.

Finally:

- Update `CLAUDE.md` ("Where the work stands" and "Next session"), staying concise.
- Update `docs/technical-reference.md` §19.17 (and §12 for anything left open).
- Add a dated as-built + error-log section to `docs/handoff-2026-10-17-v12.md`.
- Report back with the commits, the CI runs, the dev smoke results, and what is still open.

=====
