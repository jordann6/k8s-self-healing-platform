output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "region" {
  description = "AWS region."
  value       = var.region
}

output "configure_kubectl" {
  description = "Writes the lab's own kubeconfig (never ~/.kube/config)."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name} --role-arn arn:${local.partition}:iam::${var.lab_account_id}:role/${var.lab_role_name} --alias selfheal-eks"
}

output "ecr_repositories" {
  description = "Push targets for the lab images."
  value       = { for k, r in aws_ecr_repository.lab : k => r.repository_url }
}

output "amp_remote_write_url" {
  description = "Amazon Managed Prometheus remote write endpoint."
  value       = "${aws_prometheus_workspace.this.prometheus_endpoint}api/v1/remote_write"
}

output "amp_query_url" {
  description = "Amazon Managed Prometheus query endpoint (Grafana data source, SigV4)."
  value       = aws_prometheus_workspace.this.prometheus_endpoint
}

output "karpenter" {
  description = "Values the Karpenter chart and EC2NodeClass need."
  value = {
    queue_name     = module.karpenter.queue_name
    node_role_name = module.karpenter.node_iam_role_name
  }
}
