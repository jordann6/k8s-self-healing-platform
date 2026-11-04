# Two accounts, both reached by assuming a role from the operator's
# management-account credentials:
#
#   lab       the isolated workload account that holds everything hourly
#   security  the organization's delegated GuardDuty administrator. A member
#             account cannot turn Runtime Monitoring on for itself (ADR-0005),
#             so the one GuardDuty toggle this lab needs is made from here.
#
# allowed_account_ids makes a wrong profile fail at plan time instead of
# creating a cluster in the wrong account.

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.lab_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.lab_account_id}:role/${var.lab_role_name}"
    session_name = "selfheal-terraform"
  }

  # Written out literally (not local.tags) so the shared policy gate can read
  # them: resources created inside modules carry the CostCenter tag the FinOps
  # allocation gate checks for. Keep identical to local.tags.
  default_tags {
    tags = {
      Project     = "k8s-self-healing-platform"
      Environment = var.environment
      Owner       = "jordann6"
      ManagedBy   = "terraform"
      CostCenter  = var.cost_center
    }
  }
}

provider "aws" {
  alias               = "security"
  region              = var.region
  allowed_account_ids = [var.security_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.security_account_id}:role/${var.security_role_name}"
    session_name = "selfheal-terraform"
  }

  default_tags {
    tags = {
      Project     = "k8s-self-healing-platform"
      Environment = var.environment
      Owner       = "jordann6"
      ManagedBy   = "terraform"
      CostCenter  = var.cost_center
    }
  }
}

data "aws_caller_identity" "lab" {}
data "aws_partition" "current" {}

locals {
  name       = var.cluster_name
  account_id = data.aws_caller_identity.lab.account_id
  partition  = data.aws_partition.current.partition

  # Must match the provider default_tags casing exactly.
  tags = {
    Project     = "k8s-self-healing-platform"
    Environment = var.environment
    Owner       = "jordann6"
    ManagedBy   = "terraform"
    CostCenter  = var.cost_center
  }

  # Resources the lab's automation is allowed to act on carry this tag. The S4
  # remediation document and the R2 responder both refuse anything without it,
  # so neither can touch other workloads in the account.
  lab_tag = { "selfheal.lab/managed" = "true" }
}
