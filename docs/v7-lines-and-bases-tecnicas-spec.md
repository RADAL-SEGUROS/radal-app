# v7 — Broker-defined lines + Bases Técnicas (binding spec)

Status: **active build** (2026-09-08). Extends v6. Two model shifts from the team:

1. **A line (ramo) is broker-defined.** The broker authors a *line* — the ramo plus the
   antecedentes data it requires — and **assigns it to an account**. The same ramo can be defined
   differently per insured. Radal ships **recommended global TEMPLATES** (2 for the demo) a broker
   adopts and then freely edits (add/remove/reorder fields, toggle mandatory). The assigned line
   drives (a) what the antecedentes uploader requests, (b) what the AI extracts + **validates**
   (only the line's fields; mandatory flagged), (c) the PDF layout.
2. **"Bases Técnicas" is the completed antecedentes rendered to PDF — no middle step.** The
   expediente view IS the bases-técnicas builder. When the line's mandatory fields are satisfied the
   broker downloads **"Bases Técnicas"** to send. Relabel the UI + PDF accordingly (keep the
   `/antecedentes` API paths internally).

## A. Data model (migrate script; run --apply before push)

- `line_record_schema`: add `is_template Boolean default False NOT NULL`. A **template** = global
  (`broker_id IS NULL`) + `is_template=True`. A **broker line** = `broker_id` set. **Relax
  `UniqueConstraint(broker_id, insurance_line_id, version)`** → allow multiple broker lines per ramo
  (a broker may have "Incendio — Agro" and "Incendio — Retail"). Keep a light unique on
  `(broker_id, name)` (nullable broker_id ok). Keep `insurance_line_id` as the ramo *category*.
- `case_file`: add `line_record_schema_id Mapped[int|None]` FK→line_record_schema SET NULL — the
  **line assigned to the account**. Resolution order for an account's line:
  `case_file.line_record_schema_id` → else `resolve_line_record_schema(insurance_line_id, broker_id)`
  (broker line for the ramo → global template). Add the column via the migrate manifest
  (added_columns on case_file) + run --apply.
- No other schema churn. `record_expediente` unchanged (already links schema + payload + pdf).

## B. Backend

- **Lines API** (`/line-record-schemas`, gated `Settings.Manage` for writes, `View` for reads;
  broker-scoped, foreign→404):
  - `GET ""` → list the broker's lines **and** the global templates, each with **usage**:
    `{ ...LineRecordSchemaRead, is_template, usage: {accounts: int, groups: int} }` where usage counts
    `case_file`s (kind=account) whose resolved line is this row, and distinct `account_group_id`s.
  - `POST ""` create a broker line (optionally `from_template_id` to clone a template's definition).
  - `PUT /{id}` edit; `DELETE /{id}` (block if in use → 409 with the usage count).
  - `POST /case-files/{id}/line` → assign a line to an account (`{line_record_schema_id}`),
    broker-scoped; re-resolving the expediente schema. Gated `CaseFiles.Edit`.
- **Dynamic validation.** In `_antecedentes_read` compute `missing_required: [{section, field, label}]`
  by walking the resolved line definition against the payload (a required field/list that is empty).
  Add to `AntecedentesRead`: `required_fields` (the line's mandatory field list, for the uploader
  checklist) + `missing_required` + `complete: bool` (no mandatory missing). `register` still commits;
  `GET /pdf` returns **409 with a clear message** when `complete` is False (can't send incomplete
  bases técnicas) — the frontend gates the button on `complete`.
- **Bases Técnicas rename (PDF).** Cover eyebrow → "Bases Técnicas" (keep "Expediente ·" prefix off;
  just "Bases Técnicas"); running-header right label → "Bases Técnicas"; PDF filename
  `bases-tecnicas-{caseId}.pdf`; document `original_name` likewise. Keep DocumentCategory
  ANTECEDENTES_PACK.
- **PDF engine additions** (`pdf_templates.py` + `_pdf_sections`):
  - **Table totals row.** A `list` field flagged `"totals": true` renders a `<tr class="total">` after
    the body summing every **money_uf** column (blank for non-numeric columns), with the row label in
    the first column ("Total"). Sum parses Chilean/plain numbers leniently (reuse a small parse).
  - **Two-column mini-tables** already work (list with 2 fields). Ensure `.total` row styling exists.
  - `_pdf_sections`: when a section has BOTH scalar fields and a single `list` with `totals`, keep the
    matrix as its own titled table (that's the ubicaciones×materias table). Money columns right-align.
  - Long text in table cells wraps (already). Section numbering continues to skip callouts.

## C. The two templates (global, is_template=True) — seed verbatim

The seed inserts these as global templates AND clones the Property one into a Fuenzalida (broker 3)
line assigned to the demo Viña accounts (case_file 4 and 6). Field `key`s are English; labels Spanish.

**Template 1 — "Incendio y Sismo (TRBF)"** (`insurance_line_id=1`). Sections (this encodes José
Francisco's feedback):
1. `insured` "Identificación del asegurado": legal_name*(text), rut*(text), trade_name(text),
   activity(text "Giro / actividad"), commercial_address(text "Dirección comercial"), commune(text),
   region(text), contact_name(text), contact_email(text), contact_phone(text).
2. `insured_matters` "Ubicaciones y materias aseguradas": ONE list field `rows` (repeatable,
   `"totals": true`) with nested fields: location*(text "Ubicación"), commune(text "Comuna"),
   description(text "Descripción"), building_uf(money_uf "Edificio"), machinery_uf(money_uf
   "Maquinaria y equipos"), vessels_uf(money_uf "Cubas y estanques"), furniture_uf(money_uf "Muebles y
   útiles"), stock_uf(money_uf "Existencias"), bi_uf(money_uf "Perjuicio por paralización"). The PDF
   renders ubicaciones down the left, each materia asegurada as a money column, a **Total row**
   (column sums) and grand total.
3. `total_insured` "Monto total asegurado": total_insured_uf*(money_uf "Monto total asegurado") —
   the result of the matrix (human-confirmed).
4. `sublimits` "Coberturas y sublímites solicitados": list `rows` fields coverage*(text "Cobertura"),
   sublimit(text "Sublímite").
5. `deductibles` "Deducibles solicitados": list `rows` fields coverage*(text "Cobertura"),
   deductible(text "Deducible").
6. `indemnity` "Límite de indemnización": indemnity_limit(text "Límite de indemnización") — short
   (e.g. "Full value" / UF); do NOT dump the whole coverage paragraph here.
7. `vigencia` "Vigencia": start_date*(date "Desde"), end_date*(date "Hasta"). PDF renders "desde las
   12:00 h del {Desde} hasta las 12:00 h del {Hasta}".
8. `commission` "Comisión del corredor": commission_pct(percent "Comisión del corredor").
9. `loss_history` "Siniestralidad": has_claims(boolean "¿Registra siniestros?") + list `items`
   (date, peril "Evento", description, amount_uf(money_uf "Monto"), status "Estado"). One section.
10. `protections` "Medidas de protección": list `items` fields measure(text "Medida"), detail(text
    "Detalle"). Rendered as a table/bullets, not a paragraph. (No observaciones section.)

**Template 2 — "Responsabilidad Civil"** (`insurance_line_id=2`). Sections: `insured` (same identity,
commercial_address), `operation` "Actividad y operación" (activity, annual_revenue_uf(money_uf),
employees(integer), territory(text "Territorialidad"), operations_description(text)),
`sublimits` (coverage/sublimit list), `deductibles` (coverage/deductible list), `indemnity`
(indemnity_limit), `vigencia` (start_date/end_date), `commission` (commission_pct),
`loss_history` (has_claims + items). No materias matrix.

## D. Frontend

- **Líneas manager** (rename the Settings "Esquemas de ramo" tab → **"Líneas"**, still `Settings.Manage`):
  a list of the broker's lines + templates, each showing the ramo, a **Plantilla** badge for
  templates, and **usage** ("N cuentas · M grupos"). Actions: **Crear línea** (from a template picker
  — "Usar plantilla" prefills, then editable — or from scratch), edit (the existing section/field
  editor, with a **mandatory** toggle per field and field-type select incl. the matrix `list` +
  `money_uf`), clone a template to a broker line, delete (disabled with reason when in use). Great UX:
  template gallery on create, inline add/remove/reorder fields, clear mandatory markers.
- **Account line assignment.** On the account page's Bases-Técnicas tab header, show the **assigned
  line** (name + ramo) with a "Cambiar línea" control (pick from the broker's lines for that ramo, or
  adopt a template → clones a broker line + assigns). Uses `POST /case-files/{id}/line`.
- **Bases Técnicas view** (rename ExpedienteView + the account tab label to "Bases Técnicas"):
  - A **requisitos** panel = the line's `required_fields` as a checklist, ticking satisfied vs
    **missing** (from `missing_required`) — "what the line requests", visible before/after processing.
  - The **Descargar Bases Técnicas** button, gated on `complete` (DisabledHint listing what's missing
    when not). "Procesar antecedentes" CTA unchanged; the review (AntecedentesReview) flags mandatory.
  - Show the assigned **line name** in the header.
- **Overview**: each account/ramo card shows its assigned **line name** (fall back to ramo).
- **Account creation**: keep the ramo multi-select; after creation the account gets the broker's
  default line for that ramo auto-resolved (assignment editable later). (No new picker required for
  the demo — assignment lives on the account page.)
- i18n: extend the `antecedentes` + `settings` namespaces (es first, en mirror): `basesTecnicas.*`,
  `lines.*` (manager + template picker), `requisitos.*`, `assign.*`. check:locales green.

## E. Verify + eval

- migrate dry-run shows the `case_file.line_record_schema_id` ALTER + `is_template` ALTER; --apply
  before push. Hermetic pytest green (mock `_chat_model`, stub `render_html_to_pdf`). tsc/build/
  check:locales clean.
- Seed: 2 templates + Fuenzalida Property line assigned to case_file 4 & 6.
- Live eval (orchestrator does this): re-process case 4 → the AI fills the redefined line (matrix
  rows, sublímites, deducibles, vigencia, comisión); review flags any mandatory missing; register;
  Descargar Bases Técnicas → the reformatted PDF (materias matrix + totals, two coverage tables,
  vigencia/comisión numerals, merged siniestralidad, medidas list). Iterate the PDF visually.
