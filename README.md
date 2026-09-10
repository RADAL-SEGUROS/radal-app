# Radal

**Radal is the platform for insurance distribution in Chile** — the software provider. The
**broker (`corredora`) is the tenant**; Radal is never itself a broker. The product exists because
the placement cycle lives in email, WhatsApp and spreadsheets today. Radal makes the **expediente**
the folder the broker works in and normalises every insurer offer into one comparable shape.

## The broker journey

```
group (icon) → grupo-cuenta (ramo + vigencia)
   → Antecedentes → Bases Técnicas → Comparación → Propuesta → Pólizas
```

A **ramo** is an advisory recommended-files template (chosen at account creation). **Antecedentes**
gathers the ramo's recommended files + free uploads and extracts them with AI. **Bases Técnicas** is
the completed antecedentes as a branded PDF. **Comparación** is a dynamic, incremental comparison of
insurer cotizaciones with an AI recommendation. **Propuesta** is the outbound artifact built from the
comparison. **Pólizas** validates-then-dynamically extracts uploaded policies.

**"Ver expediente completo"** opens the account's super-overview — identidad, the journey with real
completion dates, every document, montos — plus a branded PDF, rendered on demand so it is always
current. What is missing prints *why* in Spanish, never a blank.

## The three ways to look at the book

The rail is GRUPOS plus five rows: **Datos · Analítica · Agente · Compañías · Configuración.**

- **Datos** (`/data`) — every consolidated table.
- **Analítica** (`/analytics`) — the shape of the portfolio: indicators and distribution.
- **inside a grupo** — one account's detail.

Both Datos and Analítica share one **scope** — grupo · grupo-cuenta · fechas — held in the URL, so a
filtered view is a link you can send. Everything exports to **XLSX or PDF** (charts to **PNG**), over
every row matching the filters rather than the page on screen. Comparing several groups side by side
is deliberately not a feature — ask the agent.

## Cómo reportar algo (para todo el equipo)

Si algo te llamó la atención usando Radal, **repórtalo**. No hace falta saber
programar ni describirlo con términos técnicos: basta con contar qué pasó.

Se reporta en **[Issues](../../issues) → New issue**, eligiendo uno de tres tipos.
Elegir el tipo correcto no es crítico —si te equivocas lo movemos nosotros—, pero
ayuda a que llegue antes a quien corresponde.

### Los tres tipos

| Tipo | Cuándo usarlo | En una frase |
|---|---|---|
| 🐞 **Error** (`bug`) | Algo **está mal y hay que corregirlo** | "Esto no debería funcionar así" |
| 🔧 **Mejora** (`improvement`) | Algo **funciona, pero debería ser distinto** | "Esto funciona, pero cuesta más de lo que debería" |
| ✨ **Funcionalidad** (`feature`) | Algo **falta y hay que desarrollarlo** | "Esto todavía no existe y lo necesito" |

**🐞 Error — algo está mal.**
La función existe, pero se comporta de forma equivocada: un botón que no hace
nada, una pantalla que queda en blanco, un PDF que no se descarga, o —la más
importante y la más fácil de dejar pasar— **un número que sale distinto al que
debería**. Un dato equivocado se ve igual de normal que uno correcto, así que si
notas uno, dinos **qué número viste y cuál debería ser**.

*Ejemplos:* "Exporté las pólizas de un grupo y el Excel trajo las de toda la
corredora." · "La prima de la propuesta muestra 1.120 y en la póliza dice 1.316."

**🔧 Mejora — algo debería ser distinto.**
Funciona y no está roto, pero es incómodo: da demasiadas vueltas, es lento, se
entiende mal, o te obliga a repetir algo que la aplicación podría recordar. Si te
descubriste pensando *"otra vez lo mismo"*, eso es una mejora.

Lo más útil que puedes contarnos acá es **cada cuánto te topas con eso**: algo que
te molesta cinco veces al día pesa mucho más que algo que pasó una vez.

*Ejemplos:* "Para llegar a las pólizas de una cuenta hago cinco clics cada vez." ·
"Cuando me equivoco de pestaña pierdo los filtros y hay que empezar de nuevo."

**✨ Funcionalidad — algo falta.**
No existe todavía en Radal, y por eso lo estás resolviendo por fuera: en un Excel
aparte, por correo, por WhatsApp o en papel. **Cuéntanos el problema antes que la
solución** —qué necesitas lograr y por qué importa—; la forma exacta la definimos
nosotros. Y dinos cómo lo haces hoy, aunque sea "a mano": eso es justamente lo que
la funcionalidad tiene que reemplazar.

*Ejemplos:* "Cuando la compañía pide antecedentes adicionales no tengo dónde
registrar qué pidió ni cuándo." · "No hay forma de avisarle al cliente que su
póliza vence."

### Si no sabes cuál elegir

Hazte una sola pregunta: **¿funciona?**

- **No funciona** → 🐞 Error
- **Funciona, pero cuesta o molesta** → 🔧 Mejora
- **No existe** → ✨ Funcionalidad

Y si sigues sin saber, elige el que más se acerque y escríbelo igual. Prefiero mil
veces un reporte en la categoría equivocada que uno que nunca se escribió.

### Qué incluir siempre

El formulario te va a pedir estos datos; vale la pena tenerlos a mano:

1. **La versión.** En Radal, entra a **Configuración** y abajo a la derecha hay una
   línea como `Radal · dev · 08d0293 · 10 sep 2026`. **Haz clic y se copia sola.**
   Sin esto no sabemos contra qué versión ocurrió y hay que investigar de cero.
2. **La dirección (URL).** Copia la barra del navegador completa. En Radal los
   filtros viajan en la dirección, así que esa línea sola suele bastar para
   reproducir exactamente lo que estabas viendo.
3. **Con qué usuario y corredora** estabas trabajando. Radal cambia según el
   permiso de cada rol, así que lo mismo puede fallarle a una persona y no a otra.
   **Nunca escribas tu contraseña**, en ningún campo.
4. **Una captura de pantalla o un video.** Arrástralo al formulario. Una imagen
   completa —con la dirección visible— explica más que varios párrafos. Tapa
   cualquier dato sensible antes de subirla.
5. **Los pasos**, numerados desde donde empezaste. Si no lograste repetirlo, dilo:
   igual sirve, pero avísanos.

> ⚠️ **Si ves datos de otra corredora o de un cliente que no es tuyo, no lo
> publiques como issue.** Escríbenos directamente a ben@nirvana-ai.com. Radal es
> multi-tenant y ese tipo de reporte no debe quedar a la vista de todos.

## Stack

- **Backend** — FastAPI + SQLAlchemy 2.x, SQLite locally / MySQL (RDS) in the cloud, `uv`-managed
  venv, API base `/api/v1`.
- **Frontend** — React + TypeScript + Vite + Tailwind (Signal design system), TanStack Query,
  react-i18next. Dev server on `:5500`.
- **AI** — DeepInfra `zai-org/GLM-5.3-Flash` via tool-calling (structured output), a per-task
  model-tiering registry, suggest → human-confirm → commit.
- **Deploy** — Lambda (container images + Lambda Web Adapter, buffered) + CloudFront + OAC + RDS
  MySQL + ECR + S3; CI via GitHub OIDC on push to `dev` → `https://dev.radalseguros.cl`.

## Run it locally

```bash
# backend :8000 — MEDIA_BACKEND=local or every document 404s (nothing dev is on S3 locally)
cd backend
uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python
cp .env.example .env                      # set AI_API_KEY
MEDIA_BACKEND=local .venv/bin/python -m app.db.import_fixtures --reset --no-upload   # blank baseline
MEDIA_BACKEND=local .venv/bin/uvicorn app.main:app --reload --port 8000

# frontend :5500
cd frontend && npm install && npm run dev
```

Demo login: **`usuario11@fuenzalidasr.cl` / `radal1234`** (Fuenzalida broker admin). The seed is a
**blank baseline** (brokers, users, 25 insurers, insurance lines, 2 ramo templates, no accounts) —
you create the journey live. See `docs/usuarios-de-prueba.md` for the full roster.

## Verify

```bash
cd backend  && MEDIA_BACKEND=local .venv/bin/python -m pytest tests/ -q   # 752 passing
cd frontend && npx tsc -b --noEmit && npm run build && npm run check:locales
```

## Where to read next

- **`CLAUDE.md`** — orientation + the ten non-negotiables + where the work stands (start here).
- **`docs/technical-reference.md`** — the full spec: setup, data model, every endpoint, the RBAC
  matrix, the AI registry, the frontend map, the changelog and the traps.
- **`docs/handoff-2026-09-10-v10.md`** — the latest handoff: Datos/Analítica, the expediente
  completo, the shared filter vocabulary and the exports, plus a candid log of the errors made and
  how each was caught.
- **`docs/handoff-2026-09-09-v9.md`** — the previous pass: the as-built broker journey and its own
  errors-and-corrections log.
- **`docs/deployment.md`** — AWS, CI/CD, the four OAC constraints, the migration runbook.

> Code is English; the UI is Spanish (locale values + LLM prompts only). There is no Alembic —
> schema changes go through `backend/scripts/migrate_case_files.py`. Do not commit `backend/.env`,
> `backend/radal.db` or `backend/media/` (all gitignored).
