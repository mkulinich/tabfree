variable "name" {
  description = "Resource name prefix, e.g. tabfree-prod."
  type        = string
}

variable "deletion_window_days" {
  description = "Days before a deleted KMS key is destroyed (7-30)."
  type        = number
  default     = 30
}

variable "secret_recovery_days" {
  description = "Days a deleted secret can be recovered (0 deletes at once; sandbox only)."
  type        = number
  default     = 30
}
