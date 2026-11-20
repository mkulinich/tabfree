# TabFree metadata DB: Aurora PostgreSQL Serverless v2, private subnets only,
# reachable only from the application security group. The master password is
# generated and stored by RDS in Secrets Manager (manage_master_user_password).

# Resolve the newest minor version of the requested major version at plan
# time, so we never pin a version that does not exist in the region.
data "aws_rds_engine_version" "postgres" {
  engine  = "aurora-postgresql"
  version = var.engine_major_version
  latest  = true
}

locals {
  identifier = "${var.name}-metadata"
}

resource "aws_db_subnet_group" "this" {
  name        = local.identifier
  description = "Private subnets for the TabFree metadata DB"
  subnet_ids  = var.subnet_ids
}

resource "aws_security_group" "db" {
  name        = "${local.identifier}-db"
  description = "TabFree metadata DB: PostgreSQL from the app security group only"
  vpc_id      = var.vpc_id

  # No egress rules: the database does not open outbound connections.
  tags = { Name = "${local.identifier}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from TabFree tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = var.app_security_group_id
}

resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  security_group_id            = var.app_security_group_id
  description                  = "PostgreSQL to the TabFree metadata DB"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.db.id
}

# Pre-create the log group so the exported logs get a retention period
# (RDS would otherwise create it with no expiry).
resource "aws_cloudwatch_log_group" "postgresql" {
  name              = "/aws/rds/cluster/${local.identifier}/postgresql"
  retention_in_days = var.log_retention_days
}

resource "aws_rds_cluster" "this" {
  cluster_identifier = local.identifier
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # Serverless v2 uses the provisioned engine mode
  engine_version     = data.aws_rds_engine_version.postgres.version_actual
  database_name      = var.database_name
  port               = 5432

  master_username               = var.master_username
  manage_master_user_password   = true
  master_user_secret_kms_key_id = var.kms_key_arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  network_type           = "IPV4"

  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  serverlessv2_scaling_configuration {
    min_capacity             = var.min_capacity
    max_capacity             = var.max_capacity
    seconds_until_auto_pause = var.min_capacity == 0 ? var.seconds_until_auto_pause : null
  }

  backup_retention_period         = var.backup_retention_days
  preferred_backup_window         = "07:00-08:00"
  preferred_maintenance_window    = "sun:08:30-sun:09:30"
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  apply_immediately               = var.apply_immediately

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${local.identifier}-final"

  lifecycle {
    # New minor versions appear over time; let Aurora's automatic minor
    # upgrades apply them instead of showing a diff on every plan. To change
    # major version, change engine_major_version and remove this temporarily.
    ignore_changes = [engine_version]
  }

  depends_on = [aws_cloudwatch_log_group.postgresql]
}

# A single writer: this is a sandbox, not production HA. Add a second
# instance in another AZ for failover.
resource "aws_rds_cluster_instance" "writer" {
  identifier                 = "${local.identifier}-1"
  cluster_identifier         = aws_rds_cluster.this.id
  instance_class             = "db.serverless"
  engine                     = aws_rds_cluster.this.engine
  engine_version             = aws_rds_cluster.this.engine_version
  db_subnet_group_name       = aws_db_subnet_group.this.name
  publicly_accessible        = false
  auto_minor_version_upgrade = true
  apply_immediately          = var.apply_immediately

  lifecycle {
    ignore_changes = [engine_version]
  }
}
