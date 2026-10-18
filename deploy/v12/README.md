# deploy/v12 — egress, webhook ingress, schedules

**Nothing in this folder has been run against AWS.** The scripts were written by the v12 build and
exercised only in `DRY_RUN=1` (which makes no AWS call at all) and against a stub `aws` binary. The
spec they implement is `docs/v12/08-infra.md`; the application side is
`docs/technical-reference.md` §19.

| File | Builds | Touches the running app? |
|---|---|---|
| `fck-nat.sh` | internet egress for the VPC-attached backend Lambda: fck-nat on `t4g.nano` (ASG of 1, static ENI, EIP), a private route table, an S3 gateway endpoint | **no** until the opt-in last step (`SWITCH_LAMBDA_SUBNETS=1`) moves the Lambda onto the new subnets |
| `webhook-ingress.sh` | API Gateway HTTP API `radal-hooks-dev`: one route `POST /api/v1/webhooks/{provider}` → `radal-backend-dev`, `hooks.radalseguros.cl` | no — a second door into the same Lambda; the CloudFront/OAC path is untouched |
| `schedules.sh` | EventBridge Scheduler: one schedule per job → the Lambda → `POST /events` | yes, once `JOBS_TOKEN` + `JOBS_EVENTS_ENABLED=true` are in the Lambda env |
| `_lib.sh` | shared helpers (dry-run, describe-before-create, redaction, account check) | — |

## Conventions (all three scripts)

- `set -euo pipefail`, plain bash 3.2 (the macOS `/bin/bash`), `--profile radal`, `us-east-1`.
- **`DRY_RUN=1` is the default and makes NO AWS call**, not even `describe`/`get`. Every check is
  printed as `would check` and treated as "not there", so you see the full create path; every mutation
  is printed as `would run` and hands a `<PLACEHOLDER>` to the next step. Printed commands can be copied
  and pasted.
- `DRY_RUN=0` applies. It refuses to start unless `aws sts get-caller-identity --profile radal` is
  account `185011028331`. Every resource is described first and created only if absent, so a
  re-run is a no-op (schedules are create-or-update).
- Every tunable is an environment variable at the top of the script (`NAME_PREFIX`, `PUBLIC_SUBNET_ID`,
  `ONLY`, `SCHEDULE_STATE`, …). Secrets are never printed: `schedules.sh` masks `JOBS_TOKEN` as
  `<redacted>` in both modes.

```bash
./deploy/v12/fck-nat.sh            # read what it would do
./deploy/v12/webhook-ingress.sh
./deploy/v12/schedules.sh
```

---

## 0. VERIFIED 2026-10-17 (read-only `aws --profile radal`): the backend Lambda has NO internet egress — state C

| check | result |
|---|---|
| `radal-backend-dev` VpcConfig | `vpc-071dd99bdc1702070` (the **default VPC**), subnets `subnet-0ce488a755b65168d` (1a), `subnet-027da40d30c60c9f3` (1b), `subnet-07cca3720c0f3135a` (1c), SG `sg-065438bbc14858097`; timeout **30 s**, 1024 MB, image, x86_64 |
| route tables | only the MAIN table `rtb-0b1e6b3a8e86a150b`: `172.31.0.0/16 local`, `0.0.0.0/0 → igw-0a08b898f10049ef3` — every subnet is public |
| NAT gateways / VPC endpoints / EC2 instances | **none / none / none** (so no S3 gateway endpoint either) |
| SG egress | all traffic to 0.0.0.0/0 (not the blocker) |
| Function URL | `AWS_IAM`, `BUFFERED` (OAC constraint 4 holds); CloudFront origin read timeout **30 s** for both origins; only `/api/*` → backend (so `/events` is not reachable through CloudFront) |
| functional evidence (CloudWatch, 60 d) | the only AI calls on dev — `POST /api/v1/ai/threads/2/stream` on 09-14 17:48/17:55 and 09-15 14:12/14:17 — each started streaming and then **died at exactly 30 000 ms** (`Status: timeout`, `hyper::Error(IncompleteMessage)`). No `POST /documents` (S3 upload) was ever attempted on dev. Downloads return 200 because they are presigned URLs (no S3 call from the Lambda). |

A VPC-attached Lambda never gets a public IP, so the igw route is useless to it: **DeepInfra (all AI),
S3 SDK calls (uploads, AI reading, packs) and every v12 integration (Zoho, mindicador, Forpay, HDI) cannot
work on dev today.** This is not a v12 regression; it has been true since the Lambda moved into the VPC.

**Decision (cheapest that keeps RDS private):** run `fck-nat.sh` in mode C — new private subnets
`PRIVATE_SUBNET_CIDRS="172.31.96.0/24 172.31.97.0/24"` (free: the six default /20s end at 172.31.95.255),
`PUBLIC_SUBNET_ID=subnet-0ce488a755b65168d`, an **S3 gateway endpoint** on the new private route table
(free, removes S3 from the NAT bill), then `SWITCH_LAMBDA_SUBNETS=1`. The SG is unchanged, so RDS (same
VPC, SG rule on 3306) keeps working. Cost ≈ US$3–4/month (t4g.nano) + EIP + data. Swap the route target to a
NAT Gateway later if HA is needed.

**Also verified, act on before relying on AI in dev:** Lambda timeout 30 s **and** CloudFront origin read
timeout 30 s — even with egress, comparisons/extractions that run > 30 s are cut. Raise the Lambda
timeout (e.g. 120 s) and the origin read timeout (60 s is the default max; up to 180 s needs a quota
increase), or move long AI work to the worker/job path. Not changed by v12.

**Other read-only facts:** RDS `radal-dev-db` MySQL 8.0.45 `db.t3.micro`, private, single-AZ, 3-day
automated snapshots (latest 2026-10-17 06:10 UTC) — take a manual snapshot before `--pass v12 --apply`.
ACM `radalseguros.cl` + `*.radalseguros.cl` ISSUED in us-east-1 (covers `hooks.radalseguros.cl`).
No API Gateway, no EventBridge schedules, no SES identities exist yet. CI workflows are
`deploy-backend-dev.yml` / `deploy-frontend-dev.yml`; the Dockerfile pins **lambda-adapter 0.7.0**,
`AWS_LWA_INVOKE_MODE=buffered`. The Lambda role has `AWSLambdaVPCAccessExecutionRole`,
`AWSLambdaBasicExecutionRole` and inline `radal-s3-app-objects`.

**Mail DNS (blocks the Zoho plan as configured):** `radalseguros.cl` MX → **Google Workspace**
(`aspmx.l.google.com`), SPF `include:_spf.google.com` only, DMARC `p=quarantine`, plus a stray
`zoho-verification` TXT. `radal.ai` MX → **Zoho** (`mx.zoho.com`), SPF `include:zohomail.com`. So a Zoho
`contacto@radalseguros.cl` would receive nothing and its mail would fail SPF/DMARC. Either set
`RADAL_MAIL_ADDRESS` to the real Zoho mailbox (presumably `contacto@radal.ai`), or move/extend
`radalseguros.cl` mail (MX + SPF `include:zohomail.com` + Zoho DKIM) before `MAIL_ENABLED=true`.
**Superseded (2026-10-17):** sending moved to Amazon SES, which needs none of those root changes — see
§4b "Mail via SES". The MX half of this note is still open (replies to `contacto@` land at Google).

## 0b. APPLIED to dev on 2026-10-17 (deploy of `5447c7b`)

| what | result |
|---|---|
| `fck-nat.sh` mode C | NAT instance `radal-dev-nat` (t4g.nano, fck-nat 1.4.0, ASG of 1), EIP **3.230.124.80**, static ENI `eni-04a8460092f573981`, private subnets `subnet-0b6e7d056fdf8216e` (172.31.96.0/24, 1a) + `subnet-0fe29528eb50f5082` (172.31.97.0/24, 1b), route table `rtb-0ff0754ae882224a3`, S3 gateway endpoint. **The backend Lambda now runs on the private subnets.** Verified: a real agent turn on dev answered in 6.6 s (DeepInfra), RDS reachable. |
| timeouts | Lambda `radal-backend-dev` 30 → **120 s**; CloudFront backend origin read timeout 30 → **60 s** (60 is the max without a quota increase) |
| RDS migration | manual snapshot **`radal-dev-db-pre-v12-20261017`**, then — RDS stays private — the migration ran **inside the VPC** through the new one-off job `db.migrate` (`app/jobs/maintenance.py`, gated by `JOBS_TOKEN` + `ALLOW_DB_MAINTENANCE=true`): 27 tables (create_all) + 28 columns + 6 indexes + 5 FKs, zero drift. Then `db.seed_catalog` (18 ramos / 54 subtipos, 25 insurer profiles) and `db.seed_demo` (Corredora Demo SpA, 8 PDFs uploaded to S3). `ALLOW_DB_MAINTENANCE` was removed from the env afterwards. |
| env | `deploy/backend.env` gained `CREDENTIALS_KEY`, `JOBS_TOKEN`, `JOBS_EVENTS_ENABLED`, `PUBLIC_APP_URL`, `PUBLIC_API_URL`, `MAIL_ENABLED=false`, `RPA_ENABLED=false`, `RPA_INLINE=false`, `FX_SOURCE_URL`. Backups: `backend.env.bak-2026-10-17-pre-v12`, `backend.env.v12-keys-backup-2026-10-17` (**keep it — losing `CREDENTIALS_KEY` makes stored secrets unreadable**). |
| schedules | group `radal-dev`, role `radal-scheduler-dev`; ENABLED: `fx.daily`, `collection.clock`, `collection.notices`, `collection.inquiries`, `notifications.expire`. NOT created (integration off): `mail.poll`, `payments.sync`, `payments.payouts`, `rpa.run`. **LWA 0.7.0 does NOT pass bare events through** (they arrive as `GET /` → 404), so `schedules.sh` now sends a Function-URL-style **HTTP v2 envelope** (POST `/events` + `{job, token}` body); verified end to end with a one-time `noop` schedule. |
| Forpay | paused by decision; the demo provider account was *Verificar*-ed and failed (points at a dead URL) → every insurer resolves to *Asistida*. |
| Lambda concurrency | the account limit is **10** (new-account default) — the live smoke throttled the frontend Lambda (429). Requested **1000** via Service Quotas (request `861e2093096845a4a6c51381233a39a2JIe4WtHV`, PENDING). Mitigation shipped: `frontend/server.cjs` now serves `/assets/*` with `max-age=31536000, immutable`, so CloudFront stops re-fetching every chunk from the Lambda. |
| `fx.daily` | mindicador.cl did not answer (also from outside AWS) on deploy day → the job records `failed`; rates fall back to the latest stored value (the demo seeded 61 days). |

## 1. Egress — `fck-nat.sh`

**Recommendation (08-infra §1): fck-nat now, NAT Gateway later.**

| option | ~monthly | notes |
|---|---|---|
| **fck-nat on `t4g.nano`**, ASG of 1, static ENI as the route target | **~US$3–4** + public IPv4 (~US$3.6) + data out | egress-only like a NAT GW; single AZ; the ASG replaces a dead instance and the static ENI keeps the route valid. No per-GB processing fee |
| NAT Gateway | ~US$32 + US$0.045/GB processed | managed, HA per AZ. The swap is **one command**: `aws ec2 replace-route --route-table-id <rt> --destination-cidr-block 0.0.0.0/0 --nat-gateway-id <nat>` |
| Split: a non-VPC "integrations" Lambda + the VPC Lambda, via SQS | ~US$0 | two deployables and an async contract — not worth it yet |

The EIP (`ALLOCATE_EIP=1`, default) gives a **stable egress IP**, which is what Forpay, HDI or a
broker's mail host will ask for if they allow-list callers.

**Run (state A or C from §0):**

```bash
# 1) read the plan
PUBLIC_SUBNET_ID=subnet-… PRIVATE_SUBNET_CIDRS="172.31.96.0/24 172.31.97.0/24" ./deploy/v12/fck-nat.sh
# 2) build everything EXCEPT the Lambda move (inert: nothing routes through it yet)
DRY_RUN=0 PUBLIC_SUBNET_ID=subnet-… PRIVATE_SUBNET_CIDRS="172.31.96.0/24 172.31.97.0/24" ./deploy/v12/fck-nat.sh
# 3) check the instance is up and the ENI attached (EC2 console or describe-instances), then move the Lambda
DRY_RUN=0 PUBLIC_SUBNET_ID=subnet-… PRIVATE_SUBNET_CIDRS="…" SWITCH_LAMBDA_SUBNETS=1 ./deploy/v12/fck-nat.sh
```

- `PUBLIC_SUBNET_ID` must be a subnet of the Lambda's VPC whose route table sends `0.0.0.0/0` to an
  `igw-*` (the script verifies). `PRIVATE_SUBNET_CIDRS` must be free ranges in the VPC, one per AZ in
  `PRIVATE_SUBNET_AZS` (≥ 2 AZs for Lambda). To reuse an existing private route table instead, pass
  `PRIVATE_ROUTE_TABLE_ID=rtb-…` and `LAMBDA_SUBNET_IDS=subnet-a,subnet-b`.
- The NAT SG accepts traffic **only from `radal-lambda-sg`**; no SSH (SSM Session Manager is attached
  for debugging). The RDS SG rule (Lambda SG → 3306) is SG-based, so moving subnets does not affect it.
- `radal-lambda-sg` egress must allow 443, 465/587 and 993 — the script warns if it is not "all".
- The S3 gateway endpoint (free) is added to the new route table so S3 never crosses the NAT.
- The user-data writes `eni_id=` / `eip_id=` to `/etc/fck-nat.conf` and restarts the service (fck-nat's
  HA mode). Verify those keys against the fck-nat version of the AMI before applying.

**Verify.** `fx.daily` (§0) succeeds; an AI extraction on dev completes; `POST /api/v1/mail/accounts/{id}/test`
reaches a real server.

**Rollback** (in this order): move the Lambda back
(`aws lambda update-function-configuration --function-name radal-backend-dev --vpc-config SubnetIds=<old>,SecurityGroupIds=sg-065438bbc14858097`),
then delete the ASG (`--force-delete`), launch template, route-table associations, route table, subnets,
ENI, EIP (`release-address`), instance profile/role, SG. The old subnets are never modified by the
script, so step one alone restores the previous behaviour.

## 2. Webhook ingress — `webhook-ingress.sh`

Why: CloudFront OAC does not sign request bodies, so every POST through CloudFront must carry
`x-amz-content-sha256` (OAC constraint 2). The SPA does that; **Forpay never will**. So the webhook
gets its own door: API Gateway **HTTP API** → Lambda proxy (payload 2.0, which LWA understands) →
the same FastAPI route. Exactly **one** route is exposed (`POST /api/v1/webhooks/{provider}` — the
`{provider}` segment is narrower than the `{proxy+}` in 08-infra, and the app has exactly one webhook
handler); anything else is API Gateway's own `404 {"message":"Not Found"}`. Throttled at 20 rps,
burst 40. The `$default` stage adds no path prefix.

**Security lives in the app** (tech-ref §19.6): `?account=<webhook_key>` → unknown/disabled 404;
`&sig=hex(HMAC-SHA256(webhook_secret, "forpay:<webhook_key>"))` constant-time → 401; body ≤ 256 KB →
413; JSON → 400; Forpay's payload HMAC → 401 (event stored with `signature_valid=false`); dedup on signed
fields → 200 `duplicate`; processing errors → 200 + `failed` (no retry storms; `payments.sync`
reconciles). No API Gateway authorizer on purpose.

```bash
./deploy/v12/webhook-ingress.sh                      # plan
DRY_RUN=0 ./deploy/v12/webhook-ingress.sh            # apply (needs the *.radalseguros.cl ACM cert ISSUED)
DRY_RUN=0 DISABLE_DEFAULT_ENDPOINT=1 ./deploy/v12/webhook-ingress.sh   # once hooks.radalseguros.cl answers
```

Then **set `PUBLIC_API_URL=https://hooks.radalseguros.cl` in `s3://radal-dev-185011028331/deploy/backend.env`
and redeploy BEFORE any Forpay mandate is created**: webhook URLs are baked into each mandate at
creation, so a mandate created with the CloudFront URL can never be delivered. Smoke tests are printed at
the end of the script (an unknown `account` must return the **app's** 404 `{"detail":"Not found"}`).
`CREATE_DOMAIN=0` stops before the domain (useful while the GoDaddy → Route 53 delegation is pending;
use the `execute-api` URL as `PUBLIC_API_URL` meanwhile — and accept that those mandates keep it).

## 3. Schedules — `schedules.sh`

EventBridge **Scheduler** (it supports `America/Santiago`, DST included) → `lambda:InvokeFunction` on
`radal-backend-dev` with input `{"job": "<name>", "token": "<JOBS_TOKEN>"}`. The Lambda Web Adapter
delivers a non-HTTP event as `POST /events` (`AWS_LWA_PASS_THROUGH_PATH`, default `/events`); that route
lives outside `/api/v1`, so CloudFront never forwards it. The app runs the job only when
`JOBS_TOKEN` is set, `JOBS_EVENTS_ENABLED=true`, and the body token matches (constant time); otherwise
404/401 and nothing runs.

| schedule | expression (America/Santiago) | job period (idempotency slot) |
|---|---|---|
| `fx.daily` | `cron(0 8 * * ? *)` | day |
| `collection.clock` | `cron(30 6 * * ? *)` | day |
| `collection.notices` | `cron(0 9 ? * MON-FRI *)` | day |
| `collection.inquiries` | `cron(0 10 ? * MON-FRI *)` | day |
| `mail.poll` | `rate(5 minutes)` | minute |
| `payments.sync` | `rate(30 minutes)` | minute |
| `payments.payouts` | `cron(0 11 * * ? *)` | day |
| `rpa.run` | `rate(2 minutes)` | minute |
| `notifications.expire` | `cron(0 3 * * ? *)` | day |

```bash
./deploy/v12/schedules.sh                                    # plan (token masked)
DRY_RUN=0 ONLY="fx.daily collection.clock notifications.expire collection.notices collection.inquiries" \
  ./deploy/v12/schedules.sh                                  # phase 1: the daily jobs
DRY_RUN=0 SCHEDULE_STATE=DISABLED ./deploy/v12/schedules.sh  # pause all (e.g. before stopping RDS)
```

- The token is read from `deploy/backend.env` (`JOBS_TOKEN=`) unless `JOBS_TOKEN` is exported; it must
  be `[A-Za-z0-9_-]` (`openssl rand -hex 32`). It ends up in the schedule's target input, readable by
  anyone with `scheduler:GetSchedule` — treat that IAM permission as a secret-read.
- **Phase the frequent schedules.** Every trigger writes a `job_run` row even when the body skips
  (feature off). `rpa.run` every 2 min + `mail.poll` every 5 min ≈ 1 000 rows/day with nothing to do,
  and there is no pruning job. Enable `mail.poll` with `MAIL_ENABLED=true`, `rpa.run` with
  `RPA_ENABLED=true`, `payments.*` once a Forpay account is `active`.
- Scheduler retries (`RETRY_ATTEMPTS=2`) are safe: a day-slot job that succeeded is not run twice.
- **Stopping RDS** (the cost lever in `docs/deployment.md`) makes every job fail; pause first.
- Verify: `aws lambda invoke … --payload '{"job":"noop","token":"…"}'` (printed by the script) and
  `GET /api/v1/platform/jobs` as a platform admin (Mantenedor → Jobs). LWA's non-HTTP pass-through is
  documented for the adapter generation in the image (`aws-lambda-adapter:0.7.0`); **confirm with that
  `noop` invoke before trusting the schedules** — if it does not reach `/events`, bump the adapter
  (0.8.x) in `backend/Dockerfile`.

## 4. `deploy/backend.env` — keys v12 adds

CI (`.github/workflows/deploy-backend-dev.yml`, "Sync backend env from S3") **replaces** the whole Lambda
environment with this file on every deploy: a key set only in the console is wiped by the next push.
The Lambda environment is capped at **4 KB** in total — check `wc -c` after adding these.

| key | dev value | note |
|---|---|---|
| `CREDENTIALS_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` | **set before any secret is stored, and back it up.** Blank = derived from `SECRET_KEY`; switching later (or rotating `SECRET_KEY` while blank) makes every stored SMTP/IMAP/Forpay/portal secret unreadable |
| `JOBS_TOKEN` | `openssl rand -hex 32` | gates `/api/v1/internal/jobs/*` (internet-reachable through CloudFront!) and `/events` |
| `JOBS_EVENTS_ENABLED` | `true` | `/events` is 404 without it |
| `PUBLIC_APP_URL` | `https://dev.radalseguros.cl` | share links `/s/{token}` and mail links (blank ⇒ first CORS origin) |
| `PUBLIC_API_URL` | `https://hooks.radalseguros.cl` | webhook URLs handed to Forpay (blank ⇒ `PUBLIC_APP_URL`, which Forpay cannot POST to) |
| `MAIL_ENABLED` | `false` until `scripts.mail_preflight` is green (§4b) | `false` ⇒ every send is recorded `skipped`, no socket, no SES client |
| `MAIL_TRANSPORT` / `SES_REGION` / `SES_CONFIGURATION_SET` | defaults `ses` / `us-east-1` / blank — **do not add** unless changing them (4 KB cap) | the system sender sends through Amazon SES with the Lambda role; no AWS key in env |
| `MAIL_SENT_COPY` / `MAIL_COPY_ADDRESS` | defaults `bcc` / `contacto@radalseguros.cl` — do not add | every system mail Bcc's the Zoho mailbox (envelope only) |
| `RADAL_MAIL_REPLY_TO` | `respuestas@correo.radalseguros.cl` | Reply-To of insurer inquiries → SES inbound → S3 |
| `MAIL_INBOUND_SOURCE` | `s3` | `mail.poll` reads the system box from `s3://<S3_BUCKET>/inbound-mail/` (prefix default — do not add `MAIL_INBOUND_S3_PREFIX`) |
| `MAIL_RECIPIENT_ALLOWLIST` | `radalseguros.cl` on dev | To/Cc outside it are dropped (none left ⇒ `skipped`); blank in production |
| `RADAL_MAIL_USERNAME` / `RADAL_MAIL_PASSWORD` | **do not add** (Zoho Free has no IMAP) | only for `MAIL_SENT_COPY=imap_append` / `MAIL_INBOUND_SOURCE=imap` on a mailbox with IMAP |
| `RADAL_MAIL_IMAP_*` | defaults `imap.zoho.com:993 ssl` — do not add | |
| `RADAL_MAIL_SMTP_HOST`/`_PORT`/`_SECURITY` | not needed with SES | only for the `MAIL_TRANSPORT=smtp` fallback |
| `RADAL_OPS_EMAIL` | the team inbox | RPA/job failures; blank ⇒ nowhere |
| `FX_SOURCE_URL` | default `https://mindicador.cl/api` | blank ⇒ `fx.daily` skips |
| `FORPAY_DEFAULT_BASE_URL` | default sandbox `https://devapi.forpayservices.cl/white/label` | per-account `base_url` wins |
| `RPA_ENABLED` | `false` until HDI credentials + page maps exist | every portal reads "en mantención" while off |
| `RPA_INLINE` | **`false` on Lambda** | `true` runs the search in a FastAPI BackgroundTask, which a Lambda freezes after the response |
| `RPA_HDI_BASE_URL` | blank until the team sends the portal | blank ⇒ "Conector en configuración" |

## 4b. Mail via SES

**Architecture (final, 2026-10-17).**

```
OUT    Radal <contacto@radalseguros.cl> ──SES──► To/Cc (MAIL_RECIPIENT_ALLOWLIST)
                                          └────► Bcc contacto@ ──► Google (default routing) ──► Zoho   ← the human copy
REPLY  insurer ──► respuestas@correo.radalseguros.cl   (Reply-To of status inquiries; token in the subject)
       MX correo.radalseguros.cl ──► SES inbound receipt rule ──► s3://radal-dev-185011028331/inbound-mail/<SES id>
       mail.poll (every minute) ──► same pipeline as IMAP mail ──► Fwd: … to contacto@ (original attached) ──► Zoho
                                └─► object moved to inbound-mail/processed/<SES id>
```

The Radal system sender **sends** through Amazon SES v2 (`send_email` with `Content.Raw`, identity
`radalseguros.cl`, us-east-1); every system mail Bcc's `contacto@` (Zoho Free, no IMAP) so a human sees what
went out. Replies Radal must process go to `respuestas@correo.radalseguros.cl`, are read from S3 by
`mail.poll`, and each one is forwarded to `contacto@`. SES rewrites `Message-ID`; the stored id is
`<{MessageId}@email.amazonses.com>`, which is what replies quote. Broker custom mailboxes keep their own
SMTP/IMAP (no Bcc, allowlist applies). The four Google Workspace users are never used. Spec: tech-ref §19.7,
`docs/v12/04-mail.md` §6–§8.

**DNS (in place 2026-10-17; the preflight re-checks DKIM and MAIL FROM status).** Only SES records are needed; the **root MX, SPF and DMARC are untouched** (Google
keeps its four users):

| record | value | why |
|---|---|---|
| 3 × `CNAME {token}._domainkey.radalseguros.cl` | `{token}.dkim.amazonses.com` | Easy DKIM, `d=radalseguros.cl` → DMARC aligns on DKIM |
| `MX mail.radalseguros.cl` | `10 feedback-smtp.us-east-1.amazonses.com` | custom MAIL FROM (bounces to SES) |
| `TXT mail.radalseguros.cl` | `"v=spf1 include:amazonses.com ~all"` | SPF passes on `mail.radalseguros.cl`, which aligns (relaxed) with the From domain |

No `include:amazonses.com` at the root SPF is needed.

**SES inbound (live and verified 2026-10-17).** `MX correo.radalseguros.cl → inbound-smtp.us-east-1.amazonaws.com`;
an active receipt rule set with a rule for `respuestas@correo.radalseguros.cl` → S3 action, bucket
`radal-dev-185011028331`, prefix `inbound-mail/` (the bucket policy must let `ses.amazonaws.com` `s3:PutObject`
there, scoped with `aws:SourceAccount`). A test mail landed as `inbound-mail/<SES message id>`. SES writes the probe object
`inbound-mail/AMAZON_SES_SETUP_NOTIFICATION`; the poller archives it without forwarding.

**IAM (sending in place; inbound to add).** The Lambda role allows `ses:SendEmail` + `ses:SendRawEmail` on
`arn:aws:ses:us-east-1:<account>:identity/radalseguros.cl`, plus `ses:GetAccount` and `ses:GetEmailIdentity`
(read by the preflight). For S3 inbound it also needs `s3:ListBucket` on `arn:aws:s3:::radal-dev-185011028331`
(condition `s3:prefix` = `inbound-mail/*` or unconditioned) and `s3:GetObject`, `s3:PutObject`,
`s3:DeleteObject` on `arn:aws:s3:::radal-dev-185011028331/inbound-mail/*` (the move is copy + delete). The
documents policy may already cover the bucket — check before adding. If `SES_CONFIGURATION_SET` is ever set, also allow `ses:SendEmail` on that
configuration-set ARN or every send fails `AccessDenied`. In the sandbox SES also authorises the
**recipient** identity — sends to `@radalseguros.cl` are covered by the same ARN.

**Sandbox → production.** The account is in the **sandbox**: only verified addresses/domains can receive
(`radalseguros.cl` is verified, so team tests work; a payer or insurer address fails with "Amazon SES está en
modo sandbox y el destinatario no está verificado" on the `mail_message` row). Before real cobranza:
SES console → *Account dashboard* → *Request production access* (mail type **Transactional**, site
`https://radalseguros.cl`, use case "payer reminders and insurer status inquiries for insurance brokers;
bounces and complaints handled"), or
`aws sesv2 put-account-details --production-access-enabled --mail-type TRANSACTIONAL --website-url https://radalseguros.cl --use-case-description "…" --profile radal --region us-east-1`.
The preflight prints `SANDBOX`/`producción`.

**Env (dev Lambda).** Add to `deploy/backend.env`:

```
RADAL_MAIL_REPLY_TO=respuestas@correo.radalseguros.cl
MAIL_INBOUND_SOURCE=s3
MAIL_RECIPIENT_ALLOWLIST=radalseguros.cl
MAIL_ENABLED=true            # once the preflight is green (and production access, for real payers/insurers)
```

Every other mail key has the right default (`MAIL_SENT_COPY=bcc`, `MAIL_COPY_ADDRESS=contacto@radalseguros.cl`,
`MAIL_INBOUND_S3_PREFIX=inbound-mail/`, bucket = `S3_BUCKET`). Do **not** add `RADAL_MAIL_USERNAME` /
`RADAL_MAIL_PASSWORD` (Zoho Free has no IMAP). Never add AWS keys (the role is the credential) and never set
`AWS_REGION` (reserved in Lambda; `SES_REGION` is used instead). Remove `MAIL_RECIPIENT_ALLOWLIST` (or widen it)
only when dev should really mail payers/insurers.

**Preflight** (from `backend/`, venv active; any creds with the IAM above, e.g. the `radal` profile):

```bash
AWS_PROFILE=radal MAIL_INBOUND_SOURCE=s3 RADAL_MAIL_REPLY_TO=respuestas@correo.radalseguros.cl \
  MAIL_RECIPIENT_ALLOWLIST=radalseguros.cl \
  python -m scripts.mail_preflight                                   # read-only: SES, settings, S3 inbound count
AWS_PROFILE=radal MAIL_INBOUND_SOURCE=s3 RADAL_MAIL_REPLY_TO=respuestas@correo.radalseguros.cl \
  python -m scripts.mail_preflight --send-test you@radalseguros.cl   # one test via SES, Bcc contacto@, Reply-To respuestas@
```

Exit `0` ready · `3` SES not ready (sending disabled, identity unverified, IAM, or the test send failed) ·
`4` IMAP needed (imap inbound / imap_append) but the login failed · `5` the S3 inbound prefix cannot be listed.
It prints IMAP host + user only, never the password. End-to-end check: reply to the `--send-test` mail from an
external mailbox → an object appears under `inbound-mail/` (preflight count) → the next `mail.poll` moves it to
`processed/` and a `Fwd:` lands in Zoho.

**SUPERSEDED (kept for history) — replies over Zoho IMAP.** The root MX of `radalseguros.cl` points to
**Google**, so a reply to `contacto@radalseguros.cl` is delivered to Google first. It reaches the Zoho
mailbox only if Google Workspace routes that address onward (split delivery / routing for unknown
recipients to Zoho's MX); otherwise Google bounces it ("no such user") or keeps it. The same applies to SES
bounce feedback, forwarded to the From address. Verify: `--send-test` to a team address, reply to it from
an external mailbox (e.g. a personal Gmail), and check the reply lands in Zoho's Inbox. Until it does, keep
the `mail.poll` schedule off (inquiry replies and bounces would never be read). Second thing to verify on
the same pass: **Zoho Free has historically not included IMAP access** — if the preflight exits `4` with
"Autenticación rechazada" even with a fresh app password, that is the likely cause, and both the sent copy
and inbound reading depend on it.

**Verified 2026-10-17 — Google → Zoho routing for `contacto@`.** A first SES test to `contacto@radalseguros.cl`
hard-bounced at Google (no such Workspace user) and SES suppressed the address (removed again with
`sesv2 delete-suppressed-destination`). Fix applied in Google Admin: *Gmail → Hosts* → route `zoho` = `mx.zoho.com:25`
(TLS + CA-signed + hostname validation; Zoho presents a valid DigiCert `*.zoho.com` certificate), and *Gmail →
Default routing* → single recipient `contacto@radalseguros.cl`, *Change route* → `zoho`, only for non-recognized
addresses. The second SES test was delivered and is visible in Zoho (TLS). The four Google users are untouched.
Zoho Free has no IMAP → done the same day: Bcc copies to `contacto@` + the SES-inbound reply subdomain (architecture above).

## 5. Order of operations for the v12 dev deploy

1. Settle §0 (read-only). If egress is missing: `fck-nat.sh` (steps 1–7), then step 8.
2. Add the §4 keys to `deploy/backend.env` (`CREDENTIALS_KEY` and `JOBS_TOKEN` first).
3. Migrate RDS: `--dialect mysql --pass v12` (review) → snapshot → `--apply` → zero drift →
   `python -m scripts.seed_v12 --apply` (tech-ref §19.12). **Before** the push: `create_all` never ALTERs.
4. Push to `dev` (CI deploys and syncs the env).
5. `webhook-ingress.sh`, then `PUBLIC_API_URL` → redeploy.
6. `schedules.sh`, phased (§3). Verify with `noop`, then `fx.daily`.

## 6. Drift found in `docs/deployment.md` while writing this (not fixed there)

- "No NAT — it only talks to RDS" — see §0.
- §CI/CD names `.github/workflows/dev-deploy.yml`; the real files are `deploy-backend-dev.yml` and
  `deploy-frontend-dev.yml`.
- §CI/CD says the workflow sets the Function URL `--invoke-mode RESPONSE_STREAM`; the workflow sets
  **`BUFFERED`**, which is what OAC constraint 4 requires. The workflow is right, the sentence is wrong.
- §"Required GitHub secrets" lists `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`; CI authenticates with
  GitHub OIDC (`AWS_ROLE_ARN`) and also needs `S3_BUCKET`.
