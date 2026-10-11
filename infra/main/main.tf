# FALLBACK ROOT (see infra/README.md): the main account on its own. The topic ARN is
# deterministic (arn:aws:sns:us-east-1:<sms-account>:glof-alerts), so apply this
# first, then infra/sms in the SMS account.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = ">= 3.6" }
  }
  backend "s3" {}
}

provider "aws" {
  profile = var.main_profile
  region  = var.main_region
}

variable "main_profile" {
  type    = string
  default = "main"
}
variable "main_region" {
  type    = string
  default = "ap-south-1"
}
variable "alert_topic_arn" { type = string }
variable "alert_dry_run" {
  type    = bool
  default = true
}
variable "sim_live_sms" {
  type    = bool
  default = false
}
variable "demo_sms_live" {
  type    = bool
  default = false
}
variable "enable_cloudfront" {
  type    = bool
  default = true
}

module "main" {
  source            = "../modules/main"
  alert_topic_arn   = var.alert_topic_arn
  alert_dry_run     = var.alert_dry_run
  sim_live_sms      = var.sim_live_sms
  demo_sms_live     = var.demo_sms_live
  enable_cloudfront = var.enable_cloudfront
}

output "site_url" { value = module.main.site_url }
output "ecr_ingest" { value = module.main.ecr_ingest }
output "ecr_api" { value = module.main.ecr_api }
output "site_bucket" { value = module.main.site_bucket }
output "archive_bucket" { value = module.main.archive_bucket }
output "distribution_id" { value = module.main.distribution_id }
