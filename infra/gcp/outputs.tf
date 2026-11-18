output "cluster_name" {
  value = google_container_cluster.lab.name
}

output "configure_kubectl" {
  description = "Writes the lab's own kubeconfig through the DNS endpoint (make gke-platform runs it)."
  value       = "KUBECONFIG=.kube/gke gcloud container clusters get-credentials ${google_container_cluster.lab.name} --location ${var.zone} --project ${var.project_id} --dns-endpoint"
}

output "registry" {
  description = "Push target for the lab images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.lab.repository_id}"
}

output "native_routing_cidr" {
  description = "Cilium ipv4NativeRoutingCIDR: the cluster's pod range (Cilium GKE install)."
  value       = google_container_cluster.lab.cluster_ipv4_cidr
}

output "runner" {
  description = "Everything the scripts and the runner need, in one object."
  value = {
    project_id    = var.project_id
    region        = var.region
    zone          = var.zone
    cluster_name  = google_container_cluster.lab.name
    registry      = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.lab.repository_id}"
    workload_pool = google_container_node_pool.workload.name
    pod_cidr      = google_container_cluster.lab.cluster_ipv4_cidr
  }
}
