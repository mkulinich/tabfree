output "raw_bucket" {
  description = "Raw-zone bucket name (Tableau downloads, ground truth)."
  value       = aws_s3_bucket.this["raw"].bucket
}

output "lake_bucket" {
  description = "Lake bucket name (Iceberg tables)."
  value       = aws_s3_bucket.this["lake"].bucket
}

output "athena_results_bucket" {
  description = "Athena query results bucket name."
  value       = aws_s3_bucket.this["athena"].bucket
}

output "bucket_arns" {
  description = "Bucket ARNs keyed by purpose (raw, lake, athena)."
  value       = { for k in keys(local.buckets) : k => aws_s3_bucket.this[k].arn }
}

output "glue_database_name" {
  description = "Glue Data Catalog database for TabFree Iceberg tables."
  value       = aws_glue_catalog_database.this.name
}

output "warehouse_location" {
  description = "S3 prefix for Iceberg tables in this database."
  value       = aws_glue_catalog_database.this.location_uri
}

output "athena_workgroup_name" {
  description = "Athena workgroup (engine v3, enforced settings)."
  value       = aws_athena_workgroup.this.name
}

output "athena_workgroup_arn" {
  description = "Athena workgroup ARN."
  value       = aws_athena_workgroup.this.arn
}
