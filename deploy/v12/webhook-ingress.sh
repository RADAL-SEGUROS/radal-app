#!/usr/bin/env bash
# v12 webhook ingress: an API Gateway HTTP API in front of the backend Lambda,
# for third parties (Forpay) that cannot POST through CloudFront OAC (OAC needs
# x-amz-content-sha256 on every POST body — a provider will never send it).
# See deploy/v12/README.md §2 and docs/v12/08-infra.md §2.
#
#   DRY_RUN=1 ./deploy/v12/webhook-ingress.sh     # default: print only, no AWS call
#   DRY_RUN=0 ./deploy/v12/webhook-ingress.sh     # apply (idempotent)
#
# What it builds (describe-before-create):
#   1. HTTP API  radal-hooks-dev
#   2. Lambda-proxy integration -> radal-backend-dev (payload 2.0; LWA translates it to HTTP)
#   3. ONE route: POST /api/v1/webhooks/{provider} — every other method/path is API Gateway's own 404
#   4. $default stage, auto-deploy, throttled (20 rps, burst 40)
#   5. lambda:InvokeFunction for apigateway.amazonaws.com, scoped to that route's ARN
#   6. Custom domain hooks.radalseguros.cl (regional, the existing *.radalseguros.cl ACM cert) + mapping
#   7. Route 53 alias A record hooks.radalseguros.cl -> the API domain
#   (8. optional) disable the execute-api default endpoint so ONLY the custom domain answers
#
# No auth on the route by design: the app authenticates every call with the
# `account` + `sig` query params, then Forpay's payload HMAC (README §2).
# After this: set PUBLIC_API_URL=https://hooks.radalseguros.cl in S3 deploy/backend.env
# BEFORE the first mandate is created — Forpay stores webhook URLs per mandate.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- variables
API_NAME="${API_NAME:-radal-hooks-dev}"
LAMBDA_FUNCTION="${LAMBDA_FUNCTION:-radal-backend-dev}"
ROUTE_KEY="${ROUTE_KEY:-POST /api/v1/webhooks/{provider}}"   # the app has exactly one webhook handler
THROTTLE_RATE="${THROTTLE_RATE:-20}"
THROTTLE_BURST="${THROTTLE_BURST:-40}"
INTEGRATION_TIMEOUT_MS="${INTEGRATION_TIMEOUT_MS:-29000}"    # HTTP API max is 30 000
HOOKS_DOMAIN="${HOOKS_DOMAIN:-hooks.radalseguros.cl}"
CERT_ARN="${CERT_ARN:-arn:aws:acm:us-east-1:185011028331:certificate/e7f7ac98-449a-446b-b228-f320e9ae0727}"  # *.radalseguros.cl
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-Z08394372WP1FCSUM9CQ3}"   # radalseguros.cl
CREATE_DOMAIN="${CREATE_DOMAIN:-1}"                         # 0 = stop after the stage (use the execute-api URL)
DISABLE_DEFAULT_ENDPOINT="${DISABLE_DEFAULT_ENDPOINT:-0}"   # 1 only after the custom domain answers
PERMISSION_SID="${PERMISSION_SID:-radal-hooks-apigw-invoke}"

# shellcheck source=deploy/v12/_lib.sh
. "$HERE/_lib.sh"

LAMBDA_ARN="arn:aws:lambda:$REGION:$ACCOUNT_ID:function:$LAMBDA_FUNCTION"
ROUTE_METHOD="${ROUTE_KEY%% *}"
ROUTE_PATH="${ROUTE_KEY#* }"

preflight

# ---------------------------------------------------------------- 1. API
log "1. HTTP API $API_NAME"
API_ID=$(aws_read apigatewayv2 get-apis --query "Items[?Name=='$API_NAME'] | [0].ApiId")
if [ -z "$API_ID" ]; then
  API_ID=$(aws_do "<API_ID>" apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP \
    --description "Radal provider webhooks (Forpay) -> $LAMBDA_FUNCTION" --tags "Project=radal,Env=dev" --query ApiId)
else
  note "exists: $API_ID"
fi

# ---------------------------------------------------------------- 2. integration
log "2. Lambda proxy integration -> $LAMBDA_FUNCTION"
INTEGRATION_ID=$(aws_read apigatewayv2 get-integrations --api-id "$API_ID" \
  --query "Items[?IntegrationUri=='$LAMBDA_ARN'] | [0].IntegrationId")
if [ -z "$INTEGRATION_ID" ]; then
  INTEGRATION_ID=$(aws_do "<INTEGRATION_ID>" apigatewayv2 create-integration --api-id "$API_ID" \
    --integration-type AWS_PROXY --integration-uri "$LAMBDA_ARN" --payload-format-version 2.0 \
    --timeout-in-millis "$INTEGRATION_TIMEOUT_MS" --query IntegrationId)
else
  note "exists: $INTEGRATION_ID"
fi

# ---------------------------------------------------------------- 3. route
log "3. route '$ROUTE_KEY' (the only one)"
ROUTE_ID=$(aws_read apigatewayv2 get-routes --api-id "$API_ID" --query "Items[?RouteKey=='$ROUTE_KEY'] | [0].RouteId")
if [ -z "$ROUTE_ID" ]; then
  aws_run apigatewayv2 create-route --api-id "$API_ID" --route-key "$ROUTE_KEY" \
    --target "integrations/$INTEGRATION_ID" --authorization-type NONE
else
  note "exists: $ROUTE_ID"
fi
OTHER_ROUTES=$(aws_read apigatewayv2 get-routes --api-id "$API_ID" --query "Items[?RouteKey!='$ROUTE_KEY'].RouteKey")
if [ -n "$OTHER_ROUTES" ]; then
  warn "the API has other routes ($OTHER_ROUTES) — the ingress should expose ONLY the webhook"
fi

# ---------------------------------------------------------------- 4. stage
log "4. \$default stage (auto-deploy, ${THROTTLE_RATE} rps / burst ${THROTTLE_BURST})"
ROUTE_SETTINGS="ThrottlingBurstLimit=$THROTTLE_BURST,ThrottlingRateLimit=$THROTTLE_RATE"
STAGE=$(aws_read apigatewayv2 get-stages --api-id "$API_ID" --query "Items[?StageName=='\$default'] | [0].StageName")
if [ -z "$STAGE" ]; then
  aws_run apigatewayv2 create-stage --api-id "$API_ID" --stage-name '$default' --auto-deploy \
    --default-route-settings "$ROUTE_SETTINGS"
else
  aws_run apigatewayv2 update-stage --api-id "$API_ID" --stage-name '$default' --default-route-settings "$ROUTE_SETTINGS"
fi
# $default has no stage prefix, so the Lambda sees exactly /api/v1/webhooks/<provider>.

# ---------------------------------------------------------------- 5. invoke permission
log "5. lambda:InvokeFunction for API Gateway (this route only)"
SOURCE_ARN="arn:aws:execute-api:$REGION:$ACCOUNT_ID:$API_ID/*/$ROUTE_METHOD${ROUTE_PATH//\{provider\}/*}"
POLICY=$(aws_read lambda get-policy --function-name "$LAMBDA_FUNCTION" --query Policy)
case "$POLICY" in
  *"\"$PERMISSION_SID\""*) note "statement $PERMISSION_SID already present";;
  *) aws_run lambda add-permission --function-name "$LAMBDA_FUNCTION" --statement-id "$PERMISSION_SID" \
       --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn "$SOURCE_ARN";;
esac
# The Function URL (AWS_IAM, CloudFront OAC) is untouched: this is a second door, not a replacement.

# ---------------------------------------------------------------- 6-7. domain + DNS
if [ "$CREATE_DOMAIN" = "1" ]; then
  log "6. custom domain $HOOKS_DOMAIN (regional, TLS 1.2)"
  DOMAIN_FOUND=$(aws_read apigatewayv2 get-domain-name --domain-name "$HOOKS_DOMAIN" --query DomainName)
  if [ -z "$DOMAIN_FOUND" ]; then
    aws_run apigatewayv2 create-domain-name --domain-name "$HOOKS_DOMAIN" \
      --domain-name-configurations "CertificateArn=$CERT_ARN,EndpointType=REGIONAL,SecurityPolicy=TLS_1_2"
  else
    note "exists: $DOMAIN_FOUND"
  fi
  MAPPING=$(aws_read apigatewayv2 get-api-mappings --domain-name "$HOOKS_DOMAIN" \
    --query "Items[?ApiId=='$API_ID'] | [0].ApiMappingId")
  if [ -z "$MAPPING" ]; then
    aws_run apigatewayv2 create-api-mapping --domain-name "$HOOKS_DOMAIN" --api-id "$API_ID" --stage '$default'
  else
    note "mapping exists: $MAPPING"
  fi

  log "7. Route 53 alias $HOOKS_DOMAIN -> API Gateway domain"
  TARGET_DNS=$(aws_read apigatewayv2 get-domain-name --domain-name "$HOOKS_DOMAIN" \
    --query 'DomainNameConfigurations[0].ApiGatewayDomainName')
  TARGET_ZONE=$(aws_read apigatewayv2 get-domain-name --domain-name "$HOOKS_DOMAIN" \
    --query 'DomainNameConfigurations[0].HostedZoneId')
  if ! is_dry && { [ -z "$TARGET_DNS" ] || [ -z "$TARGET_ZONE" ]; }; then
    die "could not read the API Gateway target of $HOOKS_DOMAIN (is the ACM cert ISSUED?) — not touching Route 53"
  fi
  TARGET_DNS=$(or_placeholder "$TARGET_DNS" "<d-xxxx.execute-api.$REGION.amazonaws.com>")
  TARGET_ZONE=$(or_placeholder "$TARGET_ZONE" "<APIGW_REGIONAL_ZONE_ID>")
  CHANGE_BATCH="{\"Comment\":\"Radal webhook ingress\",\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"$HOOKS_DOMAIN\",\"Type\":\"A\",\"AliasTarget\":{\"DNSName\":\"$TARGET_DNS\",\"HostedZoneId\":\"$TARGET_ZONE\",\"EvaluateTargetHealth\":false}}}]}"
  aws_run route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" --change-batch "$CHANGE_BATCH"
else
  log "6-7. custom domain skipped (CREATE_DOMAIN=0)"
fi

# ---------------------------------------------------------------- 8. default endpoint
if [ "$DISABLE_DEFAULT_ENDPOINT" = "1" ]; then
  log "8. disabling https://$API_ID.execute-api.$REGION.amazonaws.com (only $HOOKS_DOMAIN answers)"
  aws_run apigatewayv2 update-api --api-id "$API_ID" --disable-execute-api-endpoint
else
  log "8. default execute-api endpoint left ON (DISABLE_DEFAULT_ENDPOINT=0) — switch it off once $HOOKS_DOMAIN answers"
fi

log "Done. Next (README §2):"
note "1) S3 deploy/backend.env: PUBLIC_API_URL=https://$HOOKS_DOMAIN  (then redeploy — CI syncs the env)"
note "2) smoke: curl -i -X POST 'https://$HOOKS_DOMAIN/api/v1/webhooks/forpay?account=nope&sig=x' -H 'content-type: application/json' -d '{}'"
note "   expect 404 {\"detail\":\"Not found\"} FROM THE APP (unknown account) — proves the path reaches FastAPI"
note "3) curl -i 'https://$HOOKS_DOMAIN/api/v1/health' -> API Gateway's own 404 {\"message\":\"Not Found\"} (nothing else exposed)"
