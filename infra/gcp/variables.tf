variable "project_id" {
  description = "The lab project from infra/gcp-project. Set in the gitignored lab.auto.tfvars, never committed."
  type        = string
}

variable "region" {
  description = "us-central1, the region the landing zone proofs ran in."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "One zone: a zonal cluster's fee is covered by the GKE free tier credit, a regional one's is not (ADR-0027 row 12)."
  type        = string
  default     = "us-central1-a"
}

variable "cluster_name" {
  description = "GKE cluster name and the prefix of every lab resource."
  type        = string
  default     = "selfheal"
}

variable "kubernetes_version" {
  description = "Same minor as the EKS and AKS runs (ADR-0018 decision A). The preflight checks the REGULAR channel offers it."
  type        = string
  default     = "1.36"
}

variable "node_machine_type" {
  description = "e2-standard-2 (2 vCPU, 8 GB), the shape the AKS run used. Peak 7 nodes plus 1 surge = 16 vCPUs (live check L7)."
  type        = string
  default     = "e2-standard-2"
}

variable "system_node_count" {
  description = "Three, as on AKS: the platform, the prober and the partner. Never a fault target."
  type        = number
  default     = 3
}

variable "workload_node_count" {
  description = <<-EOT
    Four, fixed, autoscaler off (ADR-0027, D3). The GKE cluster autoscaler
    does not support DoNotSchedule topology spread, so A4 must not depend on
    it: one node stops, three domains remain for minDomains 3.
  EOT
  type        = number
  default     = 4

  validation {
    condition     = var.workload_node_count >= 4
    error_message = "A4 needs at least four workload nodes so three domains remain after one stops."
  }
}

variable "secrets_encryption_key" {
  description = "Cloud KMS key for application-layer secrets encryption. Null (off) in the lab, ADR-0027 D5; CMEK is the production answer."
  type        = string
  default     = null
}

variable "node_cidr" {
  type    = string
  default = "10.60.0.0/22"
}

variable "pod_cidr" {
  type    = string
  default = "10.64.0.0/16"
}

variable "service_cidr" {
  type    = string
  default = "10.68.0.0/20"
}

variable "control_plane_cidr" {
  description = "The /28 GKE peers for the private control plane."
  type        = string
  default     = "172.16.60.0/28"
}
