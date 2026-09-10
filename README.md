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
cd backend  && MEDIA_BACKEND=local .venv/bin/python -m pytest tests/ -q   # 643 passing
cd frontend && npx tsc -b --noEmit && npm run build && npm run check:locales
```

## Where to read next

- **`CLAUDE.md`** — orientation + the ten non-negotiables + where the work stands (start here).
- **`docs/technical-reference.md`** — the full spec: setup, data model, every endpoint, the RBAC
  matrix, the AI registry, the frontend map, the changelog and the traps.
- **`docs/handoff-2026-09-09-v9.md`** — the latest handoff: the as-built broker journey, a candid
  log of the errors made and how they were corrected, and the deploy state.
- **`docs/deployment.md`** — AWS, CI/CD, the four OAC constraints, the migration runbook.

> Code is English; the UI is Spanish (locale values + LLM prompts only). There is no Alembic —
> schema changes go through `backend/scripts/migrate_case_files.py`. Do not commit `backend/.env`,
> `backend/radal.db` or `backend/media/` (all gitignored).
