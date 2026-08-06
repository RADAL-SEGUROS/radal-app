#!/usr/bin/env bash
# Attach the custom domain (dev.radalseguros.cl) + ACM cert to the CloudFront
# distribution. Run this ONCE, AFTER:
#   1) radalseguros.cl nameservers are delegated to the Route 53 zone, AND
#   2) the ACM cert has validated (DNS validation record is already in the zone).
# Safe to re-run.
set -euo pipefail

PROFILE="${AWS_PROFILE:-radal}"
REGION="us-east-1"
DIST_ID="E2MGVTSWQDFPY3"
CERT_ARN="arn:aws:acm:us-east-1:185011028331:certificate/e7f7ac98-449a-446b-b228-f320e9ae0727"
DOMAIN="dev.radalseguros.cl"

echo "Waiting for ACM cert to be ISSUED (needs nameserver delegation first)..."
aws acm wait certificate-validated --certificate-arn "$CERT_ARN" --profile "$PROFILE" --region "$REGION"

echo "Attaching $DOMAIN + cert to CloudFront $DIST_ID ..."
aws cloudfront get-distribution-config --id "$DIST_ID" --profile "$PROFILE" > /tmp/radal_dist.json
ETAG=$(python3 -c "import json;print(json.load(open('/tmp/radal_dist.json'))['ETag'])")
python3 - "$DOMAIN" "$CERT_ARN" <<'PY'
import json, sys
domain, cert = sys.argv[1], sys.argv[2]
cfg = json.load(open('/tmp/radal_dist.json'))['DistributionConfig']
cfg['Aliases'] = {'Quantity': 1, 'Items': [domain]}
cfg['ViewerCertificate'] = {
    'ACMCertificateArn': cert, 'Certificate': cert, 'CertificateSource': 'acm',
    'SSLSupportMethod': 'sni-only', 'MinimumProtocolVersion': 'TLSv1.2_2021',
    'CloudFrontDefaultCertificate': False,
}
json.dump(cfg, open('/tmp/radal_dist_new.json', 'w'))
PY
aws cloudfront update-distribution --id "$DIST_ID" \
  --distribution-config file:///tmp/radal_dist_new.json --if-match "$ETAG" \
  --profile "$PROFILE" --query 'Distribution.Status' --output text

echo "Done. Once CloudFront redeploys, https://${DOMAIN} serves the app."
