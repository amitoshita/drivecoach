# ---------------------------------------------------------------
# Alarms -> SNS (-> email if alarm_email is set)
# ---------------------------------------------------------------
resource "aws_sns_topic" "alarms" {
  name              = "${local.name}-alarms"
  kms_master_key_id = aws_kms_key.data.arn
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarm_actions = [aws_sns_topic.alarms.arn]

  # Something we could not process automatically is waiting for a human.
  dlq_alarms = {
    ingest   = aws_sqs_queue.ingest_dlq.name
    evaluate = aws_sqs_queue.evaluate_dlq.name
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each            = local.dlq_alarms
  alarm_name          = "${local.name}-${each.key}-dlq-not-empty"
  alarm_description   = "Messages in the ${each.key} DLQ. Investigate, fix, then `make replay-dlq QUEUE=${each.key}`."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = each.value }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each            = toset(["ingest", "evaluate", "api"])
  alarm_name          = "${local.name}-${each.key}-errors"
  alarm_description   = "Unhandled errors in ${each.key} Lambda"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.fn[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Files the boxes sent that we refused (bad format, conflicting lesson_id...).
resource "aws_cloudwatch_metric_alarm" "rejected_files" {
  alarm_name          = "${local.name}-rejected-files"
  alarm_description   = "Lesson files were quarantined. See quarantine/ prefix and lesson.rejected logs."
  namespace           = "DriveCoach"
  metric_name         = "RejectedFiles"
  dimensions          = { Service = "${local.name}-ingest" }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Backlog: files are arriving faster than we process them, or processing is stuck.
resource "aws_cloudwatch_metric_alarm" "ingest_backlog" {
  alarm_name          = "${local.name}-ingest-backlog"
  alarm_description   = "Oldest lesson file has waited more than 15 minutes"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.ingest.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 900
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "Lessons", region = var.region, stat = "Sum", period = 300
          metrics = [
            ["DriveCoach", "LessonsIngested", "Service", "${local.name}-ingest"],
            [".", "DuplicateUploads", ".", "."],
            [".", "RejectedFiles", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Results", region = var.region, stat = "Sum", period = 300
          metrics = [for r in ["PASS", "REVIEW", "FAIL", "INVALID"] :
          ["DriveCoach", "Evaluations", "Service", "${local.name}-evaluate", "result", r]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "Queues", region = var.region, stat = "Maximum", period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingest.name],
            ["...", aws_sqs_queue.ingest_dlq.name],
            ["...", aws_sqs_queue.evaluate_dlq.name],
          ]
        }
      },
    ]
  })
}
