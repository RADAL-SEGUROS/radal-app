# v12 — testing

## Hermetic environment (phase 1 adds to `backend/tests/conftest.py`, BEFORE any `app.*` import — non-negotiable #9)
```python
os.environ["MAIL_ENABLED"] = "false"
os.environ["RADAL_MAIL_PASSWORD"] = ""
os.environ["RADAL_MAIL_SMTP_HOST"] = "127.0.0.1"; os.environ["RADAL_MAIL_SMTP_PORT"] = "9"
os.environ["RADAL_MAIL_IMAP_HOST"] = "127.0.0.1"; os.environ["RADAL_MAIL_IMAP_PORT"] = "9"
os.environ["FX_SOURCE_URL"] = "http://127.0.0.1:9/no-fx-in-tests"
os.environ["FORPAY_DEFAULT_BASE_URL"] = "http://127.0.0.1:9/no-forpay-in-tests"
os.environ["RPA_ENABLED"] = "false"; os.environ["RPA_INLINE"] = "false"
os.environ["JOBS_TOKEN"] = "test-jobs-token"
os.environ["CREDENTIALS_KEY"] = "<a fixed valid Fernet key literal>"
```
Fakes (phase 1, `backend/tests/fakes_v12.py`): `FakeTransport` (records sent messages, serves scripted inbound mail),
`FakePaymentProvider` (records calls, returns checkout URLs, builds signed webhook payloads), helpers
`make_policy_with_plan(db, tenant, insurer, n_installments=…, start=…)`, `seed_catalog(db)`, `make_provider_account(db, …)`.
Fixtures in conftest: `fake_transport`, `fake_provider` (monkeypatch the getters), `catalog` (seeds the catalogue).

## Required test coverage per lane (minimum)
- tenant isolation: broker B cannot read/act on broker A's v12 rows (404), list endpoints never leak.
- permission gates: an executive/collections role vs admin where grants differ; platform endpoints 403 for broker users.
- public endpoints: unknown/revoked/expired token → 404; projections never include ids of other policies, s3 keys, commissions,
  internal notes.
- webhook: unknown account 404, bad sig 401, bad provider HMAC 401 (stored with signature_valid false), duplicate 200 duplicate,
  state 2 creates exactly one payment, replay creates none, state −99 / 12 handled.
- money: Σ checks still enforced; UF/CLP conversion with `currency_rate`; tolerance.
- jobs: idempotent per `(job, scheduled_for)`, never raise, deterministic with injected `now`.
- migration: v12 manifest valid; pre-v12 SQLite DB → apply → zero drift.

## Full verification (orchestrator)
backend full suite · frontend `tsc -b` + `check:locales` + `build` · live browser pass (local backend sqlite + seed + vite dev)
at 1440 px and 375 px over: /policies, /policies/new, a policy detail, /collections, evidence inbox, /settings (Correo/Cobranza),
/platform, /portal-search, /agent, a group account with skipped stages, public /s/:token (insured + insurer).
