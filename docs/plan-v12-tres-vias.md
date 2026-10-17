# Plan v12 — Tres vías, una sola máquina

**Status:** proposal for Benja · 2026-09-29 · not yet approved
**Source of truth:** the team's *RADAL · Clic a clic* v14 (24-09-2026) and `catalogo_ramos.csv`, checked
against the code on `dev` at `51ada38`.
**Method:** each of the team's 22 *Preguntas a Benja* was answered from the code, with file:line
evidence. §3 holds the answers, written in Spanish so they can be forwarded as they are.

---

## 0. The short version

The team's document is not a set of meeting notes. It is a complete design, and it already describes
the "partial journey" from the meeting:

- **One state machine, three ways in.** Tradicional starts at *Interés*. Digital starts at
  *Cotizaciones recibidas* (comparator) or at *Póliza emitida* (upload the PDF). Manual starts at
  *Póliza emitida* with a typed-in policy marked *Sin respaldo*.
- Every earlier stage is marked **omitida** by the system (R6, R34). That is the "disabled parts of
  the journey" from the meeting.
- The **colocación** (today's "cuenta") remains the parent of everything (R1). No policy exists
  without one; a vía that registers a policy creates the colocación it needs.
- **Cobranza** gets its own menu entry and is part of the MVP, but only as a record plus alerts to
  the executive. ForPay/Fintoc, client notices and insurer reconciliation come at launch.

The design fits v9's philosophy: structure only where the journey needs it. It is also compatible
with the architecture. It asks for no new AI capability that we don't mostly have.

**We should adopt it as the plan.** The team asked us to stop changing the blueprint mid-build, and
this document is the blueprint. What follows covers:

- §1: what must be fixed first, because it is wrong today regardless of the redesign;
- §2: five decisions only you can make;
- §3: answers to their 22 questions;
- §4: the gap between their data model and dev;
- §5: the sequenced build, with estimates;
- §6: the risks.

---

## 1. Fix first — wrong today, independent of the redesign

### 1.1 Cross-broker data leak (their R33) — **compliance, Ley 21.719**

`insured` is canonical and cross-broker by design (`models/insured.py:1-6`, `rut unique=True`). Our
own CLAUDE.md non-negotiable #2 says so. The team flagged it as a compliance problem, and the code
shows they are right:

- **Broker B sees broker A's data.** `POST /clients` looks up `Insured` by RUT **with no broker
  filter** (`clients.py:340-342`). When the RUT already exists, B's typed `legal_name` is ignored.
  The response embeds the shared row through `InsuredSummary`: `legal_name`, `trade_name`, `email`,
  `phone`, address, `logo_key`. `clients/detail.tsx:255-280` then shows all of it to B.
- **B fills in A's record.** "Non-destructive enrichment" (`clients.py:367-382`) copies B's email,
  phone and address into any field A left blank, and those values then appear in A's view.
- **Existence oracle #1.** Calling the API without `legal_name` returns 422 "this RUT is not
  registered yet" only when the RUT is unknown platform-wide (`clients.py:345-350`).
- **Existence oracle #2.** `documents.py:191` marks `INSURED` as `broker_scoped=False`. Any broker
  can attach a document to any `insured.id`, and the 404-vs-success response reveals whether that id
  exists.

**Fix:** make the insured per-broker:

- add `broker_id`;
- replace the unique constraint on `rut` with one on `(broker_id, rut)`;
- migrate each shared row into one copy per broker that has a `client` for it;
- remove the enrichment;
- make `INSURED` broker-scoped for documents.

CLAUDE.md non-negotiable #2 has to be **rewritten**, not just worked around. This is the one place
where the team's document overrides a rule we wrote ourselves, and it is right to.

### 1.2 Policy upload commits with no human confirmation

`POST /policies/upload` extracts and commits in the same request (`policies.py:471-511`). It writes
straight to `policy`, `policy_location` and `coverage_item`, and the docstring's "suggest → confirm →
commit" never happens. This breaks our non-negotiable #6 and their R2.

A proper review path already exists: `POST /ai/documents/{id}/confirm` (`routers/ai.py:572-760`).
But it has no `override`, and there is no UI for it. Vía 2a turns "upload the policy" into **the main
way policies enter Radal**, so the *Confirmar datos* screen (their steps 2.4–2.7) is the first thing
to build. It is not an item to polish later.

### 1.3 The "Poder" and logo in Ossa's documents

This one is **not a leak.** `list_documents` filters `broker_id` on every row (`documents.py:746`).
The rows really are Ossa's:

- `import_fixtures.py` still seeds documents attached to fixture insureds (`documento.json` doc-019
  `poder_representante`, doc-020 `logo`, both owned by `cor-001` = Ossa).
- The client import that would give those documents a home is commented out (`import_fixtures.py:1577-1595`).

**Fix:** stop seeding insured-level documents in the blank baseline. This is a half-day task.

---

## 2. Decisions only you can make

**D1 — Analítica.** Their menu is *Dashboard · Portafolio · Cobranza · Agentes IA · Cotizadores ·
Configuración*, and Analítica is gone. In the meeting you said analytics is *really important* and
that the funnel must be tracked per colocación.

*Recommendation:* keep Analítica. Their Dashboard is a list of clickable KPI cards; Analítica is where
the funnel lives. The 12-stage machine with omitted stages is exactly what makes a real funnel
possible: you can measure conversion by vía, by stage, by ejecutivo and by ramo. Make the funnel P1
and place it under Dashboard.

**D2 — Groups in the sidebar.** Their sidebar has no client list. You said "we're still going to have
the groups." *Recommendation:* keep the recent-clients list in the sidebar. It costs nothing and it is
how brokers navigate today.

**D3 — Alembic.** Their P0 creates or alters **about 20 tables at once**. We have no Alembic, and
`create_all` never ALTERs (non-negotiable #10). Every previous pass that added columns needed
`scripts/migrate_case_files.py` and a careful by-hand RDS procedure. Doing P0 on that tooling is the
riskiest thing in this plan.

*Recommendation:* adopt Alembic as step zero of P0. Baseline it on the current schema, then write each
P0 change as a reviewed migration. Non-negotiable #10 then becomes "every schema change is an Alembic
revision". I need your yes, because it changes a rule.

**D4 — Outbound email, SSO, broker-representation mail.** Their MVP sends nothing outside the
corredora (R43, R44): alerts go to the ejecutivo's bell, and the broker sends every document from
their own mail. You mentioned mailing, SSO and sending on the broker's behalf.

*Recommendation:*

- **MVP:** transactional email *to the ejecutivo only* (their alerts, by mail as well as the bell)
  through SES. Internal only, so R43 holds.
- **Launch:** Google/Microsoft SSO, and mail to the contratante (their C5).
- **Later:** sending to insurers "on behalf of" the broker. This needs per-broker domain verification
  (SPF/DKIM on the broker's domain) and is a project of its own.

**D5 — Portal RPA / agents.** The Playwright-plus-proxy agents that quote in insurer portals are in
their "fases posteriores" (MCP connections, *Cotizadores · Pronto*). I agree with that placement. They
depend on P0 being stable: every agent action has to land in a colocación at the right stage, and that
machine doesn't exist yet. Two risks to settle with each insurer before building:

- **custody of the broker's portal credentials** (vault, rotation, audit);
- **each portal's terms of use.**

The upside you described is real. An RPA that files its result through the same *Digital · Tengo la
póliza / Comparar* entry points needs no new domain model.

**Also confirm (smaller):** adopt their meaning of *Ratificación* (§3, question 3). Merge dev's `lead`
and `intake` into their single *Interés* (§3, question 5). Accept Excel in Antecedentes (§3,
question 16).

---

## 3. Respuestas a "Preguntas a Benja"

*Respuestas desde el código de dev (`51ada38`). Se pueden reenviar tal cual al equipo.*

### Máquina de etapas

**1. ¿Qué pasa hoy si se usa Registrar póliza en una colocación en Lead? ¿Qué significa "entrar en
cualquier etapa"?**

- La póliza se crea, no hay error, y **la etapa no se mueve**: queda en Lead (`policies.py:417-558`
  no toca la etapa).
- La máquina es estricta: un paso adelante, un paso atrás, o dos saltos documentados (Antecedentes →
  Bases técnicas y Propuesta → Póliza emitida) (`case_files.py:126-188`).
- "Entrar en cualquier etapa" solo existe al **crear** la carpeta: el endpoint acepta la etapa
  inicial (`case_files.py` router ~819). También lo hace el importador, que escribe los eventos
  directamente.
- Hoy R34 no se cumple: una colocación puede tener póliza y seguir en Lead.

**2. ¿Cabe el estado *omitida* más los puntos de entrada en una sola reforma?**

Sí. Hoy no existe ningún concepto de etapa omitida. El registro de etapas es un log de eventos
(`case_file_stage_event`: from, to, fecha, usuario, nota, meta). La reforma consiste en:

- un tipo de evento "omitida" (quién = sistema, con motivo);
- puntos de entrada en 1, 2, 5, 7 y 10;
- una transición "saltar a" que omite las intermedias.

El estado de cada etapa ya se calcula a partir de los eventos (`services/expediente.py:889-902`).
Cabe en una reforma de unos 4 a 6 días, incluidas las pruebas.

**3. Ratificación: ¿qué representa hoy?**

Hoy tiene **dos significados que no calzan**:

- La etapa `ratified` es "la compañía confirmó los términos" (spec v2).
- `broker_proposal.status = ratified` es una **firma interna del corredor** que congela el
  documento, sin la compañía de por medio (`broker_proposals.py:327-370`). Además, los estados
  `ISSUED` y `SUPERSEDED` nunca se usan.

Su propuesta ("enviada a la compañía, esperando emisión") es mejor. La adoptamos, y los estados de la
propuesta pasan a: por enviar, enviada, emitida y rechazada.

**4. Propuesta rechazada → volver a Comparación: ¿calza?**

- Hoy la propuesta del corredor **no tiene estado rechazada**.
- Volver a Comparación exige tres retrocesos manuales, de un paso cada uno.
- La cotización de la compañía sí tiene *rechazada*, pero no mueve la etapa (`proposals.py:775-827`).

Hay que construirlo: un botón *Compañía rechazó* que haga ese retroceso de una vez, con motivo.

**5. ¿Cómo agrupa hoy dev las etapas en pasos?**

Hay **tres agrupaciones distintas**, y no coinciden entre sí:

- La barra del viaje: 7 nodos (`Journey.tsx:99-107`).
- El tablero de Cuentas: 6 fases, donde *Bases técnicas* cae en "Mercado" (`data/accounts.tsx:55-80`).
- El expediente completo: 5 hitos (`services/expediente.py:198-238`).

Dev además tiene **13** etapas, no 12: `lead` e `intake` son dos etapas distintas, y ustedes tienen
solo *Interés*. Propuesta:

- fusionar `lead` e `intake` en *Interés*;
- adoptar su tabla "Etapa por etapa" como **la única** agrupación, definida una vez en el servidor y
  usada por las tres pantallas.

### Datos

**6. ¿La tabla Empresa es global o por corredora?**

**Global**, y es una filtración real. El detalle está en §1.1: otra corredora ve razón social,
correo, teléfono, dirección y logo que ingresó la primera, y rellena los campos vacíos de la otra.
Hay además dos formas de averiguar si un RUT existe en la plataforma. Se arregla como parte de P0, y
es lo primero que se hace.

**7. ¿Los RUT de una colocación son relación o lista?**

**Relación**: la tabla `account_client`, con una fila por RUT, su rol (contratante o asegurado) y
cuál es el principal (`models/account_client.py:37-60`). Ya es su ColocaciónAsegurado; no hay que
migrar nada.

**8. ¿Los ramos que crea cada corredora son filas? ¿Cuánto cuesta migrar a Ramo + Plantilla?**

Sí, son filas de `line_record_schema`, y eso **ya es su Plantilla**: archivos recomendados más
contexto para la IA. Lo que falta es el catálogo cerrado:

- `insurance_line` hoy admite filas por corredora y trae 6 ramos globales.
- **No existe el concepto de subtipo.**

El costo:

- crear `ramo` y `subtipo` desde su CSV (18 ramos y 53 subtipos);
- asociar cada plantilla a un ramo del catálogo;
- partir la mixta "RC / Ingeniería / Transporte" en tres.

Son unos 2 a 3 días.

**9. ¿La póliza guarda ítems, coberturas y deducibles como filas o como texto?**

Es mixto:

- **Ubicaciones:** filas (`policy_location`).
- **Coberturas y exclusiones:** filas, pero solo con la descripción; los límites que lee la IA se
  pierden al guardar (`extraction_commit.py:1890-1915`).
- **Deducibles:** JSON.
- **Coaseguro:** existe la tabla con % y líder, pero **nadie la llena**.
- **Partes** (contratante, beneficiario, acreedor): no existen como filas.

Una colocación ya puede tener varias pólizas. PólizaÍtem, PólizaCobertura, PólizaParte y
PólizaAsegurador se construyen extendiendo lo que hay.

**10. ¿La pestaña Endosos acepta varios movimientos sin rehacerla?**

No. Hoy un endoso es **una fila con un solo tipo**, entre 16 tipos por operación, y "varios
movimientos" solo cabe dentro de un JSON. Hay que agregar MovimientoEndoso y pasar a sus 9 tipos por
objeto. La pantalla se adapta; no hay que rehacerla desde cero.

**11. ¿La validación espejo guarda las diferencias?**

No. Se **calculan en cada consulta** (`services/mirror.py`, `GET /policies/{id}/mirror-diff`). Lo
único que se guarda son borradores de endoso cuando el usuario las encola. Hay que crear
DiferenciaEspejo.

**12. El "Poder" y el logo en Ossa, ¿de dónde salen?**

**No es una filtración.** El listado de documentos filtra por corredora en cada fila. Son datos de
prueba del importador:

- La semilla carga documentos a nombre de asegurados de prueba y los asigna a Ossa (doc-019 y
  doc-020 en `documento.json`).
- La carga de clientes que los habría acompañado está comentada.

Se corrige en el importador.

### Lector y comparador

**13. ¿El lector entrega confianza por campo, partes con rol y aseguradores con %?**

**No, ninguna de las tres cosas.**

- La confianza es **una sola por documento** (0 a 100).
- Las partes son tres casillas fijas (contratante, asegurado, corredor), sin rol. No hay
  beneficiario, y el acreedor aparece solo como "acreedor prendario".
- El coaseguro no se lee: hay una sola compañía.

Para su pantalla *Confirmar datos* hay que ampliar el esquema del lector: confianza por campo, partes
con rol y aseguradores con %. Son unos 3 a 4 días de trabajo de IA, más validación con pólizas
reales.

**14. ¿Cuánto cuesta que el comparador reciba cotizaciones de portales sin bases técnicas (vía 2b)?**

**Casi nada.** El comparador ya compara las cotizaciones **entre sí**: no mira antecedentes ni bases
técnicas (`ai.py:2963-2968`, `3163-3177`). Lo que hay que resolver:

- crear la colocación directamente en *Cotizaciones recibidas*, que es la reforma de la pregunta 2;
- que la propuesta tome razón social y vigencia de la colocación y no de antecedentes;
- el botón "Promover a propuesta" exige una "colocación v1" (placement) que el viaje v9 nunca crea:
  hoy responde 422 y es un botón muerto.

**2b puede entrar al MVP.**

**15. ¿Qué implica aceptar Excel en Antecedentes?**

**Poco.** El lector de Excel ya existe y funciona (`ai.py:760-810`, openpyxl). Solo lo bloquea una
regla de subida (`documents.py:121-176`) y el selector de archivos de la pantalla. Hay que seguir
rechazando el `.xls` antiguo, porque esa librería no lo lee. Son unas horas.

### Plataforma y costo

**16. ¿dev tiene un centro de alertas?**

**No.** No hay tabla de alertas, ni campana, ni correo, ni **ninguna tarea programada** (no hay cron,
scheduler ni EventBridge). Las "acciones pendientes" de hoy se calculan al abrir la cuenta. Para C3
(alertas por cadencia) y Renovaciones (entrada automática a 90 días) hace falta:

- la tabla Alerta;
- **un proceso diario**: una regla programada en AWS que llame a un endpoint interno protegido.

Es infraestructura nueva y va en P0.

**17. ¿Cuánto cuesta el renombre de pantalla?**

Son unas **300 apariciones** en los textos de pantalla y unas 10 en el servidor. **No se puede hacer
con reemplazo automático**, porque es un intercambio en dos sentidos: hoy "Asegurado" es la carpeta,
y pasa a ser el RUT. Hay que revisar cada texto a mano. Son de 1 a 2 días, y las rutas y el código
conservan sus nombres.

**18. ¿Cuánto cuesta pasar a dos roles?**

Hoy hay **siete roles de corredora** en la matriz de permisos (`core/roles_config.py`), no cuatro:

- administrador;
- ejecutivo;
- inspector;
- técnico (el único con "Aprobar");
- comercial;
- cobranza;
- siniestros.

Hay que migrar a los usuarios existentes y simplificar la matriz. Es 1 día. El dato que importa: hoy
**ejecutivo no puede aprobar**, así que hay que darle a ejecutivo lo que hoy tiene técnico.

**19. Fechas para MVP y lanzamiento.** Ver §5: son estimaciones de esfuerzo, no fechas. Las fechas
las fija Benja.

**20. ¿El lector puede aprender el formato del estado de pago de cada compañía? (C6)**

Existe una base: el tipo de documento "estado de cobranza" (código 10) lee cuotas con estado y fecha
de pago, y el Excel ya se puede leer. Pero **no memoriza formatos** y cruza solo por número de cuota,
no por n° de póliza + cuota + monto. Recomendamos definir a mano los formatos de las tres primeras
compañías de ForPay y dejar que el lector generalice después.

**21. Tolerancia de conciliación.** Es de negocio, no de código. La implementamos parametrizable por
corredora.

**22. ¿Dónde guarda dev las notas del Pipeline y del asegurado?**

En **una tabla Nota polimórfica** que ya existe (`models/activity.py:60-84`), con autor, fecha de
seguimiento y entidad: lead, asegurado o cliente. Su tabla Nota "Reservada" **ya está hecha**.

---

## 4. Their data model against dev

**Legend:**

- ✅ exists as they describe it
- 🔧 exists and needs an adjustment
- 🆕 new
- ⏸ reserved (created now, screen later)

| Their table | In dev | Gap |
|---|---|---|
| Corredora | `broker` ✅ | — |
| Usuario | `user` 🔧 | 7 broker roles → 2 (Q18) |
| Compañía | `insurer` 🔧 | per-broker "external" insurers → `por_validar`; contacts/appetite leave the screens |
| Ramo · Subtipo | `insurance_line` 🔧 · — 🆕 | closed catalog from the CSV; subtipo carries `entrada_sugerida` |
| Plantilla | `line_record_schema` ✅ | point each one at a catalog ramo; split the mixed one |
| ValorMoneda | — 🆕 | UF/USD daily from Banco Central (needs the daily job) |
| Cliente | `account_group` 🔧 | + alias[], razón social principal, fusion state |
| Asegurado | `client` + `insured` 🔧 | **per-broker (§1.1)**; absorbs `sales_lead` (pipeline stage, prima estimada) |
| Vigencia | *label on `case_file`* 🔧 | keep it computed (their rule 1); a table only if fusion needs it |
| Colocación | `case_file(kind=account)` 🔧 | + origen, via_prevista, subtipo, resultado; `renueva_a` = `origin_case_file_id` ✅; ejecutivo = `owner_user_id` ✅ |
| ColocaciónAsegurado | `account_client` ✅ | — |
| EtapaColocación | `case_file_stage_event` 🔧 | + omitida; one grouping (Q5) |
| EnvíoMercado | — 🆕 | — |
| Cotización | `proposal` 🔧 | + elegida |
| Propuesta | `broker_proposal` 🔧 | statuses por_enviar/enviada/emitida/rechazada (Q3) |
| Póliza | `policy` 🔧 | + origen_registro, respaldo, modalidad, condicionada, fecha/motivo_termino |
| PólizaParte | — 🆕 | reader must emit roles (Q13) |
| PólizaAsegurador | `coinsurance_share` 🔧 | exists, never filled |
| PólizaÍtem | `policy_location` 🔧 | generalize to ubicación/bien/vehículo/persona |
| PólizaCobertura | `coverage_item` 🔧 | keep the limits/deductibles the reader already extracts |
| DiferenciaEspejo | — 🆕 | today computed on the fly (Q11) |
| PlanPago · Cuota | `collection_plan` · `collection_installment` 🔧 | + origen/fuente; **status becomes computed**; **commit the cuotas already read from the policy** (§5 P1.5) |
| Pago | — 🆕 | today `paid_on` on the installment |
| GestiónCobranza · Cadencia · DevoluciónPrima | — 🆕 | today a `management_note` text |
| Endoso | `endorsement` 🔧 | 16 kinds by operation → 9 by object |
| MovimientoEndoso | — 🆕 | today `effect` JSON |
| Siniestro | `claim` 🔧 | states per their machine |
| Documento · DocumentoVínculo | `document` 🔧 · — 🆕 | N:N link; a document is stored once (R15) |
| Extracción | `extraction` 🔧 | per-field confidence |
| Alerta | — 🆕 | + **daily job** (Q16) |
| Nota | `note` ✅ | already built |
| Evento | activity 🔧 | Spanish text per event (R35); today it renders `case_file.created` raw |
| Comisión · PlantillaAviso · EstadoPagoCompañía · LoteImportación | — ⏸ | reserved |

**Legacy that the new model does not use:** `placement`, `asset`, `quote_request`, `offering` and
`inspection_request`/`inspection`. Their document puts the inspection report inside the Plantilla as
an optional file and drops the inspection tab, which matches what we concluded. P0 should **stop
creating** v1/v2 rows and make the v9 paths stop depending on them, such as "Promover a propuesta" →
`placement`. Leave the tables in place until the migration to production data is done; do not drop
them in P0.

---

## 5. The build

Estimates are **mine**, in focused dev-days for one engineer working with agents. They include tests
and a live-browser check, which is the lesson of v9 and v10. Treat them as ±30 %. Dates are yours to
set.

### P0a — Fix first (≈ 1.5 weeks)

| # | What | Est. | Done when |
|---|---|---|---|
| 0 | **Alembic baseline** (D3) | 1–2 d | `alembic upgrade head` on a copy of dev RDS is a no-op; CI runs `alembic check` |
| 1 | **Per-broker insured** (§1.1) + drop enrichment + scope INSURED documents + remove the 422 oracle | 3–4 d | a test proves broker B sees *nothing* of A's for the same RUT; migration splits shared rows |
| 2 | **Policy upload → suggest/confirm** (§1.2): extraction only, commit via confirm | 2 d | no business row is written before a human confirms |
| 3 | Seed stops creating insured-level documents (§1.3) | 0.5 d | blank baseline has 0 documents |
| 4 | **Daily job** (EventBridge → internal endpoint, signed) + `Alerta` table + bell stub | 2–3 d | a scheduled run writes alerts; bell shows the count |

### P0b — Structure (≈ 3–4 weeks)

| # | What | Est. |
|---|---|---|
| 5 | Ramo/Subtipo catalog from the CSV, Plantilla re-parented (Q8) | 2–3 d |
| 6 | **State machine reform:** `omitida`, entry points 1/2/5/7/10, "skip to", `lead`+`intake`→Interés, one server-side step grouping, Ratificación/Rechazo semantics (Q1–Q5) | 5–6 d |
| 7 | Colocación fields (origen, via_prevista, subtipo, resultado) + Propuesta states | 1–2 d |
| 8 | Póliza family: fields, PólizaParte, PólizaAsegurador, PólizaÍtem, PólizaCobertura, DiferenciaEspejo | 4–5 d |
| 9 | Cobranza family: Pago, GestiónCobranza, Cadencia, DevoluciónPrima; computed cuota status | 3–4 d |
| 10 | Endoso: MovimientoEndoso, 9 kinds by object | 2–3 d |
| 11 | DocumentoVínculo N:N, ValorMoneda + daily load, Evento Spanish text, 2 roles | 3 d |
| 12 | Reader schema: per-field confidence, parties with roles, co-insurers (Q13) | 3–4 d |
| 13 | Reserved tables | 1 d |

### P1 — MVP screens (their order, ≈ 7–9 weeks)

| # | What | Their issue | Est. |
|---|---|---|---|
| 1 | Blocking bugs; tab list must not wrap (`data/index.tsx:215` → `overflow-x-auto`) | BUG 1–3 | 2 d |
| 2 | Vocabulary rename (two-way, by hand — Q17), **+ Nuevo** (3 steps, A1), Pipeline on Asegurado, menu | 1–4 | 5–6 d |
| 3 | Siniestros | 13 | 3 d |
| 4 | Endosos (E1, E2, E4, prórroga as type 6) | 11 | 5–6 d |
| 5 | **Cobranza as a record**: C1–C4, bell alerts by cadence. **Also commit the cuotas the reader already extracts from the policy**; today it only warns (`extraction_commit.py:1937-1941`). This is the fastest route to "cobranza from policies". | 12 | 6–7 d |
| 6 | Portafolio tabs + the three vías: Tradicional 1.1–1.15, Digital 2a (**Confirmar datos**), Manual 3.x, Operatividad panel | 6–10 | 10–12 d |
| 7 | Espejo that never blocks (R42) | 14 | 2–3 d |
| 8 | Renovaciones: 90/60/30 via the daily job; the three 60-day hardcodes → one rule | 15 | 3–4 d |
| 9 | Dashboard (portfolio part) **+ funnel by vía/stage in Analítica** (D1) | 5 | 3–4 d |

**If the schedule is tight,** their own order says to cut the Dashboard first, then the endorsement
before/after view. I'd add: **2b (comparator for portal quotes) fits in P1 at about 3 days** (Q14),
and should go in before the Dashboard.

### P2 / P3 — As they wrote it, plus

- Launch: SSO (D4), mail to the contratante (C5), ForPay/Fintoc and C6 in parallel, client fusion
  (A3), "ver la póliza al [fecha]".
- Later: portal RPA/agents (D5), MCP, importing the portfolio from Brokeris (their pregunta 21 needs
  a real Brokeris export first).

**Total to MVP:** roughly **12–15 weeks** of one engineer (P0a + P0b + P1). Parallel work (backend
structure and frontend screens can overlap once P0b step 6 lands) brings it toward 9–11 weeks.

---

## 6. Risks

1. **The schema migration (P0).** It is the biggest change since v2. Mitigations: D3 (Alembic),
   rehearse every migration on a snapshot of dev RDS, and do nothing destructive in P0.
2. **Per-broker insured migration.** It changes a founding rule (CLAUDE.md #2). Every query that
   joins `insured` must be re-audited for `broker_id`, and the tenant-isolation tests must grow.
3. **The daily job.** It is the first stateful background process: idempotency, missed runs, time
   zone (Chile), and the OAC path (constraint 4) for invoking it.
4. **Reader quality.** Vía 2a makes the reader the main door, and money-core extraction was already
   unreliable. The *Confirmar datos* screen is what makes that safe. Don't cut it.
5. **The full sequential QA walk has never run** (still owed from v10). Run it once on today's dev
   before P0 starts, so regressions from P0 can be told apart from existing bugs.
6. **Scope creep from the meeting ideas** (agents, RPA, outbound mail). They are good ideas, and each
   of them lands cleanly *after* P0. Putting any of them before P0 rebuilds on the old machine.

---

## 7. Next steps

1. Benja: decide D1–D5 and the three smaller confirmations (§2).
2. Forward §3 to the team; José Francisco uploads the adjusted issues (Issues_Radal_2026-09-24).
3. Start P0a with #1 (the leak), independent of every other decision.
4. After approval, fold the accepted parts into `docs/technical-reference.md` and rewrite CLAUDE.md
   non-negotiables #2 and #10.
