#!/usr/bin/env bash
# One-time: create the Terraform state bucket in the MAIN account, then write backend.hcl.
#   ./bootstrap-state.sh [main-profile] [region]
# Locking uses S3 lock files (use_lockfile), so no DynamoDB lock table is needed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
profile="${1:-main}"
region="${2:-ap-south-1}"
acct="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
bucket="glof-tfstate-$acct"

if aws s3api head-bucket --bucket "$bucket" --profile "$profile" 2>/dev/null; then
  echo "state bucket $bucket already exists"
else
  if [ "$region" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$bucket" --profile "$profile" --region "$region" >/dev/null
  else
    aws s3api create-bucket --bucket "$bucket" --profile "$profile" --region "$region" \
      --create-bucket-configuration LocationConstraint="$region" >/dev/null
  fi
  aws s3api put-bucket-versioning --bucket "$bucket" --profile "$profile" --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$bucket" --profile "$profile" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block --bucket "$bucket" --profile "$profile" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "created state bucket $bucket"
fi

cat > "$here/terraform/backend.hcl" <<HCL
bucket       = "$bucket"
key          = "glof/terraform.tfstate"
region       = "$region"
profile      = "$profile"
encrypt      = true
use_lockfile = true
HCL
echo "wrote terraform/backend.hcl"
