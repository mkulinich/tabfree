output "load_balancer_dns_name" {
  description = "Point var.domain here."
  value       = module.app.load_balancer_dns_name
}

output "ecr_repository_urls" {
  description = "Push the images here."
  value       = module.compute.ecr_repository_urls
}

output "database_endpoint" {
  description = "Aurora writer endpoint (private)."
  value       = module.metadata_db.endpoint
}

output "dr_vault_arn" {
  description = "The disaster-recovery backup vault."
  value       = aws_backup_vault.dr.arn
}
