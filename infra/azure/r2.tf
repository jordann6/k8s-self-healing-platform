# Row R2: a Defender for Containers alert on a web pod, answered without a
# human (ADR-0007 row 5, decisions D4 and D6).
#
# The path: Defender alert -> workflow automation -> Logic App (Consumption;
# the App Service quota is 0) -> the "incidents" queue -> the in-cluster
# responder (workload identity) cordons the node THEN relabels the pod, and
# writes isolated.json to the evidence container -> the Logic App does the
# Azure-side steps through ARM with its own identity: Run Command capture on
# the scale set instance, an incremental OS-disk snapshot into the lab group,
# and the evidence manifest.
#
# This file holds the parts that do not depend on how the alert reaches the
# Logic App: the Defender plan, the evidence account, and the roles.

# --- Defender for Containers -------------------------------------------------

# A subscription-wide plan. Destroying this resource sets Containers back to
# Free; the sensor already deployed to the cluster is removed with the
# cluster (ADR-0007 row 11), and verify-gone checks both.
#
# Only the runtime sensor: the agentless discovery and registry scanning
# extensions bill and act outside this lab.
resource "azurerm_security_center_subscription_pricing" "containers" {
  tier          = var.defender_enabled ? "Standard" : "Free"
  resource_type = "Containers"

  dynamic "extension" {
    for_each = var.defender_enabled ? ["ContainerSensor"] : []
    content {
      name = extension.value
    }
  }
}

# --- evidence ----------------------------------------------------------------

# Entra ID only (no shared keys, no SAS signed with them), no anonymous
# access, TLS 1.2. The public endpoint stays reachable, but its firewall
# (below) admits only the lab: a Consumption Logic App has no VNet.
resource "azurerm_storage_account" "evidence" {
  #checkov:skip=CKV_AZURE_33:Queue classic logging needs shared-key data plane calls; shared keys are disabled.
  #checkov:skip=CKV_AZURE_59:The public endpoint is firewalled to the lab (azurerm_storage_account_network_rules.evidence); a Consumption Logic App has no VNet.
  #checkov:skip=CKV_AZURE_206:LRS: evidence lives for one session and is copied into the repo redacted.
  #checkov:skip=CKV2_AZURE_33:No private endpoint: a Consumption Logic App cannot use one; firewall plus Entra-only auth instead.
  #checkov:skip=CKV2_AZURE_1:Microsoft-managed keys; no customer-managed key in this lab (ADR-0007 row 3).
  name                     = "st${local.name}${random_string.suffix.result}"
  location                 = azurerm_resource_group.lab.location
  resource_group_name      = azurerm_resource_group.lab.name
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
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  tags = local.tags
}

# Deny by default. Admitted: the node subnet (the responder), the operator's
# address (the runner reads evidence), and the responder Logic App by a
# resource instance rule (Microsoft.Logic/workflows is on the trusted access
# list). A separate resource so the Logic App, which needs the account's
# endpoints, can exist first.
# No trusted-services bypass on purpose: the one Azure service that needs in
# (the Logic App) is admitted by its own instance rule, not by service type.
#trivy:ignore:AVD-AZU-0010
resource "azurerm_storage_account_network_rules" "evidence" {
  #checkov:skip=CKV_AZURE_36:No trusted-services bypass on purpose; the Logic App is admitted by its own instance rule.
  storage_account_id         = azurerm_storage_account.evidence.id
  default_action             = "Deny"
  bypass                     = ["None"]
  ip_rules                   = local.operator_ip_rules
  virtual_network_subnet_ids = [azurerm_subnet.nodes.id]

  private_link_access {
    endpoint_resource_id = azapi_resource.responder.id
    endpoint_tenant_id   = data.azurerm_client_config.current.tenant_id
  }
}

resource "azurerm_storage_queue" "incidents" {
  name               = "incidents"
  storage_account_id = azurerm_storage_account.evidence.id
}

resource "azurerm_storage_container" "evidence" {
  #checkov:skip=CKV2_AZURE_21:Classic blob logging needs shared-key data plane calls; shared keys are disabled.
  name                  = "evidence"
  storage_account_id    = azurerm_storage_account.evidence.id
  container_access_type = "private"
}

locals {
  # Data-plane roles are scoped to the one queue and the one container, not
  # the account.
  incidents_queue_scope    = "${azurerm_storage_account.evidence.id}/queueServices/default/queues/${azurerm_storage_queue.incidents.name}"
  evidence_container_scope = "${azurerm_storage_account.evidence.id}/blobServices/default/containers/${azurerm_storage_container.evidence.name}"
}

# The in-cluster responder takes incidents off the queue and writes
# isolated.json.
resource "azurerm_role_assignment" "responder_queue" {
  scope                = local.incidents_queue_scope
  role_definition_name = "Storage Queue Data Message Processor"
  principal_id         = azurerm_user_assigned_identity.workload["responder"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "responder_evidence" {
  scope                = local.evidence_container_scope
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.workload["responder"].principal_id
  principal_type       = "ServicePrincipal"
}

# The runner reads the evidence back to judge the row.
resource "azurerm_role_assignment" "operator_evidence" {
  scope                = local.evidence_container_scope
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = data.azurerm_client_config.current.object_id
}

# --- the Logic App's Azure-side permissions ----------------------------------

# The least the Azure-side steps need: read the lab group's Defender alerts
# (the workflow trusts ARM's copy of an alert, never the posted body), find
# the instance, run the capture script on it, read its OS disk, write a
# snapshot of it into the lab group, and poll the long-running operations.
# No disk export (beginGetAccess), no VM write. Every action name is on the
# Learn resource provider operations pages (compute, security).
#
# A role definition is a subscription-level resource ("a role definition is a
# management group or subscription-level resource", Learn, Azure custom
# roles), so it is created at the subscription; it can only be ASSIGNED in
# the two lab groups. Defined after the cluster so the node group exists.
resource "azurerm_role_definition" "responder_arm" {
  name        = "${local.name}-r2-responder-${random_string.suffix.result}"
  scope       = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  description = "Self-healing lab row R2: read lab alerts, Run Command capture and OS-disk snapshot of one node."

  permissions {
    actions = [
      "Microsoft.Compute/virtualMachineScaleSets/read",
      "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read",
      "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/runCommand/action",
      "Microsoft.Compute/disks/read",
      "Microsoft.Compute/snapshots/read",
      "Microsoft.Compute/snapshots/write",
      "Microsoft.Compute/locations/operations/read",
      "Microsoft.Compute/locations/diskOperations/read",
      "Microsoft.Security/alerts/read",
      "Microsoft.Security/locations/alerts/read",
    ]
    not_actions = []
  }

  assignable_scopes = [
    azurerm_resource_group.lab.id,
    azurerm_kubernetes_cluster.lab.node_resource_group_id,
  ]
}

# --- the Logic App (Consumption) ----------------------------------------------

locals {
  # Pinned API versions the workflow calls. Storage needs 2017-11-09 or later
  # for Entra ID auth.
  workflow_api = {
    storage = "2023-11-03"
    compute = "2024-07-01"
    disk    = "2023-10-02"
    alerts  = "2022-01-01"
  }
}

# azurerm's Logic App resource cannot hold a whole definition (each action is
# its own resource), so the workflow is created through the ARM API with
# azapi. The definition is reviewed in responder/workflow.json.tftpl.
resource "azapi_resource" "responder" {
  type      = "Microsoft.Logic/workflows@2019-05-01"
  name      = "logic-${local.name}-r2"
  parent_id = azurerm_resource_group.lab.id
  location  = azurerm_resource_group.lab.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      state = "Enabled"
      definition = jsondecode(templatefile("${path.module}/responder/workflow.json.tftpl", {
        cluster_id_lower     = lower(azurerm_kubernetes_cluster.lab.id)
        cluster_name         = local.name
        lab_namespace        = "selfheal"
        subscription_id      = data.azurerm_client_config.current.subscription_id
        lab_resource_group   = azurerm_resource_group.lab.name
        node_resource_group  = azurerm_kubernetes_cluster.lab.node_resource_group
        location             = azurerm_resource_group.lab.location
        arm                  = "https://management.azure.com"
        blob_container_url   = "${azurerm_storage_account.evidence.primary_blob_endpoint}${azurerm_storage_container.evidence.name}"
        queue_url            = "${azurerm_storage_account.evidence.primary_queue_endpoint}${azurerm_storage_queue.incidents.name}"
        storage_api_version  = local.workflow_api.storage
        compute_api_version  = local.workflow_api.compute
        disk_api_version     = local.workflow_api.disk
        alerts_api_version   = local.workflow_api.alerts
        incident_ttl_seconds = 900
        # Covers the worker's three attempts on a 120s queue visibility timeout
        # with margin; R2 is still judged on isolation within 300s.
        isolation_wait_minutes = 8
        # ARM's alert list can lag the automation's delivery (the AWS
        # ListFindings lesson, ADR-0007 row 5): poll before refusing.
        alert_lookup_minutes = 10
        snapshot_tags        = jsonencode(merge(local.tags, { "selfheal.lab/evidence" = "source" }))
      }))
      parameters = {}
    }
  }
}

# The trigger's callback URL carries its own signature: sensitive, kept only
# in the (local) state and handed to the workflow automation.
resource "azapi_resource_action" "responder_callback" {
  type                             = "Microsoft.Logic/workflows/triggers@2019-05-01"
  resource_id                      = "${azapi_resource.responder.id}/triggers/manual"
  action                           = "listCallbackUrl"
  method                           = "POST"
  sensitive_response_export_values = ["value"]
}

locals {
  responder_principal_id = azapi_resource.responder.identity[0].principal_id
}

resource "azurerm_role_assignment" "logicapp_queue" {
  scope                = local.incidents_queue_scope
  role_definition_name = "Storage Queue Data Message Sender"
  principal_id         = local.responder_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "logicapp_evidence" {
  scope                = local.evidence_container_scope
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = local.responder_principal_id
  principal_type       = "ServicePrincipal"
}

# Read the instance and run the capture in the node group; write the
# snapshot in the lab group (where Terraform's destroy fails closed if one
# is left behind).
resource "azurerm_role_assignment" "logicapp_nodes" {
  scope              = azurerm_kubernetes_cluster.lab.node_resource_group_id
  role_definition_id = azurerm_role_definition.responder_arm.role_definition_resource_id
  principal_id       = local.responder_principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "logicapp_snapshots" {
  scope              = azurerm_resource_group.lab.id
  role_definition_id = azurerm_role_definition.responder_arm.role_definition_resource_id
  principal_id       = local.responder_principal_id
  principal_type     = "ServicePrincipal"
}

# --- Defender alert -> Logic App ----------------------------------------------

# Scoped to the lab group, so only alerts on lab resources (the cluster) are
# sent; the workflow then refuses anything not naming the lab cluster, and
# the in-cluster responder refuses anything not a web pod in selfheal.
#
# enabled is the baseline/hardened switch for this row (ADR-0001 Azure
# addendum): the runner turns it off for the baseline run and on for the
# hardened run, so Terraform ignores it after create.
resource "azurerm_security_center_automation" "r2" {
  name                = "auto-${local.name}-r2"
  location            = azurerm_resource_group.lab.location
  resource_group_name = azurerm_resource_group.lab.name
  description         = "Self-healing lab row R2: Defender for Containers alerts on the lab cluster to the responder Logic App."
  scopes              = [azurerm_resource_group.lab.id]
  enabled             = true
  tags                = local.tags

  action {
    type        = "logicapp"
    resource_id = azapi_resource.responder.id
    trigger_url = azapi_resource_action.responder_callback.sensitive_output.value
  }

  source {
    event_source = "Alerts"
  }

  lifecycle {
    ignore_changes = [enabled]
  }

  depends_on = [
    azurerm_role_assignment.logicapp_queue,
    azurerm_role_assignment.logicapp_evidence,
    azurerm_role_assignment.logicapp_nodes,
    azurerm_role_assignment.logicapp_snapshots,
  ]
}
