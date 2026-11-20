variable "name" {
  description = "Name prefix, e.g. \"tabfree-sandbox\"."
  type        = string
}

variable "vpc_id" {
  description = "VPC for the database security group."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets in at least two Availability Zones."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Aurora needs subnets in at least two Availability Zones."
  }
}

variable "app_security_group_id" {
  description = "The only security group allowed to connect to the database."
  type        = string
}

variable "engine_major_version" {
  description = "Aurora PostgreSQL major version. The newest available minor version is chosen at creation."
  type        = string
  default     = "17"
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "tabfree"
}

variable "master_username" {
  description = "Master user name. Its password is generated and kept in Secrets Manager by RDS."
  type        = string
  default     = "tabfree_admin"
}

variable "min_capacity" {
  description = "Minimum Aurora capacity units (ACUs). 0 lets the cluster pause when idle (resuming takes roughly 15 seconds or more); 0.5 keeps it always on (about $44 a month at $0.12 per ACU-hour)."
  type        = number
  default     = 0.5

  validation {
    condition     = var.min_capacity >= 0 && var.min_capacity <= 256
    error_message = "min_capacity must be between 0 and 256 ACUs."
  }
}

variable "max_capacity" {
  description = "Maximum Aurora capacity units (ACUs). Caps cost and performance."
  type        = number
  default     = 2

  validation {
    condition     = var.max_capacity >= 1 && var.max_capacity <= 256
    error_message = "max_capacity must be between 1 and 256 ACUs."
  }
}

variable "seconds_until_auto_pause" {
  description = "Idle seconds before the cluster pauses. Used only when min_capacity is 0. AWS allows 300 to 86400."
  type        = number
  default     = 3600

  validation {
    condition     = var.seconds_until_auto_pause >= 300 && var.seconds_until_auto_pause <= 86400
    error_message = "seconds_until_auto_pause must be between 300 and 86400."
  }
}

variable "backup_retention_days" {
  description = "Days of automated backups (point-in-time recovery window)."
  type        = number
  default     = 7
}

variable "log_retention_days" {
  description = "Retention for exported PostgreSQL logs, in days."
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  description = "Optional customer-managed KMS key for storage and the master-password secret. When null, AWS-managed keys are used."
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Block deletion of the cluster. Set to false and apply before running destroy."
  type        = bool
  default     = true
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. When false, a snapshot named <name>-metadata-final is kept (delete it before destroying a recreated cluster again)."
  type        = bool
  default     = false
}

variable "apply_immediately" {
  description = "Apply changes now instead of in the next maintenance window."
  type        = bool
  default     = false
}
