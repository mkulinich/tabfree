output "vault_arn" {
  description = "The backup vault."
  value       = aws_backup_vault.this.arn
}

output "plan_id" {
  description = "The backup plan."
  value       = aws_backup_plan.this.id
}
