# Radal

Operational web app for an insurance broker (*corredora de seguros*). Multi-tenant,
JWT-authenticated, with modules for clientes, pólizas, renovaciones, cotizaciones,
siniestros e inspecciones, plus a single-screen dashboard.

- **Backend** — FastAPI + SQLAlchemy 2.x + SQLite, managed with [`uv`]. API base path `/api/v1`.
- **Frontend** — React + TypeScript + Vite + Tailwind, i18n (es/en), TanStack Query.
- **Contract** — the authoritative request/response shapes live in [`docs/api-contract.md`](docs/api-contract.md).

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

## Prerequisites

- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv)
- Node.js 18+ and npm

## Quickstart

Run the two servers in separate terminals.

### 1. Backend (port 8000)

```bash
cd backend
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env            # already provided with sane defaults
python -m app.db.seed           # drops + recreates tables, loads the RADAL demo dataset
uvicorn app.main:app --reload --port 8000
```

Verify it's up:

```bash
curl http://localhost:8000/api/v1/health     # -> {"status":"ok","service":"radal-backend"}
```

Interactive API docs: <http://localhost:8000/docs>.

### 2. Frontend (port 4000)

```bash
cd frontend
npm install
cp .env.example .env            # sets VITE_API_URL=http://localhost:8000/api/v1
npm run dev
```

Open <http://localhost:4000> and log in with the demo credentials below.

## Demo credentials

The seed loads one tenant (**RADAL SEGUROS**). All demo users share the password
**`radal1234`**.

| Email                     | Rol                  | Notes                    |
| ------------------------- | -------------------- | ------------------------ |
| `jose@radalseguros.cl`    | ejecutivo_corredora  | Default demo login       |
| `admin@radalseguros.cl`   | admin_corredora      | Full read/write + config |
| `carla@radalseguros.cl`   | inspector            | Owns inspecciones        |
| `ben@nirvana-ai.com`      | admin_corredora      | Owner                    |

## Auth notes

- `POST /api/v1/auth/login` takes a **JSON** body: `{"email": "...", "password": "..."}`.
- A separate OAuth2 form endpoint exists at `/api/v1/auth/login/token` (`username` = email)
  for tooling / Swagger's "Authorize" button.
- Every request is tenant-scoped to the authenticated user's `corredora_id` (from the JWT).

## Production-style build

```bash
# Frontend
cd frontend && npm run build      # tsc -b && vite build  -> dist/

# Backend (no reload)
cd backend && uvicorn app.main:app --port 8000
```

## Project layout

```
backend/    FastAPI app (app/), Pydantic schemas, SQLAlchemy models, seed script, SQLite db
frontend/   Vite React app (src/pages per module, src/lib/api.ts, i18n locales)
docs/       api-contract.md (authoritative) + data-model.md and supporting docs
```

## Troubleshooting

- **Seed crashes on password hashing** — passlib 1.7.4 cannot drive bcrypt 5.x. The
  requirement is pinned to `bcrypt>=4.0,<4.1`; if you hit
  `ValueError: password cannot be longer than 72 bytes`, run
  `uv pip install "bcrypt>=4.0,<4.1"` and re-seed.
- **Frontend shows Spanish for an English user / missing labels** — ensure both
  `src/locales/es/*.json` and `src/locales/en/*.json` namespaces are present; all nine
  namespaces are registered in `src/i18n/index.ts`.
</content>
</invoke>
