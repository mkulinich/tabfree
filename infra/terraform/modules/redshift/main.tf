# Redshift Serverless namespace and workgroup in private subnets, reachable
# only from the application security group. The admin password is generated
# and stored by Redshift in Secrets Manager (manage_admin_password).
# The root module creates this only when enable_redshift = true.

locals {
  identifier = "${var.name}-redshift"
}

resource "aws_security_group" "redshift" {
  name        = local.identifier
  description = "TabFree Redshift Serverless: port 5439 from the app security group only"
  vpc_id      = var.vpc_id

  tags = { Name = local.identifier }
}

resource "aws_vpc_security_group_ingress_rule" "redshift_from_app" {
  security_group_id            = aws_security_group.redshift.id
  description                  = "Redshift from TabFree tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
  referenced_security_group_id = var.app_security_group_id
}

resource "aws_vpc_security_group_egress_rule" "app_to_redshift" {
  security_group_id            = var.app_security_group_id
  description                  = "Redshift Serverless"
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
  referenced_security_group_id = aws_security_group.redshift.id
}

resource "aws_redshiftserverless_namespace" "this" {
  namespace_name        = local.identifier
  db_name               = var.database_name
  admin_username        = var.admin_username
  manage_admin_password = true
  kms_key_id            = var.kms_key_arn
  log_exports           = ["userlog", "connectionlog", "useractivitylog"]
}

resource "aws_redshiftserverless_workgroup" "this" {
  workgroup_name       = local.identifier
  namespace_name       = aws_redshiftserverless_namespace.this.namespace_name
  base_capacity        = var.base_capacity
  subnet_ids           = var.subnet_ids
  security_group_ids   = [aws_security_group.redshift.id]
  publicly_accessible  = false
  enhanced_vpc_routing = true # S3 traffic stays in the VPC (S3 gateway endpoint)
}

# AWS documents Redshift Serverless as needing subnets in three Availability
# Zones; this was not re-verified when the module was written (2026-11).
# A check block warns at plan time instead of blocking, in case AWS has
# relaxed the rule. Set az_count = 3 in the root module if creation fails.
check "redshift_subnet_azs" {
  assert {
    condition     = length(var.subnet_ids) >= 3
    error_message = "Redshift Serverless is documented as requiring subnets in three Availability Zones, but only ${length(var.subnet_ids)} were given. Set az_count = 3 if workgroup creation fails."
  }
}
