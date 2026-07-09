# radal-backend

Radal corredora operational backend — FastAPI + SQLAlchemy 2.x + SQLite, uv-managed.

## Setup

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env   # already provided
python -m app.db.seed  # drops + creates tables and loads RADAL demo dataset
uvicorn app.main:app --reload --port 8000
```

API base path: `/api/v1`. Health: `GET /api/v1/health`.

Demo login: `jose@radalseguros.cl` / `radal1234`.
