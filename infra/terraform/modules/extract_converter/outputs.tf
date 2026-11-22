output "task_definition_arn" {
  description = "Converter task definition (pass to ecs run-task)."
  value       = aws_ecs_task_definition.this.arn
}

output "task_definition_family" {
  description = "Task definition family; run-task with the family uses the latest revision."
  value       = aws_ecs_task_definition.this.family
}

output "container_name" {
  description = "Container name, for run-task command overrides (e.g. --shard i/N)."
  value       = local.container_name
}

output "task_role_arn" {
  description = "IAM role the converter runs as."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "IAM role ECS uses to pull the image and write logs."
  value       = aws_iam_role.execution.arn
}
