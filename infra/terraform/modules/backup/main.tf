# Backups and disaster recovery (BUILD_PLAN P7.1). Aurora keeps continuous backups for point-in-time
# recovery (backup_retention_days on the cluster); AWS Backup adds daily snapshots kept for
# var.retention_days, monthly ones kept for var.monthly_retention_days, and, with a DR vault in
# another region, copies of each so the site can be rebuilt there (docs/operations/disaster-recovery.md).

resource "aws_backup_vault" "this" {
  name          = var.name
  kms_key_arn   = var.kms_key_arn
  force_destroy = var.force_destroy
}

resource "aws_backup_plan" "this" {
  name = var.name

  rule {
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.this.name
    schedule          = "cron(0 5 * * ? *)"
    start_window      = 60
    completion_window = 360

    lifecycle {
      delete_after = var.retention_days
    }

    dynamic "copy_action" {
      for_each = var.dr_vault_arn == null ? [] : [var.dr_vault_arn]
      content {
        destination_vault_arn = copy_action.value
        lifecycle {
          delete_after = var.retention_days
        }
      }
    }
  }

  rule {
    rule_name         = "monthly"
    target_vault_name = aws_backup_vault.this.name
    schedule          = "cron(0 6 1 * ? *)"

    lifecycle {
      cold_storage_after = 30
      delete_after       = var.monthly_retention_days
    }

    dynamic "copy_action" {
      for_each = var.dr_vault_arn == null ? [] : [var.dr_vault_arn]
      content {
        destination_vault_arn = copy_action.value
        lifecycle {
          cold_storage_after = 30
          delete_after       = var.monthly_retention_days
        }
      }
    }
  }
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${var.name}-backup"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "backup" {
  for_each = toset([
    "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup",
    "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores",
    "arn:aws:iam::aws:policy/AWSBackupServiceRolePolicyForS3Backup",
    "arn:aws:iam::aws:policy/AWSBackupServiceRolePolicyForS3Restore",
  ])

  role       = aws_iam_role.backup.name
  policy_arn = each.value
}

resource "aws_backup_selection" "this" {
  name         = var.name
  plan_id      = aws_backup_plan.this.id
  iam_role_arn = aws_iam_role.backup.arn
  resources    = var.resource_arns
}
