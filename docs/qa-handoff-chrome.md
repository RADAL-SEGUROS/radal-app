# QA handoff — testing the live broker journey with Claude-in-Chrome

The Radal v9 broker journey was rebuilt and deployed to **https://dev.radalseguros.cl**. This is a
**ready-to-paste prompt** for a fresh Claude Code session to systematically test the live app through
the **Claude-in-Chrome extension**, plus a reference appendix. Paste the block under **"PROMPT"** into
a new session (with the Chrome extension connected). The appendix is context you can keep open.

---

## PROMPT (paste this into a new Claude Code session)

> You are QA-testing the **live** Radal dev deployment at **https://dev.radalseguros.cl** by driving my
> Chrome browser with the **claude-in-chrome** extension. Verify by **taking a screenshot and looking**
> at every step — never assert a result you didn't see on screen. Do NOT push code, deploy, reset any
> database, or change AWS. Your job is to walk the broker journey, confirm what works, and **log bugs**.
>
> **Connect Chrome first:** invoke the `claude-in-chrome` skill, load the browser tools via ToolSearch
> in one call (`select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__read_console_messages,mcp__claude-in-chrome__list_connected_browsers,mcp__claude-in-chrome__select_browser,mcp__claude-in-chrome__browser_batch,mcp__claude-in-chrome__file_upload`),
> then `list_connected_browsers` → ask me which browser (list every one as an option + the "open a
> confirmation screen in every extension" option) → `select_browser` → `tabs_context_mcp` → navigate.
> Use `browser_batch` to batch clicks/types/screenshots. Use `read_console_messages` (onlyErrors) to
> catch JS errors. If a browser tool fails 2–3 times or a page won't load, STOP and ask me.
>
> **Login:** `usuario11@fuenzalidasr.cl` / `radal1234` (Fuenzalida broker admin). The env is a **blank
> baseline** — you create the data live. (Note: login works through the real frontend; a raw curl to the
> API fails with an AWS-signature error because of CloudFront OAC — that is expected, not a bug.)
>
> **Walk the journey in this order, screenshotting and checking each. Log every bug (format below).**
> 1. **Client (empresa) by RUT** — Clientes → "Nuevo cliente": create Viña Santa Alicia `96.688.830-K`
>    and Viña Indómita `99.568.600-7`. Confirm the RUT is validated + normalized (BODY-DV) and the
>    client is created.
> 2. **Group** — "Nuevo grupo": pick an icon (Emoji/Símbolo/Imagen — try each; check the initials
>    fallback when none is chosen), name it, and **add the empresas** via "Empresas que se incorporan"
>    (the picker only finds clients that already exist — see KNOWN BUG #1). Create it.
> 3. **Account (grupo-cuenta)** — "Nueva cuenta": set the vigencia, confirm the **Contratante** shows the
>    empresa, open the **Ramo dropdown** (should list Incendio y Sismo (TRBF) / RC-Ingeniería-Transporte /
>    a "Crear ramo" drawer — check the drawer is name + recommended-files only, NOT a field/section
>    editor), pick Incendio, create.
> 4. **Account view** — confirm: the **Journey rail is only on Resumen** (not on other tabs) and looks
>    good; the tabs are exactly Resumen · Antecedentes · Bases Técnicas · Comparación · Propuesta ·
>    Pólizas · Renovación · Notas · Bitácora (NO "Cotizaciones"/"Propuestas" duplicates); pending-action
>    text is **Spanish**; the **left-sidebar vigencia expands** into a journey submenu.
> 5. **Antecedentes** — confirm the ramo's **recommended-file slots** show (Slip de T&C, Desglose de
>    montos, Siniestralidad, Informe), two subtabs **Archivos / Extracción**, and a free "Otro archivo".
>    Upload a real PDF/Word into a slot (use `file_upload`); confirm **Excel is rejected** with a Spanish
>    message; confirm **download buttons actually download**. Confirm **no cotización files** appear here.
>    Run the AI reader ("Procesar antecedentes" / the process action) and confirm an extraction appears in
>    the Extracción subtab — NOTE: this is a live GLM call and may take **1–3+ minutes**; if it times out
>    (buffered Lambda), that is a KNOWN RISK to log, not a hang to abort.
> 6. **Bases Técnicas** — register, confirm it renders the completed antecedentes, and that **"Descargar
>    Bases Técnicas" downloads a PDF** (does not just change the URL).
> 7. **Comparación** — upload 2–3 insurer cotización PDFs one at a time; confirm each extracts, a
>    wrong-file is flagged, "Realinear" produces a standardized table with a **named AI recommendation**,
>    and "Promover a propuesta" works (it will ask for the insurer RUT/CMF — supply it).
> 8. **Propuesta** — generate the propuesta from the comparison, ratify, and download its PDF.
> 9. **Pólizas** — upload a policy PDF; confirm validate-then-dynamic (a non-policy → "no es una póliza"
>    422 with an override), the policy commits, and the overview lists it + opens the original.
>
> **Reproduce/confirm the known bugs (below) and report anything new.** For each bug give: the flow, exact
> steps, expected vs actual, and a saved screenshot (`save_to_disk: true`). At the end, give me a
> prioritized bug list (blocking → minor) and a short "what works" summary.

---

## Reference appendix

### Known bugs to reproduce (found in the first live pass, 2026-09-09)
1. **[BLOCKING] Can't add a company to a group.** Group creation's "Empresas que se incorporan" only
   searches *pre-existing* clients (no inline create-by-RUT); "Editar grupo" has **no** empresas section;
   the group's "Empresas" tab has **no add button**. So an empty group is unrecoverable, and the only
   working path is Clientes → Nuevo cliente **first**, then create the group and add them at creation.
2. **[minor]** Account-new shows "RUT asegurados · El grupo sólo tiene este RUT" even when the group has
   zero empresas.
3. **[watch]** AI stages (antecedentes extraction, comparison align) run **minutes** on the buffered
   Lambda — CloudFront/Function-URL may time out on the slowest calls. Do NOT "fix" by changing the
   invoke mode (OAC constraint 4). Log any timeout.
4. **[known-not-done]** Vision fallback for scanned/image antecedentes is stubbed (flags `needs_vision`).
5. **[known]** Money-core extraction is unreliable under tool-calling (0–1 of 5 premium fields).
6. **[minor]** Resumen KPI cards still labeled "Cotizaciones/Propuestas".

### Credentials & test data
- Broker admin: `usuario11@fuenzalidasr.cl` / `radal1234` (Fuenzalida). Full roster:
  `docs/usuarios-de-prueba.md`.
- RUTs: Viña Santa Alicia `96.688.830-K`, Viña Indómita `99.568.600-7`.
- Upload files (Incendio antecedentes + cotizaciones + policy): the corpus at
  `~/Downloads/EXPEDIENTES DEMO/GRUPO VIÑA INDOMITA/VIÑA SANTA ALICIA/` — must be on the machine running
  Chrome to upload via `file_upload`. Antecedentes accept **PDF/Word only** (Excel rejected).

### Claude-in-Chrome mechanics (reminders)
- Screenshots are how you verify — look, don't assert. Browsers are read-only under raw computer-use;
  the claude-in-chrome extension is what can click/type.
- Do not trigger native JS `alert/confirm/prompt` dialogs — they freeze the extension.
- Batch with `browser_batch`; coordinates in a batch refer to the screenshot taken *before* the batch.
- The as-built journey + the earlier (v8) errors-and-corrections log: `docs/handoff-2026-09-09-v9.md`.
