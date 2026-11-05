# Workload identity: one user-assigned identity per in-cluster caller, each
# federated to exactly one Kubernetes service account. Every role assignment
# is direct, never through a group (group membership of managed identities
# can take hours to apply; ADR-0007 row 13), and is made here, long before a
# row depends on it.

locals {
  workload_identities = {
    # ESO reads the partner key from Key Vault (row S1). Referenced mode: the
    # SecretStore names this service account in the lab namespace.
    eso = { namespace = "selfheal", service_account = "eso-backend" }
    # Prometheus remote-writes the lab's series to the Azure Monitor workspace.
    prometheus = { namespace = "monitoring", service_account = "kps-prometheus" }
    # Grafana reads the Azure Monitor workspace.
    grafana = { namespace = "monitoring", service_account = "kps-grafana" }
    # The R2 worker: takes incidents off the queue, cordons and relabels.
    responder = { namespace = "responder", service_account = "responder" }
  }
}

resource "azurerm_user_assigned_identity" "workload" {
  for_each            = local.workload_identities
  name                = "id-${local.name}-${each.key}"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "workload" {
  for_each                  = local.workload_identities
  name                      = "fic-${each.key}"
  user_assigned_identity_id = azurerm_user_assigned_identity.workload[each.key].id
  issuer                    = azurerm_kubernetes_cluster.lab.oidc_issuer_url
  subject                   = "system:serviceaccount:${each.value.namespace}:${each.value.service_account}"
  audience                  = ["api://AzureADTokenExchange"]
}
