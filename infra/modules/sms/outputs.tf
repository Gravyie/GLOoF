output "alert_topic_arn" { value = aws_sns_topic.glof_alerts.arn }
output "sms_account_id" { value = data.aws_caller_identity.sms.account_id }
