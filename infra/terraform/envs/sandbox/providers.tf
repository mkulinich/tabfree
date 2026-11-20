provider "aws" {
  region = var.region

  # Optional guard: refuse to run against any other account.
  allowed_account_ids = var.allowed_account_ids

  default_tags {
    tags = local.tags
  }
}
