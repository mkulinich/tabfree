output "cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ECS cluster ARN."
  value       = aws_ecs_cluster.this.arn
}

output "ecr_repository_urls" {
  description = "ECR repository URLs keyed by image name."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "app_security_group_id" {
  description = "Security group for TabFree tasks; the database and Redshift accept traffic only from it."
  value       = aws_security_group.app.id
}

output "log_group_name" {
  description = "CloudWatch log group for ECS tasks."
  value       = aws_cloudwatch_log_group.app.name
}

output "ecr_repository_arns" {
  description = "ECR repository ARNs keyed by image name."
  value       = { for k, r in aws_ecr_repository.this : k => r.arn }
}

output "log_group_arn" {
  description = "ARN of the CloudWatch log group for ECS tasks."
  value       = aws_cloudwatch_log_group.app.arn
}
