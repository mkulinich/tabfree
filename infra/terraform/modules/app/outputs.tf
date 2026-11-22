output "load_balancer_dns_name" {
  description = "Point the site's DNS name (CNAME or alias) here."
  value       = aws_lb.this.dns_name
}

output "load_balancer_zone_id" {
  description = "For a Route 53 alias record."
  value       = aws_lb.this.zone_id
}

output "web_acl_arn" {
  description = "The WAF web ACL."
  value       = aws_wafv2_web_acl.this.arn
}

output "task_role_arn" {
  description = "The tasks' IAM role."
  value       = aws_iam_role.task.arn
}

output "services" {
  description = "ECS service names."
  value       = { api = aws_ecs_service.api.name, worker = aws_ecs_service.worker.name }
}
