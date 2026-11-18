# The GKE lab's own project (ADR-0027, decision D1). Created once, under the
# organization root (the landing zone folders are being torn down), and kept
# between sessions: deleting it would add another project that counts toward
# the project quota for 30 days. Only APIs and the billing link live here;
# everything that bills by the hour is in infra/gcp and destroyed per session.

resource "random_id" "suffix" {
  byte_length = 3
}

resource "google_project" "lab" {
  # checkov:skip=CKV2_GCP_5:Admin Activity audit logs are always on; Data Access logs for every service would bill on each kubelet and controller read of the GKE API.
  name            = "selfheal-lab"
  project_id      = "${var.project_prefix}-${random_id.suffix.hex}"
  org_id          = var.org_id
  billing_account = var.billing_linked ? var.billing_account : null

  # No default network with its permissive rules (the landing zone enforced
  # compute.skipDefaultNetworkCreation; it is gone, so the project says it).
  auto_create_network = false

  # A destroy of this root must never delete the project by accident.
  deletion_policy = "PREVENT"

  labels = {
    project     = "k8s-self-healing-platform"
    owner       = "jordann6"
    managed-by  = "terraform"
    cost_center = "cc-1010"
  }
}

locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])
}

resource "google_project_service" "lab" {
  for_each = local.services

  project = google_project.lab.project_id
  service = each.value

  # Disabling container or compute on destroy fails while GKE-created
  # resources still exist and takes minutes to settle; the project is kept
  # anyway, so its APIs stay on.
  disable_on_destroy = false
}
