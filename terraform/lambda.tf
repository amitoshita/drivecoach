# ---------------------------------------------------------------
# Lambda functions. One package, four handlers, four roles.
# The starter kit gave a single role Action="*" on Resource="*";
# every function now gets only what its code calls.
# ---------------------------------------------------------------
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/../build/lambda.zip"
  excludes    = ["drivecoach/__pycache__", "drivecoach/handlers/__pycache__"]
}

locals {
  functions = {
    ingest     = { handler = "drivecoach.handlers.ingest.handler", memory = 1024, timeout = 60 }
    evaluate   = { handler = "drivecoach.handlers.evaluate.handler", memory = 512, timeout = 60 }
    api        = { handler = "drivecoach.handlers.api.handler", memory = 512, timeout = 10 }
    authorizer = { handler = "drivecoach.handlers.authorizer.handler", memory = 256, timeout = 5 }
  }

  common_env = {
    LESSONS_TABLE     = aws_dynamodb_table.lessons.name
    EVALUATIONS_TABLE = aws_dynamodb_table.evaluations.name
    STUDENTS_TABLE    = aws_dynamodb_table.students.name
    RULES_TABLE       = aws_dynamodb_table.rules.name
    METRIC_NAMESPACE  = "DriveCoach"
  }

  function_env = {
    ingest     = { EVALUATE_QUEUE_URL = aws_sqs_queue.evaluate.url, KMS_KEY_ARN = aws_kms_key.data.arn }
    evaluate   = {}
    api        = {}
    authorizer = { API_KEY_PARAM = aws_ssm_parameter.instructor_api_key.name }
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "fn" {
  for_each = local.functions
  name     = "${local.name}-${each.key}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "${local.name}-${each.key}"
  role             = aws_iam_role.fn[each.key].arn
  handler          = each.value.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  memory_size      = each.value.memory
  timeout          = each.value.timeout
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  logging_config {
    log_format = "Text" # our code already writes one JSON object per line
    log_group  = aws_cloudwatch_log_group.fn[each.key].name
  }

  environment {
    variables = merge(local.common_env, local.function_env[each.key], {
      SERVICE_NAME = "${local.name}-${each.key}"
    })
  }

  depends_on = [aws_cloudwatch_log_group.fn, aws_iam_role_policy.fn]
}

# ---------------------------------------------------------------
# Least-privilege policies
# ---------------------------------------------------------------
locals {
  log_statement = { for k, _ in local.functions : k => {
    Effect   = "Allow"
    Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    Resource = "${aws_cloudwatch_log_group.fn[k].arn}:*"
  } }

  kms_read  = { Effect = "Allow", Action = ["kms:Decrypt"], Resource = aws_kms_key.data.arn }
  kms_write = { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = aws_kms_key.data.arn }

  policies = {
    ingest = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.ingest.arn
      },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${aws_s3_bucket.raw.arn}/incoming/*" },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.raw.arn}/quarantine/*" },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.lessons.arn
      },
      # Per-student lesson index (record=LESSONS) used by the evaluator.
      { Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.students.arn },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.evaluate.arn },
      local.kms_write,
    ]
    evaluate = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.evaluate.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:BatchGetItem"]
        Resource = aws_dynamodb_table.lessons.arn
      },
      {
        # PutItem only (with a condition in code): the audit log is append-only.
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.evaluations.arn, aws_dynamodb_table.students.arn]
      },
      { Effect = "Allow", Action = ["dynamodb:Scan"], Resource = aws_dynamodb_table.rules.arn },
      local.kms_write,
    ]
    api = [
      {
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          aws_dynamodb_table.lessons.arn,
          aws_dynamodb_table.evaluations.arn, "${aws_dynamodb_table.evaluations.arn}/index/by_student",
          aws_dynamodb_table.students.arn,
        ]
      },
      { Effect = "Allow", Action = ["dynamodb:Scan"], Resource = aws_dynamodb_table.rules.arn },
      # Needed to sign the short-lived raw-file download links.
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.raw.arn}/incoming/*" },
      local.kms_read,
    ]
    authorizer = [
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = aws_ssm_parameter.instructor_api_key.arn },
      local.kms_read,
    ]
  }
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.functions
  name     = "${local.name}-${each.key}"
  role     = aws_iam_role.fn[each.key].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = concat([local.log_statement[each.key]], local.policies[each.key])
  })
}

# ---------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------
resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.fn["ingest"].arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]
  scaling_config { maximum_concurrency = 20 }
}

resource "aws_lambda_event_source_mapping" "evaluate" {
  event_source_arn        = aws_sqs_queue.evaluate.arn
  function_name           = aws_lambda_function.fn["evaluate"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}
