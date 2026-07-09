# Radal — Local Development Setup

Step-by-step to run the Radal corredora app locally: FastAPI backend on
`:8000` and Vite/React frontend on `:5173`.

Repo root: `/Users/bgg/Documents/repos/radal/radal-app`
```
backend/    FastAPI + SQLAlchemy + SQLite (uv-managed venv)
frontend/   Vite + React + TypeScript + Tailwind
docs/       Markdown specs
```

Prerequisites: **uv** (Python env/dep manager), **Python 3.11+**, **Node 18+**
with **npm**.

---

## 1. Backend (`:8000`)

All commands run from `backend/`.

```bash
cd backend

# 1. Create the uv-managed virtualenv
uv venv .venv

# 2. Activate it
source .venv/bin/activate          # macOS/Linux (zsh/bash)
# .venv\Scripts\activate           # Windows PowerShell

# 3. Install dependencies
uv pip install -e .                # if pyproject defines deps
# or, if using requirements.txt:
# uv pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env               # then edit as needed

# 5. Seed the database (creates backend/radal.db + demo data)
python -m app.seed                 # seed entrypoint

# 6. Run the API server
uvicorn app.main:app --reload --port 8000
```

- API base path: **`http://localhost:8000/api/v1`**.
- Interactive docs: `http://localhost:8000/docs`.
- DB is a SQLite file at `backend/radal.db`, created via SQLAlchemy
  `create_all` (no alembic this pass). Delete the file and re-seed to reset.

### `backend/.env` (from `.env.example`)
Typical keys:
```
DATABASE_URL=sqlite:///./radal.db
JWT_SECRET_KEY=change-me-in-prod
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
CORS_ORIGINS=http://localhost:5173
```
> **Note:** `.env` is git-ignored. It will later be uploaded to **S3** for shared
> environment provisioning; for now each dev keeps a local copy derived from
> `.env.example`.

---

## 2. Frontend (`:5173`)

All commands run from `frontend/`.

```bash
cd frontend

# 1. Install dependencies
npm install

# 2. Configure environment (point the app at the local API)
cp .env.example .env
# ensure it contains:
#   VITE_API_URL=http://localhost:8000/api/v1

# 3. Run the dev server
npm run dev
```

- App: **`http://localhost:5173`**.
- **`VITE_API_URL`** must point to the backend API base
  (`http://localhost:8000/api/v1`). All axios calls read this.
- Path alias `@/` → `src` (configured in `vite.config.ts` + `tsconfig`).
- The DEV-only floating view-switcher (Radal / Aseguradora / Asegurado) appears
  only when `import.meta.env.DEV` is true — never in production builds.

---

## 3. Brand assets

Copy the logo SVGs into the frontend before first run:

```bash
cp "/Users/bgg/Documents/radal/marketing/brand book radal/assets/radal-mark-white.svg" frontend/public/brand/
cp "/Users/bgg/Documents/radal/marketing/brand book radal/assets/radal-mark-ink.svg"   frontend/public/brand/
cp "/Users/bgg/Documents/radal/marketing/brand book radal/assets/radal-mark-teal.svg"  frontend/public/brand/
```

Referenced in code as `/brand/radal-mark-<variant>.svg`. See
`docs/design-system.md §5`.

---

## 4. Demo login credentials

Seeded tenant: **RADAL SEGUROS** (RUT 76.123.456-7). Password for **all** demo
users is `radal1234`.

| Email                   | Nombre        | Cargo               | Rol                  |
|-------------------------|---------------|---------------------|----------------------|
| `jose@radalseguros.cl`  | José Pérez    | Ejecutivo Comercial | ejecutivo_corredora  |
| `admin@radalseguros.cl` | Admin Radal   | Gerente             | admin_corredora      |
| `ben@nirvana-ai.com`    | Ben           | Owner               | admin_corredora      |

Log in at `http://localhost:5173/login`.

---

## 5. Run order & troubleshooting

1. Start the **backend first** (`uvicorn ... :8000`) so the API is up.
2. Start the **frontend** (`npm run dev` → `:5173`).
3. If login fails, confirm the DB was seeded (`python -m app.seed`) and that
   `VITE_API_URL` matches the backend port.
4. **CORS errors** → ensure `CORS_ORIGINS` in `backend/.env` includes
   `http://localhost:5173`.
5. **Reset data** → stop the server, delete `backend/radal.db`, re-seed.

`.gitignore` covers: `.venv`, `__pycache__`, `*.db`, `node_modules`, `dist`,
`.env`, `.DS_Store`.
