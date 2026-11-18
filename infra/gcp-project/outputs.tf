output "project_id" {
  description = "Copy into infra/gcp/lab.auto.tfvars as project_id."
  value       = google_project.lab.project_id
}

output "billing_linked" {
  description = "Whether the project is linked to billing."
  value       = var.billing_linked
}
