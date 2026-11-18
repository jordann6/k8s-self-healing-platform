terraform {
  required_version = ">= 1.10.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }

  # Local state on purpose, as on EKS and AKS: one deploy, demo, destroy
  # session, torn down in the same sitting.
  backend "local" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone

  # Inline literals, not a variable: the policy gate reads provider
  # default_labels statically.
  default_labels = {
    project     = "k8s-self-healing-platform"
    owner       = "jordann6"
    managed-by  = "terraform"
    environment = "lab"
    cost_center = "cc-1010"
  }
}
