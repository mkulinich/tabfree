output "kms_key_arn" {
  description = "The customer-managed KMS key for data at rest."
  value       = aws_kms_key.this.arn
}

output "secret_arns" {
  description = "Application signing keys in Secrets Manager, by name."
  value       = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}
