# Row P1 (ADR-0021): Kyverno verifies image signatures in ECR, so its admission
# and reports controllers need registry read access. Kyverno documents IRSA as
# the path for its `amazon` credential provider, so this is the one place the
# lab uses IRSA (every other workload uses Pod Identity). The EKS module already
# creates the OIDC provider (enable_irsa defaults to true).

locals {
  kyverno_service_accounts = [
    "system:serviceaccount:kyverno:kyverno-admission-controller",
    "system:serviceaccount:kyverno:kyverno-reports-controller",
  ]
  p1_repository_arns = [for k, r in aws_ecr_repository.lab : r.arn if startswith(k, "p1-")]
}

data "aws_iam_policy_document" "kyverno_irsa_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values   = local.kyverno_service_accounts
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kyverno_registry" {
  name               = "${local.name}-kyverno-registry"
  assume_role_policy = data.aws_iam_policy_document.kyverno_irsa_trust.json
}

resource "aws_iam_role_policy" "kyverno_registry" {
  name = "read-p1-repositories"
  role = aws_iam_role.kyverno_registry.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RegistryToken"
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        # The token API does not take a resource.
        Resource = "*"
      },
      {
        Sid    = "ReadP1RepositoriesOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = local.p1_repository_arns
      },
    ]
  })
}
