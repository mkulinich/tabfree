provider "aws" {
  region              = var.region
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = local.tags
  }
}

# The disaster-recovery region: a backup vault that receives a copy of every backup (P7.1).
provider "aws" {
  alias               = "dr"
  region              = var.dr_region
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = local.tags
  }
}
