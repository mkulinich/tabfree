# TabFree production (BUILD_PLAN P7.1, P7.3, P7.5): three AZs with a NAT gateway each, Aurora with a
# reader for failover and point-in-time recovery, KMS everywhere, WAF in front, secrets in Secrets
# Manager, daily and monthly backups copied to a second region. `tofu apply` deploys all of it;
# docs/operations/deploy.md lists the one-time steps (state bucket, certificate, SES, image).

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

module "security" {
  source = "../../modules/security"

  name = local.name
}

module "network" {
  source = "../../modules/network"

  name               = local.name
  vpc_cidr           = "10.40.0.0/16"
  az_count           = 3
  enable_nat_gateway = true
  nat_gateway_per_az = true
}

module "compute" {
  source = "../../modules/compute"

  name                 = local.name
  vpc_id               = module.network.vpc_id
  ecr_namespace        = var.name_prefix
  image_tag_mutability = "IMMUTABLE"
  container_insights   = "enhanced"
  log_retention_days   = 365
  kms_key_arn          = module.security.kms_key_arn
}

module "lake" {
  source = "../../modules/lake"

  name        = local.name
  kms_key_arn = module.security.kms_key_arn
}

module "metadata_db" {
  source = "../../modules/metadata_db"

  name                  = local.name
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  app_security_group_id = module.compute.app_security_group_id
  min_capacity          = 1
  max_capacity          = 16
  backup_retention_days = 14
  kms_key_arn           = module.security.kms_key_arn
  reader_count          = 1
  performance_insights  = true
  deletion_protection   = true
  skip_final_snapshot   = false
  log_retention_days    = 365
}

module "redshift" {
  source = "../../modules/redshift"
  count  = var.enable_redshift ? 1 : 0

  name                  = local.name
  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.private_subnet_ids
  app_security_group_id = module.compute.app_security_group_id
}

module "app" {
  source = "../../modules/app"

  name                  = local.name
  image                 = var.image
  base_url              = "https://${var.domain}"
  certificate_arn       = var.certificate_arn
  vpc_id                = module.network.vpc_id
  public_subnet_ids     = module.network.public_subnet_ids
  private_subnet_ids    = module.network.private_subnet_ids
  app_security_group_id = module.compute.app_security_group_id
  cluster_arn           = module.compute.cluster_arn
  cluster_name          = module.compute.cluster_name
  log_group_name        = module.compute.log_group_name
  kms_key_arn           = module.security.kms_key_arn
  db_host               = module.metadata_db.endpoint
  db_port               = module.metadata_db.port
  db_name               = module.metadata_db.database_name
  db_secret_arn         = module.metadata_db.master_user_secret_arn
  app_secret_arns       = module.security.secret_arns
  athena_workgroup_name = module.lake.athena_workgroup_name
  athena_workgroup_arn  = module.lake.athena_workgroup_arn
  read_bucket_arns      = [module.lake.bucket_arns["lake"], module.lake.bucket_arns["athena"]]
  write_bucket_arns     = [module.lake.bucket_arns["lake"], module.lake.bucket_arns["athena"]]
  usage_lake            = "s3://${module.lake.lake_bucket}/usage"
  ses_sender            = var.ses_sender
  enable_redshift       = var.enable_redshift
  enable_bedrock        = var.enable_bedrock
  api_min_count         = var.api_min_count
  api_max_count         = var.api_max_count

  extra_environment = {
    TABFREE_OIDC_ISSUER     = var.oidc_issuer
    TABFREE_OIDC_CLIENT_ID  = var.oidc_client_id
    TABFREE_EXPORT_RENDERER = "playwright"
    TABFREE_AI_PROVIDER     = var.enable_bedrock ? "bedrock" : "off"
  }
  extra_secrets = {
    TABFREE_OIDC_CLIENT_SECRET = var.oidc_client_secret_arn
  }
}

resource "aws_kms_key" "dr" {
  provider = aws.dr

  description         = "TabFree ${local.name}: disaster-recovery backups"
  enable_key_rotation = true
}

resource "aws_backup_vault" "dr" {
  provider = aws.dr

  name        = "${local.name}-dr"
  kms_key_arn = aws_kms_key.dr.arn
}

module "backup" {
  source = "../../modules/backup"

  name          = local.name
  kms_key_arn   = module.security.kms_key_arn
  resource_arns = [module.metadata_db.cluster_arn]
  dr_vault_arn  = aws_backup_vault.dr.arn
}
