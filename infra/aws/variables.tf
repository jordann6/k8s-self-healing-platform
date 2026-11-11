variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment, tagged onto every resource for cost allocation."
  type        = string
  default     = "lab"
}

variable "cost_center" {
  description = "Chargeback code, house format cc-NNNN, stamped on every resource via default_tags so the shared FinOps gate can allocate the spend."
  type        = string
  default     = "cc-1010"

  validation {
    condition     = can(regex("^cc-[0-9]{4}$", var.cost_center))
    error_message = "cost_center must match the house format cc-NNNN (e.g. cc-1010)."
  }
}

variable "lab_account_id" {
  description = "Account that holds the lab. Supplied in lab.auto.tfvars (gitignored), never committed."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.lab_account_id))
    error_message = "lab_account_id must be a 12-digit AWS account ID."
  }
}

variable "lab_role_name" {
  description = "Role assumed in the lab account."
  type        = string
  default     = "OrganizationAccountAccessRole"
}

variable "security_account_id" {
  description = "The organization's delegated GuardDuty administrator. Supplied in lab.auto.tfvars (gitignored)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.security_account_id))
    error_message = "security_account_id must be a 12-digit AWS account ID."
  }
}

variable "security_role_name" {
  description = "Role assumed in the security account. Used for the GuardDuty member feature toggle and nothing else."
  type        = string
  default     = "OrganizationAccountAccessRole"
}

variable "cluster_name" {
  description = "EKS cluster name. Also the prefix for every named resource."
  type        = string
  default     = "selfheal"
}

variable "cluster_version" {
  description = "EKS Kubernetes version. Standard support, supported by Cilium 1.20 and Karpenter 1.15. ADR-0018 decision A runs the remaining rows on 1.36."
  type        = string
  default     = "1.36"
}

variable "cluster_public_access_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach the public EKS API endpoint. The runner drives the
    cluster from the operator's machine, so the endpoint is public but scoped
    to one /32. The default is non-routable so a bare plan fails loudly;
    `make eks-plan` passes the operator's IPv4 /32.
  EOT
  type        = list(string)
  default     = ["192.0.2.1/32"] # TEST-NET-1, non-routable: forces an explicit value

  validation {
    condition     = !contains(var.cluster_public_access_cidrs, "0.0.0.0/0")
    error_message = "Refusing 0.0.0.0/0. Scope the endpoint to your own /32."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the lab VPC."
  type        = string
  default     = "10.30.0.0/16"
}

variable "system_instance_type" {
  description = "Instance type for the two system nodes (platform charts, Karpenter). Workload nodes come from Karpenter."
  type        = string
  default     = "t3.large"
}

variable "runtime_monitoring" {
  description = <<-EOT
    GuardDuty Runtime Monitoring for the lab account, set from the security
    account. Teardown is two applies: first with this false, so GuardDuty
    removes its agent and VPC endpoint while the VPC still exists, then the
    destroy (ADR-0005, docs/runbook-aws.md).
  EOT
  type        = bool
  default     = true
}

variable "forward_findings_from_security" {
  description = <<-EOT
    Fallback for R2. GuardDuty documents finding events in the administrator
    account; if the lab account's own bus does not receive them, this rule in
    the security account forwards the lab's findings to the lab account bus.
    Off by default; the live session checks which path delivers.
  EOT
  type        = bool
  default     = false
}

variable "managed_grafana" {
  description = "Create an Amazon Managed Grafana workspace. Off by default: it bills per active user per month and needs Identity Center sign-in. The in-cluster Grafana reads AMP instead."
  type        = bool
  default     = false
}

variable "db_instance_class" {
  description = "RDS instance class for the S1b rotation target."
  type        = string
  default     = "db.t4g.micro"
}
