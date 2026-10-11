# Main account: everything except SNS.
#
#   EventBridge (daily) -> Lambda glof-ingest (container) -> Sentinel-2 COGs (AWS Open Data)
#                                       |-> S3 archive bucket
#                                       '-> cross-account SNS topic (SMS account, us-east-1)
#   CloudFront -> S3 (frontend, OAC)
#              '-> /api/* -> ALB -> ECS Fargate (one task) -> S3 archive, DynamoDB, SNS topic
#   CloudWatch alarms -> EventBridge rule -> Lambda glof-alarm-relay -> SNS topic

terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = ">= 3.6" }
  }
}

data "aws_caller_identity" "me" {}
data "aws_region" "current" {}
data "aws_availability_zones" "available" { state = "available" }

locals {
  account      = data.aws_caller_identity.me.account_id
  region       = data.aws_region.current.region
  bucket       = "${var.name}-archive-${local.account}"
  site         = "${var.name}-site-${local.account}"
  alarm_prefix = "${var.name}-"
}

# ============================================================== storage (existing)
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

# Alert audit log, id counter and the demo-SMS latch (one small table).
resource "aws_dynamodb_table" "alerts" {
  name         = "${var.name}-alerts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
}

# ============================================================== container registries
resource "aws_ecr_repository" "ingest" { # existing
  name                 = "${var.name}-ingest"
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}

resource "aws_ecr_repository" "api" {
  name                 = "${var.name}-api"
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}

# ============================================================== IAM
data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Publish to the one cross-account topic only (no wildcard resource on SNS).
data "aws_iam_policy_document" "publish" {
  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [var.alert_topic_arn]
  }
}

data "aws_iam_policy_document" "archive_rw" {
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
}

data "aws_iam_policy_document" "alerts_table" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.alerts.arn]
  }
}

# -- ingest Lambda (existing role, extended)
resource "aws_iam_role" "ingest" {
  name               = "${var.name}-ingest"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role_policy_attachment" "ingest_logs" {
  role       = aws_iam_role.ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "ingest_s3" {
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.archive_rw.json
}

resource "aws_iam_role_policy" "ingest_sns" {
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.publish.json
}

# -- alarm relay Lambda
resource "aws_iam_role" "relay" {
  name               = "${var.name}-alarm-relay"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role_policy_attachment" "relay_logs" {
  role       = aws_iam_role.relay.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "relay_sns" {
  role   = aws_iam_role.relay.id
  policy = data.aws_iam_policy_document.publish.json
}

# -- ECS: execution role (pull image, logs, read the one secret) and task role (app)
resource "aws_iam_role" "ecs_execution" {
  name               = "${var.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secret" {
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.api_key.arn }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name               = "${var.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

resource "aws_iam_role_policy" "task_s3" {
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.archive_rw.json
}

resource "aws_iam_role_policy" "task_sns" {
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.publish.json
}

resource "aws_iam_role_policy" "task_dynamodb" {
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.alerts_table.json
}

# ============================================================== Lambdas + schedule
resource "aws_lambda_function" "ingest" { # existing
  function_name = "${var.name}-ingest"
  role          = aws_iam_role.ingest.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.ingest.repository_url}:${var.image_tag}"
  architectures = [var.arch == "arm64" ? "arm64" : "x86_64"]
  memory_size   = 3008
  timeout       = 900

  environment {
    variables = {
      ARCHIVE_BUCKET     = aws_s3_bucket.archive.bucket
      SNS_TOPIC_ARN      = var.alert_topic_arn # region (us-east-1) is parsed from the ARN by the app
      ALERT_DRY_RUN      = var.lambda_live_alerts ? "false" : "true"
      LAMBDA_LIVE_ALERTS = var.lambda_live_alerts ? "true" : "false"
    }
  }
}

resource "aws_cloudwatch_event_rule" "daily" { # existing
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

# Same image, different handler: relays alarm state changes to the topic. Used
# because the alarms live in this region and the topic in us-east-1.
resource "aws_lambda_function" "relay" {
  function_name = "${var.name}-alarm-relay"
  role          = aws_iam_role.relay.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.ingest.repository_url}:${var.image_tag}"
  architectures = [var.arch == "arm64" ? "arm64" : "x86_64"]
  memory_size   = 256
  timeout       = 30
  image_config { command = ["src.lambda_alarm_relay.handler"] }
  environment {
    variables = { SNS_TOPIC_ARN = var.alert_topic_arn }
  }
}

resource "aws_cloudwatch_event_rule" "alarms" {
  name = "${var.name}-alarm-state"
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = [{ prefix = local.alarm_prefix }]
      state     = { value = ["ALARM"] }
    }
  })
}

resource "aws_cloudwatch_event_target" "relay" {
  rule = aws_cloudwatch_event_rule.alarms.name
  arn  = aws_lambda_function.relay.arn
}

resource "aws_lambda_permission" "relay" {
  statement_id  = "AllowAlarmEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.relay.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alarms.arn
}

# ============================================================== secret
resource "random_password" "api_key" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "api_key" {
  name                    = "${var.name}/alert-api-key"
  recovery_window_in_days = 0 # demo stack: allow immediate re-create after destroy
}

resource "aws_secretsmanager_secret_version" "api_key" {
  secret_id     = aws_secretsmanager_secret.api_key.id
  secret_string = random_password.api_key.result
}

# ============================================================== network
resource "aws_vpc" "this" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
}

# Public subnets only: the task needs outbound internet (STAC, Sentinel-2 COGs) and a
# NAT gateway would cost more than everything else here. Inbound is locked to the ALB.
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "HTTP from CloudFront only"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "task" {
  name        = "${var.name}-api-task"
  description = "API task: inbound from the ALB only"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ============================================================== ALB + ECS
resource "aws_lb" "api" {
  name               = "${var.name}-api"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  idle_timeout       = 120 # live telemetry reads satellite data and can take a while
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id
  health_check {
    path                = "/api/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
  }
  deregistration_delay = 10
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_cluster" "this" {
  name = var.name
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.name}-api"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.arch == "arm64" ? "ARM64" : "X86_64"
  }
  container_definitions = jsonencode([{
    name         = "api"
    image        = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "AWS_REGION", value = local.region },
      { name = "ARCHIVE_BUCKET", value = aws_s3_bucket.archive.bucket },
      { name = "ALERTS_TABLE", value = aws_dynamodb_table.alerts.name },
      { name = "SNS_TOPIC_ARN", value = var.alert_topic_arn }, # region parsed from the ARN
      # the three gates for a real drill SMS; all must be set
      { name = "ALERT_DRY_RUN", value = var.alert_dry_run ? "true" : "false" },
      { name = "SIM_LIVE_SMS", value = var.sim_live_sms ? "true" : "false" },
      { name = "DEMO_SMS_LIVE", value = var.demo_sms_live ? "true" : "false" },
    ]
    secrets = [{ name = "ALERT_API_KEY", valueFrom = aws_secretsmanager_secret.api_key.arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

# Exactly one task, no autoscaling: the backend owns the replay clock in a
# background thread. Two tasks would mean two independent clocks and duplicate
# alerts. The deployment settings also stop a rolling update from briefly
# running an old and a new task side by side.
resource "aws_ecs_service" "api" {
  name                               = "${var.name}-api"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  health_check_grace_period_seconds  = 60
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.http]
}

# ============================================================== frontend hosting
resource "aws_s3_bucket" "site" {
  bucket        = local.site
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.name}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" { name = "Managed-CachingOptimized" }
data "aws_cloudfront_cache_policy" "disabled" { name = "Managed-CachingDisabled" }
data "aws_cloudfront_origin_request_policy" "all_but_host" { name = "Managed-AllViewerExceptHostHeader" }

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # includes India edge locations
  comment             = "${var.name} frontend + /api"

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id   = "api"
    domain_name = aws_lb.api.dns_name
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
  }

  # One domain for site and API: no CORS. X-API-Key passes through to the app.
  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "api"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_but_host.id
    compress                 = true
  }

  # single-page app: unknown paths serve index.html
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "CloudFrontRead"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn } }
    }]
  })
}

# ============================================================== alarms -> relay -> topic
resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${local.alarm_prefix}ingest-errors"
  alarm_description   = "The daily ingest Lambda failed."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}

# The most important alarm: a monitor that silently stops is the worst outcome.
resource "aws_cloudwatch_metric_alarm" "no_ingest_48h" {
  alarm_name          = "${local.alarm_prefix}no-successful-ingest-48h"
  alarm_description   = "No successful satellite ingest in 48 hours."
  namespace           = "GLOF"
  metric_name         = "IngestSuccess"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy" {
  alarm_name          = "${local.alarm_prefix}api-unhealthy"
  alarm_description   = "No healthy API task behind the load balancer."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { TargetGroup = aws_lb_target_group.api.arn_suffix, LoadBalancer = aws_lb.api.arn_suffix }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
}
