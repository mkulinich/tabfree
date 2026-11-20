variable "name" {
  description = "Name prefix, e.g. \"tabfree-sandbox\". Also used, with hyphens turned into underscores, as the Glue database name and the Athena workgroup name."
  type        = string
}

variable "kms_key_arn" {
  description = "Optional customer-managed KMS key for bucket and Athena result encryption. When null, SSE-S3 (AES-256) is used."
  type        = string
  default     = null
}

variable "force_destroy" {
  description = "Allow destroying non-empty buckets and the workgroup with its saved queries. Keep false unless you are tearing down a sandbox."
  type        = bool
  default     = false
}

variable "bytes_scanned_cutoff_per_query" {
  description = "Athena cancels any query in the workgroup that scans more than this many bytes. Minimum 10 MB (10485760)."
  type        = number
  default     = 10737418240 # 10 GiB, about $0.05 per query at $5/TB

  validation {
    condition     = var.bytes_scanned_cutoff_per_query >= 10485760
    error_message = "bytes_scanned_cutoff_per_query must be at least 10485760 (10 MB), Athena's minimum."
  }
}

variable "athena_results_retention_days" {
  description = "Days to keep Athena query results before S3 deletes them."
  type        = number
  default     = 7

  validation {
    condition     = var.athena_results_retention_days >= 1
    error_message = "athena_results_retention_days must be at least 1."
  }
}

variable "raw_noncurrent_version_expiration_days" {
  description = "Days to keep superseded object versions in the raw bucket."
  type        = number
  default     = 90

  validation {
    condition     = var.raw_noncurrent_version_expiration_days >= 1
    error_message = "raw_noncurrent_version_expiration_days must be at least 1."
  }
}
