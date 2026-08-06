# Radal v2 — Data modeling decisions (evidence-based)

> Derived from the team's real dataset (`radal-data-mvp 2`: 3 tenants, 240 rows, 66 files).
> Every decision below cites what the data actually shows. Companion to `v2-architecture.md`.

## The rule applied

| Evidence in the data | Structure |
|---|---|
| Shape is **fixed** across all rows **and** we filter/sort/compare on it | **Columns** |
| Shape **varies** by asset type / insurance line | **JSON** |
| **1:N collection** we must sum, compare or filter | **Child table** |
| A **file** | Row in `document` + FK from the entity |

---

## 1. Files & S3 — one source of truth

**Evidence:** 66 files on disk ↔ 66 rows in `documento` — exact 1:1, zero orphans either way.
But entities *also* carry raw S3 strings: `oferta.documento_s3`, `inspeccion.informe_s3`,
`proceso_ramo.bases_tecnicas_s3` — all 15 of which **duplicate** a `documento` row.

**Decision:** `document` is authoritative. Entities **never store a raw S3 key**.
Where a document is required, the entity holds a **FK to `document.id`**.

```
document
  id, broker_id, entity_type, entity_id, s3_key (unique), bucket,
  original_name, mime_type, size_bytes, category, phase,
  uploaded_by_id, created_at

proposal.source_document_id   FK → document.id   NOT NULL   -- mandatory context
inspection.report_document_id FK → document.id   NULL
placement.brief_document_id   FK → document.id   NULL
```

**Why not a string column:** the file's metadata (who uploaded it, when, mime, size, category)
must live in exactly one place. A bare string drifts from the row, can't be joined, and gives no
audit trail. A FK gives referential integrity and one lifecycle for S3 cleanup.

**Observed categories** (13 → enum): `cmf_certificate, appointment, logo, asset_sheet, asset_photo,
valuation, insured_amounts, evidence, inspection_report, technical_brief, claims_history,
proposal, power_of_attorney`.

**S3 key is derived, never free-form:** `documents/{entity_type}/{entity_id}/{category}-{n}.{ext}`.

---

## 2. Proposal money → **COLUMNS** (not JSON)

**Evidence:** `prima` has exactly **1 shape across all 9 proposals**
(`afecta_uf, exenta_uf, neta_uf, iva_uf, total_uf`); `tasas_pormil` likewise
(`afecta, exenta, comprensiva`).

**Decision:** flatten to columns — this is the comparator's core, so it must be sortable,
filterable and aggregatable in SQL.

```
proposal
  taxable_premium_uf, exempt_premium_uf, net_premium_uf, vat_uf, total_premium_uf
  taxable_rate_permille, exempt_rate_permille, comprehensive_rate_permille
  commission_pct, validity_business_days
  coverage_start, coverage_end          -- from vigencia {desde,hasta}
```

Invariants enforced in the model layer (from the team's handoff):
`net = taxable + exempt` · `vat = 0.19 × taxable` (**not** on net — earthquake is VAT-exempt) ·
`total = net + vat` · `comprehensive_rate = taxable_rate + exempt_rate`.

---

## 3. Deductibles → **JSON keyed by peril**

**Evidence:** 1 shape here (`incendio, sismo, otros`) — **but all three quotes are fire/property**.
Transport, fleet and cyber have entirely different perils.

**Decision:** JSON, structured (not free text):
```json
{"fire": {"basis":"loss","pct":5,"min_uf":25},
 "earthquake": {"basis":"insured_amount","pct":1,"min_uf":300},
 "other": {"basis":"fixed","min_uf":15}}
```
The source data is prose (`"5% de la pérdida, mínimo UF 25"`). **Parsing that into this structure is
an AI-extraction target** — and it's exactly where the comparator earns its value, because fire is
`% of loss` while earthquake is `% of the insured amount of the affected item`.

---

## 4. Coverages / exclusions → **CHILD TABLE**

**Evidence:** free-text lists, 6–8 coverages and 5–6 exclusions per proposal.

**Decision:** `proposal_coverage(proposal_id, kind ∈ {coverage,exclusion}, text, normalized_code NULL, sort_order)`

**Why not a JSON array:** the comparator must **align items across competing proposals**
("who covers earthquake?"). That's a join, not a blob. `normalized_code` starts NULL and gets
populated by AI normalization later — impossible to add cleanly inside a JSON array.

---

## 5. Quote line items (`partidas`) → **CHILD TABLE**

**Evidence:** 3 items per quote (`partida, valor_uf, detalle`), and
**Σ valor_uf == valor_declarado verified true for all 3 quotes**.

**Decision:** `quote_line_item(quote_request_id, name, value_uf, detail, sort_order)`.
`quote_request.declared_value_uf` remains a stored column, validated against the sum.

---

## 6. Asset attributes → **HYBRID** (common columns + JSON)

**Evidence:** 3 assets, 3 **different** key sets:
- `planta_industrial` → + `refrigeracion`
- `centro_distribucion` → + `altura_rack_m`, `muelles`
- `clinica` → + `camas`, `pabellones`, `gases_medicinales`

**10 keys are common to all three:** `superficie_construida_m2, superficie_terreno_m2,
ano_construccion, estructura, pisos, actividad, proteccion_incendio, energia,
distancia_bomberos_km, sismicidad`.

**Decision:** promote the 10 shared ones to **columns** (they drive underwriting, filtering and
comparison); keep the type-specific tail in `attributes` JSON, shaped by
`insurance_line.min_fields`.

```
asset
  built_area_m2, land_area_m2, construction_year, structure, floors,
  activity, fire_protection, power_supply, fire_station_distance_km, seismic_zone,
  attributes (JSON)   -- type-specific remainder
```

**Why hybrid, not pure JSON:** you will filter by construction year, area and fire protection.
**Why not all columns:** the tail is genuinely open-ended and line-driven — it must stay dynamic.

---

## 7. Inspection: scores → **COLUMNS**, checklist → **JSON**

**Evidence:** `puntajes` has **1 fixed shape** (8 numeric/classification keys) across all 3
inspections. `checklist` has **3 different section sets** across the same 3 inspections
(sections follow the asset type: *Refrigeración (NH3)* only appears on the plant).

**Decision:**
```
inspection
  technical_score, commercial_score, location_score, loss_estimate_score,
  overall_score, pml_pct, eml_pct, risk_classification     -- COLUMNS (filter: score < 70)
  checklist (JSON, versioned — the data already carries checklist.version)
  visit_date, report_date, folio, findings_summary
inspection_boundary(inspection_id, orientation, description, distance, aggravating)  -- CHILD
```
`colindancias` becomes a child table: it's a queryable 1:N (`orientacion, colindancia, distancia,
agravante`), and aggravating boundaries matter to underwriting.

---

## 8. Insurance line ↔ CMF codes → **JUNCTION TABLE**

**Evidence:** `ramo.codigos_cmf` (list[int]) + `codigos_compuestos` (list[str]) referencing the
official 44-row FECU/CMF taxonomy.

**Decision:** `insurance_line_cmf_code(insurance_line_id, cmf_line_id, composite_code)` — a real FK
to `cmf_line` instead of loose integers, so the taxonomy stays authoritative and joinable.

---

## 9. Everything else → plain columns

`broker, insured, insurer, user, client, claim, activity, note` are flat in the source data and
stay flat. Two notes:
- `siniestro.participacion` (list[str]) → JSON; claims are out of scope this pass anyway.
- `corredora.nota_registro` documents a real edge case (a broker shown as **eliminated** in the CMF
  registry but seeded active) → keep `cmf_status` + `registry_note` so it can gate operations later.

---

## 10. What the dataset does **not** cover (v2 additions)

The data was generated for the v1 model. These have no fixtures yet and must be seeded synthetically
or requested next round:

| Missing | Needed for |
|---|---|
| `insurer.is_native` + `native_insurer_profile` | native vs external split |
| `insurer_contact` (per broker, per line) | routing quote requests |
| `extraction` (AI job + confidence + raw output) | proposal pre-fill audit |
| `offering` (shareable package, token, PDF) | WhatsApp/email delivery |
| `agent_thread` / `agent_message` | AI chat |
| `insured_account_request` | manual account claim |
| Proposals from an **external** insurer | the tracking signal |

Note the dataset's 9 proposals all come from **catalog** insurers — so **every proposal is
effectively "native"**. We need at least one external-insurer proposal to exercise that path.

---

## 11. Import mapping (their Spanish keys → v2 English model)

Their JSON keys stay as-is; the importer maps. No need for another round-trip.

| Source | → v2 | Note |
|---|---|---|
| `corredora` | `broker` | Radal is no longer one of these |
| `cliente` | `client` (broker↔insured) + `insured` | RUT is the join key |
| `asegurado` | `insured` | canonical |
| `aseguradora_entidad` | `insurer` | + `is_native=true` (they're catalog cos.) |
| `aseguradora` (25) | `insurer` seed | official CMF list |
| `activo` | `asset` | 10 attrs promoted to columns |
| `proceso_ramo` | `placement` | `estado=adjudicado` → `awarded` |
| `cotizacion` (+`partidas`) | `quote_request` + `quote_line_item` | |
| `oferta` | `proposal` (+`proposal_coverage`) | money flattened |
| `inspeccion` | `inspection` (+`inspection_boundary`) | scores flattened |
| `documento` | `document` | becomes the only file reference |
| `vinculo_*` | `client` / broker↔insurer link | no access code needed |
| `usuario` | `user` | subroles remapped to English roles |
