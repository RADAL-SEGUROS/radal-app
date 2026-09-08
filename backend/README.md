# radal-backend

FastAPI + SQLAlchemy 2.x, `uv`-managed. SQLite locally, MySQL on RDS. API base `/api/v1`;
health check `GET /api/v1/health`.

**The authoritative documentation is [`../docs/technical-reference.md`](../docs/technical-reference.md)** — setup, the full data model,
every endpoint, the permission matrix, the AI extraction registry, the changelog and known issues.
Product and domain context lives in [`../CLAUDE.md`](../CLAUDE.md).

Quick start (local, no AWS — the corpus bytes are mirrored under `media/`):

```bash
uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python
.venv/bin/python -m playwright install chromium        # one-time: browser for HTML->PDF exports
cp .env.example .env                                   # set AI_API_KEY

MEDIA_BACKEND=local .venv/bin/python -m app.db.import_fixtures --reset --no-upload
MEDIA_BACKEND=local .venv/bin/python -m app.db.import_expedientes \
    --source "~/Downloads/EXPEDIENTES DEMO" --no-upload

MEDIA_BACKEND=local .venv/bin/uvicorn app.main:app --reload --port 8000
```

`.env` ships `MEDIA_BACKEND=s3` but nothing was uploaded to S3 — the `MEDIA_BACKEND=local`
override is required locally or every download and pack returns 404.

Tests: `.venv/bin/python -m pytest tests/ -q` → **315 passed in ~60s**.
Schema changes: there is **no Alembic** — use `scripts/migrate_case_files.py` (dry-run, then
`--apply`) against the dev RDS *before* pushing. Logins: `docs/usuarios-de-prueba.md`, password
`radal1234`.
