# Radal Corredora — Modules Spec

Functional spec for the 7 corredora modules of the MVP. The corredora is always
the **owner** of every expediente. Only corredora screens are implemented this
pass; asegurado/aseguradora screens come later. UI copy is Spanish (Chile),
money in UF.

**Sidebar sections**
- **Gestión** (implemented): Dashboard, Clientes, Pólizas, Renovaciones,
  Cotizaciones, Siniestros, Inspecciones.
- **Comercial y operaciones** — **OUT OF MVP**: Pipeline, Facturación, Reportes
  render **disabled/greyed** with tooltip "Próximamente".

Shared color/business rules (coverage analyzer, días-restantes) live in
`docs/design-system.md §6` and are referenced below.

---

## 0. Dashboard  `/`

**Purpose.** No-scroll command center — everything above the fold. Orients the
logged-in colaborador and routes them into work.

**Header band**
- **Global search** (real-time, categorized: clientes / pólizas / activos).
  Results grouped by category, each clickable → detail route.
- **Personalized greeting** by time-of-day: "Buenos días / Buenas tardes /
  Buenas noches" + weekday + user name.

**KPI cards (4).**
1. **Pólizas vigentes** — count of `poliza.estado = vigente`.
2. **Clientes activos** — count of `cliente.estado = activo`.
3. **Renovaciones activas** — count of `renovacion.estado in
   (por_iniciar, cotizando, negociando)`.
4. **Pipeline ponderado** — sum of estimated primas (UF) across open
   opportunities.

**Quick actions.** Nuevo cliente · Nueva póliza · Reportar siniestro ·
Solicitar inspección.

**"Requiere atención".** Top 3 pendientes for the logged-in colaborador
(assigned renovaciones vencidas, cotizaciones por vencer, siniestros en
documentación, inspecciones observadas).

**"Principales clientes".** Ranked list (by prima cartera) + **Ver todos** →
`/clientes`.

**"Actividad reciente".** Last 2 activities; accordion expands to last 10. Rows
use role-colored dots (design-system §6.4).

**"Próximas renovaciones".** 2–5 items sorted by `fecha_vencimiento` + **Ver
todas** → `/renovaciones`. Días-restantes colored per §6.2.

**Business rules.** All counts/lists scoped by `corredora_id`. "Requiere
atención" and "Actividad reciente" filter to the current usuario where relevant.

---

## 1. Clientes  `/clientes` · `/clientes/:id`

**Purpose.** Nivel-1 expediente — the asegurado as a client of the corredora.
Root of the two-level backbone (cliente → activo → proceso_ramo).

**KPI cards.** Total clientes · Activos · En onboarding · Prospectos.

**Table columns.** Nombre · RUT (mono) · Sector · Estado (badge) · Ejecutivo ·
Nº pólizas · Prima cartera (UF, mono, right) · Fecha alta.

**Detail-view blocks / accordions.**
- **Resumen** — nombre, RUT, sector, estado, contacto_principal, teléfono,
  email, ejecutivo asignado, fecha_alta.
- **Activos** (nivel-2) — bienes asegurables del cliente (tipo, nombre,
  dirección, estado).
- **Pólizas** — pólizas del cliente with estado + cobertura badge.
- **Renovaciones** — renovaciones asociadas.
- **Cotizaciones** — cotizaciones en curso.
- **Siniestros** — historial.
- **Asegurados adicionales** — terceros con interés asegurable (banco/leasing).
- **Documentos** — shared documents for the cliente entity.
- **Actividad** — activity log filtered to this cliente.

**Quick actions.** Nueva póliza · Nueva cotización · Reportar siniestro ·
Solicitar inspección · Agregar activo.

**Business rules.** `estado ∈ {activo, onboarding, prospecto, suspendido,
archivado}`; only `activo` counts toward "Clientes activos" KPI. Scoped by
`corredora_id`. Ejecutivo defaults to creating usuario.

---

## 2. Pólizas  `/polizas` · `/polizas/:id`

**Purpose.** The insurance contract record, including coaseguro participation,
ubicaciones, coberturas/exclusiones, and payment plan.

**KPI cards.** Pólizas vigentes · Suma asegurada total (UF) · Prima total
cartera (UF) · Con coaseguro (count).

**Table columns.** Nº póliza (mono) · Cliente · Ramo · Aseguradora líder ·
Coaseguro (Sí/No) · Prima (UF, mono) · Cobertura (analyzer badge, §6.1) ·
Vigencia (inicio–fin) · Estado (vigente/no_vigente badge).

**Detail-view blocks / accordions.**
- **Encabezado** — nº póliza, ramo, cliente, aseguradora líder, estado,
  vigencia_inicio/fin, prima, comisión %.
- **Análisis de cobertura** — suma_asegurada vs valor → `cobertura_pct` badge
  (infra/óptima/sobre per §6.1); `tipo_cobertura`.
- **Coaseguro** — participaciones (`coaseguro_participacion`): aseguradora, es_líder,
  porcentaje. Sum must total 100%.
- **Ubicaciones** — `ubicacion_poliza`: nombre, dirección, suma_asegurada,
  porcentaje (must total 100% of suma).
- **Coberturas y exclusiones** — `cobertura_item` grouped by tipo (cobertura /
  exclusión).
- **Deducible y límites** — `deducible_texto`, `limite_indemnizacion`.
- **Plan de pago** — `plan_pago_cuotas`, `plan_pago_metodo`, `pago_url`.
- **Documentos** · **Actividad**.

**Quick actions.** Iniciar renovación · Reportar siniestro · Descargar póliza ·
Ver sitio de pago (`pago_url`).

**Business rules.** Coverage analyzer per §6.1. Coaseguro percentages and
ubicación percentages each sum to 100%. `estado ∈ {vigente, no_vigente}`; only
`vigente` counts toward KPI. Money in UF. Scoped by `corredora_id`.

*Flagship example: **POL-2024-0072** (Agrícola Los Robles), ramo Incendio/Sismo,
líder Chubb 60% + coaseguro HDI 25% + Mapfre 15%, cobertura óptima ~100%, suma
UF 9.000.*

---

## 3. Renovaciones  `/renovaciones` · `/renovaciones/:id`

**Purpose.** Manage the renewal cycle of expiring pólizas — defend prima and
comisión into the next period.

**KPI cards.** Renovaciones activas · En negociación · Prima en juego (UF) ·
**Por vencer 30D** (rojo, count of `<= 30` días).

**Table columns.** ID (REN-###, mono) · Cliente · Ramo · Aseguradora · Prima a
defender (UF, mono) · Comisión % · Fecha vencimiento · Días restantes (colored
§6.2) · Coaseguro (Sí/No) · Estado (badge) · Ejecutivo.

**Detail-view blocks / accordions.**
- **Resumen** — póliza origen, cliente, ramo, aseguradora, aplica_coaseguro,
  prima_defender, comisión %, fecha_vencimiento, ejecutivo.
- **Estado de negociación** — `estado` + `estado_negociacion_texto` narrative.
- **Cotizaciones / ofertas** asociadas.
- **Documentos** · **Actividad**.

**Quick actions.** Iniciar negociación · Solicitar cotización · Marcar renovada.

**Business rules.** `estado ∈ {por_iniciar, cotizando, negociando}` (all count as
"activas"). Días-restantes: **ámbar if `<= 60`, else gris** (§6.2). KPI "Por
vencer 30D" in **rojo**. Scoped by `corredora_id`.

---

## 4. Cotizaciones  `/cotizaciones`

**Purpose.** Track outbound quote requests to aseguradoras and their responses
(ofertas). List-centric (no dedicated detail route in MVP; drilldown via
inline/expander).

**KPI cards.** Cotizaciones en curso · Valor declarado total (UF) · Alta
prioridad (count) · **Por vencer L7D** (count of `< 7` días).

**Table columns.** Cliente · Ramo · Bien a asegurar · Valor declarado (UF,
mono) · Fecha envío · Fecha vence · Días restantes (colored §6.3) · Prioridad
(baja/media/alta badge) · Estado (pendiente/respondida badge).

**Detail / expander blocks.**
- **Solicitud** — cliente, activo, ramo, bien_asegurar, valor_declarado,
  fecha_envio, fecha_vence, prioridad.
- **Ofertas recibidas** — `oferta`: aseguradora, prima, deducible, coberturas,
  exclusiones, vigencia, estado (borrador/enviada/ajustada/aceptada/rechazada).
- **Documentos** · **Actividad**.

**Quick actions.** Registrar oferta · Aceptar oferta · Convertir a póliza.

**Business rules.** `estado ∈ {pendiente, respondida}`. Días-restantes: **rojo if
`<= 4`, else ámbar** (§6.3). KPI "Por vencer L7D" = `< 7` días. `prioridad ∈
{baja, media, alta}`. Scoped by `corredora_id`.

---

## 5. Siniestros  `/siniestros` · `/siniestros/:id`

**Purpose.** Claims lifecycle from report through liquidation and closure.

**KPI cards.** Siniestros abiertos · En evaluación · Monto estimado total (UF) ·
Monto liquidado total (UF).

**Table columns.** ID (mono) · Cliente · Póliza (mono) · Tipo · Fecha evento ·
Estado (badge) · Monto estimado (UF, mono) · Monto liquidado (UF, mono).

**Detail-view blocks / accordions.**
- **Resumen** — póliza, cliente, activo, fecha_evento, tipo, descripción,
  estado.
- **Evaluación / liquidación** — monto_estimado, monto_liquidado,
  fecha_liquidacion.
- **Documentos** (peritajes, respaldos) · **Observaciones** · **Actividad**.

**Quick actions.** Avanzar estado · Cargar documento · Registrar liquidación.

**Business rules.** `estado ∈ {reportado, en_documentacion, en_evaluacion,
pre_liquidado, liquidado, cerrado}`. "Abiertos" = not in {liquidado, cerrado}.
Montos in UF. Scoped by `corredora_id`.

---

## 6. Inspecciones  `/inspecciones` · `/inspecciones/:id`

**Purpose.** Inspection requests and inspections tied to activos/procesos —
required for ramos with `requiere_inspeccion = true` (e.g. Incendio/Sismo).

**KPI cards.** Inspecciones activas · Solicitudes pendientes · En progreso ·
Observadas.

**Table columns.** ID (mono) · Activo · Cliente · Inspector · Motivo/Urgencia ·
Versión (mono) · Estado (badge) · Fecha objetivo.

**Detail-view blocks / accordions.**
- **Solicitud** — `solicitud_inspeccion`: motivo, urgencia, fecha_objetivo,
  created_by, estado (solicitada/asignada).
- **Inspección** — inspector, version, estado, checklist (JSON rendered as
  items; e.g. Incendio checklist).
- **Documentos** (fotos, informe) · **Observaciones** · **Actividad**.

**Quick actions.** Asignar inspector · Iniciar inspección · Enviar · Observar ·
Validar.

**Business rules.** Solicitud `estado ∈ {solicitada, asignada}`. Inspección
`estado ∈ {solicitada, asignada, en_progreso, enviada, observada, validada,
cerrada}`. "Activas" = not cerrada. Inspections are versioned (`version`).
Triggered by ramos where `requiere_inspeccion = true`. Scoped by `corredora_id`.

---

## Out of MVP (disabled nav)
**Pipeline**, **Facturación**, **Reportes** live under "Comercial y
operaciones". Render greyed/`cursor-not-allowed` with tooltip "Próximamente". No
routes wired this pass.
