#!/usr/bin/env bash
# v12 schedules: EventBridge Scheduler -> backend Lambda, one schedule per job.
# The Lambda Web Adapter hands a non-HTTP event to the app as POST /events
# (AWS_LWA_PASS_THROUGH_PATH, default /events); the app runs the job when the
# body token equals settings.JOBS_TOKEN AND JOBS_EVENTS_ENABLED=true.
# See deploy/v12/README.md §3 and docs/v12/08-infra.md §3.
#
#   DRY_RUN=1 ./deploy/v12/schedules.sh                         # default: print only, no AWS call
#   DRY_RUN=0 ./deploy/v12/schedules.sh                         # apply all (token read from S3 backend.env)
#   DRY_RUN=0 ONLY="fx.daily mail.poll" ./deploy/v12/schedules.sh
#   DRY_RUN=0 SCHEDULE_STATE=DISABLED ./deploy/v12/schedules.sh # pause everything (e.g. while RDS is stopped)
#
# What it builds (describe-before-create; schedules are create-or-update):
#   1. IAM role radal-scheduler-dev: trusted by scheduler.amazonaws.com (this account only),
#      allowed lambda:InvokeFunction on the backend function only
#   2. schedule group radal-dev
#   3. one schedule per job, timezone America/Santiago, flexible window OFF
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- variables
LAMBDA_FUNCTION="${LAMBDA_FUNCTION:-radal-backend-dev}"
SCHEDULE_GROUP="${SCHEDULE_GROUP:-radal-dev}"
SCHEDULE_PREFIX="${SCHEDULE_PREFIX:-radal-dev}"
SCHEDULER_ROLE="${SCHEDULER_ROLE:-radal-scheduler-dev}"
TIMEZONE="${TIMEZONE:-America/Santiago}"
SCHEDULE_STATE="${SCHEDULE_STATE:-ENABLED}"         # ENABLED | DISABLED
ONLY="${ONLY:-}"                                     # space-separated job names; blank = all
S3_ENV_URI="${S3_ENV_URI:-s3://radal-dev-185011028331/deploy/backend.env}"
JOBS_TOKEN="${JOBS_TOKEN:-}"                         # blank -> read JOBS_TOKEN= from $S3_ENV_URI (apply mode)
RETRY_ATTEMPTS="${RETRY_ATTEMPTS:-2}"                # jobs are idempotent per slot, so a retry is safe
MAX_EVENT_AGE_S="${MAX_EVENT_AGE_S:-600}"

# job | schedule expression (evaluated in $TIMEZONE; rate() ignores it). Keep in step with
# docs/technical-reference.md §19.5 and `python -m app.jobs --list`.
JOBS_TABLE='
fx.daily|cron(0 8 * * ? *)
collection.clock|cron(30 6 * * ? *)
collection.notices|cron(0 9 ? * MON-FRI *)
collection.inquiries|cron(0 10 ? * MON-FRI *)
mail.poll|rate(5 minutes)
payments.sync|rate(30 minutes)
payments.payouts|cron(0 11 * * ? *)
rpa.run|rate(2 minutes)
notifications.expire|cron(0 3 * * ? *)
'

# shellcheck source=deploy/v12/_lib.sh
. "$HERE/_lib.sh"

LAMBDA_ARN="arn:aws:lambda:$REGION:$ACCOUNT_ID:function:$LAMBDA_FUNCTION"
case "$SCHEDULE_STATE" in ENABLED|DISABLED) ;; *) die "SCHEDULE_STATE must be ENABLED or DISABLED";; esac

preflight

# ---------------------------------------------------------------- token
if is_dry; then
  JOBS_TOKEN="${JOBS_TOKEN:-DRYRUNTOKEN}"
else
  if [ -z "$JOBS_TOKEN" ]; then
    log "reading JOBS_TOKEN from $S3_ENV_URI"
    JOBS_TOKEN=$(aws s3 cp "$S3_ENV_URI" - --profile "$PROFILE" --region "$REGION" \
      | sed -n 's/^JOBS_TOKEN=//p' | tail -n 1 | tr -d '\r') || true
  fi
  [ -n "$JOBS_TOKEN" ] || die "JOBS_TOKEN is empty — add it to $S3_ENV_URI first (openssl rand -hex 32), with JOBS_EVENTS_ENABLED=true"
fi
case "$JOBS_TOKEN" in
  *[!A-Za-z0-9_-]*) die "JOBS_TOKEN must be [A-Za-z0-9_-] only (use: openssl rand -hex 32)";;
esac
REDACT="$JOBS_TOKEN"   # never printed, in dry-run or apply

# ---------------------------------------------------------------- 1. role
log "1. scheduler role $SCHEDULER_ROLE (invoke $LAMBDA_FUNCTION only)"
ROLE_ARN=$(aws_read iam get-role --role-name "$SCHEDULER_ROLE" --query 'Role.Arn')
if [ -z "$ROLE_ARN" ]; then
  ROLE_ARN=$(aws_do "arn:aws:iam::$ACCOUNT_ID:role/$SCHEDULER_ROLE" iam create-role --role-name "$SCHEDULER_ROLE" \
    --assume-role-policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"scheduler.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT_ID\"}}}]}" \
    --query 'Role.Arn')
  if ! is_dry; then note "waiting 10 s for IAM propagation"; sleep 10; fi
else
  note "exists: $ROLE_ARN"
fi
aws_run iam put-role-policy --role-name "$SCHEDULER_ROLE" --policy-name invoke-backend \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":[\"$LAMBDA_ARN\",\"$LAMBDA_ARN:*\"]}]}"

# ---------------------------------------------------------------- 2. group
log "2. schedule group $SCHEDULE_GROUP"
GROUP=$(aws_read scheduler get-schedule-group --name "$SCHEDULE_GROUP" --query Name)
if [ -z "$GROUP" ]; then
  aws_run scheduler create-schedule-group --name "$SCHEDULE_GROUP"
else
  note "exists: $GROUP"
fi

# ---------------------------------------------------------------- 3. schedules
log "3. schedules ($SCHEDULE_STATE, $TIMEZONE)"
COUNT=0
while IFS='|' read -r JOB EXPR; do
  [ -n "$JOB" ] || continue
  if [ -n "$ONLY" ]; then
    case " $ONLY " in *" $JOB "*) ;; *) continue;; esac
  fi
  NAME="$SCHEDULE_PREFIX-$(printf '%s' "$JOB" | tr '.' '-')"
  # Lambda Web Adapter 0.7.0 does NOT pass bare non-HTTP events through (verified 2026-10-17: it
  # turns them into "GET /" -> 404). So the scheduler sends a Function-URL-style HTTP v2 event:
  # POST /events with the {job, token} JSON body, which 0.7.0 forwards as a normal request.
  TARGET=$(JOB="$JOB" TOKEN="$JOBS_TOKEN" ARN="$LAMBDA_ARN" ROLE="$ROLE_ARN" RETRY="$RETRY_ATTEMPTS" AGE="$MAX_EVENT_AGE_S" python3 -c '
import json, os
body = json.dumps({"job": os.environ["JOB"], "token": os.environ["TOKEN"]})
event = {"version": "2.0", "routeKey": "$default", "rawPath": "/events", "rawQueryString": "",
         "headers": {"content-type": "application/json"},
         "requestContext": {"http": {"method": "POST", "path": "/events", "protocol": "HTTP/1.1",
                                     "sourceIp": "127.0.0.1", "userAgent": "eventbridge-scheduler"},
                            "accountId": "anonymous", "apiId": "scheduler", "domainName": "scheduler",
                            "domainPrefix": "scheduler", "requestId": "scheduler", "routeKey": "$default",
                            "stage": "$default", "time": "01/Jan/2026:00:00:00 +0000", "timeEpoch": 0},
         "body": body, "isBase64Encoded": False}
print(json.dumps({"Arn": os.environ["ARN"], "RoleArn": os.environ["ROLE"], "Input": json.dumps(event),
                  "RetryPolicy": {"MaximumRetryAttempts": int(os.environ["RETRY"]),
                                  "MaximumEventAgeInSeconds": int(os.environ["AGE"])}}))')
  FOUND=$(aws_read scheduler get-schedule --name "$NAME" --group-name "$SCHEDULE_GROUP" --query Name)
  if [ -z "$FOUND" ]; then VERB=create-schedule; else VERB=update-schedule; fi
  note "$JOB  $EXPR  -> $NAME ($VERB)"
  aws_run scheduler "$VERB" --name "$NAME" --group-name "$SCHEDULE_GROUP" \
    --schedule-expression "$EXPR" --schedule-expression-timezone "$TIMEZONE" \
    --flexible-time-window Mode=OFF --state "$SCHEDULE_STATE" \
    --description "Radal job $JOB (POST /events)" --target "$TARGET"
  COUNT=$((COUNT + 1))
done <<EOF
$JOBS_TABLE
EOF
[ "$COUNT" -gt 0 ] || die "no job matched ONLY='$ONLY'"

log "Done: $COUNT schedule(s). Before they do anything the Lambda env (S3 deploy/backend.env) needs:"
note "JOBS_TOKEN=<same token>  JOBS_EVENTS_ENABLED=true   — otherwise POST /events answers 404 and every run is a no-op"
note "verify: aws lambda invoke --profile $PROFILE --region $REGION --function-name $LAMBDA_FUNCTION \\"
note "        (send an HTTP v2 envelope: POST /events with body {\"job\":\"noop\",\"token\":\"<JOBS_TOKEN>\"} — see the TARGET builder above)"
note "        -> {\"job\":\"noop\",\"status\":\"succeeded\",\"executed\":true,…}; then GET /api/v1/platform/jobs as a platform admin"
