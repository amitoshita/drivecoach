# ---------------------------------------------------------------
# Tables. On-demand billing: cost follows lesson volume, zero when idle.
# ---------------------------------------------------------------
# One item per lesson_id: header (no PII), metrics, event timeline, S3 location.
resource "aws_dynamodb_table" "lessons" {
  name         = "${local.name}-lessons"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "lesson_id"

  attribute {
    name = "lesson_id"
    type = "S"
  }
  attribute {
    name = "student_id"
    type = "S"
  }
  attribute {
    name = "started_at"
    type = "S"
  }

  global_secondary_index {
    name = "by_student"
    key_schema {
      attribute_name = "student_id"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "started_at"
      key_type       = "RANGE"
    }
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
  point_in_time_recovery { enabled = true }
}

# Append-only audit log: one item per (lesson, evaluation). Never updated.
resource "aws_dynamodb_table" "evaluations" {
  name         = "${local.name}-evaluations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "lesson_id"
  range_key    = "evaluated_at"

  attribute {
    name = "lesson_id"
    type = "S"
  }
  attribute {
    name = "evaluated_at"
    type = "S"
  }
  attribute {
    name = "student_id"
    type = "S"
  }

  global_secondary_index {
    name = "by_student"
    key_schema {
      attribute_name = "student_id"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "evaluated_at"
      key_type       = "RANGE"
    }
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
  point_in_time_recovery { enabled = true }
}

# Per student: record=INITIAL (enrolment snapshot, managed here) and
# record=STATE (computed by λ evaluate). No personal data.
resource "aws_dynamodb_table" "students" {
  name         = "${local.name}-students"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "student_id"
  range_key    = "record"

  attribute {
    name = "student_id"
    type = "S"
  }
  attribute {
    name = "record"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
  point_in_time_recovery { enabled = true }
}

# Versioned rule sets. Publishing a new version is a data change, not a code deploy.
resource "aws_dynamodb_table" "rules" {
  name         = "${local.name}-rules"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "version"

  attribute {
    name = "version"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.data.arn
  }
  point_in_time_recovery { enabled = true }
}

# ---------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------
locals {
  rule_files = fileset("${path.module}/../rules", "rules_*.json")
  rule_sets = {
    for f in local.rule_files :
    jsondecode(file("${path.module}/../rules/${f}")).version => file("${path.module}/../rules/${f}")
  }

  # students.csv holds personal data (name, national ID, DOB, phone, email).
  # Only the operational columns are loaded; personal data never enters AWS.
  students = { for row in csvdecode(file("${path.module}/../sample_data/students.csv")) : row.student_id => {
    program           = row.program
    lessons_completed = tonumber(row.lessons_completed)
    review_count      = tonumber(row.review_count)
    next_lesson_type  = row.next_lesson_type
    status            = row.status
  } }
}

resource "aws_dynamodb_table_item" "rules" {
  for_each   = local.rule_sets
  table_name = aws_dynamodb_table.rules.name
  hash_key   = aws_dynamodb_table.rules.hash_key

  item = jsonencode({
    version        = { S = each.key }
    document       = { S = each.value }
    effective_from = { S = jsondecode(each.value).effective_from }
  })
}

resource "aws_dynamodb_table_item" "student_initial" {
  for_each   = local.students
  table_name = aws_dynamodb_table.students.name
  hash_key   = aws_dynamodb_table.students.hash_key
  range_key  = aws_dynamodb_table.students.range_key

  item = jsonencode({
    student_id = { S = each.key }
    record     = { S = "INITIAL" }
    state = { M = {
      program                  = { S = each.value.program }
      lessons_completed        = { N = tostring(each.value.lessons_completed) }
      review_count             = { N = tostring(each.value.review_count) }
      next_lesson_type         = { S = each.value.next_lesson_type }
      status                   = { S = each.value.status }
      final_score              = { NULL = true }
      passed_regular_count     = { N = "0" }
      passed_regular_score_sum = { N = "0" }
    } }
  })
}
