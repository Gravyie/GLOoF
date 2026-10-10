# Glacial Lake Monitor: AWS footprint.
#
#   EventBridge (daily) -> Lambda "ingest" (container) -> Sentinel-2 COGs (AWS Open Data, anonymous)
#                                 |-> S3 archive bucket (outlines + notified-level state)
#                                 '-> SNS topic "alerts" -> email / SMS subscriptions, filtered by audience
#   The FastAPI backend reads the same S3 archive and publishes replay alerts to the same topic.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { project = var.name } }
}

data "aws_caller_identity" "me" {}

locals {
  bucket = "${var.name}-archive-${data.aws_caller_identity.me.account_id}"
}

# --------------------------------------------------------------------------- S3
resource "aws_s3_bucket" "archive" {
  bucket        = local.bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "archive" {
  bucket                  = aws_s3_bucket.archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id
  versioning_configuration { status = "Enabled" } # every ingest is a recoverable version
}

# --------------------------------------------------------------------------- SNS
resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

# One topic, many audiences: each subscription only receives the audiences it
# lists, via a filter policy on the "audience" message attribute.
resource "aws_sns_topic_subscription" "subscribers" {
  for_each      = { for s in var.subscribers : "${s.protocol}:${s.endpoint}" => s }
  topic_arn     = aws_sns_topic.alerts.arn
  protocol      = each.value.protocol
  endpoint      = each.value.endpoint
  filter_policy = jsonencode({ audience = each.value.audiences })
}

# --------------------------------------------------------------------------- ECR + Lambda
resource "aws_ecr_repository" "ingest" {
  name                 = "${var.name}-ingest"
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${var.name}-ingest"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "app" {
  statement {
    sid       = "ArchiveReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.archive.arn}/*"]
  }
  statement {
    sid       = "ArchiveList" # lets GetObject on a missing key return 404 instead of 403
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.archive.arn]
  }
  statement {
    sid       = "Notify"
    actions   = ["sns:Publish", "sns:GetTopicAttributes"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.app.json
}

# Same permissions for the FastAPI backend (attach to the user/role that runs it).
resource "aws_iam_policy" "api" {
  name   = "${var.name}-api"
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.name}-ingest"
  role          = aws_iam_role.ingest.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.ingest.repository_url}:${var.image_tag}"
  architectures = [var.lambda_arch]
  memory_size   = 3008
  timeout       = 900

  environment {
    variables = {
      ARCHIVE_BUCKET = aws_s3_bucket.archive.bucket
      SNS_TOPIC_ARN  = aws_sns_topic.alerts.arn
      ALERT_DRY_RUN  = var.dry_run ? "true" : "false"
    }
  }
}

# --------------------------------------------------------------------------- Schedule
resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${var.name}-ingest-daily"
  schedule_expression = var.schedule
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule = aws_cloudwatch_event_rule.daily.name
  arn  = aws_lambda_function.ingest.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}
