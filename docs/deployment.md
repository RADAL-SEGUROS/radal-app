# Radal — Deployment & Infrastructure (dev)

Real AWS dev environment. Target shape (nirvana-style): **Lambda (container images) + CloudFront +
RDS MySQL + ECR**, deployed by **GitHub Actions (Blacksmith) → ECR → `update-function-code`**.

> AWS account **185011028331** · region **us-east-1**. Use the **`radal`** CLI profile (`--profile radal`).
> Never overwrite `[default]`.

## Architecture

```
                 CloudFront (d2tup8vfejxx98.cloudfront.net)
                 ├─ default behavior  ──► radal-frontend-dev (Lambda, Express serves the Vite SPA)
                 └─ /api/*  behavior   ──► radal-backend-dev  (Lambda, FastAPI via uvicorn)
                                                    │  (VPC-attached)
                                                    ▼
                                          RDS MySQL 8  radal-dev-db  (private)
```

- Both Lambdas are **container images** (ECR) running the **AWS Lambda Web Adapter** (`public.ecr.aws/awsguru/aws-lambda-adapter:0.7.0`).
  Backend = uvicorn on :8000; frontend = Express (`server.cjs`) serving `dist/` on :8080.
- **Auth model**: this account blocks *public* Lambda Function URLs (auth `NONE`). So Function URLs use
  **`AWS_IAM`** auth and CloudFront reaches them via **Origin Access Control (OAC, SigV4 signing)**.
  Each Lambda's resource policy allows `cloudfront.amazonaws.com` to `InvokeFunctionUrl`, scoped to the
  distribution ARN. CloudFront uses the managed **AllViewerExceptHostHeader** origin-request policy
  (Function URLs reject a forwarded `Host` header).
- The SPA calls **same-origin `/api/v1`** — CloudFront routes `/api/*` to the backend Lambda, so no CORS.
- Backend Lambda is **VPC-attached** (subnets in the RDS VPC + `radal-lambda-sg`, which RDS allows on 3306).
  No NAT — it only talks to RDS. Cold starts open new DB connections; add **RDS Proxy** for prod.

## Provisioned resources (dev)

| Resource | Name / ID |
|---|---|
| CloudFront distribution | `E2MGVTSWQDFPY3` → `https://d2tup8vfejxx98.cloudfront.net` |
| Origin Access Control (lambda, sigv4) | `E3BGCO4XOX79HP` |
| Backend Lambda (VPC, image) | `radal-backend-dev` (Function URL, AWS_IAM) |
| Frontend Lambda (image) | `radal-frontend-dev` (Function URL, AWS_IAM) |
| Lambda exec role | `radal-lambda-role` (Basic + VPCAccess) |
| Lambda security group | `radal-lambda-sg` (`sg-065438bbc14858097`) |
| RDS MySQL 8 (private) | `radal-dev-db` (db `radal`, user `radaladmin`) — SG `sg-0da8dd955985aaa32` |
| ECR repos (lifecycle: keep 5) | `radal-backend`, `radal-frontend` |
| S3 (env staging) | `radal-dev-185011028331` |
| CI/CD IAM user (scoped) | `radal-github-actions` |

> The EC2 dev box + its SG/key were **decommissioned** — the backend runs on Lambda now.

## CI/CD

`.github/workflows/dev-deploy.yml` on push to **`dev`** (Blacksmith runners):
1. Build + push `radal-backend` and `radal-frontend` images to ECR (`docker buildx --provenance=false --platform linux/amd64`).
2. `aws lambda update-function-code` for each function; `wait function-updated`; set Function URL `--invoke-mode RESPONSE_STREAM`.
3. `aws cloudfront create-invalidation --paths "/*"`.

### Required GitHub secrets
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `ECR_REGISTRY`, `BACKEND_LAMBDA`
(`radal-backend-dev`), `FRONTEND_LAMBDA` (`radal-frontend-dev`), `CLOUDFRONT_DISTRIBUTION_ID`
(`E2MGVTSWQDFPY3`). Values in `~/radal-cicd-secrets.txt` (chmod 600).

## Manual deploy (Docker + `radal` profile)

```bash
ECR=185011028331.dkr.ecr.us-east-1.amazonaws.com
aws ecr get-login-password --profile radal --region us-east-1 | docker login --username AWS --password-stdin $ECR
docker buildx build --platform linux/amd64 -t $ECR/radal-backend:dev --push ./backend
docker buildx build --platform linux/amd64 --build-arg VITE_API_URL=/api/v1 -t $ECR/radal-frontend:dev --push ./frontend
aws lambda update-function-code --profile radal --function-name radal-backend-dev  --image-uri $ECR/radal-backend:dev
aws lambda update-function-code --profile radal --function-name radal-frontend-dev --image-uri $ECR/radal-frontend:dev
aws cloudfront create-invalidation --profile radal --distribution-id E2MGVTSWQDFPY3 --paths "/*"
```

## HTML → PDF rendering (Playwright / Chromium) — NEW provisioning step

The branded expediente PDF (`app/services/pdf.py`) renders a self-contained HTML string to A4 via
**headless Chromium driven by Playwright**. `pip install -r requirements.txt` installs the
`playwright` Python package but **NOT** the browser binary — that is a separate, one-time step per
environment:

```bash
cd backend && source .venv/bin/activate
python -m playwright install chromium          # local/dev (macOS/Windows)
python -m playwright install --with-deps chromium   # Linux / CI / Docker (also installs OS libs)
```

- **Docker/Lambda image**: add `RUN python -m playwright install --with-deps chromium` to the backend
  Dockerfile after `pip install`, so the image ships with the browser. Chromium is ~150 MB.
- **Boot is resilient**: the app warms a shared browser at startup but swallows any launch failure, so
  it still boots where Chromium is absent. The first PDF request then either lazy-launches Chromium or
  returns a clean `502` (`PDFGenerationError`) — never a raw `ImportError` or a 500.
- **Offline-safe render**: fonts (Inter woff2) and logos are embedded as `data:` URIs in the HTML; no
  http(s) resource is ever fetched at render time, so `networkidle` resolves even with no network.
- **Tests need no Chromium**: `render_expediente_html` is a pure string builder (unit-tested in
  `tests/test_pdf_template.py`); the real render is mocked in endpoint tests.

## CloudFront OAC → Lambda gotchas (all handled — don't regress)

Four non-obvious requirements make the OAC→Lambda path work; changing any one silently breaks it:
1. **Both permissions**: each Lambda resource policy must allow CloudFront (`cloudfront.amazonaws.com`,
   scoped to the distribution ARN) BOTH `lambda:InvokeFunctionUrl` AND `lambda:InvokeFunction`
   (the latter required by AWS since Oct 2025). Missing `InvokeFunction` → 403 Forbidden.
2. **POST/PUT bodies**: OAC does NOT sign request bodies. The client must send
   `x-amz-content-sha256: <sha256hex(body)>` — the frontend axios interceptor (`src/lib/api.ts`) does this.
   Missing → `InvalidSignatureException` on POST.
3. **JWT header**: OAC (SigningBehavior `always`) overwrites `Authorization` with its SigV4 signature, so the
   app's JWT rides in **`X-Radal-Token`** (backend `deps.py` reads either header; frontend sends both).
4. **Invoke mode must match**: the LWA `AWS_LWA_INVOKE_MODE` (Dockerfiles) and the Function URL `InvokeMode`
   must BOTH be **`buffered`** — a mismatch delivers empty response bodies (200 with content-length 0).
   `RESPONSE_STREAM` is not used (OAC + streaming is finicky; our payloads are < 6 MB).

## Custom domain — radalseguros.cl (in progress)

- Domain **radalseguros.cl**; dev app subdomain **`dev.radalseguros.cl`** (prod will be `app.` later).
- Route 53 hosted zone **`Z08394372WP1FCSUM9CQ3`** created. **Delegate at GoDaddy** — set the domain's
  nameservers to (migration done manually by the owner):
  `ns-1795.awsdns-32.co.uk`, `ns-955.awsdns-55.net`, `ns-1360.awsdns-42.org`, `ns-33.awsdns-04.com`.
- ACM wildcard cert (us-east-1) `…/certificate/e7f7ac98-449a-446b-b228-f320e9ae0727` for
  `radalseguros.cl` + `*.radalseguros.cl` — DNS-validation record already in the zone (validates once
  nameservers are delegated).
- `dev.radalseguros.cl` A/AAAA **alias → CloudFront** already in the zone.
- **Final step (after delegation + cert validation)**: run `deploy/attach-domain.sh` to add the alias +
  cert to the CloudFront distribution. Then `https://dev.radalseguros.cl` serves the app.
- The custom domain is already in the backend env (`CORS_ORIGINS`, `APP_URL` → `https://dev.radalseguros.cl`)
  stored in `s3://radal-dev-185011028331/deploy/backend.env`; **CI applies it to the Lambda on every deploy**
  (the "Sync backend env from S3" step). The SPA calls same-origin `/api/v1`, so no frontend rebuild is needed.

## Seeding the DB (RDS is private)

RDS is already seeded with the RADAL demo. To re-seed: temporarily set `radal-dev-db` publicly accessible +
allow your IP on `sg-0da8dd955985aaa32:3306`, run `DATABASE_URL=mysql+pymysql://… python -m app.db.seed`
from the backend venv, then revert public access. (Or run a one-off task in the VPC.)

## Schema migration — case-files pass

> **BLOCKER: run this BEFORE the first push of the case-files branch to `dev`.**
> Startup only runs `Base.metadata.create_all()`, which creates missing **tables** but never
> **ALTERs** existing ones. The case-files branch adds 9 new tables (create_all handles those on
> Lambda boot) **plus 38 additive columns on 9 pre-existing tables** and widens two enum VARCHARs
> (`document.category` 29→37, `extraction.kind` 22→25). Deployed against the current
> `radal-dev-db` schema, nearly every query 500s until the columns exist.

The tool is **`backend/scripts/migrate_case_files.py`** — idempotent, dry-run first, and it
derives every column spec from the live SQLAlchemy metadata (compiled with the target dialect),
so it cannot drift from the models.

```bash
cd backend && source .venv/bin/activate
python -m scripts.migrate_case_files                  # dry run vs DATABASE_URL — exit 1 on drift
python -m scripts.migrate_case_files --apply          # execute the plan, then verify zero drift
python -m scripts.migrate_case_files --dialect mysql  # OFFLINE: print the full MySQL plan, no DB
```

### Option A — run the script against RDS (recommended for dev)

Additive and idempotent: existing demo data survives, and re-running is a no-op. RDS is private,
so use the temporary-public-access procedure (same as re-seeding):

```bash
MYIP=$(curl -s https://checkip.amazonaws.com)

# 1) OPEN: public access + your IP on the RDS SG
aws rds modify-db-instance --profile radal --db-instance-identifier radal-dev-db \
  --publicly-accessible --apply-immediately
aws ec2 authorize-security-group-ingress --profile radal --group-id sg-0da8dd955985aaa32 \
  --protocol tcp --port 3306 --cidr ${MYIP}/32
aws rds wait db-instance-available --profile radal --db-instance-identifier radal-dev-db

# 2) get the DB URL (never hardcode the password; it lives in the runtime env file)
aws s3 cp s3://radal-dev-185011028331/deploy/backend.env - --profile radal | grep ^DATABASE_URL
# → export RDS_URL='mysql+pymysql://radaladmin:…@<endpoint>/radal'  (endpoint DNS resolves
#   publicly while publicly-accessible is on)

# 3) dry run, READ the plan, then apply (re-run after apply: must print "nothing to do")
cd backend && source .venv/bin/activate
python -m scripts.migrate_case_files --url "$RDS_URL"
python -m scripts.migrate_case_files --url "$RDS_URL" --apply

# 4) REVERT BOTH — do not skip
aws rds modify-db-instance --profile radal --db-instance-identifier radal-dev-db \
  --no-publicly-accessible --apply-immediately
aws ec2 revoke-security-group-ingress --profile radal --group-id sg-0da8dd955985aaa32 \
  --protocol tcp --port 3306 --cidr ${MYIP}/32

# 5) confirm only the Lambda SG remains allowed on 3306 (no CIDR entries)
aws ec2 describe-security-groups --profile radal --group-ids sg-0da8dd955985aaa32 \
  --query 'SecurityGroups[0].IpPermissions'   # expect one rule → sg-065438bbc14858097
```

Then push the branch; Lambda's `create_all` on boot is a no-op against the migrated schema.

### Option B — destructive reset

Open access as above, then run the importers with `--reset` against RDS
(`DATABASE_URL="$RDS_URL" python -m app.db.import_fixtures --reset --profile radal`, then the
expedientes importer). **Drops and recreates everything — all data created since seeding is
lost.** Only acceptable while dev holds nothing but re-importable demo data; Option A is the
recommended path.

### CI check (future)

Dry-run mode exits **1** when the live schema drifts from the models, so a future
`dev-deploy.yml` step can gate deploys:
`python -m scripts.migrate_case_files --url "$DATABASE_URL"` (run it from inside the VPC or a
runner with DB access). Not wired up yet — do not add it without solving runner→RDS connectivity.

### The generated MySQL plan (ALTERs only)

Produced by `python -m scripts.migrate_case_files --dialect mysql` (compiled from the models —
regenerate rather than edit). The 9 `CREATE TABLE` + 80 `CREATE INDEX` statements are elided here;
the offline plan prints all 139 statements.

```sql
ALTER TABLE claim ADD COLUMN case_file_id INTEGER;
ALTER TABLE claim ADD COLUMN occurred_at DATETIME;
ALTER TABLE claim ADD COLUMN reported_at DATETIME;
ALTER TABLE claim ADD COLUMN notice_deadline_days INTEGER;
ALTER TABLE claim ADD COLUMN adjuster_name VARCHAR(160);
ALTER TABLE claim ADD COLUMN adjuster_registry VARCHAR(32);
ALTER TABLE claim ADD COLUMN coverage_ruling VARCHAR(29) NOT NULL DEFAULT 'pending';
ALTER TABLE claim ADD COLUMN deductible_uf NUMERIC(14, 4);
ALTER TABLE claim ADD COLUMN loss_ratio_pct NUMERIC(6, 3);
ALTER TABLE claim ADD FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE document ADD COLUMN case_file_id INTEGER;
ALTER TABLE document ADD COLUMN section VARCHAR(27);
ALTER TABLE document ADD COLUMN document_code VARCHAR(8);
ALTER TABLE document ADD CONSTRAINT fk_document_case_file FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE extraction ADD COLUMN case_file_id INTEGER;
ALTER TABLE extraction ADD COLUMN category VARCHAR(37);
ALTER TABLE extraction ADD CONSTRAINT fk_extraction_case_file FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE inspection ADD COLUMN case_file_id INTEGER;
ALTER TABLE inspection ADD FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE note ADD COLUMN follow_up_on DATE;
ALTER TABLE placement ADD COLUMN case_file_id INTEGER;
ALTER TABLE placement ADD CONSTRAINT fk_placement_case_file FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE policy ADD COLUMN case_file_id INTEGER;
ALTER TABLE policy ADD COLUMN source_document_id INTEGER;
ALTER TABLE policy ADD COLUMN renews_policy_id INTEGER;
ALTER TABLE policy ADD COLUMN period_start_at DATETIME;
ALTER TABLE policy ADD COLUMN period_end_at DATETIME;
ALTER TABLE policy ADD COLUMN cover_mode VARCHAR(64);
ALTER TABLE policy ADD COLUMN cmf_policy_code VARCHAR(32);
ALTER TABLE policy ADD COLUMN insured_amount_semantics VARCHAR(255);
ALTER TABLE policy ADD COLUMN aggregate_limit_uf NUMERIC(14, 4);
ALTER TABLE policy ADD COLUMN average_rate_permille NUMERIC(9, 4);
ALTER TABLE policy ADD COLUMN indemnity_limit TEXT;
ALTER TABLE policy ADD CONSTRAINT fk_policy_case_file FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE policy ADD FOREIGN KEY(renews_policy_id) REFERENCES policy (id) ON DELETE SET NULL;
ALTER TABLE policy ADD FOREIGN KEY(source_document_id) REFERENCES document (id) ON DELETE SET NULL;
ALTER TABLE proposal ADD COLUMN case_file_id INTEGER;
ALTER TABLE proposal ADD COLUMN ai_summary TEXT;
ALTER TABLE proposal ADD COLUMN ai_summary_model VARCHAR(120);
ALTER TABLE proposal ADD COLUMN ai_summary_prompt_version VARCHAR(64);
ALTER TABLE proposal ADD COLUMN is_summary_confirmed BOOL NOT NULL DEFAULT 0;
ALTER TABLE proposal ADD COLUMN quotation_number VARCHAR(64);
ALTER TABLE proposal ADD COLUMN cover_mode VARCHAR(64);
ALTER TABLE proposal ADD COLUMN outcome VARCHAR(23);
ALTER TABLE proposal ADD CONSTRAINT fk_proposal_case_file FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE quote_request ADD COLUMN case_file_id INTEGER;
ALTER TABLE quote_request ADD COLUMN round_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE quote_request ADD CONSTRAINT fk_quote_request_case_file FOREIGN KEY(case_file_id) REFERENCES case_file (id) ON DELETE SET NULL;
ALTER TABLE document MODIFY COLUMN category VARCHAR(37) NOT NULL;
ALTER TABLE extraction MODIFY COLUMN kind VARCHAR(25) NOT NULL;
```

Notes / limits: enum values need no DDL (all enums are `native_enum=False` VARCHARs — new
members are app-side only, which the script cannot verify beyond column width); the script never
drops or narrows anything; index comparison is by name (create_all's deterministic `ix_*` names);
on SQLite the two MODIFYs are correctly skipped (lengths unenforced) and FKs on added columns
cannot be ALTER-added (unenforced there by default anyway).

## Cost notes
- ECR lifecycle keeps only the **last 5 images** per repo. Lambda + CloudFront are pay-per-use (near-zero idle).
- Main idle cost is **RDS** (`db.t3.micro`) — `aws rds stop-db-instance --db-instance-identifier radal-dev-db`
  when not in use (auto-restarts after 7 days).
- Rotate the temporary admin keys used to provision; CI uses the scoped `radal-github-actions` user only.
