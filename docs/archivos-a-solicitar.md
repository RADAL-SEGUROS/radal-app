# Radal — Archivos y datos a solicitar al equipo

> DELIVERABLE. Lista exhaustiva de **todos** los archivos/documentos y datos que
> el equipo debe entregar para simular el perfil **corredora Radal** de punta a
> punta, y luego para dar de alta perfiles **asegurado** y **aseguradora**.
>
> Está agrupado por entidad/módulo. Para cada ítem se indica: nombre del
> archivo/dato, formato, obligatorio u opcional, a qué tabla.campo alimenta, y por
> qué se necesita.
>
> Fuentes: `docs/data-model-v2.md`, `docs/modules.md`, `docs/architecture.md`.
> Money en UF. RUT se guarda normalizado (`BODY-DV`). Fotos/logos → WEBP 512×512.

**Convención de "Obligatorio"**
- **Sí** = bloqueante para simular el flujo de esa entidad de punta a punta.
- **Opcional** = enriquece el perfil pero el flujo funciona sin él (columna nullable).

---

## 1. Corredora (Radal) — perfil de la corredora / entidad canónica raíz

Alimenta la tabla `corredora` **[KEPT+]**. Es la raíz del tenant y el primer
perfil a simular. Varios campos ya están definidos en el seed v2 (§5.2 del data
model); igual conviene confirmarlos y adjuntar los respaldos oficiales.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Certificado de registro CMF (corredor vigente) | PDF | Sí | `corredora.cmf_estado`, `corredora.cmf_numero_inscripcion`, `corredora.cmf_tipo_registro`, `corredora.cmf_verified_at`, `corredora.vigencia` | Prueba oficial de que Radal es corredor con registro vigente; de él se extraen nº inscripción, tipo de registro (CSNAT/CSJUR) y estado. Habilita `cmf_verification_method=certificate`. |
| Código CMF de Radal | texto | Opcional (hoy "TBD") | `corredora.codigo_cmf` | El registro CMF es RUT-keyed; no hay código público estable. Se guarda como metadata si el equipo lo tiene; si no, queda "TBD" con `cmf_estado=unknown`. |
| Certificado / documento de nombramiento | PDF | Sí | `corredora.tipo_doc_nombramiento`, `corredora.fecha_doc_nombramiento` | Documento que acredita el nombramiento; de él sale el tipo ("Certificado") y la fecha (hoy null / TBD). |
| Logo de la corredora | PNG/JPG/WEBP (→ WEBP 512×512) | Sí | `corredora.foto_key`, `corredora.foto_url` (legacy `corredora.logo_url`) | Identidad visual en la app (header, settings). El pipeline lo convierte a WEBP 512. |
| RUT de la corredora | texto (`78.418.927-9`) | Sí | `corredora.rut` (UNIQUE normalizado `78418927-9`) | Identificador canónico del tenant; UNIQUE global. Ya conocido, confirmar. |
| Razón social | texto | Sí | `corredora.razon_social` | "Radal Seguros SpA". Nombre legal. |
| Nombre de fantasía | texto | Opcional | `corredora.nombre_fantasia` | "Radal". Display comercial. |
| Tipo de persona | enum `juridica`/`natural` | Sí | `corredora.tipo_persona` | Persona Jurídica. Determina campos de perfil. |
| Teléfono de contacto | texto | Opcional | `corredora.telefono` | Datos de contacto del perfil corredora. |
| Correo de contacto | texto | Opcional | `corredora.correo` | Datos de contacto del perfil corredora. |
| Domicilio | texto | Opcional | `corredora.domicilio` | Dirección legal ("Antonio Bellet 193, Of. 1210"). |
| Comuna | texto | Opcional | `corredora.comuna` | "Providencia". |
| Región | texto/código | Opcional | `corredora.region` | Código región ("13"). |

### 1a. Usuarios de la corredora

Alimenta `usuario` **[KEPT+]**. En el seed hay 4 usuarios; confirmar la lista real.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Lista de usuarios (nombre, email, cargo, subrol) | planilla/CSV | Sí | `usuario.nombre`, `usuario.email` (UNIQUE), `usuario.cargo`, `usuario.subrol`, `usuario.macro_perfil=corredora`, `usuario.rol` | Sin usuarios no hay login ni ownership de expedientes. Subrol define permisos (RBAC config-driven). |
| Foto de perfil por usuario | PNG/JPG/WEBP (→ WEBP 512) | Opcional | `usuario.foto_key`, `usuario.foto_url` | Avatar en la UI; opcional. |
| Mapeo de subroles a personas | texto | Sí | `usuario.subrol` (admin_corredora / ejecutivo_corredora / inspector / especialista_tecnico) | Determina qué ve/hace cada usuario. Validado contra `roles_config.ROLES`. |

---

## 2. Clientes / Asegurados — Nivel-1 del expediente + entidad canónica

Dos capas: la ficha **operacional** `cliente` **[KEPT+]** (per-corredora) y la
ficha **canónica** `asegurado` **[NEW]** (cross-tenant, UNIQUE por RUT). Al
simular la corredora, los clientes se crean; al crear el perfil asegurado se usa
la misma identidad canónica.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| RUT del asegurado/cliente | texto (RUT o DNI) | Sí | `asegurado.rut` (UNIQUE normalizado), `cliente.rut` (sin UNIQUE), `cliente.asegurado_id` | Identidad canónica. Se valida módulo-11. Enlaza cliente operacional ↔ ficha canónica. |
| Tipo de persona | enum `natural`/`juridica` | Sí | `asegurado.tipo_persona` | Determina si aplica `nombre` (natural) o `razon_social` (jurídica). |
| Nombre / Razón social | texto | Sí | `asegurado.nombre` / `asegurado.razon_social`, `cliente.nombre` | Identificación del cliente en tablas y listados. |
| Nombre de fantasía | texto | Opcional | `asegurado.nombre_fantasia` | Display comercial. |
| Giro / Sector | texto | Opcional | `asegurado.giro`, `cliente.sector` | Columna "Sector" en la tabla de clientes; segmentación. |
| Contacto principal (nombre) | texto | Opcional | `asegurado.contacto_nombre`, `cliente.contacto_principal` | Bloque "Resumen" del detalle de cliente. |
| Teléfono | texto | Opcional | `asegurado.telefono`, `cliente.telefono` | Contacto en detalle de cliente. |
| Correo | texto | Opcional | `asegurado.correo`, `cliente.email` | Contacto en detalle de cliente. |
| Domicilio / Comuna / Región | texto | Opcional | `asegurado.domicilio`, `asegurado.comuna`, `asegurado.region` | Ubicación del asegurado. |
| Logo / foto del asegurado | PNG/JPG/WEBP (→ WEBP 512) | Opcional | `asegurado.foto_key`, `asegurado.foto_url` | Identidad visual del cliente en la UI. |
| Estado del cliente | enum | Sí | `cliente.estado` (activo/onboarding/prospecto/suspendido/archivado) | KPI "Clientes activos"; sólo `activo` cuenta. |
| Ejecutivo asignado | ref usuario | Opcional | `cliente.ejecutivo_id` | Columna "Ejecutivo"; default = usuario creador. |
| Fecha de alta | fecha | Opcional | `cliente.fecha_alta` | Columna "Fecha alta". |

### 2a. Asegurados adicionales (terceros con interés asegurable)

Alimenta `asegurado_adicional` **[KEPT]** — banco/leasing/acreedor prendario.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| RUT + nombre del tercero (banco/leasing) | texto | Opcional | `asegurado_adicional.rut`, `asegurado_adicional.nombre`, `asegurado_adicional.cliente_id` | Registra al tercero con interés asegurable sobre las pólizas del cliente. |
| Tipo de interés / relación | texto | Opcional | `asegurado_adicional` (campo tipo/relación) | Distingue banco vs leasing vs acreedor prendario. |

---

## 3. Activos / Bienes — Nivel-2 del expediente

Alimenta `activo` **[KEPT]** y su carpeta operativa `proceso_ramo` **[KEPT]**.
Un cliente tiene muchos activos; cada activo tiene un proceso por ramo.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Ficha del bien asegurable | PDF/planilla | Sí | `activo.tipo_activo`, `activo.nombre`, `activo.atributos` (JSON) | Nivel-2 del expediente; sin activo no hay proceso, póliza ni inspección. |
| Ubicación / dirección del activo | texto | Sí | `activo.direccion` | Ubicación física del bien (planta, edificio, flota). |
| Atributos técnicos del bien | JSON/planilla | Opcional | `activo.atributos` (JSON flexible) | Campos dinámicos exigidos por `ramo.campos_min` (config por ramo). |
| Estado del activo | enum | Opcional | `activo.estado` | Estado del bien en el detalle de cliente → Activos. |
| Ramo(s) bajo los que se asegura | ref ramo | Sí | `proceso_ramo.ramo_id`, `proceso_ramo.activo_id` | Abre la carpeta operativa por ramo (define si requiere inspección, campos, comparador). |

### 3a. Configuración de ramos (por corredora)

Alimenta `ramo` **[KEPT]** — config-driven (`docs/architecture.md §4`).

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Catálogo de ramos que opera Radal | planilla | Sí | `ramo.nombre`, `ramo.requiere_inspeccion` | Define los ramos disponibles (Incendio/Sismo, RC, etc.) y cuáles exigen inspección. |
| Campos mínimos por ramo | JSON | Opcional | `ramo.campos_min` | Determina qué campos pide el formulario al capturar el activo. |
| Reglas de presuscripción | JSON | Opcional | `ramo.reglas_presuscripcion` | Reglas evaluadas antes de cotizar. |
| Campos del comparador de ofertas | JSON | Opcional | `ramo.campos_comparador` | Qué campos comparan las ofertas de un ramo. |

---

## 4. Pólizas — contrato de seguro

Alimenta `poliza` **[KEPT]** y sus sub-tablas: `coaseguro_participacion`,
`ubicacion_poliza`, `cobertura_item` **[KEPT]**.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| PDF de la póliza | PDF | Sí | `documento` (adjunto a la póliza); metadatos → `poliza.numero_poliza` | Documento contractual descargable ("Descargar póliza"). Fuente de todos los campos siguientes. |
| Nº de póliza | texto | Sí | `poliza.numero_poliza` | Identificador mostrado (mono) en tablas y encabezado. |
| Cliente y activo asociados | ref | Sí | `poliza.cliente_id`, `poliza.activo_id`, `poliza.ramo_id` | Vincula la póliza al expediente. |
| Aseguradora líder | ref aseguradora | Sí | `poliza.aseguradora_id` | Columna "Aseguradora líder". |
| Vigencia (inicio–fin) | fechas | Sí | `poliza.vigencia_inicio`, `poliza.vigencia_fin` | Estado vigente/no_vigente; días-restantes; renovaciones. |
| Prima (UF) | número UF | Sí | `poliza.prima` | KPI prima cartera; renovaciones. |
| Comisión % | número | Opcional | `poliza.comision_pct` | Encabezado de póliza; comisión a defender en renovación. |
| Suma asegurada y valor del bien (UF) | número UF | Sí | `poliza.suma_asegurada`, valor → `cobertura_pct` | Analizador de cobertura (§6.1): infra/óptima/sobre. |
| Tipo de cobertura | texto | Opcional | `poliza.tipo_cobertura` | Análisis de cobertura. |
| Coberturas y exclusiones | lista/planilla | Sí | `cobertura_item` (tipo: cobertura/exclusión, texto) | Bloque "Coberturas y exclusiones". |
| Deducible | texto | Opcional | `poliza.deducible_texto` | Bloque "Deducible y límites". |
| Límite de indemnización | número/texto | Opcional | `poliza.limite_indemnizacion` | Bloque "Deducible y límites". |
| Coaseguro (participaciones) | planilla | Sí (si aplica) | `coaseguro_participacion.aseguradora_id`, `.es_lider`, `.porcentaje` (suma 100%) | Reparto entre aseguradoras (ej. Chubb 60% + HDI 25% + Mapfre 15%). |
| Ubicaciones aseguradas | planilla | Sí (si aplica) | `ubicacion_poliza.nombre`, `.direccion`, `.suma_asegurada`, `.porcentaje` (suma 100%) | Reparto de suma por ubicación. |
| Plan de pago (nº cuotas, método) | texto | Opcional | `poliza.plan_pago_cuotas`, `poliza.plan_pago_metodo` | Bloque "Plan de pago". |
| URL de sitio de pago | URL | Opcional | `poliza.pago_url` | Acción "Ver sitio de pago". |
| Estado de la póliza | enum | Sí | `poliza.estado` (vigente/no_vigente) | KPI "Pólizas vigentes". |

---

## 5. Renovaciones

Alimenta `renovacion` **[KEPT]**. Deriva de pólizas por vencer.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Póliza origen a renovar | ref poliza | Sí | `renovacion.poliza_id`, `renovacion.cliente_id`, `renovacion.aseguradora_id` | Base de la renovación. |
| Prima a defender (UF) | número UF | Sí | `renovacion.prima_defender` | KPI "Prima en juego"; columna. |
| Comisión % | número | Opcional | `renovacion.comision_pct` | Comisión a defender. |
| Fecha de vencimiento | fecha | Sí | `renovacion.fecha_vencimiento` | Días-restantes; KPI "Por vencer 30D". |
| Estado de negociación (narrativa) | texto | Opcional | `renovacion.estado`, `renovacion.estado_negociacion_texto` | Bloque "Estado de negociación". |
| Aplica coaseguro | bool | Opcional | `renovacion.aplica_coaseguro` | Columna coaseguro. |

---

## 6. Cotizaciones / Ofertas

Alimenta `cotizacion` **[KEPT]** y `oferta` **[KEPT]**.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Solicitud de cotización | planilla | Sí | `cotizacion.cliente_id`, `.activo_id`, `.ramo_id`, `.bien_asegurar`, `.valor_declarado`, `.fecha_envio`, `.fecha_vence`, `.prioridad` | Registro de la cotización enviada a aseguradoras. |
| Valor declarado (UF) | número UF | Sí | `cotizacion.valor_declarado` | KPI "Valor declarado total". |
| Prioridad | enum baja/media/alta | Opcional | `cotizacion.prioridad` | Badge de prioridad. |
| Oferta recibida (por aseguradora) | planilla/PDF | Sí | `oferta.aseguradora_id`, `.prima`, `.deducible`, `.coberturas`, `.exclusiones`, `.vigencia`, `.estado` | Bloque "Ofertas recibidas"; comparador; convertir a póliza. |
| Documento de la oferta | PDF | Opcional | `documento` (adjunto a la cotización/oferta) | Respaldo de la oferta de la aseguradora. |

---

## 7. Inspecciones

Alimenta `solicitud_inspeccion` **[KEPT]** e `inspeccion` **[KEPT]**. Requerido
para ramos con `requiere_inspeccion=true`.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Solicitud de inspección | planilla | Sí | `solicitud_inspeccion.activo_id`, `.motivo`, `.urgencia`, `.fecha_objetivo`, `.created_by`, `.estado` | Origina la inspección de un activo/proceso. |
| Checklist de inspección | JSON | Sí | `inspeccion.checklist` (JSON) | Ítems evaluados (ej. checklist Incendio) renderizados en el detalle. |
| Evidencia fotográfica | JPG/PNG | Sí | `documento` (fotos, adjuntas a la inspección) | Respaldo visual de la inspección. |
| Informe de inspección | PDF | Opcional | `documento` (informe) | Documento de cierre de la inspección. |
| Inspector asignado + versión | ref / texto | Opcional | `inspeccion.inspector_id`, `inspeccion.version`, `inspeccion.estado` | Asignación y versionado de la inspección. |
| Observaciones | texto | Opcional | `observacion` (por entidad) | Comentarios sobre la inspección. |

---

## 8. Siniestros

Alimenta `siniestro` **[KEPT]**.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Antecedentes del siniestro | planilla | Sí | `siniestro.poliza_id`, `.cliente_id`, `.activo_id`, `.fecha_evento`, `.tipo`, `.descripcion`, `.estado` | Registro del siniestro y su ciclo de vida. |
| Monto estimado (UF) | número UF | Sí | `siniestro.monto_estimado` | KPI "Monto estimado total". |
| Monto liquidado (UF) + fecha | número/fecha | Opcional | `siniestro.monto_liquidado`, `siniestro.fecha_liquidacion` | Bloque "Evaluación/liquidación"; KPI liquidado. |
| Peritajes y respaldos | PDF/JPG | Opcional | `documento` (adjuntos al siniestro) | Documentación de respaldo del siniestro. |
| Observaciones | texto | Opcional | `observacion` | Comentarios del siniestro. |

---

## 9. Aseguradoras — catálogo per-tenant + entidad canónica

Dos capas: `aseguradora` **[KEPT+]** (per-corredora, target de FKs operacionales)
y `aseguradora_entidad` **[NEW]** (canónica cross-tenant por RUT/CMF). Necesarias
para pólizas, ofertas, coaseguro y renovaciones, y para el futuro perfil aseguradora.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Listado de aseguradoras con que opera Radal | planilla | Sí | `aseguradora.nombre`, `aseguradora.rut`, `aseguradora.corredora_id` | Catálogo per-tenant; target de `poliza.aseguradora_id`, `oferta.aseguradora_id`, `coaseguro_participacion.aseguradora_id`, `renovacion.aseguradora_id`. |
| RUT de la aseguradora | texto | Sí | `aseguradora_entidad.rut` (UNIQUE normalizado), `aseguradora.rut` | Identidad canónica cross-tenant. |
| Razón social / nombre de fantasía | texto | Sí | `aseguradora_entidad.razon_social`, `.nombre_fantasia` | Identificación de la compañía. |
| Certificado / código CMF de la aseguradora | PDF / texto | Opcional | `aseguradora_entidad.codigo_cmf`, `.cmf_estado`, `.cmf_verified_at`, `.cmf_verification_method` | Verificación del registro CMF de la compañía (manual/certificate). |
| Tipo de persona | enum | Opcional | `aseguradora_entidad.tipo_persona` (default juridica) | Perfil de la entidad. |
| Contacto (nombre/teléfono/correo/domicilio) | texto | Opcional | `aseguradora_entidad.contacto_nombre`, `.telefono`, `.correo`, `.domicilio` | Datos de contacto del perfil aseguradora. |
| URL sitio de pago | URL | Opcional | `aseguradora.sitio_pago_url`, `aseguradora_entidad.sitio_pago_url` | Enlace de pago de primas. |
| Logo de la aseguradora | PNG/JPG/WEBP (→ WEBP 512) | Opcional | `aseguradora_entidad.foto_key`, `.foto_url` | Identidad visual en la UI. |
| Enlace canónico (per-tenant ↔ entidad) | ref | Opcional | `aseguradora.entidad_id` | Relaciona la fila per-tenant con la entidad canónica. |

---

## 10. Vínculos y códigos de acceso (alta de perfiles asegurado/aseguradora)

Para **crear** los perfiles asegurado/aseguradora y conectarlos a la corredora se
usan `vinculo_asegurado` / `vinculo_aseguradora` **[NEW]** y `codigo_acceso`
**[NEW]**. No son "archivos" a subir, pero son datos operativos a definir.

| Archivo / Dato | Formato | Obligatorio | Alimenta (tabla.campo) | Por qué se necesita |
|---|---|---|---|---|
| Scope del vínculo (ramo / tramo) | ref/texto | Opcional | `vinculo_asegurado.scope_ramo_id`/`.scope_tramo`, `vinculo_aseguradora.scope_ramo_id`/`.scope_tramo` | Limita qué ve la corredora sobre la entidad canónica. |
| Estado del vínculo | enum | Sí | `vinculo_*.estado` (pendiente/activo/revocado) | Aislamiento tenant sobre la capa canónica. |
| Código de acceso (para entidad ya registrada) | generado por la app | Opcional | `codigo_acceso.code_hash`, `.entidad_tipo`, `.entidad_id`, `.expires_at` | Gate para vincular una entidad canónica existente a una corredora nueva (hand-off single-use). |

---

## Notas transversales

- **RUT**: entregar en cualquier formato; se normaliza a `BODY-DV` y se valida
  módulo-11. UNIQUE global sólo en tablas canónicas (`asegurado`,
  `aseguradora_entidad`, `corredora`), no en `cliente`/`aseguradora` v1.
- **Logos/fotos**: JPG/PNG/WEBP; el pipeline los convierte a **WEBP 512×512**.
- **Money**: siempre en **UF**.
- **CMF**: verificación hoy **manual/TBD**; el certificado permite pasar a
  `cmf_verification_method=certificate`.
- **Documentos** (PDF de póliza, informes, fotos, peritajes, ofertas) se
  adjuntan como filas `documento` ligadas a la entidad correspondiente y pueden
  compartirse vía `documento.compartido_con`.
- El **seed v2** ya crea la corredora Radal + 4 usuarios; las tablas
  operacionales (clientes, activos, pólizas, etc.) quedan **vacías** para datos
  reales — de ahí la necesidad de estos archivos.
