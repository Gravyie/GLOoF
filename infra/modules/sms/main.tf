# SMS account: the alert topic and its subscriptions, nothing else.
#
# This module is applied with the SMS account's provider (us-east-1, where the SMS
# sandbox numbers are verified). That account is restricted to SNS resources only:
# never create compute, storage, or IAM here. The single exception is the optional
# delivery-status logging role, created only when enable_sms_delivery_logs = true.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

data "aws_caller_identity" "sms" {}

resource "aws_sns_topic" "glof_alerts" {
  name = var.topic_name
}

# Owner keeps its default rights; the only outside principals are the main
# account's publisher roles, and they may only Publish.
data "aws_iam_policy_document" "topic" {
  statement {
    sid = "OwnerDefault"
    actions = ["SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:AddPermission", "SNS:RemovePermission",
    "SNS:DeleteTopic", "SNS:Subscribe", "SNS:ListSubscriptionsByTopic", "SNS:Publish"]
    resources = [aws_sns_topic.glof_alerts.arn]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceOwner"
      values   = [data.aws_caller_identity.sms.account_id]
    }
  }
  statement {
    sid       = "MainAccountPublishers"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.glof_alerts.arn]
    principals {
      type        = "AWS"
      identifiers = var.publisher_role_arns
    }
  }
}

resource "aws_sns_topic_policy" "glof_alerts" {
  arn    = aws_sns_topic.glof_alerts.arn
  policy = data.aws_iam_policy_document.topic.json
}

# Filter policies keep phones for public alerts and drills only (audience=public), so
# monitoring alarms (audience=operators) go to email instead of spending the SMS budget.
# Every message the app, the Lambdas and the relay publish carries an audience
# attribute; a message WITHOUT one would be dropped for these subscriptions.
resource "aws_sns_topic_subscription" "sms" {
  for_each      = toset(nonsensitive(var.demo_sms_numbers))
  topic_arn     = aws_sns_topic.glof_alerts.arn
  protocol      = "sms"
  endpoint      = each.value
  filter_policy = var.enable_filter_policies ? jsonencode({ audience = ["public"] }) : null
}

# Each email subscription stays PendingConfirmation until its link is clicked.
# Terraform cannot delete an unconfirmed subscription; SNS expires it after 3 days.
resource "aws_sns_topic_subscription" "email" {
  for_each      = toset(nonsensitive(var.alert_emails))
  topic_arn     = aws_sns_topic.glof_alerts.arn
  protocol      = "email"
  endpoint      = each.value
  # no filter: email receives every audience, alarms included. (Attributes cannot be
  # set on a subscription that is still pending confirmation anyway.)
}

# Optional: SNS needs an IAM role to write SMS delivery status to CloudWatch Logs.
data "aws_iam_policy_document" "sns_assume" {
  count = var.enable_sms_delivery_logs ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sms_delivery_logs" {
  count              = var.enable_sms_delivery_logs ? 1 : 0
  name               = "${var.topic_name}-sms-delivery-logs"
  assume_role_policy = data.aws_iam_policy_document.sns_assume[0].json
}

resource "aws_iam_role_policy" "sms_delivery_logs" {
  count = var.enable_sms_delivery_logs ? 1 : 0
  role  = aws_iam_role.sms_delivery_logs[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutMetricFilter", "logs:PutRetentionPolicy"]
      Resource = "*"
    }]
  })
}

resource "aws_sns_sms_preferences" "this" {
  default_sms_type                      = "Transactional"
  monthly_spend_limit                   = var.sms_spend_limit
  delivery_status_iam_role_arn          = var.enable_sms_delivery_logs ? aws_iam_role.sms_delivery_logs[0].arn : null
  delivery_status_success_sampling_rate = var.enable_sms_delivery_logs ? "100" : null
}
