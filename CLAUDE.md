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
  backend/      FastAPI + SQLAlchemy 2.x — SQLite locally, MySQL on AWS RDS (uv-managed venv)
  frontend/     Vite + React + TypeScript + Tailwind + shadcn-style components + framer-motion
  deploy/       docker-compose + deploy scripts for the EC2 dev box
  docs/         Canonical markdown specs (source of truth)
  .github/      CI/CD (dev-deploy.yml -> ECR -> SSM -> EC2)
  CLAUDE.md     This file
```

## Where the specs live (source of truth)

- `docs/architecture.md` — the expediente backbone, ownership, "subir vs enviar", "dynamic by ramo".
- `docs/data-model.md` — **authoritative** logical + physical model. Backend tables/columns must match
  it EXACTLY (snake_case, Spanish nouns). Includes a mermaid ER diagram.
- `docs/api-contract.md` — **authoritative** REST contract. Backend AND frontend build strictly to it.
- `docs/design-system.md` — "Aqua Spectrum" brand tokens, fonts, color rules.
- `docs/deployment.md` — **AWS infra + CI/CD** (EC2 dev box + RDS MySQL + S3 + ECR, deploy via SSM).

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
- Seed: `python -m app.db.seed` (drops+creates+inserts the RADAL demo — makes KPIs look real).
- **Engine portability**: `DATABASE_URL` switches SQLite (`sqlite:///./radal.db`) ↔ MySQL
  (`mysql+pymysql://…` on RDS). MySQL requires bounded `VARCHAR` — a `@compiles(String,"mysql")`
  hook in `app/models/base_class.py` defaults length-less strings to `VARCHAR(255)`; long free text
  uses `Text`. Avoid engine-specific SQL (no `NULLS LAST`; use `col.is_(None)` ordering). Keep
  `GROUP BY` `ONLY_FULL_GROUP_BY`-safe. Demo login: `jose@radalseguros.cl` / `radal1234`.

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

## Access is role-based — no view-switcher (NON-NEGOTIABLE)

- There is **NO** view-switcher. The old dev-only Radal/Aseguradora/Asegurado toggle was **removed** —
  this is a real app, not a design proposal. Do not re-introduce any client-side role/view switching.
- The user's role + cargo come solely from the authenticated profile
  (`AuthProvider` → `/auth/me` → `usuario.rol` / `usuario.cargo`), shown in the sidebar user card.
- Only the **corredora** view is built. A super-admin (all views) comes later.

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

## UI look & motion

- Visual target = the "Aqua Spectrum" reference: soft **16px** cards on `--bone` with layered shadow +
  hover-lift, refined light/dark token ramp (teal primary/active, **blue** primary actions, lime success,
  amber/red signals), Space Grotesk headings + KPI numbers, Inter Tight body, IBM Plex Mono for IDs.
- Motion via **framer-motion**: staggered `fadeUp` section entrance, KPI count-up, `dropIn` search
  dropdown, animated activity accordion, hover micro-interactions. All respect `prefers-reduced-motion`.
  Shared helpers in `src/components/common/motion.tsx`.

## AWS / deployment (dev)

Full detail in `docs/deployment.md`. Shape (nirvana-style): **Lambda (container images) + CloudFront +
RDS MySQL + ECR**, deployed by `.github/workflows/dev-deploy.yml` (push to `dev`, **Blacksmith** runners) →
build+push images → ECR → `aws lambda update-function-code` → CloudFront invalidation.

- **Account** `185011028331`, **region** `us-east-1`. Use the **`radal`** AWS CLI profile locally
  (`--profile radal`) — **never** overwrite `[default]`.
- **CloudFront** `E2MGVTSWQDFPY3` → `https://d2tup8vfejxx98.cloudfront.net`: default behavior →
  `radal-frontend-dev` Lambda (Express serves the Vite SPA), `/api/*` → `radal-backend-dev` Lambda (FastAPI,
  VPC-attached to the private RDS `radal-dev-db`). Both are ECR container images running the **Lambda Web
  Adapter** (`aws-lambda-adapter:0.7.0`); frontend server is `frontend/server.cjs`.
- **Function URL auth**: this account blocks public (`NONE`) Function URLs, so URLs use **`AWS_IAM`** and
  CloudFront signs to them via **OAC** (`E3BGCO4XOX79HP`) + the managed AllViewerExceptHostHeader policy.
  Don't switch them to `NONE`. Lambda-compatible images require `docker buildx --provenance=false`.
  **OAC gotchas (see docs/deployment.md, don't regress)**: Lambdas need both `lambda:InvokeFunctionUrl`
  + `lambda:InvokeFunction`; POST bodies need `x-amz-content-sha256` (frontend axios); the JWT rides in
  **`X-Radal-Token`** (OAC steals `Authorization`); LWA + Function URL invoke mode must both be `buffered`.
- CI auth uses the scoped IAM user **`radal-github-actions`** (NOT admin keys). GitHub secrets:
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `ECR_REGISTRY`, `BACKEND_LAMBDA`,
  `FRONTEND_LAMBDA`, `CLOUDFRONT_DISTRIBUTION_ID`. Values in `~/radal-cicd-secrets.txt`.
- ECR **lifecycle policy keeps only the last 5 images** per repo. RDS is the main idle cost — stop it when unused.
- Route 53 / custom domain: TODO (running on `*.cloudfront.net`); migrate GoDaddy → Route 53 + ACM (us-east-1) later.
- Real secrets (`*.pem`, DB creds) live in S3 / local only — **never** commit them.

## .gitignore must include

`.venv`, `__pycache__`, `*.db`, `node_modules`, `dist`, `.env`, `.DS_Store`, `deploy/backend.env`, `*.pem`.
