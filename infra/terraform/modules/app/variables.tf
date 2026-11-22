variable "name" {
  description = "Resource name prefix, e.g. tabfree-prod."
  type        = string
}

variable "image" {
  description = "The API image (infra/docker/api), by digest in production."
  type        = string
}

variable "base_url" {
  description = "The public URL, e.g. https://tabfree.example.com."
  type        = string
}

variable "site_id" {
  description = "The default site."
  type        = string
  default     = "default"
}

variable "site_hosts" {
  description = "Host name to site id, for several sites on one service (P6.9)."
  type        = map(string)
  default     = {}
}

variable "certificate_arn" {
  description = "ACM certificate for the load balancer."
  type        = string
}

variable "allowed_cidrs" {
  description = "Who may reach the load balancer (default: everyone; narrow it for an intranet-only site)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "vpc_id" {
  type        = string
  description = "VPC."
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "Subnets for the load balancer (one per AZ)."
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Subnets for the tasks (one per AZ)."
}

variable "app_security_group_id" {
  type        = string
  description = "The application security group from the compute module."
}

variable "cluster_arn" {
  type        = string
  description = "ECS cluster ARN."
}

variable "cluster_name" {
  type        = string
  description = "ECS cluster name."
}

variable "log_group_name" {
  type        = string
  description = "CloudWatch log group for the tasks."
}

variable "log_retention_days" {
  type        = number
  description = "Days to keep WAF logs."
  default     = 90
}

variable "kms_key_arn" {
  type        = string
  description = "The KMS key from the security module."
}

variable "db_host" {
  type        = string
  description = "Aurora writer endpoint."
}

variable "db_port" {
  type        = number
  description = "Aurora port."
  default     = 5432
}

variable "db_name" {
  type        = string
  description = "Database name."
  default     = "tabfree"
}

variable "db_secret_arn" {
  type        = string
  description = "The Aurora-managed master user secret (username and password keys)."
}

variable "app_secret_arns" {
  type        = map(string)
  description = "Signing keys from the security module (link-signing-key, oauth-signing-key, frame-signing-key)."
}

variable "extra_environment" {
  type        = map(string)
  description = "More TABFREE_* settings (never secrets)."
  default     = {}
}

variable "extra_secrets" {
  type        = map(string)
  description = "More settings from Secrets Manager, e.g. TABFREE_OIDC_CLIENT_SECRET = <secret ARN>."
  default     = {}
}

variable "athena_workgroup_name" {
  type        = string
  description = "The site's Athena workgroup."
}

variable "athena_workgroup_arn" {
  type        = string
  description = "Its ARN."
}

variable "read_bucket_arns" {
  type        = list(string)
  description = "Buckets the service reads (the lake, Athena results)."
}

variable "write_bucket_arns" {
  type        = list(string)
  description = "Buckets the service writes (Athena results, refresh output, the usage lake)."
}

variable "usage_lake" {
  type        = string
  description = "s3:// location of the usage export (P6.7)."
}

variable "ses_sender" {
  type        = string
  description = "A verified SES sender (null: mail is logged, not sent)."
  default     = null
}

variable "enable_redshift" {
  type        = bool
  description = "Allow the Redshift Data API."
  default     = false
}

variable "enable_bedrock" {
  type        = bool
  description = "Allow Claude on Amazon Bedrock (Edit with Claude, the migration repair agent)."
  default     = false
}

variable "api_cpu" {
  type        = number
  description = "API task CPU units."
  default     = 1024
}

variable "api_memory" {
  type        = number
  description = "API task memory (MiB)."
  default     = 2048
}

variable "api_min_count" {
  type        = number
  description = "Fewest API tasks (at least 2 for HA: ECS spreads them across AZs)."
  default     = 2
}

variable "api_max_count" {
  type        = number
  description = "Most API tasks under load."
  default     = 10
}

variable "worker_cpu" {
  type        = number
  description = "Worker task CPU units (subscriptions render with Chromium)."
  default     = 1024
}

variable "worker_memory" {
  type        = number
  description = "Worker task memory (MiB)."
  default     = 4096
}

variable "worker_count" {
  type        = number
  description = "Worker tasks (jobs are claimed safely by several)."
  default     = 2
}

variable "cpu_architecture" {
  type        = string
  description = "X86_64 or ARM64."
  default     = "X86_64"
}

variable "rate_limit_per_5_minutes" {
  type        = number
  description = "WAF: requests per IP in 5 minutes before blocking."
  default     = 3000
}

variable "deletion_protection" {
  type        = bool
  description = "Protect the load balancer from deletion."
  default     = true
}
