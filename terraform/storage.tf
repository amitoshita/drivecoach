# ---------------------------------------------------------------
# Raw lesson files. Private, encrypted, versioned.
# The starter kit made this bucket public "so instructors can download
# from a browser". Instructors now get a 5-minute presigned link from
# GET /lessons/{id} instead.
# ---------------------------------------------------------------
resource "aws_s3_bucket" "raw" {
  bucket_prefix = "${local.name}-raw-"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    object_ownership = "BucketOwnerEnforced" # disables ACLs entirely
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true # fewer KMS calls, lower cost
  }
}

# Raw files are the source of truth: versioning protects against overwrite/delete.
resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket     = aws_s3_bucket.raw.id
  depends_on = [aws_s3_bucket_versioning.raw]

  rule {
    id     = "age-out-raw-lessons"
    status = "Enabled"
    filter { prefix = "incoming/" }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 180
      storage_class = "GLACIER_IR"
    }
    # Overwritten versions may be the recording an audit record refers to: keep them, cheaply.
    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER_IR"
    }
  }

  rule {
    id     = "expire-quarantine"
    status = "Enabled"
    filter { prefix = "quarantine/" }
    expiration { days = 180 }
    noncurrent_version_expiration { noncurrent_days = 7 }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_s3_bucket_policy" "raw" {
  bucket     = aws_s3_bucket.raw.id
  depends_on = [aws_s3_bucket_public_access_block.raw]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "DenyOutdatedTls"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]
        Condition = { NumericLessThan = { "s3:TlsVersion" = "1.2" } }
      },
    ]
  })
}

# New lesson files under incoming/ are queued for ingestion.
# quarantine/ is deliberately not watched (no loops).
resource "aws_s3_bucket_notification" "raw" {
  bucket = aws_s3_bucket.raw.id

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "incoming/"
    filter_suffix = ".csv"
  }

  depends_on = [aws_sqs_queue_policy.ingest]
}
