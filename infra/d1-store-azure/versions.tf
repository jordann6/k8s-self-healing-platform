terraform {
  required_version = ">= 1.10.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.12"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # Local state on purpose, and kept after the session: this root is destroyed
  # in a second step at least 24 hours later, once the retention has expired and
  # the blob versions are deleted (ADR-0022). Never delete terraform.tfstate
  # before that destroy.
  backend "local" {}
}
