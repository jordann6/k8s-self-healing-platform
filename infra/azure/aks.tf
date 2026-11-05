# AKS 1.34, bring your own CNI (ADR-0007 row 1: the pinned Cilium and Hubble
# the kind and EKS runs use, installed by make aks-platform right after this
# apply; the nodes are NotReady until then).
#
# Two pools, as on EKS: a system pool that runs the platform, the prober and
# the partner and is never a fault target, and a tainted, autoscaled workload
# pool that only the web pods tolerate. A4, S4 and R2 act on workload nodes
# only.

# The control plane joins a subnet the lab owns, so its identity must hold
# the join permission before the cluster exists. A user-assigned identity
# makes that ordering explicit (a system-assigned one exists only after create).
resource "azurerm_user_assigned_identity" "control_plane" {
  name                = "id-${local.name}-aks"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "control_plane_subnet" {
  scope                = azurerm_subnet.nodes.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.control_plane.principal_id
  principal_type       = "ServicePrincipal"
}

# Defender's sensor registers with a workspace (ADR-0007 row 16). Small daily
# cap: a runaway log source costs at most the cap, then ingestion stops.
resource "azurerm_log_analytics_workspace" "lab" {
  name                = "log-${local.name}-${random_string.suffix.result}"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
  tags                = local.tags
}

# network_policy is unset because the CNI is brought by the lab: the pinned
# Cilium enforces Kubernetes and Cilium network policies (ADR-0007 row 1).
#trivy:ignore:AVD-AZU-0043
resource "azurerm_kubernetes_cluster" "lab" {
  #checkov:skip=CKV_AZURE_170:Free tier for a bounded lab session; no SLA needed (ADR-0007 row 12).
  #checkov:skip=CKV_AZURE_172:The Secrets Store CSI driver is not used; ESO syncs Key Vault secrets (row S1).
  #checkov:skip=CKV_AZURE_115:Public API server limited to the operator's /32 by authorized IP ranges, as on EKS (ADR-0007 row 10).
  #checkov:skip=CKV_AZURE_117:Encryption at host with platform-managed keys; no customer-managed key in this lab (ADR-0007 row 3).
  #checkov:skip=CKV_AZURE_7:Bring your own CNI; the pinned Cilium enforces network policy (ADR-0007 row 1).
  #checkov:skip=CKV2_AZURE_29:Bring your own CNI on purpose: the same Cilium and Hubble as kind and EKS (ADR-0007 row 1, D2).
  #checkov:skip=CKV_AZURE_232:The system pool runs the lab platform and probers; only the workload pool is a fault target.
  #checkov:skip=CKV_AZURE_226:Row R2 snapshots the OS disk, which an ephemeral disk cannot give (ADR-0007, OS disks).
  #checkov:skip=CKV_AZURE_116:Azure Policy for Kubernetes is not part of the experiment; admission is unchanged across targets (ADR-0001).
  #checkov:skip=CKV_AZURE_6:Authorized IP ranges are set from var.api_authorized_cidrs, which checkov cannot resolve.
  #checkov:skip=CKV_AZURE_171:No cluster upgrade channel so the control plane cannot change version mid-run; node images upgrade weekly (ADR-0007 row 17).
  #checkov:skip=CKV_AZURE_4:Container Insights is not used; in-cluster Prometheus remote-writes to the Azure Monitor workspace.
  name                = local.name
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  node_resource_group = local.node_resource_group
  dns_prefix          = local.name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Free"
  tags                = local.tags

  # Workload identity for every pod that calls Azure; no keys anywhere.
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  # Entra ID sign-in and Azure RBAC for Kubernetes; no local admin kubeconfig.
  local_account_disabled            = true
  role_based_access_control_enabled = true
  azure_active_directory_role_based_access_control {
    azure_rbac_enabled = true
    tenant_id          = data.azurerm_client_config.current.tenant_id
  }

  # Nodes are replaced, not patched: a new node image every week, inside a
  # four-hour window the preflight keeps clear of the session. The control
  # plane has no upgrade channel, so it cannot change version mid-run.
  node_os_upgrade_channel = "NodeImage"
  maintenance_window_node_os {
    frequency   = "Weekly"
    interval    = 1
    duration    = 4
    day_of_week = var.maintenance_day
    start_time  = var.maintenance_start
    utc_offset  = "+00:00"
  }

  # Node access is Run Command / command invoke, never SSH (no key is kept).
  run_command_enabled  = true
  azure_policy_enabled = false

  # Cluster autoscaler only (ADR-0007 row 8): no node auto-provisioning.
  node_provisioning_profile {
    mode = "Manual"
  }

  api_server_access_profile {
    authorized_ip_ranges = var.api_authorized_cidrs
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.control_plane.id]
  }

  network_profile {
    network_plugin    = "none"
    pod_cidr          = local.pod_cidr
    service_cidr      = local.service_cidr
    dns_service_ip    = local.dns_ip
    load_balancer_sku = "standard"
    outbound_type     = "loadBalancer"
    load_balancer_profile {
      managed_outbound_ip_count = 1
      # Long port-forwards and watches survive idle gaps (ADR-0007 row 10:
      # the default reclaims idle flows after four minutes).
      idle_timeout_in_minutes = 30
    }
  }

  # Row A4's replacement node: the autoscaler reacts to the Pending replica
  # within seconds instead of the default 10s scan plus delays.
  auto_scaler_profile {
    scan_interval                              = "10s"
    new_pod_scale_up_delay                     = "0s"
    max_node_provisioning_time                 = "10m"
    scale_down_unneeded                        = "10m"
    scale_down_delay_after_add                 = "10m"
    skip_nodes_with_system_pods                = true
    daemonset_eviction_for_empty_nodes_enabled = true
  }

  storage_profile {
    disk_driver_enabled         = true
    file_driver_enabled         = false
    blob_driver_enabled         = false
    snapshot_controller_enabled = false
  }

  dynamic "microsoft_defender" {
    for_each = var.defender_enabled ? [1] : []
    content {
      log_analytics_workspace_id = azurerm_log_analytics_workspace.lab.id
    }
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = var.node_vm_size
    node_count                   = var.system_node_count
    vnet_subnet_id               = azurerm_subnet.nodes.id
    max_pods                     = 60
    only_critical_addons_enabled = false
    # R2 snapshots the OS disk; an ephemeral disk cannot be snapshotted.
    os_disk_type                = "Managed"
    os_disk_size_gb             = 64
    host_encryption_enabled     = true
    node_public_ip_enabled      = false
    node_labels                 = { "selfheal.lab/nodes" = "system" }
    temporary_name_for_rotation = "systemtmp"
    upgrade_settings {
      max_surge = "1"
    }
    tags = local.tags
  }

  depends_on = [
    azurerm_role_assignment.control_plane_subnet,
    azurerm_subnet_network_security_group_association.nodes,
    azurerm_security_center_subscription_pricing.containers,
  ]
}

resource "azurerm_kubernetes_cluster_node_pool" "workload" {
  name                    = "work"
  kubernetes_cluster_id   = azurerm_kubernetes_cluster.lab.id
  mode                    = "User"
  vm_size                 = var.node_vm_size
  vnet_subnet_id          = azurerm_subnet.nodes.id
  auto_scaling_enabled    = true
  min_count               = var.workload_min_nodes
  max_count               = var.workload_max_nodes
  node_count              = var.workload_min_nodes
  max_pods                = 60
  os_disk_type            = "Managed"
  os_disk_size_gb         = 64
  host_encryption_enabled = true
  node_public_ip_enabled  = false
  node_labels             = { "selfheal.lab/nodes" = "workload" }
  node_taints             = ["selfheal.lab/workload=true:NoSchedule"]
  upgrade_settings {
    max_surge = "1"
  }
  tags = local.tags

  lifecycle {
    # The autoscaler owns the count between min and max.
    ignore_changes = [node_count]
  }
}

# --- images -----------------------------------------------------------------

resource "azurerm_container_registry" "lab" {
  #checkov:skip=CKV_AZURE_237:Dedicated data endpoints need Premium; Basic for a lab registry.
  #checkov:skip=CKV_AZURE_233:Zone redundancy needs Premium; Basic for a lab registry.
  #checkov:skip=CKV_AZURE_167:Retention policies need Premium; the registry is destroyed with the lab.
  #checkov:skip=CKV_AZURE_164:Content trust needs Premium; images are built from this repo at a pinned git SHA.
  #checkov:skip=CKV_AZURE_165:One region, one session; no geo-replication.
  #checkov:skip=CKV_AZURE_166:Image quarantine needs Premium; images are built from this repo at a pinned git SHA.
  #checkov:skip=CKV_AZURE_163:Defender registry scanning is left off: only the runtime sensor is enabled (r2.tf).
  #checkov:skip=CKV_AZURE_139:Private endpoints need Premium; pulls authenticate with the kubelet identity, admin user off.
  name                          = "acr${local.name}${random_string.suffix.result}"
  location                      = azurerm_resource_group.lab.location
  resource_group_name           = azurerm_resource_group.lab.name
  sku                           = "Basic"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = true
  tags                          = local.tags
}

# The kubelet identity AKS creates in the node resource group pulls images.
resource "azurerm_role_assignment" "kubelet_acr_pull" {
  scope                = azurerm_container_registry.lab.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.lab.kubelet_identity[0].object_id
  principal_type       = "ServicePrincipal"
}

# --- the operator (whoever runs terraform with az login) ---------------------

resource "azurerm_role_assignment" "operator_cluster_user" {
  scope                = azurerm_kubernetes_cluster.lab.id
  role_definition_name = "Azure Kubernetes Service Cluster User Role"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "operator_cluster_admin" {
  scope                = azurerm_kubernetes_cluster.lab.id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "operator_acr_push" {
  scope                = azurerm_container_registry.lab.id
  role_definition_name = "AcrPush"
  principal_id         = data.azurerm_client_config.current.object_id
}
