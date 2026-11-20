terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is local by default (terraform.tfstate in this directory, which is
  # gitignored). For a shared sandbox, move it to S3: create a versioned,
  # encrypted bucket, replace the block below with the S3 example, then run
  # `tofu -chdir=infra/terraform/envs/sandbox init -migrate-state`.
  backend "local" {}

  # backend "s3" {
  #   bucket       = "<state-bucket-name>"
  #   key          = "tabfree/sandbox/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true # S3-native locking (OpenTofu 1.10+, Terraform 1.10+); no DynamoDB table needed
  # }
}
