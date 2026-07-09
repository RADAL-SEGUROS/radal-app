# Radal — Architecture

This document explains the conceptual model behind the corredora app: the **expediente backbone**,
who owns what, the "subir vs enviar" distinction, how processes are **dynamic by ramo**, and how the
model leaves room for a future versioned / multi-actor extension without a rewrite.

> Physical tables & columns: see `docs/data-model.md` (authoritative).
> REST endpoints: see `docs/api-contract.md` (authoritative).

## 1. The two-level expediente backbone

Radal organizes everything around an **expediente** (case file) with two levels, then hangs flat
operational tables off it so the UI has direct, fast reads.

```
corredora (tenant)
  └─ cliente            NIVEL-1 expediente  (the asegurado AS a client of the corredora)
       └─ activo        NIVEL-2             (an insurable good: plant, building, fleet, etc.)
            └─ proceso_ramo                 ("carpeta operativa" of the activo for one ramo)
                 ├─ poliza / cotizacion / oferta
                 ├─ siniestro
                 └─ solicitud_inspeccion / inspeccion
```

- **cliente (NIVEL-1)** — the client relationship. Sector, estado (activo/onboarding/prospecto/
  suspendido/archivado), contacto, ejecutivo asignado. One client has many activos.
- **activo (NIVEL-2)** — a concrete insurable good owned by the client (`tipo_activo`, `direccion`,
  flexible `atributos` JSON). One activo has many proceso_ramo folders (one per ramo it's insured under).
- **proceso_ramo** — the operational folder that tracks an activo through one ramo's lifecycle
  (`creado → en_inspeccion → en_presuscripcion → en_cotizacion → en_negociacion → asegurado →
  en_siniestro → cerrado`). This is where inspection, quoting, offers, and the resulting póliza converge.

### Flat operational tables (why they exist)

The UI needs list/detail screens for pólizas, renovaciones, cotizaciones, siniestros, and inspecciones
directly. Those tables carry their own FKs (`cliente_id`, `activo_id`, `proceso_ramo_id` nullable) so a
screen can be built without always walking the full tree. The tree is the **backbone**; the flat tables
are **fast paths**. Both are kept consistent via the shared IDs.

## 2. Ownership: the corredora owns every expediente

- Every domain row carries `corredora_id`. The corredora (broker) is the **owner** of the cliente,
  the activo, every proceso_ramo, and every póliza/cotización/siniestro/inspección.
- asegurado and aseguradora users are **guests** on the corredora's expedientes. They are provisioned
  only after a deal closes (asegurado) or an aseguradora is invited into a process (aseguradora).
- Access control is role-based (`usuario.rol`). Corredora roles see everything in their tenant;
  asegurado/aseguradora roles (future) see only the slices shared with them.

## 3. "Subir" vs "Enviar" (upload vs send/share)

A deliberate distinction that shapes document and offer flows:

- **Subir (upload)** — an internal action. A corredora user attaches a `documento` or drafts an
  `oferta` (`estado = borrador`) that stays private to the corredora. Nothing leaves the tenant's view.
- **Enviar (send/share)** — an explicit, auditable action that exposes something to another actor.
  Sending an offer moves it to `estado = enviada`; sharing a document sets `documento.compartido_con`
  (a list of roles). "Enviar" is what makes an asegurado/aseguradora able to see a row.

This keeps a safe internal workspace ("subir") separate from the moment of external commitment ("enviar"),
and every "enviar" is logged in `actividad`.

## 4. Dynamic by ramo (config-driven forms & rules)

The **`ramo`** table is configurable **per corredora**. A ramo carries:

- `requiere_inspeccion` (bool) — whether the process must pass through an inspection stage.
- `campos_min` (JSON) — the minimum fields required to open a process / capture the activo for this ramo.
- `reglas_presuscripcion` (JSON) — pre-underwriting rules evaluated before quoting.
- `campos_comparador` (JSON) — which fields drive the offer comparator for this ramo.

Because these live in config (not code), the **forms, required fields, validation rules, and the offer
comparator are derived at runtime from the ramo config**. Adding a new ramo, or changing what
"Incendio/Sismo" requires, is a data change — not a code change. Each `proceso_ramo` reads its `ramo_id`
config to know which fields to collect, whether an inspection is mandatory, and how to compare offers.

Example: "Incendio/Sismo" has `requiere_inspeccion = true`, so its proceso_ramo flows through
`en_inspeccion` before `en_cotizacion`. "Responsabilidad Civil" may skip inspection entirely.

## 5. Coaseguro (co-insurance)

A póliza can be co-insured: one **líder** aseguradora plus participants, tracked in
`coaseguro_participacion` (`aseguradora_id`, `es_lider`, `porcentaje`, summing to 100). The flagship
seed póliza (POL-2024-0072) is Chubb 60% líder + HDI 25% + Mapfre 15%.

## 6. Future extension (versioned / multi-actor) — designed-in, not built now

The model is intentionally shaped so the deeper vision is an **extension, not a rewrite**:

- **Versioning / audit** — `inspeccion.version`, `documento.version`, and the `actividad` audit log are
  already present. A future pass adds full row-level version history (temporal tables or version chains)
  without changing the backbone. Nothing today assumes rows are immutable-single-version.
- **Multi-actor collaboration** — `documento.compartido_con` (roles) and `observacion` (per-entity
  comments) already model cross-profile sharing. When asegurado/aseguradora profiles ship, they attach
  to the SAME expediente rows via these mechanisms; no schema fork.
- **Offers & negotiation** — `oferta` links to either `cotizacion_id` or `proceso_ramo_id`, so the
  negotiation surface can deepen (rounds, adjustments) inside the existing shape.

The rule of thumb: **corredora screens built now must not bake in assumptions that block the asegurado
and aseguradora profiles later.** Keep `corredora_id` scoping, role checks, and the "enviar" gate honest.
