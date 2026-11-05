# The Azure module: one subscription, one region, one resource group the
# lab owns, plus the two groups Azure creates for it (the AKS node resource
# group and the Azure Monitor workspace's managed group). Facts behind every
# choice are in docs/adr/0007; the design is docs/adr/0008.
#
# The subscription ID lives in the gitignored lab.auto.tfvars. The provider
# refuses to run anywhere else, the Azure equivalent of allowed_account_ids.

provider "azurerm" {
  subscription_id = var.subscription_id

  # Register nothing implicitly: ADR-0007 lists the providers this lab needs,
  # all already registered. An unexpected registration is a mutation the
  # operator did not read in the plan.
  resource_provider_registrations = "none"

  # The evidence storage account has shared key auth disabled, so the
  # provider's own storage calls must use Entra ID too.
  storage_use_azuread = true

  features {
    key_vault {
      # Soft-deleted vault names cannot be reused for the retention period
      # (ADR-0007, row 11). The lab vault has no purge protection, so the
      # destroy purges it and the next session can recreate it.
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = false
    }
    log_analytics_workspace {
      permanently_delete_on_destroy = true
    }
    resource_group {
      # Fail closed: a destroy that finds something Terraform did not create
      # in the lab group stops instead of deleting it.
      prevent_deletion_if_contains_resources = true
    }
  }
}

provider "azapi" {
  subscription_id = var.subscription_id
}

data "azurerm_client_config" "current" {}

locals {
  name = var.cluster_name

  # azurerm has no provider-level default tags, so every resource sets these.
  # The capitalised keys satisfy the platform-guardrails policy; the lowercase
  # ones satisfy the landing zone's resource-group tag rules if it is ever
  # deployed again (ADR-0007).
  tags = {
    Project             = "k8s-self-healing-platform"
    Environment         = var.environment
    Owner               = "jordann6"
    ManagedBy           = "terraform"
    CostCenter          = var.cost_center
    owner               = "jordann6"
    environment         = var.environment
    cost_center         = var.cost_center
    data_classification = "internal"
    "selfheal.lab"      = "managed"
  }

  node_resource_group = "rg-${local.name}-lab-nodes"

  # Key Vault and Storage firewalls take a single address without the /32
  # (Storage rejects /31 and /32 prefixes outright).
  operator_ip_rules = [for c in var.api_authorized_cidrs : endswith(c, "/32") ? trimsuffix(c, "/32") : c]
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "lab" {
  name     = "rg-${local.name}-lab"
  location = var.location
  tags     = local.tags
}
