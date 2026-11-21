# TabFree AWS sandbox (Phase 0, task P0.4).
# Applying this creates billable resources. See ../../README.md.

locals {
  name = "${var.name_prefix}-${var.environment}"

  tags = merge(
    {
      Project     = "tabfree"
      Environment = var.environment
      ManagedBy   = "opentofu"
    },
    var.tags,
  )
}

module "network" {
  source = "../../modules/network"

  name               = local.name
  vpc_cidr           = var.vpc_cidr
  az_count           = var.az_count
  enable_nat_gateway = var.enable_nat_gateway
}

module "compute" {
  source = "../../modules/compute"

  name                 = local.name
  vpc_id               = module.network.vpc_id
  ecr_namespace        = var.name_prefix
  image_tag_mutability = var.ecr_image_tag_mutability
  container_insights   = var.container_insights
  log_retention_days   = var.log_retention_days
  force_destroy        = var.force_destroy
}

module "lake" {
  source = "../../modules/lake"

  name                           = local.name
  force_destroy                  = var.force_destroy
  bytes_scanned_cutoff_per_query = var.athena_bytes_scanned_cutoff_per_query
  athena_results_retention_days  = var.athena_results_retention_days
}

module "metadata_db" {
  source = "../../modules/metadata_db"

  name                  = local.name
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  app_security_group_id = module.compute.app_security_group_id
  min_capacity          = var.aurora_min_capacity
  max_capacity          = var.aurora_max_capacity
  log_retention_days    = var.log_retention_days
  deletion_protection   = var.aurora_deletion_protection
  skip_final_snapshot   = var.aurora_skip_final_snapshot
  apply_immediately     = true # sandbox: don't wait for the maintenance window
}

module "redshift" {
  source = "../../modules/redshift"
  count  = var.enable_redshift ? 1 : 0

  name                  = local.name
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  app_security_group_id = module.compute.app_security_group_id
  base_capacity         = var.redshift_base_capacity
}

# Extract converter (P2.8): a Fargate task definition and its IAM roles; runs only on RunTask.
module "extract_converter" {
  source = "../../modules/extract_converter"
  count  = var.enable_extract_converter ? 1 : 0

  name                      = local.name
  image                     = "${module.compute.ecr_repository_urls["converter"]}:${var.converter_image_tag}"
  ecr_repository_arn        = module.compute.ecr_repository_arns["converter"]
  log_group_name            = module.compute.log_group_name
  log_group_arn             = module.compute.log_group_arn
  raw_bucket_arn            = module.lake.bucket_arns["raw"]
  lake_bucket_arn           = module.lake.bucket_arns["lake"]
  athena_results_bucket_arn = module.lake.bucket_arns["athena"]
  glue_database_name        = module.lake.glue_database_name
  athena_workgroup_name     = module.lake.athena_workgroup_name
  athena_workgroup_arn      = module.lake.athena_workgroup_arn
  store_location            = "s3://${module.lake.raw_bucket}"
  output_location           = "s3://${module.lake.lake_bucket}/staging"
  iceberg_root              = trimsuffix(module.lake.warehouse_location, "/")
  cpu                       = var.converter_cpu
  memory                    = var.converter_memory
  workers                   = var.converter_workers
}
