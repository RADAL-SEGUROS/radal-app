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

## Cost notes
- ECR lifecycle keeps only the **last 5 images** per repo. Lambda + CloudFront are pay-per-use (near-zero idle).
- Main idle cost is **RDS** (`db.t3.micro`) — `aws rds stop-db-instance --db-instance-identifier radal-dev-db`
  when not in use (auto-restarts after 7 days).
- Rotate the temporary admin keys used to provision; CI uses the scoped `radal-github-actions` user only.
