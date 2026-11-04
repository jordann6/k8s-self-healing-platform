# Row R2: the same attack as R1, answered by AWS-native detection and response.
#
#   GuardDuty Runtime Monitoring (automated agent) -> EventBridge -> Step Functions
#
# The state machine follows the EKS incident response order: isolate, preserve,
# investigate. Eradication (deleting the pod, replacing the node) is left to a
# human with the evidence in hand. Steps:
#
#   1. resolve the finding to one pod, node and instance (refuse to guess)
#   2. relabel the pod into the quarantine CiliumNetworkPolicy (as in R1)
#   3. cordon the node, mark it do-not-disrupt and do-not-repair (no drain)
#   4. termination protection on the instance
#   5. move every ENI to the quarantine security group (host-level isolation,
#      which a NetworkPolicy cannot give)
#   6. EBS snapshots of every volume (point-in-time at the call)
#   7. Run Command captures volatile state over the SSM endpoints, which the
#      quarantine group still allows
#   8. revoke sessions for any Pod Identity role on the pod's service account
#   9. evidence manifest (with the audit log window) to the SSE-KMS bucket
#  10. copy the snapshots under the evidence key (custody), after the manifest

# --- GuardDuty Runtime Monitoring, set from the delegated administrator --------------
data "aws_guardduty_detector" "admin" {
  provider = aws.security
}

resource "aws_guardduty_member_detector_feature" "runtime" {
  provider = aws.security

  detector_id = data.aws_guardduty_detector.admin.id
  account_id  = var.lab_account_id
  name        = "RUNTIME_MONITORING"
  status      = var.runtime_monitoring ? "ENABLED" : "DISABLED"

  # GuardDuty installs and updates the agent add-on, and creates the VPC
  # endpoint it reports through, in every VPC with a monitored cluster.
  additional_configuration {
    name   = "EKS_ADDON_MANAGEMENT"
    status = var.runtime_monitoring ? "ENABLED" : "DISABLED"
  }
}

# --- evidence custody -------------------------------------------------------------------
# A separate key and bucket from the lab's data: access to evidence is governed
# by this key policy, not by whatever encrypted the node's volumes.
resource "aws_kms_key" "evidence" {
  description             = "${local.name}: R2 evidence (manifest, capture, snapshot copies)"
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
      {
        Sid       = "ResponderWritesEvidence"
        Effect    = "Allow"
        Principal = { AWS = [aws_iam_role.responder_fn.arn, aws_iam_role.responder_sfn.arn] }
        Action    = ["kms:Encrypt", "kms:GenerateDataKey*", "kms:ReEncrypt*", "kms:Decrypt", "kms:DescribeKey"]
        Resource  = "*"
      },
      {
        # CopySnapshot with a customer key needs a grant so EBS can use the key
        # on the state machine's behalf. Grants only for AWS resources.
        Sid       = "SnapshotCopyGrants"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.responder_sfn.arn }
        Action    = ["kms:CreateGrant", "kms:ListGrants", "kms:RevokeGrant"]
        Resource  = "*"
        Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" } }
      },
    ]
  })
}

resource "aws_kms_alias" "evidence" {
  name          = "alias/${local.name}-evidence"
  target_key_id = aws_kms_key.evidence.key_id
}

module "evidence_bucket" {
  source = "./modules/lab-bucket"

  name        = "${local.name}-evidence-${local.account_id}"
  kms_key_arn = aws_kms_key.evidence.arn
  purpose     = "R2 incident evidence"
}

# --- responder function ----------------------------------------------------------------
data "archive_file" "responder" {
  type             = "zip"
  source_file      = "${path.module}/responder/handler.py"
  output_path      = "${path.module}/build/responder.zip"
  output_file_mode = "0644"
}

# HTTPS egress is open because the function calls IAM (no regional VPC
# endpoint), EKS, Logs, SSM and S3 APIs as well as the cluster's private endpoint.
#trivy:ignore:AVD-AWS-0104
resource "aws_security_group" "responder" {
  name        = "${local.name}-responder"
  description = "R2 responder function: HTTPS to the EKS API and AWS APIs"
  vpc_id      = module.vpc.vpc_id

  egress {
    #checkov:skip=CKV_AWS_382:The responder calls the EKS private endpoint and public AWS APIs (EKS, IAM, Logs, SSM, S3) through the NAT gateway.
    description = "HTTPS to the EKS private endpoint and AWS APIs"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-responder" }
}

# The EKS control plane admits the responder on 443 (private endpoint).
resource "aws_vpc_security_group_ingress_rule" "cluster_from_responder" {
  security_group_id            = module.eks.cluster_security_group_id
  referenced_security_group_id = aws_security_group.responder.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "EKS API from the R2 responder function"
}

resource "aws_cloudwatch_log_group" "responder_fn" {
  #checkov:skip=CKV_AWS_158:Logs carry step names and resource ids, no secrets; the default CloudWatch encryption applies.
  #checkov:skip=CKV_AWS_338:14 days covers a deploy, demo, destroy session.
  name              = "/aws/lambda/${local.name}-responder"
  retention_in_days = 14
}

resource "aws_iam_role" "responder_fn" {
  name               = "${local.name}-responder-fn"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "responder_fn" {
  #checkov:skip=CKV_AWS_290:The only unscoped writes are the Lambda VPC ENI actions, which do not support resource-level scoping on create.
  #checkov:skip=CKV_AWS_355:Same ENI actions plus ssm:GetCommandInvocation, neither supports resource-level scoping.
  name = "r2-responder"
  role = aws_iam_role.responder_fn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.responder_fn.arn}:*"
      },
      {
        Sid      = "ReadTheAuditWindow"
        Effect   = "Allow"
        Action   = ["logs:FilterLogEvents"]
        Resource = "arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/aws/eks/${local.name}/cluster:*"
      },
      {
        Sid      = "WriteEvidence"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.evidence_bucket.s3_bucket_arn}/evidence/*"
      },
      {
        Sid      = "ReadCaptureOutput"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = "*" # GetCommandInvocation does not support resource-level scoping.
      },
      {
        Sid      = "FindPodIdentity"
        Effect   = "Allow"
        Action   = ["eks:ListPodIdentityAssociations", "eks:DescribePodIdentityAssociation"]
        Resource = ["arn:${local.partition}:eks:${var.region}:${local.account_id}:cluster/${local.name}", "arn:${local.partition}:eks:${var.region}:${local.account_id}:podidentityassociation/${local.name}/*"]
      },
      {
        # Revocation can only touch workload roles under this path; the
        # function checks the path too.
        Sid      = "RevokeWorkloadSessions"
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:PutRolePolicy"]
        Resource = "arn:${local.partition}:iam::${local.account_id}:role/selfheal-workload/*"
      },
      {
        Sid      = "ReadRoles"
        Effect   = "Allow"
        Action   = ["iam:GetRole"]
        Resource = "arn:${local.partition}:iam::${local.account_id}:role/*"
      },
      {
        Sid      = "AttachToTheVpc"
        Effect   = "Allow"
        Action   = ["ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"]
        Resource = "*" # Lambda's VPC ENI actions do not support resource-level scoping on create.
      },
    ]
  })
}

resource "aws_lambda_function" "responder" {
  #checkov:skip=CKV_AWS_115:The account Lambda concurrency quota is 10; a reservation would drop unreserved concurrency below the minimum.
  #checkov:skip=CKV_AWS_116:Step Functions is the only caller; it retries and records failures.
  #checkov:skip=CKV_AWS_272:Code signing adds a signing pipeline for one function in a lab.
  #checkov:skip=CKV_AWS_173:The environment holds the cluster endpoint, its public CA and resource names, no secrets.
  function_name    = "${local.name}-responder"
  description      = "R2: resolve a GuardDuty runtime finding, isolate the pod, cordon the node, revoke, write evidence"
  role             = aws_iam_role.responder_fn.arn
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  handler          = "handler.handler"
  filename         = data.archive_file.responder.output_path
  source_code_hash = data.archive_file.responder.output_base64sha256
  timeout          = 60
  memory_size      = 256

  tracing_config {
    mode = "Active"
  }

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.responder.id]
  }

  environment {
    variables = {
      CLUSTER_NAME        = module.eks.cluster_name
      CLUSTER_ENDPOINT    = module.eks.cluster_endpoint
      CLUSTER_CA          = module.eks.cluster_certificate_authority_data
      LAB_NAMESPACE       = "selfheal"
      EVIDENCE_BUCKET     = module.evidence_bucket.s3_bucket_id
      EVIDENCE_KMS_ARN    = aws_kms_key.evidence.arn
      REVOCABLE_ROLE_PATH = "/selfheal-workload/"
    }
  }

  depends_on = [aws_cloudwatch_log_group.responder_fn, aws_iam_role_policy.responder_fn]
}

# Kubernetes identity: a group the cluster binds to a narrow ClusterRole
# (cluster/rbac-responder.yaml): get/list/patch pods in the lab namespace,
# get/patch nodes. Nothing else.
resource "aws_eks_access_entry" "responder" {
  cluster_name      = module.eks.cluster_name
  principal_arn     = aws_iam_role.responder_fn.arn
  kubernetes_groups = ["selfheal:responder"]
  type              = "STANDARD"
}

# --- state machine ------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "responder_sfn" {
  #checkov:skip=CKV_AWS_158:Execution logs carry resource ids and step results, no secrets; the default CloudWatch encryption applies.
  #checkov:skip=CKV_AWS_338:14 days covers a deploy, demo, destroy session.
  name              = "/aws/vendedlogs/states/${local.name}-responder"
  retention_in_days = 14
}

resource "aws_iam_role" "responder_sfn" {
  name = "${local.name}-responder-sfn"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "responder_sfn" {
  name = "r2-state-machine"
  role = aws_iam_role.responder_sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeResponder"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [aws_lambda_function.responder.arn, "${aws_lambda_function.responder.arn}:*"]
      },
      {
        Sid      = "Describe"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeSnapshots", "ec2:DescribeNetworkInterfaces"]
        Resource = "*" # Describe* calls do not support resource-level scoping.
      },
      {
        # Containment actions only on lab-managed instances and their ENIs.
        Sid       = "ContainLabInstances"
        Effect    = "Allow"
        Action    = ["ec2:ModifyInstanceAttribute", "ec2:CreateSnapshots"]
        Resource  = "arn:${local.partition}:ec2:${var.region}:${local.account_id}:instance/*"
        Condition = { StringEquals = { "aws:ResourceTag/selfheal.lab/managed" = "true" } }
      },
      {
        Sid      = "SnapshotVolumes"
        Effect   = "Allow"
        Action   = ["ec2:CreateSnapshots", "ec2:CreateSnapshot", "ec2:CreateTags", "ec2:CopySnapshot"]
        Resource = ["arn:${local.partition}:ec2:${var.region}:${local.account_id}:volume/*", "arn:${local.partition}:ec2:${var.region}::snapshot/*"]
      },
      {
        Sid      = "SwapToQuarantineGroup"
        Effect   = "Allow"
        Action   = ["ec2:ModifyNetworkInterfaceAttribute"]
        Resource = ["arn:${local.partition}:ec2:${var.region}:${local.account_id}:network-interface/*", aws_security_group.quarantine.arn, "arn:${local.partition}:ec2:${var.region}:${local.account_id}:security-group/*"]
      },
      {
        Sid       = "CaptureOnLabNodes"
        Effect    = "Allow"
        Action    = ["ssm:SendCommand"]
        Resource  = "arn:${local.partition}:ec2:${var.region}:${local.account_id}:instance/*"
        Condition = { StringEquals = { "aws:ResourceTag/selfheal.lab/managed" = "true" } }
      },
      {
        Sid      = "CaptureDocument"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = "arn:${local.partition}:ssm:${var.region}::document/AWS-RunShellScript"
      },
      {
        Sid      = "CaptureStatus"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = "*" # GetCommandInvocation does not support resource-level scoping.
      },
      {
        # Reading the source snapshots' default EBS key, through EC2 only.
        Sid       = "ReadSourceSnapshots"
        Effect    = "Allow"
        Action    = ["kms:Decrypt", "kms:DescribeKey", "kms:CreateGrant", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncrypt*"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ec2.${var.region}.amazonaws.com" } }
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
        Resource = "*" # Log delivery setup calls do not support resource-level scoping.
      },
      {
        Sid      = "Tracing"
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
        Resource = "*" # X-Ray write calls do not support resource-level scoping.
      },
    ]
  })
}

resource "aws_sfn_state_machine" "responder" {
  name     = "${local.name}-responder"
  role_arn = aws_iam_role.responder_sfn.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.responder_sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  tracing_configuration {
    enabled = true
  }

  definition = templatefile("${path.module}/responder/state-machine.json.tftpl", {
    function_arn    = aws_lambda_function.responder.arn
    quarantine_sg   = aws_security_group.quarantine.id
    evidence_kms    = aws_kms_key.evidence.arn
    lab_tag_key     = "selfheal.lab/managed"
    capture_timeout = 120
    region          = var.region
  })
}

# --- EventBridge routing ---------------------------------------------------------------------
# state is the baseline/hardened switch for this row (ADR-0001 addendum): the
# runner disables the rule for the baseline run and enables it for the
# hardened run, so Terraform ignores it after create.
resource "aws_cloudwatch_event_rule" "runtime_findings" {
  name        = "${local.name}-guardduty-runtime"
  description = "GuardDuty Runtime Monitoring findings (medium and above) for the lab cluster"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    detail = {
      severity = [{ numeric = [">=", 4] }]
      resource = {
        resourceType      = ["EKSCluster"]
        eksClusterDetails = { name = [local.name] }
      }
    }
  })

  lifecycle {
    ignore_changes = [state]
  }
}

resource "aws_iam_role" "events" {
  name = "${local.name}-events"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "events" {
  name = "start-responder"
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "states:StartExecution", Resource = aws_sfn_state_machine.responder.arn }]
  })
}

resource "aws_cloudwatch_event_target" "responder" {
  rule     = aws_cloudwatch_event_rule.runtime_findings.name
  arn      = aws_sfn_state_machine.responder.arn
  role_arn = aws_iam_role.events.arn
}

# --- fallback: forward the lab's findings from the administrator account ------------------------
# Off by default (variables.tf). The lab account's default bus admits the
# security account, and a rule there forwards only this lab's findings.
resource "aws_cloudwatch_event_bus_policy" "from_security" {
  count          = var.forward_findings_from_security ? 1 : 0
  event_bus_name = "default"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "SecurityAccountForwardsLabFindings"
      Effect    = "Allow"
      Principal = { AWS = "arn:${local.partition}:iam::${var.security_account_id}:root" }
      Action    = "events:PutEvents"
      Resource  = "arn:${local.partition}:events:${var.region}:${local.account_id}:event-bus/default"
    }]
  })
}

resource "aws_iam_role" "forwarder" {
  count    = var.forward_findings_from_security ? 1 : 0
  provider = aws.security

  name = "${local.name}-forward-findings"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.security_account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "forwarder" {
  count    = var.forward_findings_from_security ? 1 : 0
  provider = aws.security

  name = "put-to-lab-bus"
  role = aws_iam_role.forwarder[0].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "events:PutEvents", Resource = "arn:${local.partition}:events:${var.region}:${local.account_id}:event-bus/default" }]
  })
}

resource "aws_cloudwatch_event_rule" "forward" {
  count    = var.forward_findings_from_security ? 1 : 0
  provider = aws.security

  name        = "${local.name}-forward-lab-findings"
  description = "Forward the lab account's EKS runtime findings to the lab account's bus"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
    detail = {
      accountId = [var.lab_account_id]
      resource  = { eksClusterDetails = { name = [local.name] } }
    }
  })
}

resource "aws_cloudwatch_event_target" "forward" {
  count    = var.forward_findings_from_security ? 1 : 0
  provider = aws.security

  rule     = aws_cloudwatch_event_rule.forward[0].name
  arn      = "arn:${local.partition}:events:${var.region}:${local.account_id}:event-bus/default"
  role_arn = aws_iam_role.forwarder[0].arn

  depends_on = [aws_cloudwatch_event_bus_policy.from_security]
}
