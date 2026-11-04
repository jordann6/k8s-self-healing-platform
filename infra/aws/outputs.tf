output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "region" {
  description = "AWS region."
  value       = var.region
}

output "configure_kubectl" {
  description = "Writes the lab's own kubeconfig (make eks-platform runs this with LAB_PROFILE)."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name} --kubeconfig .kube/eks --alias selfheal-eks"
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

# --- read by the scripts and the runner ---------------------------------------------
output "db_endpoint" {
  description = "PostgreSQL host for row S1b."
  value       = aws_db_instance.this.address
}

output "db_master_secret_arn" {
  description = "RDS-managed master secret (bootstrap only)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "db_app_secret_arn" {
  description = "Row S1b's rotated application secret."
  value       = aws_secretsmanager_secret.db_app.arn
}

output "rotation_function_arn" {
  description = "Alternating-users rotation function."
  value       = aws_lambda_function.rotation.arn
}

output "runner" {
  description = "Everything the runner needs for the AWS-native rows, in one object."
  value = {
    partner_secret       = aws_secretsmanager_secret.partner.name
    db_app_secret        = aws_secretsmanager_secret.db_app.name
    fis_template_id      = aws_fis_experiment_template.node_kill.id
    prober_alarm         = aws_cloudwatch_metric_alarm.prober_errors.alarm_name
    prober_metric_ns     = local.prober_metric_namespace
    config_rule          = aws_config_config_rule.sg_open.name
    remediation_document = aws_ssm_document.revoke_public_ingress.name
    exposure_sg          = aws_security_group.exposure.id
    quarantine_sg        = aws_security_group.quarantine.id
    findings_rule        = aws_cloudwatch_event_rule.runtime_findings.name
    responder_arn        = aws_sfn_state_machine.responder.arn
    evidence_bucket      = module.evidence_bucket.s3_bucket_id
  }
}
