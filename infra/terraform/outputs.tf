output "sns_topic_arn" { value = aws_sns_topic.alerts.arn }
output "archive_bucket" { value = aws_s3_bucket.archive.bucket }
output "ecr_repository" { value = aws_ecr_repository.ingest.repository_url }
output "lambda_name" { value = aws_lambda_function.ingest.function_name }
output "api_policy_arn" { value = aws_iam_policy.api.arn }

output "backend_env" {
  description = "Paste into backend/.env to point the API at this deployment"
  value       = <<-EOT
    AWS_REGION=${var.region}
    SNS_TOPIC_ARN=${aws_sns_topic.alerts.arn}
    ARCHIVE_BUCKET=${aws_s3_bucket.archive.bucket}
    SIM_LIVE_SMS=true
    ALERT_DRY_RUN=false
  EOT
}
