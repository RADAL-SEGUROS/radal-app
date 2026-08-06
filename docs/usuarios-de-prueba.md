# Radal — Usuarios de prueba (ambiente de desarrollo)

**Contraseña de todos los usuarios: `radal1234`**

> Radal es **la plataforma** (proveedor de software). Cada **corredora es un tenant**
> independiente: sólo ve sus propios datos. Los nombres "Usuario 1…15" vienen así de la
> data de prueba; los correos y roles sí son funcionales.

---

## Las 3 corredoras (tenants)

| # | Corredora | RUT | Código CMF | Cliente que atiende |
|---|---|---|---|---|
| 1 | **Ossa Covarrubias Ltda.** | 78.069.390-8 | 6168 | Agroindustrial Santa Elisa SpA |
| 2 | **Unidad Corredores** | 77.128.471-K | 9688 | Distribuidora y Logística Altamira SpA |
| 3 | **Fuenzalida SR** | 77.290.425-8 | 9192 | Clínica del Valle de Ñuble SpA |

Cada corredora tiene exactamente: **1 cliente · 1 activo · 1 colocación · 1 cotización ·
3 propuestas · 1 inspección · 22 documentos**.

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

| Rol | Módulos visibles | Diferencia clave |
|---|---|---|
| **`broker_admin`** | Todos + **Configuración** | Único que administra usuarios y el perfil de la corredora |
| **`broker_executive`** | Todos, **sin** Configuración | Operativo: no borra, no aprueba, no administra usuarios |
| **`broker_inspector`** | **Sólo Dashboard, Colocaciones e Inspecciones** | No ve clientes, cotizaciones, propuestas ni aseguradoras |
| **`broker_technician`** | Todos, **sin** Configuración | Es el único que **aprueba propuestas** (estandarización técnica) |

Los permisos vienen del servidor (`GET /auth/permissions`); el menú lateral se arma con esa
respuesta. Si un rol no tiene permiso, **el ítem no aparece** — nunca un link que lleve a un error.

Fuera de alcance de esta versión y por eso **deshabilitados con el sello "PRONTO"**:
Pólizas, Siniestros, Renovaciones, Pipeline, Facturación, Reportes.

---

## Caso destacado: aseguradoras nativas vs externas

Las **nativas** son las que Radal tiene con acuerdo comercial (7 de 25); las **externas**
aparecen sólo porque una corredora subió una propuesta de ellas (18 de 25).

**Fuenzalida SR (cotización de la Clínica)** es el caso a revisar — 2 de sus 3 propuestas
son de aseguradoras externas:

| Propuesta | Aseguradora | Tipo | Estado | Prima total |
|---|---|---|---|---|
| #7 | HDI Seguros | nativa | rechazada | UF 113,26 |
| #8 | Orion Seguros Generales | **externa** | rechazada | UF 116,42 |
| #9 | Reale Chile Seguros Generales | **externa** | **aceptada** | UF 100,07 |

En las otras dos corredoras las 3 propuestas son de aseguradoras nativas (HDI, Chubb, Mapfre).

---

## Aislamiento entre corredoras (importante)

Cada usuario ve **únicamente** los datos de su corredora. Un usuario de Fuenzalida SR no
puede ver el cliente, las cotizaciones ni los documentos de Ossa Covarrubias, aunque escriba
la URL a mano: el servidor responde 403.

El **asegurado** y la **aseguradora** sí son entidades compartidas entre corredoras
(identificadas por RUT, y RUT + código CMF respectivamente), pero cada corredora sólo alcanza
las suyas a través de sus propios registros.

---

## Acceso

| | |
|---|---|
| Aplicación | http://localhost:5500 |
| API | http://localhost:8000/api/v1 |
| Documentación de la API | http://localhost:8000/docs |

**Documentos:** los 66 archivos del paquete del equipo están cargados en S3
(`radal-dev-185011028331`) y las descargas funcionan — el sistema entrega una URL firmada
válida por 15 minutos. Cada corredora sólo puede descargar sus propios documentos.
