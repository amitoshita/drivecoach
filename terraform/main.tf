# DriveCoach infrastructure.
#
#   S3 raw (private, KMS) --ObjectCreated--> SQS ingest --> λ ingest --> DynamoDB lessons
#                                                              |
#                                     SQS FIFO evaluate (group = student) --> λ evaluate
#                                                              --> DynamoDB evaluations (audit) + students
#   API Gateway (HTTP API + authorizer) --> λ api --> DynamoDB (read only)
#
# Each concern lives in its own file: kms, storage, queues, dynamodb, lambda, api, observability.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = var.project_name
  account_id = data.aws_caller_identity.current.account_id
}

# One customer-managed key for data at rest: S3, DynamoDB, SSM, SNS.
resource "aws_kms_key" "data" {
  description             = "${local.name} data at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        # CloudWatch alarms publish to the encrypted SNS topic.
        Sid       = "CloudWatchAlarmsToSns"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
      },
    ]
  })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}
