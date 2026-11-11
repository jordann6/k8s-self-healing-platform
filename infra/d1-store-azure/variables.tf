variable "subscription_id" {
  description = "The lab subscription. Pass -var-file=../azure/lab.auto.tfvars (gitignored)."
  type        = string
}

variable "location" {
  description = "Region. Must equal the AKS root's region so the node subnet can reach the account."
  type        = string
  default     = "eastus2"
}

variable "environment" {
  description = "Deployment environment, tagged onto every resource."
  type        = string
  default     = "lab"
}

variable "cost_center" {
  description = "Chargeback code, house format cc-NNNN."
  type        = string
  default     = "cc-1010"
}

variable "immutability_state" {
  description = <<-EOT
    State of the account's default version-level policy. Locked is the contract
    (ADR-0022) and cannot be deleted or shortened afterwards. Unlocked exists for
    the case where the provider cannot create the account Locked (live check
    L21): apply Unlocked, then re-apply Locked. Read the plan before applying.
  EOT
  type        = string
  default     = "Locked"

  validation {
    condition     = contains(["Locked", "Unlocked"], var.immutability_state)
    error_message = "immutability_state must be Locked or Unlocked."
  }
}
