variable "region" {
  description = "AWS region. Must equal the region of the EKS root."
  type        = string
  default     = "us-east-1"
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

  validation {
    condition     = can(regex("^cc-[0-9]{4}$", var.cost_center))
    error_message = "cost_center must look like cc-1010."
  }
}

variable "lab_account_id" {
  description = "Account that holds the lab. Pass -var-file=../aws/lab.auto.tfvars (gitignored)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.lab_account_id))
    error_message = "lab_account_id must be a 12 digit account ID."
  }
}

variable "lab_role_name" {
  description = "Role assumed in the lab account."
  type        = string
  default     = "OrganizationAccountAccessRole"
}
