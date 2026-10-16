output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "lambda_role_arn" {
  value = aws_iam_role.lambda_exec.arn
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}
