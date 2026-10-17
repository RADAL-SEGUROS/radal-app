# Handoff: fix the v12 Chrome-review findings

On 2026-10-17 a full Chrome walk of v12 was run against the local demo world (`./review-v12.sh`).
The Spanish report and screenshots are in `/tmp/radal-review-findings/` (`informe-v12.md` plus the
`0N-*.jpg/png` files). `/tmp` does not survive a reboot, so **everything a fixing session needs is
restated below**. Paste the block under **PROMPT** into a fresh Claude Code session at the repo root.

---

## PROMPT (paste this into a new Claude Code session)

> You are fixing the bugs found in a QA walk of **Radal v12**. The work is on branch
> `feat/v12-policies-cobranza`, which is uncommitted and undeployed. Read `CLAUDE.md` first: it is
> orientation only, and its non-negotiables apply. All identifiers are English. Spanish lives only in
> `locales/*/**.json`. Never use dynamic i18n keys. Money rules: `vat = 0.19 × taxable`, UF
> everywhere. No Alembic: any new column goes through `backend/scripts/migrate_case_files.py`. Do not
> touch the `AI_API_KEY`/`AI_BASE_URL` lines in `tests/conftest.py`. Then skim `docs/technical-reference.md`
> §19 (v12 spec) and `docs/handoff-2026-10-17-v12.md`. Use `.claude/agents/` (radal-backend,
> radal-frontend, radal-data-model) for the work. **Do not commit, push, deploy or migrate any RDS
> unless I ask.**
>
> **How to reproduce.** `./review-v12.sh` seeds a fresh disposable world in `/tmp/radal-review.db` and
> starts the backend on :8000 and the frontend on :5500, with AI, mail and RPA off. It prints the
> logins (password `radal1234`) and three public `/s/…` share links. `./review-v12.sh stop` stops it.
> Each run re-seeds, so the links change.
>
> **Fix in this order. For each item, write a failing test first where one is feasible, fix it, then
> tick it off.**
>
> **P0: money (must fix)**
> 1. **Confirming evidence over-pays a cuota.** Repro: on cuota 2/6 of policy `5520-0001` (UF 28,80),
>    register a partial payment of `150.000` CLP. It becomes UF 3,80 "Declarado". Then, in
>    *Respuestas por confirmar*, confirm Sura's reply "Pagada UF 28,80". Result: **Pagado UF 32,60**
>    on a UF 28,80 cuota, with both payments live. The same happens when a receipt is reassigned with
>    *Otra cuota* to HDI cuota 4/12 (balance UF 0,92; $72.375 = UF 1,83 got recorded). The views then
>    disagree. The cuota panel says 32,60. The plan page `/collections/:id` says "Pagado UF 57,6"
>    (capped). The Cobranza KPI "Cobrado este mes" is inflated.
>    Look at `backend/app/services/mail/evidence.py::confirm_evidence` (~l.185),
>    `backend/app/services/collection/ledger.py::confirm_payment` (~l.323) and the tolerance logic there.
>    The frontend form lives under `/collections/evidence`.
>    **Expected:**
>    - The confirm form shows the cuota's existing payments and balance, and pre-fills the
>      **balance**, not the gross amount.
>    - The server refuses (409, Spanish reason) or explicitly reconciles a payment that would push the
>      total over gross + tolerance. Decide what happens to the prior "Declarado" payment (supersede it
>      or confirm it) and document the decision in tech-ref §19.
>    - The plan page, the panel and the KPIs all agree.
>    - *Otra cuota* marks or excludes already-paid cuotas and shows the chosen cuota and its balance.
>      The pre-filled amount is formatted (`72.375`).
> 2. **Reminder asks for the gross amount, not the balance, and greets the wrong person.**
>    `backend/app/services/notices.py` (~l.363–398) renders `amount_uf` from `gross_amount_uf` and
>    `insured_name(policy)`, but the mail goes to the **pagador**. Repro: with the partial payment
>    above, open *Enviar recordatorio* → *Aviso de mora*. The preview says "Monto: UF 28,80" (the
>    balance is UF 25,00) and "Hola Inmobiliaria Los Andes SpA", while the recipient is
>    `pagos@rentaslosandes…`. *Consultar a la compañía* already uses the balance.
>    **Expected:** the amount is the balance (or "pagado X de Y"), the greeting matches the actual
>    recipient, and a `{{balance_uf}}`/`{{balance_clp}}` variable is added to the template catalogue
>    (Configuración → Cobranza → Mensajes), with es/en labels.
>
> **P1: visible breakage**
> 3. **Bitácora shows raw keys and UTC times.** `/groups/2` → *Bitácora del asegurado*. It shows
>    titles like `case_file.skipped_to` and text like "Etapa policy_issued → active (omitidas:
>    mirror_validation)" and "lead, intake, pre_underwriting…". The writer is in
>    `backend/app/services/case_files.py`, and the frontend renders the text verbatim. Times show
>    **15:29** for a 12:29 (America/Santiago) event, while `/collections/evidence` shows 12:29
>    correctly, so this view formats UTC as local. It also shows "(nunca bloquea, R42)" and "Lead",
>    policy dates render with a fake "00:00", and an entry-with-policy produces about 12 "omitida"
>    lines per account. **Expected:** Spanish labels from the stage catalogue, Chile time, no internal
>    rule codes, and one collapsed line ("La cuenta entró con la póliza; 10 etapas omitidas").
> 4. **The whole app shell scrolls away, leaving a blank screen.** On `/policies/new`, scrolling the
>    wheel over the sidebar moves `html` (measured `scrollTop` 753). The document is 1 476 px tall
>    because the hidden native `<select>`/`<input>`s behind the Radix comboboxes overflow the
>    `h-screen` layout. The same `html` shift happened when focusing fields in Configuración →
>    Cobranza. **Expected:** `html`/`body` never scroll (`overflow: hidden` on the root, or contain the
>    hidden inputs). Check every page with comboboxes.
> 5. **Notifications miss the important things.** The bell's *Por hacer* says "Nada por hacer / Estás
>    al día" while Cobranza has 2 overdue cuotas: HDI 3/12 in *Riesgo de término* after *PAC
>    rechazado*, and PYME 1/3, 15 days late. A **receipt uploaded on the insured's public link
>    creates no notification**; the insurer's report does. *Historial* has two identical "Cuota
>    vencida · póliza 5520-0001". The account summary says "Acciones pendientes: Todo al día" for an
>    account whose cuota is in *Riesgo de término*. **Expected:** every overdue or at-risk cuota and
>    every received receipt yields one *Por hacer* item, with dedup per cuota and event.
>
> **P2: correctness and UX**
> 6. **Mirror validation on a manual policy** (`frontend/src/pages/policies/MirrorDiffPanel.tsx`).
>    It prints "Faltan extracciones confirmadas para comparar: issuance, proposal, policy" (raw enums)
>    **and** a green "La póliza calza con la propuesta". → Show "No aplica: esta póliza no tiene
>    propuesta" in Spanish, with no green state.
> 7. **Share dialog**: after *Revocar*, the "✓ Enlace listo para enviar" block still shows the
>    revoked URL with *Copiar* and *WhatsApp*. Clear or disable it (`locales/es/share.json` and its
>    component).
> 8. **Journey after *Saltar a…***: on a new account, skipping to *Cotizaciones recibidas* omits
>    Antecedentes per the dialog. The rail nevertheless shows Antecedentes with a ✓, while *Activar
>    etapa* lists it as omitted. The status text says "Lead" while the rail says "Antecedentes 1/3".
>    "Ver etapas omitidas (4)" doesn't match the 5 OMITIDA ticks (Mercado has no tab).
>    `frontend/src/components/groups/Journey.tsx`.
> 9. **The *Otras etapas* menu opens off-screen** (`top −253px` at 1440×723). 6 of its 13 items are
>    unreachable. Make it collision-aware or scrollable. Also confirm whether moving *back* from
>    Vigente to *Validación espejo* should be allowed.
> 10. **Pólizas KPI cards stack as filters** (`frontend/src/pages/policies/index.tsx`). Clicking "Sin
>     plan de pago" then "Sin respaldo" gives `?collection=no_plan&missing=source_document` → 0 rows,
>     though the card says 2. Each card should replace the previous card filter.
> 11. **Policy detail header at 1440 px**: the title wraps to "Póliza / CHB- / TT-55102" because 6
>     buttons share the row. Put the title on its own row, or use the mobile "Más" menu.
> 12. **The Cobranza table overflows at 1440 px**: *Estado*, *Última gestión* and **Gestionar** are
>     off-screen. Partial-payment amounts wrap into 5 lines. Keep the action reachable (row click or a
>     sticky column).
> 13. **Online payment errors**: *Link de pago* (staff) and *Pagar* (public insured link) return 502,
>     "No pudimos generar el link de pago. Intenta de nuevo en unos minutos."
>     (`backend/app/services/payments/checkout.py` ~l.458). When Forpay isn't usable, say so plainly
>     and point to transfer or receipt upload. After the insured uploads a receipt, show "Comprobante
>     en revisión" and de-emphasise *Pagar*. Today it stays primary next to the stale error, which
>     invites paying twice.
> 14. **Editing one message template un-approves all of them**, so every reminder broker-wide stops.
>     Approve per message, or keep sending the last approved version until the new one is approved.
> 15. **Loading a payment plan** (policy → *Cargar plan de pago*): the first cuota defaults to the
>     vigencia start (already past), so it is born overdue with no warning. Amounts are proposed with
>     4 decimals (`9,9166`) and no unit. The new plan is left without *Cobranza iniciada* (the panel
>     shows *Iniciar cobranza*), and nothing says so. Warn about past dates, round UF to 2 decimals and
>     offer to start cobranza on save.
> 16. **Portal search run #1 stays "En cola" forever** while RPA is disabled. Show "No se ejecutará".
>     The run detail shows raw step ids (`open_login`, `fill_username`…), an ISO date `2026-10-27` and
>     "VEH-01". The HDI card says both "En mantención" and "no está habilitada en este ambiente".
> 17. **Mantenedor** (`/platform/provider-accounts`): after a failed *Verificar* ("…no tiene la llave
>     pública (public_key_pem)") the card still reads "Activa · Verificada el …". Reflect the last
>     result. Secrets were confirmed never echoed (DOM and API); keep it that way.
>
> **P3: polish (batch these)**
> - Vocabulary:
>   - "Lead" (stage) vs "Interés"; "Pre-suscripción" vs "Presuscripción".
>   - "PAC rechazado"/"Rechazada (PAC)" on a **PAT** cuota.
>   - "RADAL" vs "Radal".
>   - "Modo de cobro: Automático" vs level "Asistida".
>   - The *Renovar* tooltip says "se crea por API".
>   - The *Subir póliza* button opens a dialog titled "Registrar póliza".
>   - "queda En acuerdo".
>   - The error "Elige el ramo y subtipo" shows when only the subtipo is missing; auto-select a
>     single subtipo.
>   - The manual-entry copy "La prima bruta enciende el plan de pago" is wrong (no plan is created).
> - Formats:
>   - RUTs are dotted in Pólizas but undotted in Cobranza, the parties form, email bodies and the
>     inquiry list.
>   - Some UF KPIs use 1 decimal (`UF 75,3`, `UF 0,9`, `En mora UF 0,0`).
>   - The payment pre-fill shows `24,9977`.
>   - The last cuota shows the same `UF 1,83` but `$72.367` vs `$72.375`.
> - Dead or mis-wired controls:
>   - "Mi perfil" in the user menu is disabled with no reason.
>   - The login hint `jose@radalseguros.cl · radal1234` is hardcoded (`frontend/src/pages/auth/Login.tsx:182`)
>     and 401s in the demo world. Drive it from config or drop it.
>   - The parties dialog says "Hay un RUT que no es válido" without marking the field.
>   - The Agente creates an empty "Conversación #1" when the AI call fails.
> - Mobile (375 px):
>   - In Configuración, the active tab is scrolled out of view.
>   - The level tooltip auto-opens on panel open and covers the asegurado name (desktop too).
>   - On the policy card, "SURA-PYME-71830" collides with "UF 29,75".
>   - On the insurer public link, upcoming cuotas' amounts are centred, not right-aligned.
> - Misc:
>   - Duplicate inquiries for the same cuota are allowed silently, and an inquiry stays "Sin enviar"
>     while listing a now-paid cuota.
>   - The Cobranza tabs overlap (Próximas ⊂ Al día) and their counts don't partition; explain them.
>   - The "Consulta creada; el correo no salió" toast is green while the reminder's is orange.
>   - *Post-venta* lists "Caso · 1" and also says "no tiene casos de post-venta".
>   - The "Cobro en línea disponible — HDI ya cobra en línea" notice appears while Forpay is unusable.
>   - The webhook URLs in the Mantenedor use the SPA origin (`localhost:5500/api/...`); confirm they
>     resolve to the backend in dev and prod.
>
> **Already good, so don't regress it:** search by RUT with or without dots; Chilean number parsing
> (`12,5`, `1.234.567`, `2.500`, `150.000`); exact CLP settlement (`986.159` closes the cuota);
> *Revertir* requires a reason; manual-entry validation (gross premium check, RUT mod-11, missing
> subtipo); the closed ramo catalogue (no way to create a ramo); the ejecutivo gets read-only
> Configuración and "Sin acceso" on `/platform`; revoked and broken tokens show a clean Spanish page;
> public pages leak no commissions, notes or other policies; mailbox form validation; unknown template
> variables block saving.
>
> **Verify before saying done:**
> - `cd backend && .venv/bin/pytest -q` is green (baseline: 1 428 tests). Add tests for items 1, 2, 3
>   and 5 at minimum.
> - `cd frontend && npx tsc -b && npm run build && npm run check:locales` are clean, with es complete
>   and en mirrored.
> - Re-run `./review-v12.sh` and re-walk items 1–13 in Chrome (claude-in-chrome) at 1440 px. For
>   375 px, Chrome won't shrink below 500 px, so load the app in a 375 px same-origin `<iframe>`.
>   Screenshot each fix.
> - If any schema changes, add it to `migrate_case_files.py --pass v12` and note it in the v12 handoff
>   "what is owed" list.
> - Finish with a short table, *item → fixed / deferred (why) → how verified*. Report anything you
>   could not verify as not verified.

---

## ORCHESTRATOR CORRECTIONS (2026-10-17, verified against the code — these override the prompt above)

- **Test command:** `cd backend && MEDIA_BACKEND=local .venv/bin/python -m pytest tests -q -p no:cacheprovider`
  (`backend/.env` sets `MEDIA_BACKEND=s3`; conftest does not override it). Baseline 1 428.
- **#5 rewritten.** Notifications go to the account OWNER (team spec: "el ejecutivo de la colocación; sin él, los
  administradores"); in the demo the owner is `ejecutivo@demo.cl`, and the reviewer was logged in as admin. The public
  receipt upload DOES notify (`public_share._notify_evidence` → owner). `review-v12.sh` runs no scheduler, so the cuota
  clock never re-runs after seeding. Real bugs that remain: (a) the duplicate "Cuota vencida" history entry;
  (b) the account summary's *Acciones pendientes* ignores collection state ("Todo al día" while a cuota is in *Riesgo
  de término*) — make collection risk/overdue feed it; (c) **decision: admins get a "Mías / Todas" switch in the bell**
  (backend `GET /notifications?scope=all` for broker admins = every open notification of the broker, de-duplicated
  per origin; executives keep "Mías" only); (d) after a payment/plan write that creates an overdue cuota (e.g. a plan
  loaded with past dates), run the cuota's alert sync immediately instead of waiting for the clock.
- **Forpay is PAUSED by decision** (no sandbox). Do not try to make payments work. Rule to implement: a cuota resolves to
  *En línea* only if its insurer channel's provider account is `active` **and has passed Verificar**
  (`last_verified_at` set and no `last_error` after it). That removes the 502 path on unverified accounts, the false
  "HDI ya cobra en línea" notice (#P3) and fixes #17 (the card must show the last verify result). When not native,
  *Pagar*/*Link de pago* are not offered; the copy points to the insurer's instructions / receipt upload.
- **WhatsApp stays an open door** (disabled "pronto" + the wa.me share button). **Google/Microsoft OAuth deferred.**
- **Product calls decided:** #9 — no moving back once the account is *Vigente* (remove backward transitions from
  `active`; *Otras etapas* must not offer them). #14 — **approval per message**: editing one template un-approves only
  that template; the others keep sending; an unapproved template's slot sends nothing. Duplicate inquiries — refuse a
  second OPEN inquiry for the same cuota (409 with a link to the existing one); an inquiry listing a now-paid cuota shows
  it as paid and the job never sends it. Cobranza tabs — keep the team-spec semantics (cuota tabs vs plan tabs) and say
  so in a one-line helper under the tabs.
- **Webhook URLs** in the Mantenedor must use `settings.public_api_url` (backend origin), never the SPA origin.

---

## Not covered by the 2026-10-17 walk (test these after the fixes)
- There is no "Completar" button on a *Falta* item: the blocks only offered *Subir póliza* and
  *Cargar plan de pago*. Check whether that is intended.
- AI extraction (*Subir póliza*), a real Forpay payment, real mail and WhatsApp, webhook events, a new
  portal search and the agent's replies. All are off in the demo.
- Endosos, siniestros, Portafolio, Analítica, dark mode, exports, and a true touch device.
