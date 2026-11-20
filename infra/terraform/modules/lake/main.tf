# Data lake: S3 buckets for raw downloads, Iceberg tables and Athena results;
# a Glue Data Catalog database; and an Athena engine v3 workgroup.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # S3 bucket names are global, so suffix them with account and region.
  buckets = {
    raw    = "${var.name}-raw-${local.account_id}-${local.region}"
    lake   = "${var.name}-lake-${local.account_id}-${local.region}"
    athena = "${var.name}-athena-${local.account_id}-${local.region}"
  }

  glue_database_name = replace(var.name, "-", "_")
  use_kms            = var.kms_key_arn != null
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = var.force_destroy

  tags = { Purpose = each.key }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].bucket

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = local.buckets

  bucket                  = aws_s3_bucket.this[each.key].bucket
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].bucket

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.use_kms ? "aws:kms" : "AES256"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = local.use_kms
  }
}

# Deny any request that does not use TLS.
data "aws_iam_policy_document" "tls_only" {
  for_each = local.buckets

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this[each.key].arn,
      "${aws_s3_bucket.this[each.key].arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each = local.buckets

  bucket = aws_s3_bucket.this[each.key].bucket
  policy = data.aws_iam_policy_document.tls_only[each.key].json

  depends_on = [aws_s3_bucket_public_access_block.this]
}

# Raw zone: keep history of downloaded .twbx/.tdsx/.hyper files (ground truth).
resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.this["raw"].bucket

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.this["raw"].bucket

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.raw_noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.raw]
}

# Lake zone: Iceberg keeps its own snapshots, so no bucket versioning; just
# clean up abandoned multipart uploads.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.this["lake"].bucket

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Athena results are disposable: expire them.
resource "aws_s3_bucket_lifecycle_configuration" "athena" {
  bucket = aws_s3_bucket.this["athena"].bucket

  rule {
    id     = "expire-query-results"
    status = "Enabled"

    filter {}

    expiration {
      days = var.athena_results_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_glue_catalog_database" "this" {
  name         = local.glue_database_name
  description  = "TabFree Iceberg tables (${var.name})."
  location_uri = "s3://${aws_s3_bucket.this["lake"].bucket}/warehouse/${local.glue_database_name}/"
}

resource "aws_athena_workgroup" "this" {
  name          = var.name
  description   = "TabFree queries (${var.name}). Settings are enforced on every query."
  state         = "ENABLED"
  force_destroy = var.force_destroy

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location       = "s3://${aws_s3_bucket.this["athena"].bucket}/results/"
      expected_bucket_owner = local.account_id

      encryption_configuration {
        encryption_option = local.use_kms ? "SSE_KMS" : "SSE_S3"
        kms_key_arn       = var.kms_key_arn
      }
    }
  }
}
