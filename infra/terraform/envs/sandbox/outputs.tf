output "vpc_id" {
  description = "VPC ID."
  value       = module.network.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs."
  value       = module.network.private_subnet_ids
}

output "public_subnet_ids" {
  description = "Public subnet IDs."
  value       = module.network.public_subnet_ids
}

output "raw_bucket" {
  description = "Raw-zone bucket."
  value       = module.lake.raw_bucket
}

output "lake_bucket" {
  description = "Iceberg lake bucket."
  value       = module.lake.lake_bucket
}

output "athena_results_bucket" {
  description = "Athena results bucket."
  value       = module.lake.athena_results_bucket
}

output "glue_database_name" {
  description = "Glue database for TabFree Iceberg tables."
  value       = module.lake.glue_database_name
}

output "athena_workgroup_name" {
  description = "Athena workgroup."
  value       = module.lake.athena_workgroup_name
}

output "metadata_db_endpoint" {
  description = "Aurora PostgreSQL writer endpoint."
  value       = module.metadata_db.endpoint
}

output "metadata_db_name" {
  description = "Metadata database name."
  value       = module.metadata_db.database_name
}

output "metadata_db_secret_arn" {
  description = "Secrets Manager secret with the Aurora master credentials."
  value       = module.metadata_db.master_user_secret_arn
}

output "ecs_cluster_name" {
  description = "ECS cluster."
  value       = module.compute.cluster_name
}

output "ecr_repository_urls" {
  description = "ECR repository URLs by image."
  value       = module.compute.ecr_repository_urls
}

output "app_security_group_id" {
  description = "Security group for TabFree tasks."
  value       = module.compute.app_security_group_id
}

output "ecs_log_group_name" {
  description = "CloudWatch log group for ECS tasks."
  value       = module.compute.log_group_name
}

output "redshift_workgroup_name" {
  description = "Redshift Serverless workgroup (null when enable_redshift = false)."
  value       = one(module.redshift[*].workgroup_name)
}

output "redshift_endpoint" {
  description = "Redshift Serverless endpoint (null when enable_redshift = false)."
  value       = one(module.redshift[*].endpoint)
}

output "redshift_admin_secret_arn" {
  description = "Secrets Manager secret with the Redshift admin password (null when enable_redshift = false)."
  value       = one(module.redshift[*].admin_password_secret_arn)
}

output "converter_task_definition" {
  description = "Extract converter task definition family (null when enable_extract_converter = false)."
  value       = one(module.extract_converter[*].task_definition_family)
}

output "converter_task_role_arn" {
  description = "IAM role of the extract converter (null when enable_extract_converter = false)."
  value       = one(module.extract_converter[*].task_role_arn)
}
