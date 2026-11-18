# GKE Standard, zonal, BYO Cilium on the legacy datapath (ADR-0027, D2). The
# landing zone paved road is carried over control by control (ADR-0027,
# "Cluster hardening"); what is dropped says why.
#
# Two pools, as on EKS and AKS: a system pool that runs the platform, the
# prober and the partner and is never a fault target, and a tainted workload
# pool of four fixed nodes that only the web pods tolerate (D3). A4 acts on
# workload nodes only.
#
# Both pools register with Cilium's agent-not-ready taint, as the Cilium GKE
# install requires: no pod starts on a node until the agent is up and removes
# it. make gke-platform installs Cilium right after this apply.

locals {
  name = var.cluster_name

  cilium_taint = {
    key    = "node.cilium.io/agent-not-ready"
    value  = "true"
    effect = "NO_EXECUTE"
  }
}

resource "google_container_cluster" "lab" {
  # checkov:skip=CKV_GCP_12:NetworkPolicy is enforced by the pinned Cilium (BYO CNI, ADR-0027 D2), not a GKE provider.
  # checkov:skip=CKV_GCP_65:Google Groups for RBAC needs a Cloud Identity directory; the operator is a project IAM principal.
  # checkov:skip=CKV_GCP_24:PodSecurityPolicy was removed in Kubernetes 1.25; admission is Pod Security plus Kyverno.
  # checkov:skip=CKV_GCP_66:Binary Authorization is off in the lab (ADR-0027 hardening table); P1 is out of GKE scope.
  # checkov:skip=CKV_GCP_61:Intranode visibility is dropped; Cilium owns the datapath and Hubble covers flows (ADR-0027).
  # checkov:skip=CKV_GCP_21:Labels come from provider default_labels plus resource_labels.
  name     = local.name
  location = var.zone

  network    = google_compute_network.lab.id
  subnetwork = google_compute_subnetwork.nodes.id

  deletion_protection      = false
  remove_default_node_pool = true
  initial_node_count       = 1
  min_master_version       = var.kubernetes_version

  # cost_center as a cluster label, as the landing zone's custom constraint
  # required, so the cluster still applies if that constraint returns.
  resource_labels = {
    cost_center = "cc-1010"
    environment = "lab"
  }

  release_channel {
    channel = "REGULAR"
  }

  # No upgrade during a session: an upgrade mid-run is an unrecorded fault.
  # The exclusion starts at the plan and lasts two days; it is set at create
  # only, so later plans in the same session do not move it.
  maintenance_policy {
    recurring_window {
      start_time = "2026-01-03T06:00:00Z"
      end_time   = "2026-01-03T12:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA,SU"
    }

    maintenance_exclusion {
      exclusion_name = "lab-session"
      start_time     = plantimestamp()
      end_time       = timeadd(plantimestamp(), "48h")

      exclusion_options {
        scope = "NO_UPGRADES"
      }
    }
  }

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Legacy datapath, GKE network policy off: Cilium is the CNI (D2). Dataplane
  # V2 can only be chosen at create, so this is fixed for the cluster's life.
  datapath_provider = "LEGACY_DATAPATH"

  network_policy {
    enabled  = false
    provider = "PROVIDER_UNSPECIFIED"
  }

  addons_config {
    network_policy_config {
      disabled = true
    }
    # No Ingress in the GKE rows, so no GKE-created load balancer can outlive
    # the cluster (ADR-0027 row 11).
    http_load_balancing {
      disabled = true
    }
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
    master_ipv4_cidr_block  = var.control_plane_cidr

    master_global_access_config {
      enabled = false
    }
  }

  # Operator and runner reach the API only through the DNS endpoint, which
  # IAM authorizes; a public IP change cannot lock the runner out (row 10).
  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
  }

  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled = false
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  dynamic "database_encryption" {
    for_each = var.secrets_encryption_key == null ? [] : [var.secrets_encryption_key]
    content {
      state    = "ENCRYPTED"
      key_name = database_encryption.value
    }
  }

  enable_shielded_nodes = true

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_BASIC"
  }

  # System logs only: workload logs would bill for the prober's request log.
  logging_config {
    enable_components = ["SYSTEM_COMPONENTS"]
  }

  # Managed collection off (row 3): the rows read the in-cluster Prometheus,
  # unchanged from kind.
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus {
      enabled = false
    }
  }

  # The default pool is created and removed at once; it still gets the node
  # identity, so it never runs as the default compute account.
  node_config {
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  lifecycle {
    ignore_changes = [
      node_config,
      maintenance_policy[0].maintenance_exclusion,
    ]
  }

  depends_on = [time_sleep.iam_settle]
}

resource "google_container_node_pool" "system" {
  name       = "system"
  location   = var.zone
  cluster    = google_container_cluster.lab.name
  node_count = var.system_node_count

  node_config {
    machine_type    = var.node_machine_type
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = { "selfheal.lab/nodes" = "system" }

    taint {
      key    = local.cilium_taint.key
      value  = local.cilium_taint.value
      effect = local.cilium_taint.effect
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  lifecycle {
    ignore_changes = [node_config[0].kubelet_config, version]
  }
}

resource "google_container_node_pool" "workload" {
  name       = "workload"
  location   = var.zone
  cluster    = google_container_cluster.lab.name
  node_count = var.workload_node_count

  # No autoscaling block: the count is fixed (D3).

  node_config {
    machine_type    = var.node_machine_type
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = { "selfheal.lab/nodes" = "workload" }

    taint {
      key    = "selfheal.lab/workload"
      value  = "true"
      effect = "NO_SCHEDULE"
    }

    taint {
      key    = local.cilium_taint.key
      value  = local.cilium_taint.value
      effect = local.cilium_taint.effect
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  # Destroy order: the workload pool goes before the system pool, so the
  # platform keeps running while web pods drain.
  depends_on = [google_container_node_pool.system]

  lifecycle {
    ignore_changes = [node_config[0].kubelet_config, version]
  }
}
