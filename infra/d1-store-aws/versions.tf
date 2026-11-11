terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.71"
    }
  }

  # Local state on purpose, and kept after the session: this root is destroyed
  # in a second step at least 24 hours later, once the COMPLIANCE retention has
  # expired (ADR-0022). Never delete terraform.tfstate before that destroy.
  backend "local" {}
}
