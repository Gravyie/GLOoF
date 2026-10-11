variable "name" {
  type    = string
  default = "glof"
}

variable "alert_topic_arn" {
  description = "ARN of the SNS topic in the SMS account (us-east-1)"
  type        = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "arch" {
  description = "arm64 or x86_64; images are built for this platform"
  type        = string
  default     = "arm64"
}

variable "schedule" {
  type    = string
  default = "rate(1 day)" # Sentinel-2 revisits every ~5 days; daily catches each scene
}

variable "lambda_live_alerts" {
  description = "Let the daily ingest Lambda notify for real. Off: a deploy can never text anyone on its own."
  type        = bool
  default     = false
}

# The three gates for the API's demo drill. All three must be set for a real SMS.
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
