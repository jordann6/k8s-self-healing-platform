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

variable "node_vm_size" {
  description = "On the landing zone's allowed-size list and its family has quota (ADR-0007; DSv5 quota is 0)."
  type        = string
  default     = "Standard_D2s_v3"
}

variable "system_node_count" {
  type    = number
  default = 2
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
