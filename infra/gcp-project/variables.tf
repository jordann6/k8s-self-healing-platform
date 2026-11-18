variable "org_id" {
  description = "Numeric organization ID. Set in the gitignored lab.auto.tfvars, never committed."
  type        = string
}

variable "billing_account" {
  description = "Billing account ID (XXXXXX-XXXXXX-XXXXXX). Set in the gitignored lab.auto.tfvars, never committed."
  type        = string
}

variable "billing_linked" {
  description = <<-EOT
    True while GKE sessions are planned. Planning with false unlinks billing
    and parks the empty project, which frees the billing link without adding
    a pending-deletion project to the project quota (ADR-0027, D1).
  EOT
  type        = bool
  default     = true
}

variable "project_prefix" {
  description = "Project ID prefix; a random suffix keeps the ID unique."
  type        = string
  default     = "jn-selfheal"
}
