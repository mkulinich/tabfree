variable "region" {
  description = "Primary AWS region."
  type        = string
}

variable "dr_region" {
  description = "Disaster-recovery region for backup copies."
  type        = string
}

variable "allowed_account_ids" {
  description = "Refuse to run against any other account."
  type        = list(string)
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "tabfree"
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "prod"
}

variable "domain" {
  description = "The site's host name, e.g. tabfree.example.com."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for the domain (in var.region)."
  type        = string
}

variable "image" {
  description = "The API image, by digest: <account>.dkr.ecr.<region>.amazonaws.com/tabfree/api@sha256:..."
  type        = string
}

variable "ses_sender" {
  description = "Verified SES sender address (null: mail is not sent)."
  type        = string
  default     = null
}

variable "oidc_issuer" {
  description = "The identity provider (OpenID Connect issuer URL)."
  type        = string
}

variable "oidc_client_id" {
  description = "The OIDC client id."
  type        = string
}

variable "oidc_client_secret_arn" {
  description = "Secrets Manager ARN holding the OIDC client secret (created outside OpenTofu)."
  type        = string
}

variable "enable_redshift" {
  description = "Create Redshift Serverless (otherwise Athena only)."
  type        = bool
  default     = false
}

variable "enable_bedrock" {
  description = "Claude on Amazon Bedrock for authoring and migration repair."
  type        = bool
  default     = true
}

variable "api_min_count" {
  description = "Fewest API tasks."
  type        = number
  default     = 2
}

variable "api_max_count" {
  description = "Most API tasks."
  type        = number
  default     = 12
}

variable "tags" {
  description = "Extra tags."
  type        = map(string)
  default     = {}
}
