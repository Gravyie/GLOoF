variable "main_profile" {
  description = "AWS credentials profile for the main account (~/.aws/credentials)"
  type        = string
  default     = "main"
}

variable "sms_profile" {
  description = "AWS credentials profile for the SMS account (SNS-only IAM user)"
  type        = string
  default     = "sms"
}

variable "main_region" {
  type    = string
  default = "ap-south-1"
}

variable "expected_main_account_id" {
  description = "Checked by deploy.sh preflight"
  type        = string
}

variable "expected_sms_account_id" {
  description = "Checked by deploy.sh preflight"
  type        = string
}

variable "demo_sms_numbers" {
  description = "E.164 numbers verified in the SNS SMS sandbox of the SMS account (us-east-1)"
  type        = list(string)
  sensitive   = true
}

variable "alert_emails" {
  type      = list(string)
  sensitive = true
  default   = []
}

variable "sms_spend_limit" {
  description = "Monthly SMS spend cap in USD"
  type        = number
  default     = 1
}

variable "enable_sms_delivery_logs" {
  type    = bool
  default = false
}

variable "enable_filter_policies" {
  description = "Phones receive only audience=public (drills, public alerts); alarms go to email only"
  type        = bool
  default     = true
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "arch" {
  type    = string
  default = "arm64"
}

variable "lambda_live_alerts" {
  type    = bool
  default = false
}

# Demo drill gates on the API task. All three must be true for a real SMS.
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
  description = "False for accounts AWS has not yet verified for CloudFront: the site is then served over HTTP from the ALB."
  type        = bool
  default     = true
}

variable "ses_sender" {
  description = "Sender address for the designed HTML alert email via Amazon SES (main account). Empty: plain-text SNS email."
  type        = string
  default     = ""
}
