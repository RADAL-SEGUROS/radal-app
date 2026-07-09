# Radal — API Contract (authoritative)

Base path: **`/api/v1`**. JSON in/out. Auth: **JWT Bearer** (`Authorization: Bearer <access_token>`).
Every request is **tenant-scoped** to the authenticated user's `corredora_id` (from the JWT). Money is
UF numeric; percentages 0–100; dates ISO `YYYY-MM-DD`; timestamps ISO 8601 UTC.

Backend AND frontend module agents build **strictly** to this contract.

## Conventions

- **List endpoints** accept `?q=`, `?estado=`, `?page=` (default 1), `?page_size=` (default 25), plus
  resource-specific filters noted below. They return:
  ```json
  { "items": [ ... ], "total": 0, "page": 1, "page_size": 25 }
  ```
- **Errors**: `{ "detail": "mensaje" }` with standard HTTP status (400/401/403/404/409/422).
- **Ownership**: create/update bodies never set `corredora_id`; the server injects it from the JWT.
- Objects below show representative fields; full column meaning is in `docs/data-model.md`.

---

## Auth — `/auth`

### POST `/auth/login`
Purpose: exchange credentials for tokens.
Request: `{ "email": "jose@radalseguros.cl", "password": "radal1234" }`
Response `200`:
```json
{
  "access_token": "jwt...",
  "refresh_token": "jwt...",
  "token_type": "bearer",
  "usuario": { "id": 1, "nombre": "José Pérez", "email": "jose@radalseguros.cl",
               "cargo": "Ejecutivo Comercial", "rol": "ejecutivo_corredora", "corredora_id": 1 }
}
```

### POST `/auth/refresh`
Request: `{ "refresh_token": "jwt..." }`
Response: `{ "access_token": "jwt...", "refresh_token": "jwt...", "token_type": "bearer" }`

### POST `/auth/logout`
Request: `{ "refresh_token": "jwt..." }` → `204`.

### GET `/auth/me`
Response: the current `usuario` object (as embedded in login) + `corredora` summary
`{ "id", "nombre", "logo_url" }`.

---

## Dashboard — `/dashboard`

### GET `/dashboard`
Purpose: the single no-scroll dashboard payload for the logged-in colaborador.
Response `200`:
```json
{
  "saludo": { "periodo": "manana|tarde|noche", "dia_semana": "miércoles", "nombre": "José" },
  "kpis": {
    "polizas_vigentes": 10,
    "clientes_activos": 4,
    "renovaciones_activas": 6,
    "pipeline_ponderado_uf": 5440.0
  },
  "requiere_atencion": [
    { "tipo": "renovacion|cotizacion|siniestro|inspeccion", "id": 3,
      "titulo": "REN-001 Agrícola Los Robles", "detalle": "Vence en 45 días", "url": "/renovaciones/3" }
  ],
  "principales_clientes": [
    { "id": 1, "nombre": "Agrícola Los Robles S.A.", "sector": "Agroindustria",
      "prima_total_uf": 1800.0, "polizas_vigentes": 3, "estado": "activo" }
  ],
  "actividad_reciente": [
    { "id": 20, "accion": "Oferta recibida", "descripcion": "HDI ofertó POL-2024-0072",
      "usuario": "José Pérez", "rol": "ejecutivo_corredora", "created_at": "2026-07-07T14:02:00Z" }
  ],
  "proximas_renovaciones": [
    { "id": 3, "codigo": "REN-001", "cliente": "Agrícola Los Robles S.A.",
      "fecha_vencimiento": "2026-08-20", "dias_restantes": 43, "prima_defender_uf": 900.0,
      "estado": "cotizando" }
  ]
}
```
KPIs/aggregates: `polizas_vigentes` (count póliza estado=vigente), `clientes_activos`
(count cliente estado=activo), `renovaciones_activas` (renovacion estado ≠ terminal),
`pipeline_ponderado_uf` (sum of estimated primas in pipeline). `actividad_reciente` returns last 2 by
default; `?limit=10` returns last 10 (accordion). `requiere_atencion` = top 3 pendientes for the user.

---

## Search — `/search`

### GET `/search?q=<term>`
Purpose: real-time global search, categorized, clickable to detail.
Response `200`:
```json
{
  "clientes":  [ { "id": 1, "nombre": "Agrícola Los Robles S.A.", "url": "/clientes/1" } ],
  "polizas":   [ { "id": 7, "numero_poliza": "POL-2024-0072", "cliente": "Agrícola Los Robles S.A.",
                   "url": "/polizas/7" } ],
  "activos":   [ { "id": 4, "nombre": "Planta Talca", "cliente": "Agrícola Los Robles S.A.",
                   "url": "/clientes/1" } ]
}
```
Optional `?categorias=clientes,polizas,activos` to limit. Each item includes a `url` for navigation.

---

## Clientes — `/clientes`

### GET `/clientes`
Filters: `q`, `estado`, `sector`, `ejecutivo_id`.
Item shape:
```json
{ "id": 1, "nombre": "Agrícola Los Robles S.A.", "rut": "...", "sector": "Agroindustria",
  "estado": "activo", "contacto_principal": "...", "telefono": "...", "email": "...",
  "ejecutivo": { "id": 1, "nombre": "José Pérez" }, "polizas_vigentes": 3,
  "prima_total_uf": 1800.0, "fecha_alta": "2024-01-10" }
```
Aggregates per row: `polizas_vigentes`, `prima_total_uf`.

### GET `/clientes/{id}`
Purpose: NIVEL-1 detail with nested backbone.
Response adds: `activos` (each with `proceso_ramos`), `polizas` (summary), `asegurados_adicionales`,
`siniestros` (summary), plus rollups `kpis: { polizas_vigentes, prima_total_uf, suma_asegurada_uf,
activos_count, siniestros_abiertos }`.

### POST `/clientes`  → create. Body: nombre, rut, sector, estado, contacto_principal, telefono, email, ejecutivo_id, fecha_alta.
### PATCH `/clientes/{id}` → partial update (same fields).
### DELETE `/clientes/{id}` → `204` (soft: sets estado=archivado).

### Nested activos
- GET `/clientes/{id}/activos` → list activos for the cliente.
- POST `/clientes/{id}/activos` → create activo (tipo_activo, nombre, direccion, atributos, estado).
- PATCH `/activos/{activo_id}` / DELETE `/activos/{activo_id}`.

---

## Pólizas — `/polizas`

### GET `/polizas`
Filters: `q`, `estado` (vigente/no_vigente), `cliente_id`, `ramo_id`, `aseguradora_id`, `tiene_coaseguro`.
Item shape:
```json
{ "id": 7, "numero_poliza": "POL-2024-0072", "cliente": { "id": 1, "nombre": "Agrícola Los Robles S.A." },
  "ramo": { "id": 1, "nombre": "Incendio/Sismo" },
  "aseguradora": { "id": 1, "nombre": "Chubb Generales" }, "tiene_coaseguro": true,
  "prima_uf": 900.0, "comision_pct": 15.0, "suma_asegurada_uf": 9000.0,
  "tipo_cobertura": "optima", "cobertura_pct": 100.0, "cobertura_estado": "optima",
  "vigencia_inicio": "2024-06-01", "vigencia_fin": "2025-06-01", "estado": "vigente" }
```
`cobertura_estado` derived: `<95` infracobertura, `95–105` optima, `>105` sobrecobertura.

### GET `/polizas/{id}`
Adds: `coaseguro_participaciones` (aseguradora, es_lider, porcentaje), `ubicaciones`
(nombre, direccion, suma_asegurada_uf, porcentaje), `coberturas` and `exclusiones`
(from cobertura_item split by tipo), `deducible_texto`, `limite_indemnizacion_uf`,
`plan_pago: { cuotas, metodo }`, `pago_url`, `documentos`, `observaciones`.

### GET `/polizas/summary`
KPIs for the list header:
```json
{ "polizas_vigentes": 10, "prima_total_uf": 5440.0, "suma_asegurada_total_uf": 65700.0,
  "con_coaseguro": 3 }
```

### POST `/polizas` → create. Body includes all póliza fields plus nested `coaseguro_participaciones[]`,
`ubicaciones[]`, `coberturas[]`, `exclusiones[]`.
### PATCH `/polizas/{id}` / DELETE `/polizas/{id}`.

---

## Renovaciones — `/renovaciones`

### GET `/renovaciones`
Filters: `q`, `estado` (por_iniciar/cotizando/negociando), `ejecutivo_id`, `aseguradora_id`.
Item shape:
```json
{ "id": 3, "codigo": "REN-001", "cliente": { "id": 1, "nombre": "Agrícola Los Robles S.A." },
  "poliza": { "id": 7, "numero_poliza": "POL-2024-0072" }, "ramo": { "id": 1, "nombre": "Incendio/Sismo" },
  "aseguradora": { "id": 1, "nombre": "Chubb Generales" }, "aplica_coaseguro": true,
  "prima_defender_uf": 900.0, "comision_pct": 15.0, "fecha_vencimiento": "2026-08-20",
  "dias_restantes": 43, "dias_color": "ambar", "estado": "cotizando",
  "estado_negociacion_texto": "..." }
```
`dias_color`: `ambar` if `dias_restantes <= 60` else `gris`.

### GET `/renovaciones/{id}` → full record + linked póliza summary + documentos/observaciones.

### GET `/renovaciones/summary`
```json
{ "renovaciones_activas": 6, "en_negociacion": 2, "prima_en_juego_uf": 4540.0, "por_vencer_30d": 0 }
```
`por_vencer_30d` rendered in red by the UI.

### POST `/renovaciones` / PATCH `/renovaciones/{id}` / DELETE `/renovaciones/{id}`.

---

## Cotizaciones — `/cotizaciones`

### GET `/cotizaciones`
Filters: `q`, `estado` (pendiente/respondida), `prioridad`, `ramo_id`, `cliente_id`.
Item shape:
```json
{ "id": 5, "cliente": { "id": 2, "nombre": "..." }, "ramo": { "id": 3, "nombre": "Transporte" },
  "bien_asegurar": "Carga refrigerada", "valor_declarado_uf": 12000.0,
  "fecha_envio": "2026-07-01", "fecha_vence": "2026-07-11", "dias_restantes": 3,
  "dias_color": "rojo", "prioridad": "alta", "estado": "pendiente" }
```
`dias_color`: `rojo` if `dias_restantes <= 4` else `ambar`.

### GET `/cotizaciones/{id}` → adds `ofertas[]` (aseguradora, prima_uf, deducible, coberturas,
exclusiones, vigencia, estado) for the comparator.

### GET `/cotizaciones/summary`
```json
{ "en_curso": 4, "valor_declarado_total_uf": 36600.0, "alta_prioridad": 3, "por_vencer_l7d": 2 }
```
`por_vencer_l7d` = count with `dias_restantes < 7`.

### POST `/cotizaciones` / PATCH `/cotizaciones/{id}` / DELETE `/cotizaciones/{id}`.
### POST `/cotizaciones/{id}/ofertas` → attach an oferta (subir=borrador; enviar sets estado=enviada).
### PATCH `/ofertas/{oferta_id}` → update offer estado (ajustada/aceptada/rechazada).

---

## Siniestros — `/siniestros`

### GET `/siniestros`
Filters: `q`, `estado`, `cliente_id`, `poliza_id`.
Item shape:
```json
{ "id": 2, "cliente": { "id": 1, "nombre": "..." },
  "poliza": { "id": 7, "numero_poliza": "POL-2024-0072" }, "tipo": "Incendio",
  "fecha_evento": "2026-05-12", "estado": "en_evaluacion",
  "monto_estimado_uf": 1200.0, "monto_liquidado_uf": null, "fecha_liquidacion": null }
```

### GET `/siniestros/{id}` → full record + activo summary + documentos + observaciones + actividad.

### GET `/siniestros/summary`
```json
{ "abiertos": 2, "en_evaluacion": 1, "monto_estimado_total_uf": 2000.0,
  "monto_liquidado_total_uf": 0.0 }
```

### POST `/siniestros` (Reportar siniestro) / PATCH `/siniestros/{id}` / DELETE `/siniestros/{id}`.

---

## Inspecciones — `/inspecciones`

### GET `/inspecciones`
Filters: `q`, `estado`, `inspector_id`, `activo_id`.
Item shape:
```json
{ "id": 4, "activo": { "id": 4, "nombre": "Planta Talca" },
  "cliente": { "id": 1, "nombre": "Agrícola Los Robles S.A." },
  "inspector": { "id": 3, "nombre": "..." }, "version": 1, "estado": "en_progreso",
  "solicitud_id": 2 }
```

### GET `/inspecciones/{id}` → adds `checklist` (JSON, ramo-driven), `solicitud`
(motivo, urgencia, fecha_objetivo), documentos, observaciones.

### GET `/inspecciones/summary`
```json
{ "solicitadas": 1, "en_progreso": 1, "validadas": 1, "abiertas_total": 3 }
```

### POST `/inspecciones/solicitudes` (Solicitar inspección) → creates a `solicitud_inspeccion`
(activo_id, proceso_ramo_id?, motivo, urgencia, fecha_objetivo).
### POST `/inspecciones` → create/assign an inspeccion (activo_id, solicitud_id?, inspector_id, checklist).
### PATCH `/inspecciones/{id}` → advance estado / update checklist.

---

## Aseguradoras — `/aseguradoras`

### GET `/aseguradoras` → catalog list.
Item: `{ "id": 1, "nombre": "Chubb Generales", "rut": "...", "sitio_pago_url": "..." }`.
### GET `/aseguradoras/{id}` / POST / PATCH / DELETE (admin_corredora only for writes).

---

## Ramos — `/ramos`

### GET `/ramos` → configurable lines for the tenant.
Item:
```json
{ "id": 1, "nombre": "Incendio/Sismo", "requiere_inspeccion": true,
  "campos_min": { ... }, "reglas_presuscripcion": { ... }, "campos_comparador": { ... } }
```
### GET `/ramos/{id}` → full config (drives dynamic forms/rules/comparator).
### POST / PATCH / DELETE (admin_corredora only) — this is how processes become "dynamic by ramo".

---

## Role access summary

- **admin_corredora**: full read/write across the tenant (incl. aseguradoras/ramos config).
- **ejecutivo_corredora**: read/write on operational resources (clientes, polizas, renovaciones,
  cotizaciones, siniestros); read on catalogs.
- **inspector**: read on assigned activos/solicitudes; write on own inspecciones.
- **asegurado_/aseguradora_** roles: not served this pass (endpoints exist role-gated for the future
  profiles; only the corredora slice is implemented now).
