variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "name" {
  type    = string
  default = "glof"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "lambda_arch" {
  type    = string
  default = "arm64" # build with --platform linux/arm64 (or set x86_64)
}

variable "schedule" {
  type    = string
  default = "rate(1 day)" # Sentinel-2 revisits every ~5 days; daily catches each scene
}

variable "dry_run" {
  type    = bool
  default = false
}

# Who gets notified. audiences: any of "operators", "officials", "public".
# Email subscriptions must be confirmed from the inbox before SNS delivers.
variable "subscribers" {
  type = list(object({
    protocol  = string # "email" | "sms" | "https"
    endpoint  = string
    audiences = list(string)
  }))
  default = []
}
