# ---------------------------------------------------------------
# Ingest: S3 events -> standard queue -> λ ingest. Poison messages -> DLQ.
# ---------------------------------------------------------------
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate and replay
  sqs_managed_sse_enabled   = true    # messages hold only bucket/key, no lesson data
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name}-ingest"
  visibility_timeout_seconds = 6 * local.functions.ingest.timeout
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "ingest_dlq" {
  queue_url = aws_sqs_queue.ingest_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingest.arn]
  })
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowRawBucketEvents"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.raw.arn }
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })
}

# ---------------------------------------------------------------
# Evaluate: FIFO, one message group per student, so a student's
# state is rebuilt by one worker at a time (no lost updates).
# ---------------------------------------------------------------
resource "aws_sqs_queue" "evaluate_dlq" {
  name                      = "${local.name}-evaluate-dlq.fifo"
  fifo_queue                = true
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "evaluate" {
  name                       = "${local.name}-evaluate.fifo"
  fifo_queue                 = true
  deduplication_scope        = "messageGroup"
  fifo_throughput_limit      = "perMessageGroupId"
  visibility_timeout_seconds = 6 * local.functions.evaluate.timeout
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.evaluate_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "evaluate_dlq" {
  queue_url = aws_sqs_queue.evaluate_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.evaluate.arn]
  })
}
