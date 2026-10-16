output "raw_bucket" {
  value = aws_s3_bucket.raw.bucket
}

output "api_url" {
  value = aws_apigatewayv2_api.read.api_endpoint
}

output "api_key_parameter" {
  description = "Read the key with: aws ssm get-parameter --with-decryption --name <this>"
  value       = aws_ssm_parameter.instructor_api_key.name
}

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "ingest_dlq_arn" {
  value = aws_sqs_queue.ingest_dlq.arn
}

output "evaluate_queue_url" {
  value = aws_sqs_queue.evaluate.url
}

output "evaluate_dlq_arn" {
  value = aws_sqs_queue.evaluate_dlq.arn
}

output "account_id" {
  value = local.account_id
}
