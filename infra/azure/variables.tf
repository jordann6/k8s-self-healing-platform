variable "subscription_id" {
  description = "The lab subscription. Set in the gitignored lab.auto.tfvars, never committed."
  type        = string
}

variable "location" {
  description = "Region. East US 2: on the landing zone's allowed list and AKS 1.34 is offered there (ADR-0007)."
  type        = string
  default     = "eastus2"
}

variable "cluster_name" {
  description = "AKS cluster name and the prefix of every lab resource."
  type        = string
  default     = "selfheal"
}

variable "kubernetes_version" {
  description = "Same minor as the EKS run."
  type        = string
  default     = "1.34"
}

variable "environment" {
  type    = string
  default = "lab"
}

variable "cost_center" {
  type    = string
  default = "cc-1010"
}

variable "system_node_vm_size" {
  description = "D2s_v3 on the DSv3 quota already approved (10); that family can no longer be raised (ADR-0008, D1 revised). The preflight checks 6 free."
  type        = string
  default     = "Standard_D2s_v3"
}

variable "workload_node_vm_size" {
  description = "D2s_v6 on the Dsv6 quota (10); same 2 vCPU and 8 GiB shape. Gen2, NVMe, MANA, no temp disk (ADR-0008, D1 revised). The preflight checks 8 free."
  type        = string
  default     = "Standard_D2s_v6"
}

variable "system_node_count" {
  description = <<-EOT
    Three, from the requests of everything that runs on system nodes
    (rendered pinned charts, 2026-11-04): about 1380m in Deployments
    (platform 585m, lab pods 170m, AKS metrics-server about 306m, CoreDNS,
    konnectivity, Defender misc) plus about 820m per node in DaemonSets with
    the Defender sensor at its 420m limit. Allocatable per 2-vCPU node is 1900m
    (AKS reserves 100m on 2 cores). Two nodes: 3020m of 3800m (79%); three:
    3840m of 5700m (67%). Peak vCPU with A4's fourth workload node: 3x2 + 4x2
    = 14 of the 16 regional vCPUs in decision D1
    (revised: 6 DSv3 + 8 Dsv6, regional limit raised to 16).
  EOT
  type        = number
  default     = 3
}

variable "workload_min_nodes" {
  description = "Three: the hardened spread needs three hostname domains (minDomains = 3)."
  type        = number
  default     = 3
}

variable "workload_max_nodes" {
  description = "Four leaves room for row A4's replacement node. Needs 16 vCPUs of quota (ADR-0007, D1)."
  type        = number
  default     = 4
}

variable "api_authorized_cidrs" {
  description = "Who may reach the API server: the operator's IPv4 /32 (make aks-plan passes it)."
  type        = list(string)

  validation {
    condition     = length(var.api_authorized_cidrs) > 0 && alltrue([for c in var.api_authorized_cidrs : can(cidrhost(c, 0))])
    error_message = "Give at least one CIDR, normally your own IPv4 /32."
  }
}

variable "maintenance_day" {
  description = "Weekly 4-hour window for node image upgrades (UTC). The preflight refuses a session that overlaps it."
  type        = string
  default     = "Sunday"
}

variable "maintenance_start" {
  description = "Start of the weekly window, HH:MM UTC."
  type        = string
  default     = "08:00"
}

variable "defender_enabled" {
  description = "Defender for Containers plan on the subscription (row R2). Destroying the plan resource resets it to Free."
  type        = bool
  default     = true
}
