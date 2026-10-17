# v12 — infrastructure plan (DOCS + SCRIPTS ONLY — nothing is applied by the build)

Current (docs/deployment.md): CloudFront → OAC (SigV4, `AWS_IAM` Function URLs) → backend Lambda (VPC-attached, **no NAT**, talks
to RDS MySQL) and frontend Lambda. OAC requires `x-amz-content-sha256` on every POST ⇒ third parties cannot POST through
CloudFront. v12 adds outbound internet needs (Forpay, Zoho SMTP/IMAP, mindicador.cl, HDI portal via Playwright, DeepInfra) and
inbound webhooks (Forpay) and schedules.

## 1. Egress — recommendation: **NAT instance (fck-nat) now, NAT Gateway later**
| option | ~monthly | protection | notes |
|---|---|---|---|
| **fck-nat on t4g.nano** in a public subnet, private-subnet route 0.0.0.0/0 → instance ENI | **~US$3–4** + data | same as NAT GW for egress-only (no inbound) | single AZ, no HA; ASG of 1 for self-healing. Cheapest that keeps Lambda in VPC with RDS private. |
| NAT Gateway | ~US$32 + US$0.045/GB | managed, HA per AZ | the "scale later" option — swap the route target, nothing else changes |
| Split: non-VPC "integrations" Lambda (internet) + VPC Lambda (RDS), talking via SQS | ~US$0 | good | more moving parts (two deployables, async contract) — not worth it yet |
Also: SES/SMTP to Zoho port 465 outbound must be allowed in `radal-lambda-sg` egress (default SG egress is all — verify).
Verify first how DeepInfra calls egress today (the doc says no NAT; if AI works in dev, something already routes out — check the
Lambda's VPC config before adding anything).

## 2. Webhook ingress
API Gateway **HTTP API** `radal-hooks-dev` with a single route `POST /api/v1/webhooks/{proxy+}` → Lambda proxy integration to
`radal-backend-dev` (payload v2; LWA understands API Gateway events). No OAC, no IAM auth on the route — security is the
`account`+`sig` query params and Forpay's HMAC (03 §5). Custom domain `hooks.radalseguros.cl` (ACM cert in the API's region).
Throttling: 20 rps burst 40. `settings.PUBLIC_API_URL` = `https://hooks.radalseguros.cl` for webhook URLs handed to Forpay.

## 3. Schedules
EventBridge **Scheduler** (supports `America/Santiago` timezone) → target Lambda `radal-backend-dev` with input
`{"job": "<name>", "token": "<JOBS_TOKEN>"}`; LWA delivers non-HTTP events to `POST /events`
(AWS_LWA_PASS_THROUGH_PATH default). The app's `/events` route (outside `/api/v1`, so CloudFront never forwards it) runs the
job only when `body.token` equals `settings.JOBS_TOKEN` (constant-time; empty setting ⇒ 404).
| schedule | cron (America/Santiago) |
|---|---|
| fx.daily | `cron(0 8 * * ? *)` |
| collection.clock | `cron(30 6 * * ? *)` |
| collection.notices | `cron(0 9 ? * MON-FRI *)` |
| collection.inquiries | `cron(0 10 ? * MON-FRI *)` |
| mail.poll | `rate(5 minutes)` |
| payments.sync | `rate(30 minutes)` |
| payments.payouts | `cron(0 11 * * ? *)` |
| rpa.run | `rate(2 minutes)` |
| notifications.expire | `cron(0 3 * * ? *)` |

## 4. Secrets / env (S3 `deploy/backend.env`)
`MAIL_ENABLED`, `RADAL_MAIL_*` (Zoho app password), `RADAL_OPS_EMAIL`, `JOBS_TOKEN`, `JOBS_EVENTS_ENABLED`, `CREDENTIALS_KEY`
(Fernet key — generate once, back it up: losing it makes stored mail/Forpay/portal secrets unreadable), `PUBLIC_API_URL`,
`PUBLIC_APP_URL`, `FX_SOURCE_URL`, `RPA_ENABLED`, `RPA_HDI_BASE_URL`.

## 5. Scripts (written by the infra/doc step, NOT executed)
`deploy/v12/fck-nat.sh`, `deploy/v12/webhook-ingress.sh`, `deploy/v12/schedules.sh`, `deploy/v12/README.md` — idempotent AWS
CLI with `--profile radal`, `set -euo pipefail`, a `DRY_RUN=1` default that only prints.

## 6. Database migration on RDS (when deploying)
`python -m scripts.migrate_case_files --dialect mysql --pass v12` (review) → snapshot RDS → `--apply` against RDS → zero-drift →
`python -m scripts.seed_v12 --apply`.
