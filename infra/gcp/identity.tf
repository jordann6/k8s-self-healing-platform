# Node identity: least privilege, never the default compute account
# (landing zone paved road). Pods never see it (GKE_METADATA on every pool).

resource "google_service_account" "nodes" {
  account_id   = "${var.cluster_name}-nodes"
  display_name = "selfheal GKE nodes (least privilege)"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/autoscaling.metricsWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = google_service_account.nodes.member
}

resource "google_artifact_registry_repository" "lab" {
  # checkov:skip=CKV_GCP_84:CMEK is off in the lab (ADR-0027 D5); images are rebuilt from a pinned git SHA every session.
  repository_id = var.cluster_name
  location      = var.region
  format        = "DOCKER"
  description   = "Lab images, pushed at the git SHA by make gke-images."

  docker_config {
    immutable_tags = true
  }
}

resource "google_artifact_registry_repository_iam_member" "nodes_pull" {
  project    = var.project_id
  location   = google_artifact_registry_repository.lab.location
  repository = google_artifact_registry_repository.lab.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.nodes.member
}

# IAM changes take "Typically 2 minutes, potentially 7 minutes or longer"
# (ADR-0027 row 13). The pools wait, so the first pull does not race them.
resource "time_sleep" "iam_settle" {
  create_duration = "120s"

  depends_on = [
    google_project_iam_member.nodes,
    google_artifact_registry_repository_iam_member.nodes_pull,
  ]
}
