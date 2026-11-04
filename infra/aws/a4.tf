# Row A4: a workload node dies. AWS FIS terminates one Karpenter-launched node;
# Karpenter replaces it, the PDB and topology spread keep two replicas serving
# in the meantime.
#
# Stop condition: FIS checks a CloudWatch alarm, but the SLO lives in
# Prometheus. The runner relays the independent prober's error rate as a
# high-resolution CloudWatch metric while the row runs, and this alarm on it
# is the stop condition. Missing data counts as breaching: if the runner (the
# only publisher) dies mid-experiment, FIS stops instead of carrying on blind.
# The runner publishes for 30s before starting, so the alarm is OK at start.
#
# FIS pod actions (aws:eks:pod-*) are not used anywhere: they need a writable
# root filesystem, which the hardened pod spec forbids. Pod faults use Chaos
# Mesh on every target.

locals {
  prober_metric_namespace = "SelfHeal/Prober"
}

resource "aws_cloudwatch_metric_alarm" "prober_errors" {
  alarm_name          = "${local.name}-prober-error-rate"
  alarm_description   = "Prober error rate above the abort line (50% over 10s). FIS stop condition for row A4."
  namespace           = local.prober_metric_namespace
  metric_name         = "ErrorRate"
  dimensions          = { Cluster = local.name }
  statistic           = "Maximum"
  period              = 10
  evaluation_periods  = 1
  threshold           = 50
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
}

resource "aws_cloudwatch_log_group" "fis" {
  #checkov:skip=CKV_AWS_158:Experiment logs name targets and actions, no secrets; the default CloudWatch encryption applies.
  #checkov:skip=CKV_AWS_338:14 days covers a deploy, demo, destroy session.
  name              = "/aws/fis/${local.name}"
  retention_in_days = 14
}

resource "aws_iam_role" "fis" {
  name = "${local.name}-fis"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "fis.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "fis" {
  name = "terminate-lab-workload-nodes"
  role = aws_iam_role.fis.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "FindTargets"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*" # Describe* calls do not support resource-level scoping.
      },
      {
        Sid      = "TerminateLabWorkloadNodesOnly"
        Effect   = "Allow"
        Action   = ["ec2:TerminateInstances"]
        Resource = "arn:${local.partition}:ec2:${var.region}:${local.account_id}:instance/*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/selfheal.lab/managed"  = "true"
            "aws:ResourceTag/karpenter.sh/nodepool" = "workload"
          }
        }
      },
      {
        Sid      = "WriteExperimentLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogDelivery", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
        Resource = "*" # Log delivery setup calls do not support resource-level scoping.
      },
    ]
  })
}

resource "aws_fis_experiment_template" "node_kill" {
  description = "A4: terminate one Karpenter workload node"
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = aws_cloudwatch_metric_alarm.prober_errors.arn
  }

  target {
    name           = "one-workload-node"
    resource_type  = "aws:ec2:instance"
    selection_mode = "COUNT(1)"

    resource_tag {
      key   = "karpenter.sh/nodepool"
      value = "workload"
    }

    resource_tag {
      key   = "selfheal.lab/managed"
      value = "true"
    }

    filter {
      path   = "State.Name"
      values = ["running"]
    }
  }

  action {
    name      = "terminate"
    action_id = "aws:ec2:terminate-instances"

    target {
      key   = "Instances"
      value = "one-workload-node"
    }
  }

  log_configuration {
    log_schema_version = 2

    cloudwatch_logs_configuration {
      log_group_arn = "${aws_cloudwatch_log_group.fis.arn}:*"
    }
  }

  tags = { Name = "${local.name}-a4-node-kill" }
}
