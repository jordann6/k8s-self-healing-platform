terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.71"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Local state on purpose, same as the earlier EKS labs: a deploy, demo,
  # destroy session in an isolated workload account, torn down in the same
  # sitting. A longer-lived deployment would use the landing zone's dedicated
  # S3 state backend.
  backend "local" {}
}
