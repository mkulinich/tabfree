variable "name" {
  description = "Resource name prefix."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for the backup vault."
  type        = string
}

variable "resource_arns" {
  description = "What to back up: the Aurora cluster, and S3 buckets (they need versioning)."
  type        = list(string)
}

variable "retention_days" {
  description = "Days to keep daily backups."
  type        = number
  default     = 35
}

variable "monthly_retention_days" {
  description = "Days to keep monthly backups (moved to cold storage after 30 days; at least 120)."
  type        = number
  default     = 365
}

variable "dr_vault_arn" {
  description = "A backup vault in the DR region to copy every backup to (null: no copies)."
  type        = string
  default     = null
}

variable "force_destroy" {
  description = "Allow deleting the vault with recovery points in it (sandbox only)."
  type        = bool
  default     = false
}
