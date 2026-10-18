# Handoff prompt — land the copilot home, fix the dev-review blockers, redeploy, re-review

Paste everything between the `=====` lines into a fresh Claude Code session opened in
`/Users/bgg/Documents/repos/radal/radal-app`.

=====

You are continuing Radal v12 (read `CLAUDE.md` first; the spec is `docs/technical-reference.md` §19, with
**§19.17** for this pass). Three things happened in parallel on 2026-10-17/18:

1. A **computer-use review** walked the deployed build at https://dev.radalseguros.cl and found three serious
   problems (below). It may still be running, and its full report (Spanish) will arrive from the owner.
2. A **copilot pass** was built on the branch **`feat/copilot-home`** (git worktree
   `../radal-app-copilot`, one commit on top of `dev` @ `8502b84`). It is **not merged and not deployed**.
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

## B. The dev-review blockers. Diagnose and fix them, smallest correct change first

1. **Documents can't be viewed or downloaded on dev.**
   - Cause: `frontend/src/lib/download.ts` always fetches `GET /documents/{id}/content`, and on the S3 backend
     that route answers **409** "Use the presigned URL from /download"
     (`backend/app/api/routers/documents.py`, around line 860).
   - Fix: call `GET /documents/{id}/download` first. When the `url` is absolute (an S3 presigned URL), open
     it or navigate to it directly; do **not** fetch it through axios, because it would carry our auth/OAC
     headers and S3 CORS would bite. Only for the local relative `/content` URL, keep the authenticated blob
     fetch.
   - Check every caller (preview and download), and the public share page if it shows documents.
   - Add a Vitest or unit test if the repo has a harness for it. Otherwise verify on dev.
2. **"Subir póliza" fails at ~60 s (3/3), and the Lambda dies at 120 s.**
   - Cause: `POST /policies/upload` runs the AI policy extraction **synchronously inside the request**.
     CloudFront's origin response timeout is **60 s**, so the browser gets an error while the Lambda keeps
     working.
   - The same root cause hits **comparisons**:
     - each cotización takes 60–100 s;
     - the UI loses the result until a reload;
     - *Realinear* hits 120 s and is mislabelled "El proveedor de IA no está disponible"
       (`locales/es/comparison.json`). That copy shows for a timeout, not just a provider error.
   - Options, cheapest first. Propose to the owner before doing (b) or (c):
     - **(a)** Make the UI honest. When the request times out (504 or a network error after ~60 s), say
       "sigue procesando", and poll the extraction / comparison state instead of failing. The `extraction`
       row is written first (rule 6), so the work is recoverable, and the list shows it after a reload. This
       alone removes the "lost silently" and mislabel findings.
     - **(b)** Raise CloudFront's origin response timeout. The default maximum is 60 s; the account can request
       up to 180 s through Service Quotas. This is an AWS change, so ask the owner first.
     - **(c)** The real fix. Run extraction and comparison **asynchronously**: return 202 with the job or
       extraction id, then the Lambda does the work and the UI polls. FastAPI `BackgroundTasks` is **not
       reliable on Lambda** (the runtime freezes after the response). Use an async self-invoke
       (`InvocationType=Event`) with the HTTP v2 envelope to `POST /events`; LWA 0.7.0 only accepts
       HTTP-shaped events (see `deploy/v12/schedules.sh` and `app/jobs`). This needs `lambda:InvokeFunction`
       on itself in the role.
   - Also check model tiering. Faster models for extraction are in the memory notes about GLM.
3. Then work through the reviewer's full report when the owner pastes it, severity first. Each fix gets a
   regression test.

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
   - No migration is needed for the copilot pass. If B.2(c) adds columns, they go through
     `scripts/migrate_case_files.py` and the `db.migrate` job.
   - Smoke dev at 1440 and 375 px:
     - `/` lands on `/agent` for `admin@demo.cl`;
     - the indicators are right for the demo world (cross-check Cobranza and Pólizas);
     - a common query answers;
     - a session survives a reload and shows in the sub-sidebar;
     - `/portal-search` shows Pronto;
     - a document downloads;
     - a policy upload either finishes or says "sigue procesando".

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
