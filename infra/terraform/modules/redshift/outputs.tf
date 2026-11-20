output "namespace_name" {
  description = "Redshift Serverless namespace."
  value       = aws_redshiftserverless_namespace.this.namespace_name
}

output "workgroup_name" {
  description = "Redshift Serverless workgroup (use with the Redshift Data API)."
  value       = aws_redshiftserverless_workgroup.this.workgroup_name
}

output "endpoint" {
  description = "Workgroup endpoint address."
  value       = aws_redshiftserverless_workgroup.this.endpoint[0].address
}

output "port" {
  description = "Workgroup port."
  value       = aws_redshiftserverless_workgroup.this.endpoint[0].port
}

output "database_name" {
  description = "Initial database name."
  value       = aws_redshiftserverless_namespace.this.db_name
}

output "admin_password_secret_arn" {
  description = "Secrets Manager secret with the admin password (managed by Redshift)."
  value       = aws_redshiftserverless_namespace.this.admin_password_secret_arn
}

output "security_group_id" {
  description = "Redshift security group."
  value       = aws_security_group.redshift.id
}
