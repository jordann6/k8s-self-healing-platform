# Registries, the lab's data key, Amazon Managed Prometheus, and the Pod
# Identity roles that let in-cluster components reach them. No IRSA, no static
# keys anywhere in the cluster.

# --- KMS --------------------------------------------------------------------------
# One key for lab data at rest (secrets, the database, AMP, ECR). Evidence has
# its own key in responder.tf: a separate custody domain, as in the incident
# forensics pipeline this reuses.
resource "aws_kms_key" "data" {
  description             = "${local.name}: secrets, database, metrics and images at rest"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdministers"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
    ]
  })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

# --- ECR ----------------------------------------------------------------------------
# Immutable tags: `make eks-images` pushes the git short SHA, so a report names
# the exact image it measured. linux/amd64 builds, as the nodes are x86.
resource "aws_ecr_repository" "lab" {
  for_each = toset(["app", "gitserver", "dbprobe", "qworker", "q2", "o1", "q3"])

  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # lets the destroy remove a repository with images in it

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

# --- Amazon Managed Prometheus ---------------------------------------------------------
# The in-cluster Prometheus still evaluates the Sloth rules and the canary
# analysis (the same PromQL as on kind, so the rows are comparable); it
# remote-writes everything here for retention and the Grafana view.
resource "aws_prometheus_workspace" "this" {
  alias       = local.name
  kms_key_arn = aws_kms_key.data.arn
}

# --- Pod Identity ------------------------------------------------------------------------
data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

locals {
  # role key => service account it is bound to, and its inline policy
  pod_roles = {
    external-secrets = {
      namespace       = "external-secrets"
      service_account = "external-secrets"
      statements = [
        {
          Sid      = "ReadLabSecretsOnly"
          Effect   = "Allow"
          Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
          Resource = "arn:${local.partition}:secretsmanager:${var.region}:${local.account_id}:secret:${local.name}/*"
        },
        {
          Sid       = "DecryptThroughSecretsManager"
          Effect    = "Allow"
          Action    = ["kms:Decrypt"]
          Resource  = aws_kms_key.data.arn
          Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com" } }
        },
      ]
    }
    prometheus = {
      namespace       = "monitoring"
      service_account = "kps-prometheus"
      statements = [
        {
          Sid      = "RemoteWrite"
          Effect   = "Allow"
          Action   = ["aps:RemoteWrite"]
          Resource = aws_prometheus_workspace.this.arn
        },
        # The workspace is encrypted with the lab's data key, so AMP needs
        # the caller's KMS permission too (a 403 on kms:GenerateDataKey on
        # every remote write in the first live session without it).
        {
          Sid       = "WorkspaceKeyThroughAmp"
          Effect    = "Allow"
          Action    = ["kms:GenerateDataKey", "kms:Decrypt"]
          Resource  = aws_kms_key.data.arn
          Condition = { StringEquals = { "kms:ViaService" = "aps.${var.region}.amazonaws.com" } }
        },
      ]
    }
    grafana = {
      namespace       = "monitoring"
      service_account = "kps-grafana"
      statements = [
        {
          Sid      = "QueryOnly"
          Effect   = "Allow"
          Action   = ["aps:QueryMetrics", "aps:GetLabels", "aps:GetSeries", "aps:GetMetricMetadata"]
          Resource = aws_prometheus_workspace.this.arn
        },
        {
          Sid       = "WorkspaceKeyThroughAmp"
          Effect    = "Allow"
          Action    = ["kms:Decrypt"]
          Resource  = aws_kms_key.data.arn
          Condition = { StringEquals = { "kms:ViaService" = "aps.${var.region}.amazonaws.com" } }
        },
      ]
    }
  }
}

resource "aws_iam_role" "pod" {
  for_each = local.pod_roles

  name               = "${local.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy" "pod" {
  for_each = local.pod_roles

  name   = each.key
  role   = aws_iam_role.pod[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = each.value.statements })
}

resource "aws_eks_pod_identity_association" "pod" {
  for_each = local.pod_roles

  cluster_name    = module.eks.cluster_name
  namespace       = each.value.namespace
  service_account = each.value.service_account
  role_arn        = aws_iam_role.pod[each.key].arn
}

# --- optional Amazon Managed Grafana ------------------------------------------------------
# Off by default (ADR-0005). When on, it needs IAM Identity Center for sign-in
# and bills each active editor for the month.
resource "aws_iam_role" "grafana_workspace" {
  count = var.managed_grafana ? 1 : 0

  name = "${local.name}-managed-grafana"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "grafana.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "grafana_workspace" {
  count = var.managed_grafana ? 1 : 0

  role       = aws_iam_role.grafana_workspace[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonPrometheusQueryAccess"
}

resource "aws_grafana_workspace" "this" {
  count = var.managed_grafana ? 1 : 0

  name                     = local.name
  account_access_type      = "CURRENT_ACCOUNT"
  authentication_providers = ["AWS_SSO"]
  permission_type          = "SERVICE_MANAGED"
  data_sources             = ["PROMETHEUS"]
  role_arn                 = aws_iam_role.grafana_workspace[0].arn
}
