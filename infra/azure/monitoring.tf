# Azure Monitor managed service for Prometheus. As on EKS, the in-cluster
# Prometheus still evaluates the Sloth rules and the canary analysis (same
# PromQL as kind) and remote-writes only the series the lab reads. Microsoft-
# managed keys only, so there is no CMK permission to get wrong (ADR-0007
# row 3). The workspace creates its own MA_*_managed group with the default
# data collection endpoint and rule; deleting the workspace deletes them.

resource "azurerm_monitor_workspace" "lab" {
  name                          = "amw-${local.name}"
  location                      = azurerm_resource_group.lab.location
  resource_group_name           = azurerm_resource_group.lab.name
  public_network_access_enabled = true
  tags                          = local.tags
}

# Remote write authenticates as the Prometheus workload identity against the
# workspace's default data collection rule. The docs allow about 30 minutes
# for this to take effect; make aks-platform waits for a clean remote write.
resource "azurerm_role_assignment" "prometheus_publisher" {
  scope                = azurerm_monitor_workspace.lab.default_data_collection_rule_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.workload["prometheus"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "grafana_reader" {
  scope                = azurerm_monitor_workspace.lab.id
  role_definition_name = "Monitoring Data Reader"
  principal_id         = azurerm_user_assigned_identity.workload["grafana"].principal_id
  principal_type       = "ServicePrincipal"
}

# The remote write URL is built from the default endpoint and the rule's
# immutable ID (Connect self-managed Prometheus, ADR-0007 row 3).
data "azurerm_monitor_data_collection_endpoint" "amw" {
  name                = element(split("/", azurerm_monitor_workspace.lab.default_data_collection_endpoint_id), 8)
  resource_group_name = element(split("/", azurerm_monitor_workspace.lab.default_data_collection_endpoint_id), 4)
}

data "azurerm_monitor_data_collection_rule" "amw" {
  name                = element(split("/", azurerm_monitor_workspace.lab.default_data_collection_rule_id), 8)
  resource_group_name = element(split("/", azurerm_monitor_workspace.lab.default_data_collection_rule_id), 4)
}

locals {
  amw_remote_write_url = "${data.azurerm_monitor_data_collection_endpoint.amw.metrics_ingestion_endpoint}/dataCollectionRules/${data.azurerm_monitor_data_collection_rule.amw.immutable_id}/streams/Microsoft-PrometheusMetrics/api/v1/write?api-version=2023-04-24"
}
