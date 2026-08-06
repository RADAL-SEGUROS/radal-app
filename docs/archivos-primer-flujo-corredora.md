# Radal — Archivos e información: **primer flujo de la corredora**

> **Alcance.** Sólo la **primera interacción**: el embudo previo al cierre, cuando **todavía NO existe perfil de asegurado ni de aseguradora** (el asegurado es un **prospecto**; las aseguradoras son **referencias de catálogo**). El hito final —la **"última" prueba**— es la **creación de los perfiles** al cerrar. Lo posterior (póliza y post-venta) es un **segundo lote**: ver [`archivos-a-solicitar.md`](archivos-a-solicitar.md).

## Modelo de almacenamiento — **BD vs S3**

Cada ítem indica **Tipo**: `BD` (dato estructurado a la base), `S3` (archivo al bucket) o `S3 + BD` (el archivo va a S3 y de él se extraen campos a la base).

- **Bucket:** `radal-dev-185011028331` (privado, versionado).
- **Imágenes** (logos/fotos) → se convierten a **WEBP 512×512** y viven en `media/`:
  `media/{{entidad_tipo}}/{{id}}/logo.webp` · `media/usuario/{{id}}/avatar.webp`.
- **Documentos** (PDF/JPG/PNG: certificados, informes, pólizas, propuestas, evidencia) → viven en `documentos/` y se registran como filas de la tabla polimórfica `documento`:
  `documentos/{{entidad_tipo}}/{{id}}/{{tipo}}-{{n}}.{{ext}}`.
- **Planillas/CSV de captura** (usuarios, ramos, catálogo aseguradoras, ofertas) son **insumo**: sus datos se cargan a la **BD**, no se guardan como archivo en S3.
- Los `{{id}}` de las rutas se asignan **al momento de cargar** (cuando existe la fila en BD); el equipo entrega los archivos identificando a qué entidad pertenecen y el cargador los ubica.


**Convención "Obligatorio":** *Sí* bloqueante · *Opcional* enriquece · *Sí (si aplica)* obligatorio sólo cuando corresponde.

---


## Fase 0 · Corredora operativa

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Certificado de registro CMF (corredor vigente) | PDF | Sí | **S3 + BD** | S3: documentos/corredora/{id}/certificado-cmf.pdf · BD: corredora.cmf_estado/.cmf_numero_inscripcion/.vigencia | El PDF va a S3 (fila `documento`); los campos se extraen a BD. |
| Certificado / documento de nombramiento | PDF | Sí | **S3 + BD** | S3: documentos/corredora/{id}/nombramiento.pdf · BD: corredora.tipo_doc_nombramiento/.fecha_doc_nombramiento | Fecha hoy TBD. |
| Código CMF de Radal | texto | Opcional (TBD) | **BD** | corredora.codigo_cmf | Sólo dato. |
| Logo de la corredora | PNG/JPG/WEBP | Sí | **S3 + BD** | S3: media/corredora/{id}/logo.webp (WEBP 512²) · BD: corredora.foto_key/.foto_url | Se convierte a WEBP 512². |
| Lista real de usuarios + subrol | planilla/CSV | Sí | **BD** | usuario.nombre/.email/.cargo/.subrol | La planilla es insumo; los datos van a BD (no se guarda en S3). |
| Catálogo de ramos que opera Radal | planilla | Sí | **BD** | ramo.nombre/.requiere_inspeccion | Datos → BD. |
| Catálogo mínimo de aseguradoras (nombre + RUT) | planilla | Sí | **BD** | aseguradora.nombre/.rut | Referencia; datos → BD. |

## Fase 1 · Prospecto (SIN perfil)

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| RUT del prospecto | texto | Sí | **BD** | cliente.rut | Validación módulo-11. |
| Nombre / razón social | texto | Sí | **BD** | cliente.nombre |  |
| Tipo de persona | natural/jurídica | Sí | **BD** | (se usa en Fase 8) |  |
| Giro / sector | texto | Opcional | **BD** | cliente.sector |  |
| Contacto principal (nombre/tel/correo) | texto | Opcional | **BD** | cliente.contacto_principal/.telefono/.email |  |
| Estado del prospecto | enum | Sí | **BD** | cliente.estado = prospecto/onboarding |  |
| Ejecutivo asignado | ref usuario | Opcional | **BD** | cliente.ejecutivo_id |  |
| Interés / necesidad (nota) | texto | Opcional | **BD** | observacion / actividad |  |

## Fase 2 · Bien asegurable (activo)

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Ficha del bien asegurable | PDF/planilla | Sí | **S3 + BD** | S3: documentos/activo/{id}/ficha.pdf (si PDF) · BD: activo.tipo_activo/.nombre/.atributos | Si es planilla, sólo BD. |
| Ubicación / dirección del bien | texto | Sí | **BD** | activo.direccion |  |
| Atributos técnicos (según ramo) | JSON/planilla | Opcional | **BD** | activo.atributos |  |
| Valor declarado del bien (UF) | número UF | Sí | **BD** | cotizacion.valor_declarado |  |
| Fotos del bien | JPG/PNG | Opcional | **S3 + BD** | S3: documentos/activo/{id}/foto-{n}.jpg · BD: documento (fila por foto) | Archivo a S3. |
| Documentos de valorización (tasación/facturas) | PDF | Opcional | **S3 + BD** | S3: documentos/activo/{id}/valorizacion-{n}.pdf · BD: documento | Archivo a S3. |
| Ramo(s) a cotizar | ref ramo | Sí | **BD** | proceso_ramo.ramo_id/.activo_id |  |

## Fase 3 · Inspección (si aplica)

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Solicitud de inspección | planilla | Sí (si aplica) | **BD** | solicitud_inspeccion.activo_id/.motivo/.urgencia/.estado |  |
| Checklist de inspección | JSON | Sí (si aplica) | **BD** | inspeccion.checklist | Se completa en formulario → BD. |
| Evidencia fotográfica | JPG/PNG | Sí (si aplica) | **S3 + BD** | S3: documentos/inspeccion/{id}/evidencia-{n}.jpg · BD: documento | Archivo a S3. |
| Informe de inspección | PDF | Opcional | **S3 + BD** | S3: documentos/inspeccion/{id}/informe.pdf · BD: documento | Archivo a S3. |
| Inspector asignado | ref usuario | Opcional | **BD** | inspeccion.inspector_id/.version/.estado |  |

## Fase 4 · Pre-suscripción

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Resumen / bases técnicas | texto/PDF | Opcional | **S3 + BD** | S3: documentos/proceso_ramo/{id}/bases-tecnicas.pdf (si PDF) · BD: proceso_ramo.estado; documento | Si es texto, sólo BD. |
| Observaciones internas | texto | Opcional | **BD** | observacion |  |

## Fase 5 · Cotización

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Solicitud de cotización | planilla | Sí | **BD** | cotizacion.cliente_id/.activo_id/.ramo_id/.bien_asegurar/.valor_declarado/.fecha_envio/.fecha_vence/.prioridad |  |
| Coberturas solicitadas / vigencia deseada | texto | Opcional | **BD** | (contexto de la cotización) |  |
| Aseguradoras a las que se envió | ref catálogo | Sí | **BD** | aseguradora (nombre/RUT) | Referencias, no perfiles. |

## Fase 6 · Ofertas recibidas

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Oferta por aseguradora (transcrita) | planilla | Sí | **BD** | oferta.aseguradora_id/.prima/.deducible/.coberturas/.exclusiones/.vigencia/.estado | Una fila por propuesta. |
| Documento de la propuesta | PDF | Opcional | **S3 + BD** | S3: documentos/oferta/{id}/propuesta.pdf · BD: documento | Archivo a S3. |

## Fase 7 · Comparación y selección

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| Oferta seleccionada / adjudicada | ref oferta | Sí | **BD** | oferta.estado=aceptada |  |
| Confirmación del asegurado (fecha/medio) | texto | Opcional | **BD** | actividad |  |

## Fase 8 · ★ Crear perfil ASEGURADO ★

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| RUT validado + tipo de persona | texto | Sí | **BD** | asegurado.rut (UNIQUE)/.tipo_persona; cliente.asegurado_id |  |
| Razón social / nombre (+ fantasía) | texto | Sí | **BD** | asegurado.razon_social/.nombre/.nombre_fantasia |  |
| Representante legal / poder (si jurídica) | texto/PDF | Opcional | **S3 + BD** | S3: documentos/asegurado/{id}/poder-representante.pdf (si PDF) · BD: asegurado.contacto_nombre | Poder a S3 si lo hay. |
| Contacto completo (tel/correo/domicilio/comuna/región) | texto | Opcional | **BD** | asegurado.telefono/.correo/.domicilio/.comuna/.region |  |
| Logo / foto del asegurado | PNG/JPG/WEBP | Opcional | **S3 + BD** | S3: media/asegurado/{id}/logo.webp (WEBP 512²) · BD: asegurado.foto_key/.foto_url |  |
| Vínculo corredora ↔ asegurado | generado por la app | Sí | **BD** | vinculo_asegurado.estado=activo/.scope_ramo_id |  |

## Fase 8 · ★ Crear perfil ASEGURADORA ★

| Archivo / Dato | Formato | Obligatorio | Tipo | Destino (BD tabla.campo · ruta S3) | Notas |
|---|---|---|---|---|---|
| RUT + razón social | texto | Sí | **BD** | aseguradora_entidad.rut (UNIQUE)/.razon_social; aseguradora.entidad_id |  |
| Código / certificado CMF | texto/PDF | Opcional | **S3 + BD** | S3: documentos/aseguradora_entidad/{id}/certificado-cmf.pdf · BD: aseguradora_entidad.codigo_cmf/.cmf_estado | Verificación manual. |
| Contacto + sitio de pago | texto/URL | Opcional | **BD** | aseguradora_entidad.contacto_*/.sitio_pago_url |  |
| Logo de la aseguradora | PNG/JPG/WEBP | Opcional | **S3 + BD** | S3: media/aseguradora_entidad/{id}/logo.webp (WEBP 512²) · BD: aseguradora_entidad.foto_key/.foto_url |  |
| Vínculo corredora ↔ aseguradora | generado por la app | Sí | **BD** | vinculo_aseguradora.estado=activo/.scope_ramo_id |  |

---

## Fuera de alcance (siguiente lote)

Después de crear los perfiles: **póliza emitida** (`poliza.*` + PDF en `documentos/poliza/{id}/poliza.pdf`), coaseguro, ubicaciones, coberturas/exclusiones, plan de pago, renovaciones, siniestros, usuarios del asegurado/aseguradora y **códigos de acceso** para vincular entidades ya existentes. Detalle en [`archivos-a-solicitar.md`](archivos-a-solicitar.md).


## Notas transversales
- **RUT** normalizado `BODY-DV` + módulo-11; UNIQUE global sólo en canónicas.
- **Imágenes** → WEBP 512² en `media/`. **Documentos** → `documentos/` + fila `documento`.
- **Money** en UF. **CMF** verificación manual/TBD.
