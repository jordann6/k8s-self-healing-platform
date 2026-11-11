# Row D1 on AKS (ADR-0022): the WORM backup store, in its own resource group so
# the lab's resource group delete never blocks on retained blobs (the same class
# as ResourceGroupDeletionBlocked on 2026-11-10). Version-level immutability
# support can be enabled only when the account is created and cannot be disabled,
# and with it on the account and its containers can be deleted only when empty.
# A Locked policy cannot be deleted or shortened. Read the plan before applying.
#
# The firewall, the workload identities and the data-plane role live in the AKS
# root, which owns the node subnet; the account is open at the network layer
# (Entra-only, no shared keys) only between sessions.

provider "azurerm" {
  subscription_id = var.subscription_id

  # Register nothing implicitly, as in the AKS root.
  resource_provider_registrations = "none"
  storage_use_azuread             = true

  features {}
}

data "azurerm_client_config" "current" {}

locals {
  tags = {
    Project     = "k8s-self-healing-platform"
    Environment = var.environment
    Owner       = "jordann6"
    ManagedBy   = "terraform"
    CostCenter  = var.cost_center
    cost_center = var.cost_center
  }
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "store" {
  name     = "rg-selfheal-d1-store"
  location = var.location
  tags     = local.tags
}

# No firewall in this root on purpose (ADR-0022 decision 5): the node subnet does not exist
# when this root is applied, so the AKS root adds default-deny with the node subnet and the
# operator address for the life of the session. Between sessions the account is Entra-only
# (no shared keys, no anonymous access), holds one synthetic backup, and is locked.
#trivy:ignore:AVD-AZU-0012
resource "azurerm_storage_account" "worm" {
  #checkov:skip=CKV_AZURE_33:Queue classic logging needs shared-key data plane calls; shared keys are disabled.
  #checkov:skip=CKV_AZURE_59:Entra-only auth with no shared keys; the AKS root adds the firewall while a session runs (ADR-0022).
  #checkov:skip=CKV_AZURE_206:LRS: the store holds one tiny synthetic backup for about a day.
  #checkov:skip=CKV2_AZURE_33:No private endpoint: Entra-only auth and a firewall during the session instead.
  #checkov:skip=CKV2_AZURE_1:Microsoft-managed keys; no customer-managed key in this lab, and a key vault would outlive the store.
  #checkov:skip=CKV2_AZURE_38:Soft delete would add a second retention clock to the second destroy; the locked immutability policy already protects every version.
  #checkov:skip=CKV_AZURE_35:Default deny is applied by the AKS root during the session; between sessions the account is Entra-only and locked.
  name                     = "stselfhealworm${random_string.suffix.result}"
  location                 = azurerm_resource_group.store.location
  resource_group_name      = azurerm_resource_group.store.name
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  infrastructure_encryption_enabled = true
  cross_tenant_replication_enabled  = false
  local_user_enabled                = false
  sftp_enabled                      = false
  public_network_access             = "Enabled"

  blob_properties {
    versioning_enabled = true
  }

  # Version-level WORM: every blob version inherits a one-day time-based
  # retention. Lengthening a locked policy is allowed, shortening is not.
  immutability_policy {
    state                         = var.immutability_state
    period_since_creation_in_days = 1
    allow_protected_append_writes = false
  }

  tags = local.tags
}

resource "azurerm_storage_container" "backups" {
  #checkov:skip=CKV2_AZURE_21:Classic blob logging needs shared-key data plane calls; shared keys are disabled.
  name                  = "d1-backups"
  storage_account_id    = azurerm_storage_account.worm.id
  container_access_type = "private"
}

# The operator is the "administrator" of the deletion check: authorized to delete,
# and refused by the retention policy, not by a missing permission.
resource "azurerm_role_assignment" "operator_owner" {
  scope                = azurerm_storage_account.worm.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = data.azurerm_client_config.current.object_id
}
