# Backend test suite

```bash
cd backend
source .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest tests/ -q
```

The suite runs against a throwaway SQLite file created per session under a temp
directory — it never touches `backend/radal.db`. `tests/conftest.py` pins
`DATABASE_URL` **before** importing any `app.*` module, because
`app.db.session` builds its engine at import time.

| File | Covers |
|---|---|
| `test_identifiers.py` | RUT módulo-11 validation + canonical normalization (valid / invalid / K / 0 cases) |
| `test_money_invariants.py` | `net = taxable + exempt`, `vat = 0.19 * taxable`, `total = net + vat`, `comprehensive_rate = taxable_rate + exempt_rate`; bad combinations rejected 422 |
| `test_quote_declared_value.py` | `quote_request.declared_value_uf == SUM(quote_line_item.value_uf)` on every mutation path |
| `test_proposal_rules.py` | a proposal cannot exist without `source_document_id`, nor with an insurer missing `rut`/`cmf_code` |
| `test_insurer_dedup.py` | find-or-create dedups on normalized `cmf_code`/`rut` and **never** on name |
| `test_proposal_accept.py` | accepting rejects siblings, closes the quote, moves the placement to `awarded` |
| `test_tenant_isolation.py` | broker A cannot read or write broker B's clients / quotes / proposals / documents |
| `test_rbac.py` | a low-privilege role (`broker_executive`) is blocked from Manage actions |

## Fixtures

`world` builds **two** brokers (`world.a`, `world.b`) holding mirror-image
records. Tenant isolation is only meaningful when broker B actually owns
something for broker A to fail to reach — asserting "A sees 1 client" proves
nothing unless B also owns one.

Insurer RUTs are allocated so no test collides with a fixture row:

| RUT | Owner |
|---|---|
| `99301000-6` | `world.insurer` (complete: rut + cmf_code) |
| `5000023-0` | `world.insurer_no_cmf` (missing cmf_code) |
| — | `world.insurer_no_rut` (missing rut) |
| `5000001-K`, `5000015-K`, `5000029-K`, `5000032-K`, `5000046-K` | free for tests to create |

`bcrypt` is deliberately slow and the world is rebuilt per test, so the single
test password is hashed once and reused (`_password_hash()`).
