# Row S1's secret backend. RBAC mode (no access policies), 7-day soft delete,
# no purge protection: the destroy purges it (main.tf features) so the name
# can be reused next session (ADR-0007 row 11).
#
# The network allows the operator's address and the node subnet (through its
# service endpoint) only.
# The partner key itself is written by make aks-bootstrap, never by
# Terraform, so no secret value lands in state.

resource "azurerm_key_vault" "lab" {
  #checkov:skip=CKV_AZURE_42:Soft delete is on (7 days); purge protection is off so the destroy can purge and the name can be reused (ADR-0007 row 11).
  #checkov:skip=CKV_AZURE_110:Same: purge protection would keep the vault name for the retention period after every session.
  #checkov:skip=CKV2_AZURE_32:No private endpoint: firewall to the node subnet and the operator's address instead.
  name                          = "kv-${local.name}-${random_string.suffix.result}"
  location                      = azurerm_resource_group.lab.location
  resource_group_name           = azurerm_resource_group.lab.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  purge_protection_enabled      = false
  public_network_access_enabled = true
  tags                          = local.tags

  network_acls {
    bypass                     = "None"
    default_action             = "Deny"
    ip_rules                   = local.operator_ip_rules
    virtual_network_subnet_ids = [azurerm_subnet.nodes.id]
  }
}

resource "azurerm_role_assignment" "operator_secrets_officer" {
  scope                = azurerm_key_vault.lab.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "eso_secrets_user" {
  scope                = azurerm_key_vault.lab.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.workload["eso"].principal_id
  principal_type       = "ServicePrincipal"
}
