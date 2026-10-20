# 13 · Review round 3 fixes — build contract

Branch `fix/review-r3` (worktree `../radal-app-copilot`), from `dev` @ `708097f`. Source: the round-3 computer-use
review on dev (`.review-tmp/r3-report.md` + `.review-tmp/r3-shots/`, Spanish). Owner decisions 2026-10-19 marked
**[D]**. Same rules as `12-review-r2-fixes.md` (English identifiers, Spanish copy, tenant 404s, no dead buttons,
explicit i18n maps, suggest → confirm, ledger-only money, prefer no schema change, structured Spanish errors).

## Backend (agent B3)

R1. **Agent pays the wrong cuota (critical).** `register_payment` (and every write tool that targets a cuota:
`record_payment_promise`, `send_payment_reminder`, `send_status_inquiry`, … — check `services/agent_tools.py`)
takes only an internal `installment_id`; when the cuota is not in `list_installments`' ≤ 30 rows the model guesses
an id (round 2 "Cuota 68", round 3 targeted cuota 5 of V-FL-0091447 when asked for cuota 2 of TRBF-0020126441).
Fix: these tools accept **`policy_number` + `installment_number`** (preferred; resolved server-side within the
broker, exact match on the policy number with normalisation of spaces/dashes/case; ambiguous or missing → the
proposal is REFUSED with a Spanish message the agent relays, never a guess); `installment_id` stays accepted but,
when both are given, they must agree or the proposal is refused. Make `list_installments` accept `policy_number`
and return `policy_number` + `number` per row. Update tool descriptions + the agent prompt to prefer
number-based targeting. The confirm card `display` already shows the resolved policy + cuota. Tests incl. the
exact round-3 scenario.

R2. **Comparison deductibles show a bare "10".** Deductible (and any composite) cells must keep their full
condition text ("10 % de la pérdida, mínimo UF 25") — the numeric `value`/`unit` normalisation in
`comparison_standardize.finalize_dimensions` must not drop the condition: for group `deductible` the cell's display
value is the verbatim condition (keep a parsed numeric only as an extra field). Same for sublimit cells whose
verbatim carries "máx UF …".

R3. **Recommendation attributed HDI's coverage to Southbridge** (non-deterministic). It ran in parallel over the
provisional (unmerged) table. Run the recommendation **after** the alignment over the final table (accept the
extra ~15–40 s); keep insurer names + prima total in its input.

R4. **Propuesta "Tasa integral 4,420 ‰" vs the PDF's 2,21 ‰ + 0,100 ‰.** Find where the comprehensive rate comes
from (read vs computed in promote / broker proposal) and fix it to what the cotización states; never sum rates
that are not additive. Test with the Southbridge cotización facts.

R5. **Reintentar on columns with missing core:** an entry whose read succeeded but has a non-empty `missing_core`
(e.g. old reads without insurer/prima) must expose retry (`can_retry: true`) — and the recommendation must never
invent a prima total (if a column lacks `total_premium_uf`, say so instead of computing one from exempt + IVA).

R6. **Portafolio → Documentos raw values** ("email_message", "status_inquiry"): the API should expose Spanish
labels or the frontend maps them — coordinate: backend returns the enum as is; frontend maps (agent F3).

R7. **[D] Team inboxes stay team-wide:** "Pagos sin confirmar" and "Respuestas por confirmar" (and pending
evidence) are team inboxes — keep them broker-wide in both scopes; the summary payload marks them
`scope: "team"` so the UI can label them "Equipo".

## Frontend (agent F3)

F1. **Comparison at 375 px:** the sticky label column (460 px inside a 330 px container) hides every company
column — make the label column narrow on mobile (e.g. ≤ 40 % width, wrapping labels) or switch to a stacked
per-dimension card layout under `sm`; values always visible. Check 375 and 1440.

F2. **Deductible cells** render the condition text (R2), not a bare number.

F3. **Labels:** agent step/tool names → explicit Spanish map (e.g. `search_document_text` → "Buscó en el
documento", `list_installments` → "Revisó cuotas"…; unknown tool → generic "Consultó datos", never the raw
name); Portafolio → Documentos categories/kinds → explicit Spanish map (e.g. `email_message` → "Correo",
`status_inquiry` → "Consulta de estado"; unknown → "Otro").

F4. **"Equipo" chip** on the team-inbox cards (R7) for every user, with a tooltip "Bandeja del equipo: no se
filtra por Mías".

F5. **Retry visibility:** show *Reintentar* when `can_retry` (R5) — including columns that read but lack prima.

## Verification

Backend suite green (baseline 1 624); frontend `tsc -b` / `check-locales` / `build` clean; tests for every
behaviour change; append "§19.20 Review round 3 fixes" to `docs/technical-reference.md` (re-read before editing).
