terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Production state belongs in an encrypted, versioned S3 bucket with locking (it holds the generated
  # signing keys). Create the bucket once, then fill this in and run `tofu init`.
  backend "s3" {}
}
