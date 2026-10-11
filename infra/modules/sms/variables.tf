variable "topic_name" {
  type    = string
  default = "glof-alerts"
}

variable "publisher_role_arns" {
  description = "Main-account IAM role ARNs allowed to sns:Publish (ingest Lambda, ECS task, alarm relay)"
  type        = list(string)
}

variable "demo_sms_numbers" {
  description = "E.164 numbers already verified in the SMS sandbox (us-east-1)"
  type        = list(string)
  sensitive   = true
  default     = []
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

variable "enable_filter_policies" {
  description = "Phones receive only audience=public (drills, public alerts); alarms go to email only"
  type        = bool
  default     = true
}

variable "enable_sms_delivery_logs" {
  type    = bool
  default = false
}
