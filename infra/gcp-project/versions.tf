terraform {
  required_version = ">= 1.10.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # Local state, like every root in this repo. This root outlives the lab
  # sessions: it owns only the project, its billing link and its APIs.
  backend "local" {}
}

provider "google" {
  # Inline literals, not a variable: the policy gate reads provider
  # default_labels statically.
  default_labels = {
    project     = "k8s-self-healing-platform"
    owner       = "jordann6"
    managed-by  = "terraform"
    cost_center = "cc-1010"
  }
}
