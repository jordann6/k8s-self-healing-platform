# Row D1 (ADR-0022). Everything here is off unless the WORM account from
# infra/d1-store-azure is named, so a session without D1 changes nothing.
#
#   make d1-store-azure-plan / apply   the account, in its own root, first
#   make aks-plan AKS_PLAN_ARGS="-var=d1_store_account_id=<id>"   this file

variable "d1_store_account_id" {
  description = "Resource ID of the WORM storage account created by infra/d1-store-azure. Empty disables every D1 resource in this root."
  type        = string
  default     = ""
}

variable "d1_store_container" {
  description = "Container in the WORM account that holds the backups."
  type        = string
  default     = "d1-backups"
}

locals {
  d1_enabled         = var.d1_store_account_id != ""
  d1_container_scope = "${var.d1_store_account_id}/blobServices/default/containers/${var.d1_store_container}"

  # One identity per service account, as everywhere else in this root. Both hold
  # read and write on the one container and no delete (ADR-0022 decision 5).
  d1_workload_identities = local.d1_enabled ? {
    d1_backup  = { namespace = "selfheal-d1-source", service_account = "d1-backup" }
    d1_restore = { namespace = "selfheal-d1-restore", service_account = "d1-restore" }
  } : {}
}

# Built-in Storage Blob Data Contributor includes blob delete, so the backup
# identity gets a custom data role: read (which lists versions and returns the
# immutability headers) and write, nothing else. A role definition is a
# subscription-level resource.
resource "azurerm_role_definition" "d1_backup_writer" {
  count = local.d1_enabled ? 1 : 0

  name        = "${local.name}-d1-backup-writer-${random_string.suffix.result}"
  scope       = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  description = "Self-healing lab row D1: read and write backup blobs, never delete."

  permissions {
    actions = []
    data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
    ]
    not_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/deleteBlobVersion/action",
    ]
  }

  assignable_scopes = ["/subscriptions/${data.azurerm_client_config.current.subscription_id}"]
}

resource "azurerm_role_assignment" "d1_backup_writer" {
  for_each = local.d1_workload_identities

  scope              = local.d1_container_scope
  role_definition_id = azurerm_role_definition.d1_backup_writer[0].role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.workload[each.key].principal_id
  principal_type     = "ServicePrincipal"
}

# Deny by default while the cluster runs: the node subnet (the Job pods) and the
# operator's address (the runner's administrator check). The account itself is in
# its own root and is open at the network layer, Entra-only, between sessions;
# this resource is destroyed with the lab.
#trivy:ignore:AVD-AZU-0010
resource "azurerm_storage_account_network_rules" "d1_store" {
  #checkov:skip=CKV_AZURE_36:No trusted-services bypass on purpose.
  count = local.d1_enabled ? 1 : 0

  storage_account_id         = var.d1_store_account_id
  default_action             = "Deny"
  bypass                     = ["None"]
  ip_rules                   = local.operator_ip_rules
  virtual_network_subnet_ids = [azurerm_subnet.nodes.id]
}
