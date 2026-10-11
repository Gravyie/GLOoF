# One stack, two AWS accounts.
#   SMS account  (provider aws.sms, us-east-1): the alert topic + SMS/email subscriptions
#   Main account (provider aws.main):           everything else
# See infra/README.md for credentials, the state bootstrap and the fallback split.

terraform {
  required_version = ">= 1.10" # S3 backend native locking (use_lockfile)
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = ">= 3.6" }
  }
  # Partial config: terraform init -backend-config=backend.hcl (see backend.hcl.example)
  backend "s3" {}
}

provider "aws" {
  alias   = "main"
  profile = var.main_profile
  region  = var.main_region
  default_tags { tags = { project = "glof" } }
}

# The SMS account is restricted to SNS resources only. Never use this provider
# for compute, storage, or IAM (the one exception is the optional SMS
# delivery-log role inside the sms module). Region is fixed: SMS sandbox
# verification is per-region and the numbers were verified in us-east-1.
provider "aws" {
  alias   = "sms"
  profile = var.sms_profile
  region  = "us-east-1"
  default_tags { tags = { project = "glof" } }
}

module "main" {
  source    = "../modules/main"
  providers = { aws = aws.main }

  alert_topic_arn    = module.sms.alert_topic_arn
  image_tag          = var.image_tag
  arch               = var.arch
  lambda_live_alerts = var.lambda_live_alerts
  alert_dry_run      = var.alert_dry_run
  sim_live_sms       = var.sim_live_sms
  demo_sms_live      = var.demo_sms_live
  enable_cloudfront  = var.enable_cloudfront
}

module "sms" {
  source    = "../modules/sms"
  providers = { aws = aws.sms }

  publisher_role_arns      = [module.main.ingest_role_arn, module.main.task_role_arn, module.main.relay_role_arn]
  demo_sms_numbers         = var.demo_sms_numbers
  alert_emails             = var.alert_emails
  sms_spend_limit          = var.sms_spend_limit
  enable_filter_policies   = var.enable_filter_policies
  enable_sms_delivery_logs = var.enable_sms_delivery_logs
}
