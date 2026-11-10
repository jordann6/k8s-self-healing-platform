# Row D1 (ADR-0022). Everything here is off unless the WORM bucket from
# infra/d1-store-aws is named, so a session without D1 changes nothing.
#
#   make d1-store-aws-plan / apply   the bucket, in its own root, first
#   make eks-plan EKS_PLAN_ARGS="-var=d1_store_bucket=<bucket>"   this file

variable "d1_store_bucket" {
  description = "Name of the WORM bucket created by infra/d1-store-aws. Empty disables every D1 resource in this root."
  type        = string
  default     = ""
}

locals {
  d1_enabled = var.d1_store_bucket != ""

  # role key => service account in the D1 namespaces. Both identities read and
  # write the one bucket and can never delete (ADR-0022 decision 5).
  d1_pod_roles = local.d1_enabled ? {
    d1-backup  = { namespace = "selfheal-d1-source", service_account = "d1-backup" }
    d1-restore = { namespace = "selfheal-d1-restore", service_account = "d1-restore" }
  } : {}

  # The EBS CSI add-on (EKS has no driver today), with its own Pod Identity role.
  # The controller tolerates the Cilium startup taint like CoreDNS, or the add-on
  # create would wait for a CNI that is installed after the apply.
  ebs_csi_addon = local.d1_enabled ? {
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi[0].arn
        service_account = "ebs-csi-controller-sa"
      }]
      configuration_values = jsonencode({
        controller = {
          tolerations = [
            { key = "CriticalAddonsOnly", operator = "Exists" },
            { key = "node.cilium.io/agent-not-ready", operator = "Exists", effect = "NoExecute" },
          ]
        }
      })
    }
  } : {}
}

resource "aws_iam_role" "ebs_csi" {
  count = local.d1_enabled ? 1 : 0

  name               = "${local.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  count = local.d1_enabled ? 1 : 0

  role       = aws_iam_role.ebs_csi[0].name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role" "d1" {
  for_each = local.d1_pod_roles

  name               = "${local.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy" "d1" {
  for_each = local.d1_pod_roles

  name = each.key
  role = aws_iam_role.d1[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListTheWormBucket"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:GetBucketObjectLockConfiguration",
          "s3:ListBucket",
          "s3:ListBucketVersions",
        ]
        Resource = "arn:${local.partition}:s3:::${var.d1_store_bucket}"
      },
      {
        # Put, get and read retention. There is deliberately no DeleteObject,
        # DeleteObjectVersion or BypassGovernanceRetention anywhere.
        Sid    = "WriteAndReadBackups"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:GetObjectRetention",
        ]
        Resource = "arn:${local.partition}:s3:::${var.d1_store_bucket}/*"
      },
    ]
  })
}

resource "aws_eks_pod_identity_association" "d1" {
  for_each = local.d1_pod_roles

  cluster_name    = module.eks.cluster_name
  namespace       = each.value.namespace
  service_account = each.value.service_account
  role_arn        = aws_iam_role.d1[each.key].arn
}
