# Row S4: a security group opened to the internet is closed again without a
# human. Detective, on purpose: production puts prevention first (an SCP or
# a deny policy), and the README says so. This row proves the detective layer.
#
#   AWS Config  managed rule vpc-sg-open-only-to-authorized-ports, scoped by
#               tag to the lab's own groups, evaluated on every change
#   SSM         a CUSTOM Automation document. The managed
#               AWS-DisablePublicAccessForSecurityGroup only revokes 22 and
#               3389; this one revokes 0.0.0.0/0 and ::/0 on any port, and
#               refuses any group without the lab tag.
#
# One Config recorder per account and region. The lab account had none when
# this was written (the landing zone records in its security account), so the
# lab owns one, records security groups only, and removes it on destroy.
# `make eks-plan` fails early if another recorder appears (scripts/eks-preflight.sh).

resource "aws_iam_role" "config" {
  name = "${local.name}-config"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "config.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "config" {
  role       = aws_iam_role.config.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWS_ConfigRole"
}

resource "aws_iam_role_policy" "config_delivery" {
  name = "deliver-to-lab-bucket"
  role = aws_iam_role.config.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteSnapshotsAndHistory"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetBucketAcl"]
        Resource = [module.config_bucket.s3_bucket_arn, "${module.config_bucket.s3_bucket_arn}/*"]
      },
      {
        Sid      = "EncryptDeliveries"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = aws_kms_key.data.arn
      },
    ]
  })
}

module "config_bucket" {
  source = "./modules/lab-bucket"

  name        = "${local.name}-config-${local.account_id}"
  kms_key_arn = aws_kms_key.data.arn
  purpose     = "AWS Config delivery for the lab recorder"
}

resource "aws_config_configuration_recorder" "this" {
  #checkov:skip=CKV2_AWS_48:Scoped to security groups on purpose: the lab needs one rule, and the organization records everything in its security account.
  name     = "${local.name}-recorder"
  role_arn = aws_iam_role.config.arn

  recording_group {
    all_supported                 = false
    include_global_resource_types = false
    resource_types                = ["AWS::EC2::SecurityGroup"]
  }

  recording_mode {
    recording_frequency = "CONTINUOUS"
  }
}

resource "aws_config_delivery_channel" "this" {
  name           = "${local.name}-delivery"
  s3_bucket_name = module.config_bucket.s3_bucket_id
  s3_kms_key_arn = aws_kms_key.data.arn

  depends_on = [aws_config_configuration_recorder.this, aws_iam_role_policy.config_delivery]
}

resource "aws_config_configuration_recorder_status" "this" {
  #checkov:skip=CKV2_AWS_45:Same scoping as the recorder above: security groups only.
  name       = aws_config_configuration_recorder.this.name
  is_enabled = true

  depends_on = [aws_config_delivery_channel.this]
}

# No authorizedTcpPorts: any inbound rule from 0.0.0.0/0 or ::/0 is
# non-compliant. Scoped by tag so the rule (and with it the automatic
# remediation) can only ever see the lab's own groups.
resource "aws_config_config_rule" "sg_open" {
  name        = "${local.name}-sg-open-to-internet"
  description = "Lab security groups must not allow inbound traffic from 0.0.0.0/0 or ::/0 on any port"

  source {
    owner             = "AWS"
    source_identifier = "VPC_SG_OPEN_ONLY_TO_AUTHORIZED_PORTS"
  }

  # Tag only: Config rejects a scope that names both resource types and a tag
  # (found on the first apply). The managed rule only evaluates security
  # groups, and the recorder only records them.
  scope {
    tag_key   = "selfheal.lab/managed"
    tag_value = "true"
  }

  depends_on = [aws_config_configuration_recorder_status.this]
}

resource "aws_ssm_document" "revoke_public_ingress" {
  name            = "${local.name}-RevokePublicIngress"
  document_type   = "Automation"
  document_format = "YAML"
  content         = file("${path.module}/ssm/revoke-public-ingress.yaml")
}

resource "aws_iam_role" "remediation" {
  name = "${local.name}-remediation"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ssm.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "remediation" {
  name = "revoke-public-ingress-on-lab-groups"
  role = aws_iam_role.remediation.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadGroups"
        Effect   = "Allow"
        Action   = ["ec2:DescribeSecurityGroups", "ec2:DescribeSecurityGroupRules"]
        Resource = "*" # Describe* calls do not support resource-level scoping.
      },
      {
        # The document checks the tag too; IAM enforces it independently.
        Sid       = "RevokeOnLabGroupsOnly"
        Effect    = "Allow"
        Action    = ["ec2:RevokeSecurityGroupIngress"]
        Resource  = "arn:${local.partition}:ec2:${var.region}:${local.account_id}:security-group/*"
        Condition = { StringEquals = { "aws:ResourceTag/selfheal.lab/managed" = "true" } }
      },
    ]
  })
}

# automatic is the baseline/hardened switch for this row (ADR-0001 addendum):
# the runner sets it false for the baseline run and true for the hardened run,
# so Terraform ignores it after create.
resource "aws_config_remediation_configuration" "sg_open" {
  config_rule_name = aws_config_config_rule.sg_open.name
  target_type      = "SSM_DOCUMENT"
  target_id        = aws_ssm_document.revoke_public_ingress.name
  resource_type    = "AWS::EC2::SecurityGroup"

  automatic                  = true
  maximum_automatic_attempts = 3
  retry_attempt_seconds      = 60

  parameter {
    name         = "AutomationAssumeRole"
    static_value = aws_iam_role.remediation.arn
  }

  parameter {
    name           = "GroupId"
    resource_value = "RESOURCE_ID"
  }

  lifecycle {
    ignore_changes = [automatic]
  }
}
