# Radal

Operational web app for an insurance broker (*corredora de seguros*). Multi-tenant,
JWT-authenticated, with modules for clientes, pólizas, renovaciones, cotizaciones,
siniestros e inspecciones, plus a single-screen dashboard.

- **Backend** — FastAPI + SQLAlchemy 2.x + SQLite, managed with [`uv`]. API base path `/api/v1`.
- **Frontend** — React + TypeScript + Vite + Tailwind, i18n (es/en), TanStack Query.
- **Contract** — the authoritative request/response shapes live in [`docs/api-contract.md`](docs/api-contract.md).

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

### 2. Frontend (port 5173)

```bash
cd frontend
npm install
cp .env.example .env            # sets VITE_API_URL=http://localhost:8000/api/v1
npm run dev
```

Open <http://localhost:5173> and log in with the demo credentials below.

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
