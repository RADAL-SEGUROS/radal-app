# Radal — Corredora App (CLAUDE.md)

> Read this file FIRST. It is the practical guide for any Claude session working in this repo.
> The authoritative deep specs live in `docs/`. When in doubt, `docs/` wins over memory.

## What this is

**Radal** is connective SaaS for the insurance industry. This repository is the **corredora**
(insurance broker) operational web app. It is **multi-tenant**: any corredora can onboard.
The first seeded tenant is **RADAL SEGUROS**.

- Domain + UI copy language: **Spanish (Chile)**.
- Money: **UF** (Unidad de Fomento), stored as numeric.
- Greenfield build. No legacy to preserve.

## The three profiles

Radal is designed for three user profiles (`usuario.rol` enum). Build role-based access **now**,
but only implement corredora screens this pass.

| Profile | Roles | When provisioned |
|---|---|---|
| **corredora** (broker) | `admin_corredora`, `ejecutivo_corredora`, `inspector` | Now — the ONLY users at first |
| **asegurado** (insured client) | `admin_asegurado`, `ejecutivo_asegurado` | AFTER a deal closes |
| **aseguradora** (insurer) | `admin_aseguradora`, `ejecutivo_aseguradora` | When an aseguradora is invited |

**The corredora is always the OWNER of every expediente.** asegurado/aseguradora users only get
created later. Only corredora screens are implemented in this pass.

## Monorepo layout

```
radal-app/
  backend/      FastAPI + SQLAlchemy 2.x + SQLite (uv-managed venv)
  frontend/     Vite + React + TypeScript + Tailwind + shadcn-style components
  docs/         Canonical markdown specs (source of truth)
  CLAUDE.md     This file
```

## Where the specs live (source of truth)

- `docs/architecture.md` — the expediente backbone, ownership, "subir vs enviar", "dynamic by ramo".
- `docs/data-model.md` — **authoritative** logical + physical model. Backend tables/columns must match
  it EXACTLY (snake_case, Spanish nouns). Includes a mermaid ER diagram.
- `docs/api-contract.md` — **authoritative** REST contract. Backend AND frontend build strictly to it.
- `docs/design-system.md` — "Aqua Spectrum" brand tokens, fonts, color rules.

## How to run

### Backend (uv venv + uvicorn)

```bash
cd backend
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt        # or: uv pip install -e .
cp .env.example .env                        # first time only
uvicorn app.main:app --reload --port 8000
```

- SQLite file: `backend/radal.db`. Schema via SQLAlchemy `create_all` (no alembic this pass).
- Config from `backend/.env` (see `backend/.env.example`).
- API base path: `/api/v1`. Auth: JWT Bearer (access + refresh).
- Seed data (RADAL SEGUROS demo) runs on first boot or via a seed script — makes KPIs look real.

### Frontend (npm)

```bash
cd frontend
npm install
npm run dev        # Vite dev server (proxies /api -> backend :8000)
```

- Path alias `@/` -> `src`.
- Component style: shadcn conventions (cva variants, `cn()` merge).

## Multi-tenant rule (NON-NEGOTIABLE)

- Every domain table carries **`corredora_id`** (FK -> `corredora.id`).
- Every query is **scoped by tenant**. The tenant comes from the authenticated user's `corredora_id`
  (JWT claim). Never trust a `corredora_id` from the request body for scoping.
- Uniqueness (e.g. `numero_poliza`, ramo names) is per-tenant, not global.

## i18n rule (NON-NEGOTIABLE)

- `react-i18next` + `i18next`. Default locale **`es`**, secondary **`en`**.
- **ALL** UI strings go through `t()`. No hardcoded copy in components.
- Namespaced per module: `frontend/src/locales/{es,en}/<ns>.json` where
  `ns ∈ [common, auth, dashboard, clientes, polizas, renovaciones, cotizaciones, siniestros, inspecciones]`.
- `es` must be **complete**; `en` **mirrors the same keys** (translated).

## Dev-only view-switcher rule (NON-NEGOTIABLE)

- A floating view-switcher (Radal / Aseguradora / Asegurado) exists to preview the three profiles.
- It MUST be hidden unless `import.meta.env.DEV`. **Never render it in production.**

## Coding conventions

- **DB**: lowercase `snake_case`, Spanish domain nouns (`cliente`, `poliza`, `prima`).
- **Money**: UF as numeric. **Percentages**: 0-100.
- **Timestamps**: `created_at` / `updated_at`, UTC.
- **REST**: English-plural paths mirroring Spanish tables, under `/api/v1`. JSON everywhere.
  Resources: `/clientes /polizas /renovaciones /cotizaciones /siniestros /inspecciones`
  `+ /dashboard + /search + /auth + /aseguradoras + /ramos`.
- **Frontend routes**: `/login`, `/` (dashboard), `/clientes`, `/clientes/:id`, `/polizas`,
  `/polizas/:id`, `/renovaciones`, `/renovaciones/:id`, `/cotizaciones`, `/siniestros`,
  `/siniestros/:id`, `/inspecciones`, `/inspecciones/:id`.
- **Frontend stack**: react-router-dom v6, @tanstack/react-query, axios, react-hook-form + zod,
  tailwind + cva + clsx + tailwind-merge, lucide-react, @radix-ui primitives, recharts, date-fns, sonner.
- **Backend stack**: fastapi, uvicorn[standard], sqlalchemy 2.x, pydantic v2, pydantic-settings,
  python-jose[cryptography], passlib[bcrypt], python-multipart, email-validator.

## Business rules cheat-sheet

- **Coverage analyzer** (polizas/renovaciones by `cobertura_pct`): `<95%` infracobertura,
  `95–105%` óptima (green), `>105%` sobrecobertura.
- **Días restantes** — Renovaciones: ámbar if `<=60` days else gris; "Por vencer 30D" KPI in red.
  Cotizaciones: rojo if `<=4` days else ámbar; "Por vencer L7D" = `<7` days.
- **Dashboard is no-scroll** (everything above the fold): global search, time-of-day greeting,
  4 KPI cards, quick actions, "Requiere atención" (top 3), principales clientes, actividad reciente
  (last 2, accordion -> last 10), próximas renovaciones (2–5).

## Sidebar nav

- **Gestión**: Dashboard, Clientes, Pólizas, Renovaciones, Cotizaciones, Siniestros, Inspecciones.
- **Comercial y operaciones**: Pipeline, Facturación, Reportes — OUT OF MVP: render disabled/greyed.
- Bottom: collaborator name + cargo. Top-right user chip. Theme toggle + language toggle.

## .gitignore must include

`.venv`, `__pycache__`, `*.db`, `node_modules`, `dist`, `.env`, `.DS_Store`.
