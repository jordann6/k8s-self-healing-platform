output "cluster_name" {
  description = "AKS cluster name."
  value       = azurerm_kubernetes_cluster.lab.name
}

output "resource_group" {
  description = "The lab's own resource group."
  value       = azurerm_resource_group.lab.name
}

output "configure_kubectl" {
  description = "Writes the lab's own kubeconfig and converts it to Azure CLI sign-in (make aks-platform runs these)."
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.lab.name} --name ${azurerm_kubernetes_cluster.lab.name} --file .kube/aks --context selfheal-aks --overwrite-existing && KUBECONFIG=.kube/aks kubelogin convert-kubeconfig -l azurecli"
}

output "acr_login_server" {
  description = "Push target for the lab images."
  value       = azurerm_container_registry.lab.login_server
}

output "amw_remote_write_url" {
  description = "Prometheus remote write URL (the workspace's default data collection endpoint and rule)."
  value       = local.amw_remote_write_url
}

output "amw_query_endpoint" {
  description = "Azure Monitor workspace query endpoint (Grafana data source)."
  value       = azurerm_monitor_workspace.lab.query_endpoint
}

output "workload_identities" {
  description = "Client IDs for the service account annotations (azure.workload.identity/client-id)."
  value       = { for k, id in azurerm_user_assigned_identity.workload : k => id.client_id }
}

output "runner" {
  description = "Everything the scripts and the runner need for the Azure-native rows, in one object."
  value = {
    tenant_id            = data.azurerm_client_config.current.tenant_id
    resource_group       = azurerm_resource_group.lab.name
    node_resource_group  = azurerm_kubernetes_cluster.lab.node_resource_group
    cluster_id           = azurerm_kubernetes_cluster.lab.id
    workload_pool        = azurerm_kubernetes_cluster_node_pool.workload.name
    key_vault            = azurerm_key_vault.lab.name
    key_vault_uri        = azurerm_key_vault.lab.vault_uri
    node_nsg             = azurerm_network_security_group.nodes.name
    policy_assignment_id = azurerm_resource_group_policy_assignment.close_open_ingress.id
    automation_id        = azurerm_security_center_automation.r2.id
    logic_app_id         = azapi_resource.responder.id
    storage_account      = azurerm_storage_account.evidence.name
    incidents_queue      = azurerm_storage_queue.incidents.name
    evidence_container   = azurerm_storage_container.evidence.name
    acr_login_server     = azurerm_container_registry.lab.login_server
    responder_client_id  = azurerm_user_assigned_identity.workload["responder"].client_id
    eso_client_id        = azurerm_user_assigned_identity.workload["eso"].client_id
  }
}
