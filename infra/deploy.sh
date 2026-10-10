#!/usr/bin/env bash
# One command to stand up (or update) the AWS side. Idempotent; safe to re-run.
#   cd infra && cp terraform/terraform.tfvars.example terraform/terraform.tfvars  # add your email
#   ./deploy.sh
# Needs: valid AWS credentials (aws sts get-caller-identity), terraform >= 1.5, docker.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tf="terraform -chdir=$here/terraform"
arch="${LAMBDA_ARCH:-arm64}"

aws sts get-caller-identity >/dev/null || { echo "AWS credentials are not valid: run 'aws configure' first"; exit 1; }
region="$(awk -F'"' '/^region/ {print $2}' "$here/terraform/terraform.tfvars" 2>/dev/null || true)"
region="${region:-${AWS_REGION:-ap-south-1}}"

$tf init -input=false >/dev/null
# 1) registry first, so there is somewhere to push the image
$tf apply -input=false -auto-approve -target=aws_ecr_repository.ingest -var "lambda_arch=$arch"
repo="$($tf output -raw ecr_repository)"

# 2) build + push the ingest image
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "${repo%%/*}"
docker build --platform "linux/$arch" --provenance=false -f "$here/../backend/Dockerfile.lambda" -t "$repo:latest" "$here/../backend"
docker push "$repo:latest"

# 3) everything else (bucket, topic, subscriptions, lambda, schedule)
$tf apply -input=false -auto-approve -var "lambda_arch=$arch"
bucket="$($tf output -raw archive_bucket)"
fn="$($tf output -raw lambda_name)"

# 4) seed the archive so the Lambda only ever does incremental updates
for f in "$here"/../backend/archive/*.json; do
  [ -e "$f" ] && aws s3 cp "$f" "s3://$bucket/archive/$(basename "$f")"
done

# 5) make sure the function runs the image we just pushed
aws lambda update-function-code --function-name "$fn" --image-uri "$repo:latest" --region "$region" >/dev/null
aws lambda wait function-updated --function-name "$fn" --region "$region"

echo
echo "Deployed. Confirm the SNS email subscription from your inbox, then add to backend/.env:"
$tf output -raw backend_env
echo
echo "Smoke test (dry run, no notification):  aws lambda invoke --function-name $fn --payload '{\"dry_run\":true}' --cli-binary-format raw-in-base64-out /dev/stdout"
echo "Drill (sends a real WARNING drill):      aws lambda invoke --function-name $fn --payload '{\"force_level\":\"WARNING\"}' --cli-binary-format raw-in-base64-out /dev/stdout"
