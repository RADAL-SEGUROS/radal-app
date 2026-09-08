# Radal — Usuarios de prueba (ambiente de desarrollo)

**Contraseña de todos los usuarios: `radal1234`**

> Radal es **la plataforma** (proveedor de software). Cada **corredora es un tenant**
> independiente: sólo ve sus propios datos. Los nombres "Usuario 1…15" vienen así de la
> data de prueba; los correos y roles sí son funcionales.

---

## Cómo levantar el ambiente en tu computador

```bash
# 1) Backend en el puerto 8000
cd backend
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env                # si aún no tienes .env

# 2) Cargar los datos
python -m app.db.import_fixtures --reset --no-upload            # el mundo base
python -m app.db.import_expedientes \
       --source "~/Downloads/EXPEDIENTES DEMO" --no-upload      # los 7 expedientes

# 3) Levantar la API
uvicorn app.main:app --reload --port 8000
```

```bash
# 4) Frontend en el puerto 5500 (en otra terminal)
cd frontend
npm install
npm run dev
```

**Ojo con los archivos.** Si tu `backend/.env` dice `MEDIA_BACKEND=s3` (así viene el archivo de
quien ya trabajó con AWS), las descargas y la lectura de documentos van a intentar ir a S3 y
fallarán sin credenciales. Para trabajar 100 % local hay dos caminos, cualquiera sirve:

- editar `backend/.env` y dejar `MEDIA_BACKEND=local`, o
- levantar la API con la variable puesta a mano:
  `MEDIA_BACKEND=local uvicorn app.main:app --reload --port 8000`.

Con `--no-upload` los importadores **igual copian los archivos** a la carpeta local
(`backend/media/`), así que los documentos, el análisis con IA y los ZIP de carpetas funcionan
sin tocar AWS. Si tienes el perfil `radal` de AWS configurado, puedes omitir `--no-upload` y
dejar `MEDIA_BACKEND=s3`.

| | |
|---|---|
| Aplicación | http://localhost:5500 |
| API | http://localhost:8000/api/v1 |
| Documentación de la API | http://localhost:8000/docs |
| Ambiente en la nube | https://dev.radalseguros.cl |

Ejecutar de nuevo `import_expedientes` **no duplica nada**: salta todo expediente cuya
referencia ya existe. Para reconstruir sólo los expedientes, sin tocar corredoras, usuarios ni
el catálogo de aseguradoras, se usa `--reset-cases`.

---

## Las 3 corredoras (tenants)

| # | Corredora | RUT | Código CMF | Clientes | Expedientes |
|---|---|---|---|---|---|
| 1 | **Ossa Covarrubias Ltda.** | 78.069.390-8 | 6168 | 4 | **10** (5 de cuenta + 5 de post-venta) |
| 2 | **Unidad Corredores** | 77.128.471-K | 9688 | 1 | 0 |
| 3 | **Fuenzalida SR** | 77.290.425-8 | 9192 | 3 | **6** (2 de cuenta + 4 de post-venta) |

Cada corredora trae la misma **data base**: 1 cliente · 1 activo · 1 colocación · 1 cotización ·
3 propuestas · 1 inspección · 22 documentos. Los clientes base son *Agroindustrial Santa Elisa
SpA* (Ossa), *Distribuidora y Logística Altamira SpA* (Unidad) y *Clínica del Valle de Ñuble
SpA* (Fuenzalida SR).

Sobre esa base, **Ossa Covarrubias y Fuenzalida SR** reciben además los expedientes del corpus
real. **Unidad Corredores queda sin expedientes a propósito**: sirve para ver cómo se comporta
la aplicación en una corredora recién partida.

---

## Usuarios

| Correo | Rol | Corredora |
|---|---|---|
| usuario1@ossacovarrubias.cl | `broker_admin` | Ossa Covarrubias |
| usuario2@ossacovarrubias.cl | `broker_executive` | Ossa Covarrubias |
| usuario3@ossacovarrubias.cl | `broker_inspector` | Ossa Covarrubias |
| usuario4@ossacovarrubias.cl | `broker_technician` | Ossa Covarrubias |
| usuario5@ossacovarrubias.cl | `broker_technician` | Ossa Covarrubias |
| usuario6@corredoraunidad.cl | `broker_admin` | Unidad Corredores |
| usuario7@corredoraunidad.cl | `broker_executive` | Unidad Corredores |
| usuario8@corredoraunidad.cl | `broker_inspector` | Unidad Corredores |
| usuario9@corredoraunidad.cl | `broker_technician` | Unidad Corredores |
| usuario10@corredoraunidad.cl | `broker_technician` | Unidad Corredores |
| usuario11@fuenzalidasr.cl | `broker_admin` | Fuenzalida SR |
| usuario12@fuenzalidasr.cl | `broker_executive` | Fuenzalida SR |
| usuario13@fuenzalidasr.cl | `broker_inspector` | Fuenzalida SR |
| usuario14@fuenzalidasr.cl | `broker_technician` | Fuenzalida SR |
| usuario15@fuenzalidasr.cl | `broker_technician` | Fuenzalida SR |

---

## Qué ve cada rol

| Rol | Menú lateral | Diferencia clave |
|---|---|---|
| **`broker_admin`** | Todo + **Configuración** | Único que administra usuarios y el perfil de la corredora; único que borra |
| **`broker_executive`** | Todo, **sin** Configuración | Operativo de punta a punta: leads, expedientes, cotizaciones, propuestas, pólizas, endosos, siniestros |
| **`broker_technician`** | Todo, **sin** Configuración | Es quien **aprueba** propuestas, endosos y siniestros. **No puede editar ni convertir leads** (sólo los ve) |
| **`broker_inspector`** | **Dashboard, Expedientes, Colocaciones, Inspecciones, Pólizas, Siniestros y Agente IA** | Sólo ve los expedientes y colocaciones que tienen una inspección asignada. No ve clientes, leads, cotizaciones, propuestas ni aseguradoras. **No genera carpetas ni mueve el expediente de etapa** |

Los permisos vienen del servidor (`GET /auth/permissions`); el menú lateral se arma con esa
respuesta. Si un rol no tiene permiso, **el ítem no aparece** — nunca un link que lleve a un error.

**Endosos y Cobranzas no son ítems del menú a propósito**: se llegan siempre desde la póliza que
modifican o facturan.

Fuera de alcance de esta versión y por eso **deshabilitados con el sello "PRONTO"**:
Renovaciones, Pipeline, Facturación y Reportes.

### Perfiles de proceso (disponibles, sin usuario de prueba todavía)

Además de los cuatro roles de arriba, el servidor ya reconoce **tres perfiles de proceso**: mesas
de trabajo estrechas dentro de una misma corredora. **Los importadores no crean usuarios con estos
roles**, así que no hay con quién entrar a probarlos desde la aplicación; para verlos hay que
asignar el rol a un usuario existente desde **Configuración → Usuarios** (sólo `broker_admin`).

| Rol | Para quién | Qué ve |
|---|---|---|
| **`broker_commercial`** | La mesa comercial | Exactamente lo mismo que `broker_executive`, incluida la gestión de grupos. Es un alias del rol ejecutivo, pensado para separar la mesa comercial de la operación cuando el equipo lo pida |
| **`broker_collections`** | La mesa de cobranza | Manda en el plan de pago y en las cuotas. Lee la póliza que factura y sus endosos, y ve los grupos. **De los expedientes sólo ve los de cobranza y la carpeta de cuenta de la que cuelgan** — nada de clientes, leads, cotizaciones, propuestas ni siniestros |
| **`broker_claims`** | La mesa de siniestros | Manda en el siniestro de punta a punta, incluido el cierre con el pronunciamiento de cobertura. Edita la póliza sobre la que liquida y ve los grupos. **De los expedientes sólo ve los de siniestro y su carpeta de cuenta** — nunca el libro de cobranza |

El recorte de expedientes de las dos últimas mesas **no lo decide la pantalla**: es un único
mecanismo del servidor (`CASE_VIEW_SCOPE`) que se aplica igual al listado, al detalle, a las
carpetas generadas y a los documentos. Un expediente que la mesa no ve responde **404** por
cualquiera de esos caminos; no es que sólo desaparezca del listado.

La matriz definitiva de permisos del equipo todavía está sin llenar: estos tres perfiles son la
propuesta de la especificación y se editan en un solo archivo
(`backend/app/core/roles_config.py`), nunca en las pantallas.

---

## Los expedientes (corpus `EXPEDIENTES DEMO`)

El expediente es la carpeta en la que piensa el corredor. Cada uno está **detenido en un punto
distinto del viaje del riesgo**, para que se pueda ver la pantalla en todas sus fases con datos
verdaderos: montos en UF, folios, números de póliza, endosos, cobranza y siniestros transcritos
de los documentos originales del equipo (168 archivos, 4 cuentas, 7 expedientes de ramo).

En total son **16 expedientes: 7 de cuenta y 9 de post-venta**, con **89 documentos** clasificados
por subexpediente (raíz, 1 cotización, 2 cotizaciones recibidas, 3 propuesta, 4 póliza, cobranza,
endoso, siniestro).

> **Las referencias se repiten entre corredoras y eso es correcto.** `EXP-2026-0001` existe en Ossa
> Covarrubias *y* en Fuenzalida SR: la referencia es única **dentro** de cada corredora, no entre
> corredoras.

### Los 7 expedientes de cuenta

| Referencia | Cuenta · Ramo | Corredora | Etapa | Qué se ve ahí |
|---|---|---|---|---|
| `EXP-2026-0001` | **JO Pastelería** (Pacto Food SpA) · Accidentes Personales Colectivos | Ossa Covarrubias | **Lead** | Sólo el dato comercial: el lead con seguimiento al 15-05-2026, prima estimada UF 38 y 2 notas. **Sin documentos** (se cargan sólo con `--full`). |
| `EXP-2026-0002` | **Coccolino** · Vehículos Motorizados (flota de 3 furgones) | Ossa Covarrubias | **Antecedentes** | 5 documentos de antecedentes (00A a 00E) e **inspección folio 2026000512339**, nota 71/100, "Insatisfactorio — mejorable con medidas de gestión". Aún sin bases técnicas. |
| `EXP-2026-0003` | **JO Pastelería** · Responsabilidad Civil General | Ossa Covarrubias | **Envío al mercado** | 7 documentos: carpeta completa y **cotización enviada el 04-05-2026** a Unnio, Chubb y HDI. Ninguna compañía ha respondido: 0 propuestas. |
| `EXP-2026-0001` | **Viña Santa Alicia** · Todo Riesgo Bienes Físicos con PxP | Fuenzalida SR | **Comparación** | 11 documentos. Cotización enviada el 31-08-2026 a Southbridge, Mapfre y HDI; las **3 ofertas están cargadas y confirmadas** (Southbridge UF 1.316,88 · Mapfre UF 1.296,13 · HDI UF 1.120,13) y el **comparativo está generado**. Falta la decisión del asegurado. |
| `EXP-2026-0004` | **Coccolino** · Incendio y Riesgos Adicionales (Riesgos Nominados) | Ossa Covarrubias | **Propuesta emitida** | 13 documentos. 4 ofertas (BCI UF 103,07 · Consorcio UF 115,60 · HDI Nominados UF 129,91 · HDI Todo Riesgo UF 141,64). Se adjudica **HDI `HDI-INC-2026-0338`** y se envía la propuesta de emisión el 24-03-2026. **Todavía sin póliza.** |
| `EXP-2025-0001` | **Viña Indómita** · Todo Riesgo Bienes Físicos con Terremoto | Fuenzalida SR | **Vigente** | 15 documentos y la cadena completa hasta la póliza **0020119904** de Southbridge (UF 475.256 asegurados, prima total UF 1.181,28). Cuelgan de ella 4 sub-expedientes: E1, E2, CB1 y SN1. |
| `EXP-2026-0005` | **La Favorita** · Incendio y Riesgos Adicionales con Perjuicio por Paralización | Ossa Covarrubias | **Vigente** | El más completo: 21 documentos, **dos rondas al mercado** (3 declinaciones + 1 pronunciamiento condicionado en la primera), plan de ingeniería de **12 medidas por UF 9.110**, póliza **15-04-0091883** de HDI. Cuelgan 5 sub-expedientes: E1, E2, CB1, SN1 y RN1. |

### Los sub-expedientes de post-venta

Cuelgan del expediente de cuenta **y** de la póliza, con su propia numeración (`-E1`, `-E2`,
`-CB1`, `-SN1`, `-RN1`). En la ficha de la póliza se ven como un sub-embudo con fechas.

| Sub-expediente | Póliza | Tipo | Etapa | Qué demuestra |
|---|---|---|---|---|
| `EXP-2025-0001-E1` | 0020119904 · Southbridge | Endoso | Endoso aplicado | **Aumento de suma asegurada**: incluye línea de embotellado y 180 barricas, +UF 22.000 de monto, +UF 29,96 de prima total. Emitido 18-03-2026, vigente desde el 20-03-2026 a las 12:00. |
| `EXP-2025-0001-E2` | 0020119904 · Southbridge | Endoso | Endoso aplicado | **Disminución de suma asegurada**: excluye la bodega de apoyo, −UF 2.892 de monto y **prima negativa** (−UF 1,86), es decir una devolución. |
| `EXP-2025-0001-CB1` | 0020119904 · Southbridge | Cobranza | **Cobranza morosa** | Cuponera de **14 cuotas** (10 de la póliza + 3 por el endoso E1 + 1 nota de crédito del E2), total UF 1.209,38. **Dos cupones vencidos**: CUP-008 con **67 días de mora** al 11-08-2026 y CUP-E1-03. Dos cuotas más se pagaron con atraso. Es el caso para ver el riesgo de término por artículo 528. |
| `EXP-2025-0001-SN1` | 0020119904 · Southbridge | Siniestro | Siniestro liquidado | **SIN-2026-0417**: rotura de la cuba N° 14 con derrame de 58.400 litros. Denunciado el 23-05-2026, liquidador Graham Miller. 6 partidas, deducible UF 266,60, **liquidado y pagado en UF 2.399,40** sobre UF 2.980 avisados. |
| `EXP-2026-0005-E1` | 15-04-0091883 · HDI | Endoso | Endoso aplicado | **Reducción de deducible** por cumplimiento verificado del plan de ingeniería. Es un endoso **sin efecto en prima** (todos los deltas en cero) — sirve para ver que el validador de plata acepta un endoso administrativo. |
| `EXP-2026-0005-E2` | 15-04-0091883 · HDI | Endoso | Endoso aplicado | **Aumento de monto asegurado** por nueva cámara de congelado: +UF 12.400, +UF 10,54 de prima. |
| `EXP-2026-0005-CB1` | 15-04-0091883 · HDI | Cobranza | **Cobranza saldada** | **11 cuotas** en PAC (10 de la póliza + 1 por el endoso E2), total UF 433,57. La cuota 8 tuvo **PAC rechazado por saldo insuficiente**, reintento rechazado y pago por transferencia 13 días después; el plan quedó **saldado**. Es el contraste con la cobranza morosa de Viña Indómita. |
| `EXP-2026-0005-SN1` | 15-04-0091883 · HDI | Siniestro | Siniestro liquidado | **S-2027-58814**: falla del motocompresor de congelados. 5 partidas, incluida una de **perjuicio por paralización**; deducible UF 578; **pagado en UF 4.397**. Siniestralidad del año **1.146,8 %** sobre prima neta. |
| `EXP-2026-0005-RN1` | 15-04-0091883 · HDI | Renovación | Revisión de renovación | El expediente de renovación del 05-01-2028, abierto con 3 notas de seguimiento. **No tiene documentos** — ver la sección de limitaciones. |

### Cifras que conviene reconocer

- **Garantías y medidas**: la póliza de Viña Indómita trae **7 garantías de suscripción** (G-1 a
  G-7, todas *en curso*); la de La Favorita trae el **plan de ingeniería de 12 medidas** (M-1 a
  M-12), **todas cumplidas en plazo**, presupuestadas en UF 9.110 y ejecutadas en UF 9.284,20
  (1,9 % sobre lo estimado).
- **Aseguradoras del corpus**: HDI, Chubb, Mapfre, BCI y Southbridge son **nativas**; Consorcio,
  Porvenir y Unnio aparecen como **externas**. Todas ya existen en el catálogo de 25 compañías.
- **Nuevo ramo**: el importador crea *Accidentes Personales Colectivos*, que no venía en los 6
  ramos de la data base.

---

## Qué probar, según con quién entres

> Todo el recorrido de abajo se hace en el menú **Gestión → Expedientes**, salvo donde se indique.

### 1. `usuario2@ossacovarrubias.cl` — ejecutivo (el recorrido completo)

Es el perfil que ve y hace más cosas. Recorrido sugerido, en orden:

1. **Leads** → está el lead *JO Pastelería — Accidentes Personales colectivo*, calificado, con
   seguimiento al 15-05-2026. Ábrelo y usa **Convertir**: crea cliente, colocación y un
   expediente nuevo en etapa *Antecedentes*, en una sola operación.
2. **Expedientes → `EXP-2026-0002` (Coccolino · Vehículos)** — el caso en *Antecedentes*. Mira la
   pestaña **Documentos**: están agrupados por subexpediente y con su código de origen (00A, 00B…).
3. En cualquier documento, botón **Analizar con IA** → el modelo propone campos, tú los corriges,
   y recién con **Confirmar y guardar** se escriben. Nada se guarda solo. *(Necesita `AI_API_KEY`
   configurada en el `.env`; sin ella el botón responde con error de conexión.)*
4. **Avanzar etapa**: en la cabecera del expediente. El servidor valida el paso — por ejemplo, no
   deja pasar a *Comparación* si no hay al menos una propuesta confirmada, y responde con el
   motivo. Retroceder un paso siempre se puede (corrección del operador).
5. **`EXP-2026-0003` (JO Pastelería · RC)** — el caso en *Envío al mercado*. Pestaña **Carpetas**:
   genera la **carpeta de cotización** (PDF maestro + ZIP con la raíz y el subexpediente 1) y
   descárgala.
6. **`EXP-2026-0004` (Coccolino · Multirriesgo)** — el caso en *Propuesta emitida*. Aquí están la
   **carpeta comparativa** y la **carpeta de propuesta** ya generadas; y en la pestaña Propuesta,
   la oferta adjudicada con sus 3 hermanas rechazadas.
7. **`EXP-2026-0005` (La Favorita)** — el caso *Vigente*. Desde el expediente salta a la
   **póliza 15-04-0091883**: ahí está el **sub-embudo de post-venta** (E1, E2, CB1, SN1, RN1), el
   **seguimiento de garantías** (las 12 medidas) y la validación espejo.
8. Desde la póliza entra a **Cobranza** (plan saldado, con el rechazo de PAC en el libro de cuotas)
   y a **Siniestros → S-2027-58814** (partidas, deducible, informe del liquidador).

### 2. `usuario11@fuenzalidasr.cl` — administradora de la otra corredora

1. **`EXP-2026-0001` (Viña Santa Alicia)** — el caso en *Comparación*: 3 ofertas confirmadas y el
   comparativo generado. Es el mejor lugar para ver el comparador lado a lado.
2. **`EXP-2025-0001` (Viña Indómita)** → póliza **0020119904** → **Cobranza**: aquí la cobranza
   está **morosa**, con un cupón de 67 días. Contrasta con la de La Favorita.
3. Los dos endosos: **E1 suma prima** y **E2 la devuelve** (prima negativa).
4. **Siniestro SIN-2026-0417**, liquidado en UF 2.399,40.
5. En **Configuración** (sólo admin) están los usuarios y el perfil de la corredora.
6. En **Cotizaciones** está además el caso de la data base con **2 de 3 propuestas de
   aseguradoras externas** (ver más abajo).

### 3. `usuario4@ossacovarrubias.cl` — técnico

Lo mismo que el ejecutivo, con dos diferencias que vale la pena comprobar:

- **Aprueba**: propuestas, endosos y siniestros. Es el rol de la estandarización técnica.
- **No puede editar ni convertir leads**: los ve en modo lectura. El botón *Convertir* aparece
  deshabilitado con la explicación, no desaparece.

### 4. `usuario3@ossacovarrubias.cl` — inspector

Sirve para comprobar que el aislamiento por rol funciona:

- El menú se le achica solo: Dashboard, Expedientes, Colocaciones, Inspecciones, Pólizas,
  Siniestros y Agente IA. No hay Clientes, Leads, Cotizaciones, Propuestas ni Aseguradoras.
- En **Expedientes** ve **sólo los que tienen una inspección asignada** — en la práctica,
  `EXP-2026-0002` (Coccolino · Vehículos, folio 2026000512339).
- En la pestaña **Carpetas** el botón de generar aparece **deshabilitado**, con el mensaje de que
  falta el permiso. **Esto no es una falla**: generar carpetas exige el permiso *Submit* sobre
  Expedientes, que tienen administrador, ejecutivo y técnico, **no el inspector**. Tampoco puede
  mover el expediente de etapa, por el mismo permiso.

### 5. `usuario6@corredoraunidad.cl` — la corredora sin expedientes

Entra para ver los estados vacíos: Unidad Corredores no tiene expedientes, ni leads, ni pólizas.
Es la vista de una corredora que recién parte.

---

## Lo que todavía **no** se puede demostrar

Para que nadie lo reporte como error:

- **La renovación no tiene documentos.** El corpus del equipo no incluye una carpeta de
  renovación, así que `EXP-2026-0005-RN1` existe con su etapa *Revisión de renovación* y sus
  notas, pero **sin archivos**. Es un marcador de posición, no un flujo terminado. El ítem
  **Renovaciones** del menú sigue marcado "PRONTO".
- **No existen los portales del asegurado ni de la aseguradora.** Están modelados en la base de
  datos, pero no hay pantallas ni usuarios de esos tipos: hoy la aplicación es exclusivamente el
  puesto de trabajo de la corredora.
- **Pipeline, Facturación y Reportes** siguen deshabilitados con el sello "PRONTO".
- **El expediente en etapa Lead no trae archivos** por diseño (se cargan sólo con `--full` en el
  importador): un lead es un dato comercial, no una carpeta.
- **La generación de PDF con marca** para las ofertas al asegurado sigue siendo una versión
  preliminar.
- **La validación espejo sí funciona, pero con datos transcritos a mano.** Para que la pantalla
  no esté vacía, el importador dejó cargadas dos lecturas de los documentos reales de La Favorita
  (la propuesta 07 y la póliza 08) transcritas por una persona, no por el modelo. Por eso la
  comparación aparece marcada como **provisional**: es correcta y las diferencias que muestra son
  reales, pero todavía nadie las confirmó en la aplicación. Al confirmarlas, la marca desaparece.

---

## Caso destacado: aseguradoras nativas vs externas

Las **nativas** son las que Radal tiene con acuerdo comercial (7 de 25); las **externas**
aparecen sólo porque una corredora subió una propuesta de ellas (18 de 25).

**Fuenzalida SR (cotización de la Clínica, en la data base)** es el caso a revisar — 2 de sus 3
propuestas son de aseguradoras externas:

| Propuesta | Aseguradora | Tipo | Estado | Prima total |
|---|---|---|---|---|
| #7 | HDI Seguros | nativa | rechazada | UF 113,26 |
| #8 | Orion Seguros Generales | **externa** | rechazada | UF 116,42 |
| #9 | Reale Chile Seguros Generales | **externa** | **aceptada** | UF 100,07 |

En las otras dos corredoras las 3 propuestas son de aseguradoras nativas (HDI, Chubb, Mapfre).
En los expedientes del corpus el mismo fenómeno reaparece: Consorcio, Porvenir y Unnio son
externas y participan en las rondas de La Favorita, Coccolino y JO Pastelería.

---

## Aislamiento entre corredoras (importante)

Cada usuario ve **únicamente** los datos de su corredora. Un usuario de Fuenzalida SR no
puede ver el cliente, los expedientes ni los documentos de Ossa Covarrubias, aunque escriba
la URL a mano: el servidor responde 404 o 403.

El **asegurado** y la **aseguradora** sí son entidades compartidas entre corredoras
(identificadas por RUT, y RUT + código CMF respectivamente), pero cada corredora sólo alcanza
las suyas a través de sus propios registros.

**Documentos:** cada corredora sólo puede descargar los suyos. En el ambiente de la nube los
archivos viven en S3 (`radal-dev-185011028331`) y el sistema entrega una URL firmada válida por
15 minutos; en local se sirven desde `backend/media/`.

---

## Detalles del corpus que conviene conocer

- **Las corredoras del corpus son las mismas de la data base.** Los documentos imprimen
  RUT 77.245.318-9 para Ossa Covarrubias y 76.114.982-3 para Fuenzalida SR, distintos de los
  RUT de la ficha (78.069.390-8 y 77.290.425-8) y además inválidos en módulo 11. Son un dato
  del documento, no la identidad del tenant: el importador reconoce la corredora **por nombre**
  y esos RUT quedan sólo en los metadatos del expediente.
- **GRUPO VIÑA INDÓMITA es una cuenta comercial con dos RUT** — Viña Indómita SpA 99.568.600-7 y
  Viña Santa Alicia SpA 96.688.830-K — y por lo tanto **dos clientes** distintos, con expedientes
  separados.
- **"JO Pastelería" es el nombre de fantasía de Pacto Food SpA** (77.371.326-K). En las pantallas
  de cliente aparece la razón social; en el título del expediente, el nombre comercial.
- **Los corredores anteriores** que aparecen en las pólizas reales (Marsh, Mondaca Urbina, EGR)
  son texto en los metadatos: nunca crean una corredora.
- **Aseguradoras**: se reconocen por RUT y código CMF normalizados, **nunca por nombre** (los
  documentos escriben `HDI Seguros S.A.`, `HDI SEGUROS SA` y `H.D.I.` indistintamente).
- **Documentos**: se cargan 89 de los 168 archivos del corpus — los que corresponden a la etapa
  en que está cada expediente; un expediente en *Antecedentes* todavía no tiene póliza. Los 168
  se clasifican sin excepción (`--dry-run` imprime el detalle) y **ninguno queda sin categoría**.
- **Fechas**: varios expedientes del corpus tienen fechas de 2027 y 2028 (la póliza de La Favorita
  corre del 05-01-2027 al 05-01-2028). Es así en los documentos originales; no es un error de
  carga.
