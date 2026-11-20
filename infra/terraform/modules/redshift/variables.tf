variable "name" {
  description = "Name prefix, e.g. \"tabfree-sandbox\"."
  type        = string
}

variable "vpc_id" {
  description = "VPC for the Redshift security group."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the workgroup."
  type        = list(string)
}

variable "app_security_group_id" {
  description = "The only security group allowed to connect to Redshift."
  type        = string
}

variable "base_capacity" {
  description = "Base capacity in Redshift Processing Units (RPUs). 4 is the minimum (since mid-2025 in supported regions, including us-east-1): $1.50 per active hour at $0.375 per RPU-hour, nothing when idle."
  type        = number
  default     = 4

  validation {
    condition     = var.base_capacity == 4 || (var.base_capacity >= 8 && var.base_capacity <= 1024 && var.base_capacity % 8 == 0)
    error_message = "base_capacity must be 4, or a multiple of 8 between 8 and 1024 RPUs."
  }
}

variable "database_name" {
  description = "Initial database name."
  type        = string
  default     = "tabfree"
}

variable "admin_username" {
  description = "Admin user name. Its password is generated and kept in Secrets Manager by Redshift."
  type        = string
  default     = "tabfree_admin"
}

variable "kms_key_arn" {
  description = "Optional customer-managed KMS key. When null, an AWS-owned key is used."
  type        = string
  default     = null
}
