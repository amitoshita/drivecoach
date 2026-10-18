# One-time setup, run by a human with admin credentials (`make bootstrap`).
# Creates: the $10 budget alert, the Terraform state bucket, and the
# GitHub Actions OIDC role (CI never holds long-lived AWS keys).
# Uses local state on purpose: it creates the remote state bucket.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "drivecoach"
}

variable "github_repo" {
  description = "owner/name of the private GitHub repository allowed to deploy"
  type        = string
}

variable "github_subject_prefix" {
  description = <<-EOT
    OIDC "sub" prefix GitHub sends for this repo. Newer repos use immutable IDs,
    e.g. repo:owner@123/name@456 (see GET /repos/{owner}/{repo}/actions/oidc/customization/sub).
    Empty = classic repo:owner/name.
  EOT
  type        = string
  default     = ""
}

variable "budget_email" {
  description = "Where the budget alert goes"
  type        = string
}

variable "create_budget" {
  description = "Set false if the budget alert was already created by hand in the console"
  type        = bool
  default     = true
}

variable "budget_usd" {
  type    = number
  default = 10
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.project_name }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  name       = var.project_name
  sub_prefix = var.github_subject_prefix != "" ? var.github_subject_prefix : "repo:${var.github_repo}"
}

# ---------------------------------------------------------------
# Budget alert (required before deploying anything)
# ---------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  count        = var.create_budget ? 1 : 0
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}

# ---------------------------------------------------------------
# Terraform remote state
# ---------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket        = "${local.name}-tfstate-${local.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "tfstate" {
  bucket     = aws_s3_bucket.tfstate.id
  depends_on = [aws_s3_bucket_public_access_block.tfstate]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------------
# GitHub Actions -> AWS via OIDC
# ---------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "deploy" {
  name                 = "${local.name}-github-deploy"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        # Only the main branch of this one repository can deploy.
        StringLike = { "token.actions.githubusercontent.com:sub" = [
          "${local.sub_prefix}:ref:refs/heads/main",
          "${local.sub_prefix}:environment:production",
        ] }
      }
    }]
  })
}

# The deployer must create IAM roles for the Lambdas, which is effectively
# privileged. It is limited to resources named with the project prefix.
resource "aws_iam_role_policy" "deploy" {
  name = "${local.name}-deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "State"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
      },
      {
        Sid    = "ProjectResources"
        Effect = "Allow"
        Action = ["s3:*", "sqs:*", "dynamodb:*", "lambda:*", "logs:*", "sns:*", "ssm:*"]
        Resource = [
          "arn:aws:s3:::${local.name}-*",
          "arn:aws:sqs:${var.region}:${local.account_id}:${local.name}-*",
          "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.name}-*",
          "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.name}-*",
          "arn:aws:logs:${var.region}:${local.account_id}:log-group:*${local.name}*",
          "arn:aws:sns:${var.region}:${local.account_id}:${local.name}-*",
          "arn:aws:ssm:${var.region}:${local.account_id}:parameter/${local.name}/*",
        ]
      },
      {
        Sid      = "ProjectRoles"
        Effect   = "Allow"
        Action   = ["iam:*Role*", "iam:PassRole", "iam:TagRole", "iam:UntagRole"]
        Resource = "arn:aws:iam::${local.account_id}:role/${local.name}-*"
      },
      {
        Sid    = "ServicesWithoutResourceScoping"
        Effect = "Allow"
        Action = [
          "kms:CreateKey", "kms:CreateAlias", "kms:DeleteAlias", "kms:Describe*", "kms:List*",
          "kms:Get*", "kms:EnableKeyRotation", "kms:PutKeyPolicy", "kms:ScheduleKeyDeletion",
          "kms:TagResource", "kms:UntagResource", "kms:CreateGrant", "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey*",
          "apigateway:*", "cloudwatch:*", "logs:DescribeLogGroups", "logs:ListTagsForResource",
          # API Gateway access logging
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies",
          "lambda:CreateEventSourceMapping", "lambda:GetEventSourceMapping", "lambda:UpdateEventSourceMapping",
          "lambda:DeleteEventSourceMapping", "lambda:ListEventSourceMappings",
          "sqs:ListQueues", "s3:ListAllMyBuckets", "sts:GetCallerIdentity", "iam:ListRoles", "ssm:DescribeParameters",
        ]
        Resource = "*"
      },
    ]
  })
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
