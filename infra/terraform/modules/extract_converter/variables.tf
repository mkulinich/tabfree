variable "name" {
  description = "Name prefix, e.g. \"tabfree-sandbox\"."
  type        = string
}

variable "image" {
  description = "Converter image URI with an immutable tag (built from infra/docker/converter/Dockerfile)."
  type        = string
}

variable "raw_bucket_arn" {
  description = "Raw-zone bucket holding the content-addressed store written by `tabfree-migrate download`."
  type        = string
}

variable "raw_read_prefixes" {
  description = "Prefixes in the raw bucket the converter may read (no leading slash)."
  type        = list(string)
  default     = ["blobs/", "downloads/"]
}

variable "lake_bucket_arn" {
  description = "Lake bucket (Parquet staging and Iceberg warehouse)."
  type        = string
}

variable "output_prefix" {
  description = "Prefix in the lake bucket for Parquet, reports and manifests (no leading slash)."
  type        = string
  default     = "staging"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9_/-]*$", var.output_prefix)) && !startswith(var.output_prefix, "warehouse")
    error_message = "output_prefix must be a relative prefix outside warehouse/."
  }
}

variable "athena_results_bucket_arn" {
  description = "Athena query results bucket."
  type        = string
}

variable "athena_results_prefix" {
  description = "Prefix of the workgroup's result location in the results bucket."
  type        = string
  default     = "results/"
}

variable "glue_database_name" {
  description = "The one Glue database the converter may create tables in."
  type        = string
}

variable "athena_workgroup_name" {
  description = "Athena workgroup the converter runs DDL and parity queries in."
  type        = string
}

variable "athena_workgroup_arn" {
  description = "ARN of that workgroup."
  type        = string
}

variable "store_location" {
  description = "Default --store for convert-batch, e.g. s3://<raw-bucket>."
  type        = string
}

variable "output_location" {
  description = "Default --out for convert-batch, e.g. s3://<lake-bucket>/staging (must match output_prefix)."
  type        = string
}

variable "iceberg_root" {
  description = "Default --iceberg-root, normally the Glue database location (s3://<lake-bucket>/warehouse/<db>)."
  type        = string
}

variable "ecr_repository_arn" {
  description = "ECR repository the image is pulled from."
  type        = string
}

variable "log_group_name" {
  description = "CloudWatch log group for the task."
  type        = string
}

variable "log_group_arn" {
  description = "ARN of that log group (without a trailing :*)."
  type        = string
}

variable "cpu" {
  description = "Task vCPU units (1024 = 1 vCPU). Each worker runs one hyperd."
  type        = number
  default     = 4096
}

variable "memory" {
  description = "Task memory in MiB; must be valid for the chosen cpu on Fargate."
  type        = number
  default     = 16384
}

variable "workers" {
  description = "convert-batch --workers inside one task."
  type        = number
  default     = 2
}

variable "ephemeral_storage_gib" {
  description = "Task ephemeral storage (21-200 GiB): holds the staged extract and its Parquet."
  type        = number
  default     = 200

  validation {
    condition     = var.ephemeral_storage_gib >= 21 && var.ephemeral_storage_gib <= 200
    error_message = "Fargate ephemeral storage must be between 21 and 200 GiB."
  }
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key used by the buckets, if any."
  type        = string
  default     = null
}
