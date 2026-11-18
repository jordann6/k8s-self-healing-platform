# The lab's own VPC (ADR-0027 row 2): no Shared VPC, so no lien can block the
# teardown. Nodes are private; Cloud NAT is their only way out, for the chart
# images on quay.io, ghcr.io and docker.io (live check L8).

resource "google_compute_network" "lab" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "nodes" {
  name          = "${var.cluster_name}-nodes"
  network       = google_compute_network.lab.id
  region        = var.region
  ip_cidr_range = var.node_cidr

  # Nodes reach Artifact Registry and the Google APIs without NAT.
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pod_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.service_cidr
  }

  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.1
    metadata             = "EXCLUDE_ALL_METADATA"
  }
}

resource "google_compute_router" "lab" {
  name    = "${var.cluster_name}-router"
  network = google_compute_network.lab.id
  region  = var.region
}

resource "google_compute_router_nat" "lab" {
  name                               = "${var.cluster_name}-nat"
  router                             = google_compute_router.lab.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.nodes.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# The VPC's implied ingress rule already denies everything; this states it as
# a logged rule the lab owns. GKE adds its own higher-priority allows (node to
# control plane, intra-cluster) at create and deletes them with the cluster.
resource "google_compute_firewall" "deny_all_ingress" {
  name      = "${var.cluster_name}-deny-all-ingress"
  network   = google_compute_network.lab.id
  direction = "INGRESS"
  priority  = 65534

  deny {
    protocol = "all"
  }

  source_ranges = ["0.0.0.0/0"]

  log_config {
    metadata = "EXCLUDE_ALL_METADATA"
  }
}
