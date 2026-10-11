#!/usr/bin/env bash
# Two-account deploy. Idempotent; safe to re-run.
#   cd infra && ./bootstrap-state.sh main ap-south-1     # once
#   cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # fill in
#   ./deploy.sh
# Needs: terraform >= 1.10, docker (running), node/npm, aws cli v2, profiles from tfvars.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tfdir="$here/terraform"
tf="terraform -chdir=$tfdir"
fail() { echo "PREFLIGHT FAILED: $*" >&2; exit 1; }

[ -f "$tfdir/terraform.tfvars" ] || fail "terraform/terraform.tfvars is missing (copy terraform.tfvars.example)"
[ -f "$tfdir/backend.hcl" ] || fail "terraform/backend.hcl is missing (run ./bootstrap-state.sh)"
docker info >/dev/null 2>&1 || fail "Docker is not running"

$tf init -input=false -reconfigure -backend-config=backend.hcl >/dev/null
v() { echo "$1" | $tf console -var-file=terraform.tfvars 2>/dev/null | tr -d '"'; }
MAIN_PROFILE="$(v var.main_profile)"
SMS_PROFILE="$(v var.sms_profile)"
MAIN_REGION="$(v var.main_region)"
ARCH="$(v var.arch)"
EXP_MAIN="$(v var.expected_main_account_id)"
EXP_SMS="$(v var.expected_sms_account_id)"
NUMBERS="$(v 'join(" ", nonsensitive(var.demo_sms_numbers))')"
CLOUDFRONT="$(v var.enable_cloudfront)"

# ---------------------------------------------------------------- preflight
echo "== preflight"
MAIN_ID="$(aws sts get-caller-identity --profile "$MAIN_PROFILE" --query Account --output text 2>/dev/null)" \
  || fail "profile '$MAIN_PROFILE' cannot authenticate"
[ "$MAIN_ID" = "$EXP_MAIN" ] || fail "profile '$MAIN_PROFILE' is account $MAIN_ID, expected $EXP_MAIN"
SMS_ID="$(aws sts get-caller-identity --profile "$SMS_PROFILE" --query Account --output text 2>/dev/null)" \
  || fail "profile '$SMS_PROFILE' cannot authenticate"
[ "$SMS_ID" = "$EXP_SMS" ] || fail "profile '$SMS_PROFILE' is account $SMS_ID, expected $EXP_SMS"
[ "$MAIN_ID" != "$SMS_ID" ] || fail "main and SMS profiles point at the same account ($MAIN_ID)"

verified="$(aws sns list-sms-sandbox-phone-numbers --region us-east-1 --profile "$SMS_PROFILE" \
  --query "PhoneNumbers[?Status=='Verified'].PhoneNumber" --output text)"
for n in $NUMBERS; do
  grep -qw -- "$n" <<<"$verified" || fail "number ...${n: -4} is not Verified in the SMS sandbox (SMS account, us-east-1)"
done
echo "   main account $MAIN_ID, SMS account $SMS_ID, $(wc -w <<<"$NUMBERS" | tr -d ' ') verified number(s)"

# ---------------------------------------------------------------- 1-2. images
echo "== registries"
# First deploy only: create the registries so there is somewhere to push images.
# Skipped once they exist: a targeted apply can conflict with Terraform's automatic
# address moves (e.g. after toggling enable_cloudfront).
if ! $tf state list 2>/dev/null | grep -q 'module.main.aws_ecr_repository.api$'; then
  $tf apply -input=false -auto-approve -target=module.main.aws_ecr_repository.ingest -target=module.main.aws_ecr_repository.api >/dev/null
fi
ECR_INGEST="$($tf output -raw ecr_ingest)"
ECR_API="$($tf output -raw ecr_api)"
aws ecr get-login-password --region "$MAIN_REGION" --profile "$MAIN_PROFILE" \
  | docker login --username AWS --password-stdin "${ECR_INGEST%%/*}" >/dev/null

echo "== frontend build (bundled into the API image; also uploaded to S3 when CloudFront is on)"
(cd "$here/../frontend" && npm ci --silent && VITE_API_BASE="" npm run build)
find "$here/../backend/static" -mindepth 1 ! -name .gitkeep -delete
cp -R "$here/../frontend/dist/." "$here/../backend/static/"

echo "== 1. Lambda image"
docker build --platform "linux/$ARCH" --provenance=false -f "$here/../backend/Dockerfile.lambda" -t "$ECR_INGEST:latest" "$here/../backend"
docker push "$ECR_INGEST:latest"
echo "== 2. API image"
docker build --platform "linux/$ARCH" --provenance=false -f "$here/../backend/Dockerfile" -t "$ECR_API:latest" "$here/../backend"
docker push "$ECR_API:latest"

# ---------------------------------------------------------------- 3. infrastructure
echo "== 3. terraform apply"
$tf apply -input=false -auto-approve
SITE_BUCKET="$($tf output -raw site_bucket)"
DIST="$($tf output -raw distribution_id)"
ARCHIVE="$($tf output -raw archive_bucket)"
# make Lambda + ECS pick up the images just pushed (tag 'latest' does not change the definition)
aws lambda update-function-code --function-name "$($tf output -raw ingest_function)" --image-uri "$ECR_INGEST:latest" \
  --region "$MAIN_REGION" --profile "$MAIN_PROFILE" >/dev/null
aws ecs update-service --cluster "$($tf output -raw ecs_cluster)" --service "$($tf output -raw ecs_service)" \
  --force-new-deployment --region "$MAIN_REGION" --profile "$MAIN_PROFILE" >/dev/null

# ---------------------------------------------------------------- 4-6. frontend
if [ "$CLOUDFRONT" = "true" ]; then
  echo "== 4-5. upload site to S3"
  aws s3 sync "$here/../frontend/dist" "s3://$SITE_BUCKET" --delete --profile "$MAIN_PROFILE" --region "$MAIN_REGION"
  echo "== 6. invalidate CDN"
  aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/*' --profile "$MAIN_PROFILE" >/dev/null
else
  echo "== 4-6. CloudFront off: the API task serves the site through the ALB (HTTP)"
fi

# ---------------------------------------------------------------- 7. archive seed
echo "== 7. seed archive"
for f in "$here"/../backend/archive/*.json; do
  [ -e "$f" ] && aws s3 cp "$f" "s3://$ARCHIVE/archive/$(basename "$f")" --profile "$MAIN_PROFILE" --region "$MAIN_REGION"
done

# ---------------------------------------------------------------- 8. summary
echo
if [ "$CLOUDFRONT" = "true" ]; then
  echo "Site:        $($tf output -raw site_url)   (first CloudFront deploy can take ~10 min)"
else
  echo "Site:        $($tf output -raw site_url)   (HTTP via the load balancer; the new task takes ~2 min to become healthy)"
fi
echo "Alert topic: $($tf output -raw alert_topic_arn)"
echo "Operator key: aws secretsmanager get-secret-value --secret-id $($tf output -raw api_key_secret) --query SecretString --output text --profile $MAIN_PROFILE --region $MAIN_REGION"
echo
echo "Reminder: each email subscriber must click the confirmation link SNS sent (expires after 3 days)."
