# FALLBACK ROOT (see infra/README.md): use only if the SMS account cannot be reached
# by Terraform from your machine. Apply from AWS CloudShell inside the SMS account,
# AFTER infra/main, because the topic policy references the main account's roles.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

# SMS account: SNS resources only. Region fixed to the sandbox-verified region.
provider "aws" {
  region = "us-east-1"
}

variable "main_account_id" { type = string }
variable "name" {
  type    = string
  default = "glof"
}
variable "demo_sms_numbers" {
  type      = list(string)
  sensitive = true
}
variable "alert_emails" {
  type      = list(string)
  sensitive = true
  default   = []
}
variable "sms_spend_limit" {
  type    = number
  default = 1
}

module "sms" {
  source = "../modules/sms"
  # role names are fixed by modules/main
  publisher_role_arns = [for r in ["ingest", "api-task", "alarm-relay"] : "arn:aws:iam::${var.main_account_id}:role/${var.name}-${r}"]
  demo_sms_numbers    = var.demo_sms_numbers
  alert_emails        = var.alert_emails
  sms_spend_limit     = var.sms_spend_limit
}

output "alert_topic_arn" { value = module.sms.alert_topic_arn }
