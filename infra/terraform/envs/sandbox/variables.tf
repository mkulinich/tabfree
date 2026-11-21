variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "allowed_account_ids" {
  description = "If set, the AWS provider refuses to run against any other account. Recommended once you know the sandbox account ID."
  type        = list(string)
  default     = null
}

variable "name_prefix" {
  description = "Prefix for resource names and ECR repositories."
  type        = string
  default     = "tabfree"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,11}$", var.name_prefix))
    error_message = "name_prefix must be 2-12 characters: lower-case letters, digits and hyphens, starting with a letter (bucket names have a 63-character limit)."
  }
}

variable "environment" {
  description = "Environment name, appended to name_prefix."
  type        = string
  default     = "sandbox"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,9}$", var.environment))
    error_message = "environment must be 2-10 characters: lower-case letters, digits and hyphens, starting with a letter."
  }
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}

# --- Network -----------------------------------------------------------------

variable "vpc_cidr" {
  description = "VPC CIDR block (/16)."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Availability Zones to span (2 or 3). Redshift Serverless is documented as needing 3."
  type        = number
  default     = 2
}

variable "enable_nat_gateway" {
  description = "Create a single NAT gateway for outbound traffic from private subnets (needed by Fargate tasks unless you add VPC interface endpoints). About $33 a month plus data."
  type        = bool
  default     = true
}

# --- Lake --------------------------------------------------------------------

variable "athena_bytes_scanned_cutoff_per_query" {
  description = "Athena cancels queries that scan more than this many bytes (minimum 10485760)."
  type        = number
  default     = 10737418240 # 10 GiB

  validation {
    condition     = var.athena_bytes_scanned_cutoff_per_query >= 10485760
    error_message = "athena_bytes_scanned_cutoff_per_query must be at least 10485760 (10 MB), Athena's minimum."
  }
}

variable "athena_results_retention_days" {
  description = "Days to keep Athena query results."
  type        = number
  default     = 7
}

# --- Metadata DB --------------------------------------------------------------

variable "aurora_min_capacity" {
  description = "Aurora Serverless v2 minimum ACUs. 0.5 is always on (~$44/month); 0 pauses after an hour idle."
  type        = number
  default     = 0.5
}

variable "aurora_max_capacity" {
  description = "Aurora Serverless v2 maximum ACUs."
  type        = number
  default     = 2
}

variable "aurora_deletion_protection" {
  description = "Protect the metadata DB from deletion. Set false and apply before destroying the sandbox."
  type        = bool
  default     = true
}

variable "aurora_skip_final_snapshot" {
  description = "Skip the final snapshot when the cluster is destroyed."
  type        = bool
  default     = false
}

# --- Redshift ------------------------------------------------------------------

variable "enable_redshift" {
  description = "Create a Redshift Serverless namespace and workgroup. No charge while idle; $0.375 per RPU-hour while queries run."
  type        = bool
  default     = false
}

variable "redshift_base_capacity" {
  description = "Redshift Serverless base capacity in RPUs (4 is the minimum)."
  type        = number
  default     = 4
}

# --- Compute and logs ---------------------------------------------------------

variable "ecr_image_tag_mutability" {
  description = "IMMUTABLE (tag images with the git SHA) or MUTABLE."
  type        = string
  default     = "IMMUTABLE"
}

variable "container_insights" {
  description = "ECS Container Insights: disabled, enabled or enhanced."
  type        = string
  default     = "disabled"
}

variable "log_retention_days" {
  description = "Retention for ECS and database log groups, in days."
  type        = number
  default     = 30
}

variable "force_destroy" {
  description = "Allow destroy to delete non-empty S3 buckets, ECR repositories with images, and the Athena workgroup's saved queries."
  type        = bool
  default     = false
}

# --- Extract converter (P2.8) ------------------------------------------------

variable "enable_extract_converter" {
  description = "Create the converter task definition and IAM roles (no cost until tasks run)."
  type        = bool
  default     = true
}

variable "converter_image_tag" {
  description = "Tag of the converter image in the converter ECR repository (use the git SHA)."
  type        = string
  default     = "unset"
}

variable "converter_cpu" {
  description = "Converter task vCPU units (1024 = 1 vCPU)."
  type        = number
  default     = 4096
}

variable "converter_memory" {
  description = "Converter task memory in MiB."
  type        = number
  default     = 16384
}

variable "converter_workers" {
  description = "Parallel conversions inside one converter task."
  type        = number
  default     = 2
}
