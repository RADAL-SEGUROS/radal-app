# Radal

**Radal is the platform** (software provider) for insurance distribution in Chile.
The **broker is the tenant**; the app is their proposal-centric workspace.

- **Backend** — FastAPI + SQLAlchemy 2.x (SQLite locally, MySQL on RDS), managed with [`uv`]. API base `/api/v1`.
- **Frontend** — React + TypeScript + Vite + Tailwind, TanStack Query, framer-motion, i18n.
- **AI** — DeepInfra (OpenAI-compatible) for proposal extraction and chat agents.

> **Code is English; the UI is Spanish.** Every table, column, model and comment is in
> English. Spanish appears only as i18n locale *values* (es is default, en mirrors it).

## Architecture

Canonical specs — read these before changing the model:

| Doc | What it covers |
|---|---|
| [`docs/v2-architecture.md`](docs/v2-architecture.md) | Actors, full data model + ER diagram, rules, AI, S3 layout, scope |
| [`docs/v2-data-modeling-decisions.md`](docs/v2-data-modeling-decisions.md) | Why each field is a column vs JSON vs child table (evidence-based) |
| [`docs/deployment.md`](docs/deployment.md) | AWS: Lambda + CloudFront + RDS + ECR, and the 4 OAC constraints |

**Core shape:** `broker` (tenant) → `client` → `asset` → `placement` → `quote_request` →
**`proposal`** → `offering`. The canonical, cross-broker entities are `insured` (keyed by RUT)
and `insurer` (keyed by RUT + CMF code).

**Key rules**
- A **proposal requires a source document** and an insurer with `rut` + `cmf_code`.
- Money follows the Chilean market: `net = taxable + exempt`, `vat = 0.19 × taxable`
  (**not** on net — earthquake cover is VAT-exempt), `total = net + vat`. All UF.
- Insurers are **native** (Radal commercial partners, recommended, rich contacts) or
  **external** (auto-created from an uploaded proposal — a commercial signal).
- **The broker is never blocked.** Confidentiality comes from tenant scoping, not gates.
- **No dead buttons** — a control is wired to a working endpoint, or visibly disabled.

## Quickstart

### Backend (port 8000)

```bash
cd backend
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env          # then set AI_API_KEY
python -m app.db.import_fixtures --reset --profile radal   # real demo data + S3 upload
AWS_PROFILE=radal uvicorn app.main:app --reload --port 8000
```

The importer uploads the 66 fixture files to `radal-dev-185011028331` and writes the matching
document rows. Add `--no-upload` to skip S3 entirely (DB rows only) when you have no AWS access.

With `MEDIA_BACKEND=s3` the download endpoint returns a 15-minute presigned URL, so the
backend process needs AWS credentials (`AWS_PROFILE=radal`). Set `MEDIA_BACKEND=local` to
serve from disk instead.

For an empty database with just an admin user: `python -m app.db.seed_dev`.

### Frontend (port 5173)

```bash
cd frontend
npm install
cp .env.example .env          # VITE_API_URL=http://localhost:8000/api/v1
npm run dev
```

### Demo login

All fixture users share the password **`radal1234`**. Three broker tenants are seeded;
sign in as any `broker_admin` — e.g. `usuario1@ossacovarrubias.cl`.

## Tests

```bash
cd backend && source .venv/bin/activate && python -m pytest tests/ -q
```

159 tests cover RUT mod-11, the money invariants, quote line-item sums, proposal
requirements, insurer dedup by code (never by name), proposal acceptance, **tenant
isolation** and RBAC.

Frontend: `npx tsc --noEmit && npm run build`.

## Fixture data

Imported from the team's package (3 broker tenants, 3 insureds, 3 assets, 3 quotes,
9 proposals, 3 inspections, 66 documents, the official 25-company CMF insurer catalog and
the 44-line CMF taxonomy). The importer seeds **7 native / 18 external** insurers, arranged
so that one quote has **2 of its 3 proposals from non-native insurers** — exercising the
external-insurer tracking path.

## Deployment

Unchanged from v1 and still live: Lambda (backend + frontend containers, Lambda Web Adapter)
behind CloudFront, RDS MySQL, ECR, CI via GitHub Actions → `update-function-code`.
See [`docs/deployment.md`](docs/deployment.md) — **do not regress the four CloudFront→Lambda
OAC constraints** documented there.
