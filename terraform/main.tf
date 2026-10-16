# DriveCoach starter infrastructure
# Written by the previous engineer before they left. It was used for a quick demo.
# Extend it, change it, or replace it as you see fit.

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------
# Raw lesson files uploaded by the in-car boxes
# ---------------------------------------------------------------
resource "aws_s3_bucket" "raw" {
  bucket_prefix = "${var.project_name}-raw-"
  force_destroy = true
}

# Opened up so instructors can download raw files straight from a browser.
# Makes testing much easier.
resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "raw_read" {
  bucket     = aws_s3_bucket.raw.id
  depends_on = [aws_s3_bucket_public_access_block.raw]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "InstructorDownload"
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:GetObject"]
      Resource  = "${aws_s3_bucket.raw.arn}/*"
    }]
  })
}

# ---------------------------------------------------------------
# Execution role for the processing Lambda
# ---------------------------------------------------------------
resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# TODO: tighten before production. Wide open for now so nothing blocks the demo.
resource "aws_iam_role_policy" "lambda_all" {
  name = "${var.project_name}-lambda-all"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "*"
      Resource = "*"
    }]
  })
}

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
resource "aws_ssm_parameter" "instructor_api_key" {
  name  = "/${var.project_name}/instructor_api_key"
  type  = "String"
  value = var.instructor_api_key
}
