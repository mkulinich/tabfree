variable "name" {
  description = "Name prefix, e.g. \"tabfree-sandbox\"."
  type        = string
}

variable "vpc_id" {
  description = "VPC for the application security group."
  type        = string
}

variable "ecr_namespace" {
  description = "Prefix for ECR repository names: <namespace>/<repository>."
  type        = string
}

variable "repositories" {
  description = "ECR repositories to create, one per TabFree image."
  type        = list(string)
  default     = ["api", "worker", "author", "snapshot"]
}

variable "image_tag_mutability" {
  description = "IMMUTABLE (recommended: tag images with the git SHA) or MUTABLE."
  type        = string
  default     = "IMMUTABLE"

  validation {
    condition     = contains(["IMMUTABLE", "MUTABLE"], var.image_tag_mutability)
    error_message = "image_tag_mutability must be IMMUTABLE or MUTABLE."
  }
}

variable "untagged_image_expiry_days" {
  description = "Days after which untagged images are deleted."
  type        = number
  default     = 7
}

variable "max_tagged_images" {
  description = "Number of images to keep per repository; older ones are deleted."
  type        = number
  default     = 30
}

variable "container_insights" {
  description = "ECS Container Insights: disabled, enabled or enhanced. Anything but disabled adds CloudWatch charges."
  type        = string
  default     = "disabled"

  validation {
    condition     = contains(["disabled", "enabled", "enhanced"], var.container_insights)
    error_message = "container_insights must be disabled, enabled or enhanced."
  }
}

variable "log_retention_days" {
  description = "Retention for the ECS log group, in days."
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  description = "Optional customer-managed KMS key for ECR and CloudWatch Logs. When null, AWS-managed encryption is used. A key used for logs needs a key policy that allows CloudWatch Logs."
  type        = string
  default     = null
}

variable "force_destroy" {
  description = "Allow deleting ECR repositories that still contain images."
  type        = bool
  default     = false
}
