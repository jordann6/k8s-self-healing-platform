# Rows S1 and S1b: secrets that rotate under live traffic.
#
#   S1   a partner API key in Secrets Manager. ESO syncs it, Reloader restarts
#        the Rollout, the partner accepts old and new during a grace window
#        (the same mechanics as on kind, with a real backend).
#   S1b  a PostgreSQL password. One role has one live password, so rotating it
#        in place breaks every client that has not caught up. Secrets Manager's
#        alternating-users scheme rotates selfheal_app and selfheal_app_clone in
#        turn, so the previous credential stays valid while the next is set.
#
# No password lives in Terraform. The app secret is created empty;
# scripts/eks-db-bootstrap.sh creates the DB user, seeds the secret and only
# then enables rotation (enabling it first would test the function against a
# user that does not exist and can strand AWSPENDING).

# --- S1: partner API key -----------------------------------------------------------
resource "aws_secretsmanager_secret" "partner" {
  #checkov:skip=CKV2_AWS_57:Row S1 rotates this key on demand (the runner writes a new version, then revokes the old one at the partner). There is no rotation function to schedule.
  name        = "${local.name}/partner-api-key"
  description = "Row S1: the partner API key the web pods present"
  kms_key_id  = aws_kms_key.data.arn

  # Deploy/demo/destroy: a recovery window would hold the name and fail the
  # next session's create.
  recovery_window_in_days = 0
}

# --- S1b: PostgreSQL -----------------------------------------------------------------
locals {
  db_port = 5432
  db_name = "selfheal"
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL from the cluster nodes and the rotation function only"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "PostgreSQL from nodes (pod ENIs carry the node group under Cilium ENI IPAM)"
    from_port       = local.db_port
    to_port         = local.db_port
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  ingress {
    description     = "PostgreSQL from the rotation function"
    from_port       = local.db_port
    to_port         = local.db_port
    protocol        = "tcp"
    security_groups = [aws_security_group.rotation.id]
  }

  tags = { Name = "${local.name}-db" }
}

resource "aws_db_parameter_group" "this" {
  name   = "${local.name}-pg17"
  family = "postgres17"

  # TLS only: the rotation function and the db prober both verify the RDS CA.
  # Static parameter: AWS stores it as pending-reboot (the default is 1 on
  # postgres17 anyway), so it is declared that way to keep plans clean.
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  # Connection and auth failures are the S1b evidence on the server side.
  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_161:IAM database auth is not the subject here: S1b is the password path for a client that cannot use IAM tokens.
  #checkov:skip=CKV_AWS_157:Single-AZ on purpose; S1b tests credential rotation, not database failover.
  #checkov:skip=CKV_AWS_118:Enhanced monitoring adds a role and cost a 4-hour session does not need.
  #checkov:skip=CKV_AWS_353:Performance Insights is not needed for a rotation test.
  #checkov:skip=CKV_AWS_293:Deletion protection would block the same-session destroy.
  #checkov:skip=CKV2_AWS_60:Copy tags to snapshots is moot: no final snapshot is taken.
  #checkov:skip=CKV2_AWS_30:Query logging is off; connection logging (the S1b evidence) is on through the parameter group.
  identifier     = local.name
  engine         = "postgres"
  engine_version = "17"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true
  kms_key_id        = aws_kms_key.data.arn

  db_name  = local.db_name
  username = "selfheal_admin"
  port     = local.db_port

  # RDS keeps the master password in its own Secrets Manager secret; it never
  # appears in Terraform or state.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.data.arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false
  multi_az               = false

  auto_minor_version_upgrade      = true
  backup_retention_period         = 1
  enabled_cloudwatch_logs_exports = ["postgresql"]

  deletion_protection = false
  skip_final_snapshot = true
  apply_immediately   = true
}

resource "aws_secretsmanager_secret" "db_app" {
  #checkov:skip=CKV2_AWS_57:Rotation is enabled by scripts/eks-db-bootstrap.sh once the DB user exists and the secret holds a value; see the header.
  name        = "${local.name}/db-app"
  description = "Row S1b: selfheal_app PostgreSQL credential, rotated with alternating users"
  kms_key_id  = aws_kms_key.data.arn

  recovery_window_in_days = 0
}

# --- rotation function (alternating users) ------------------------------------------------
# HTTPS egress is open because the Secrets Manager and RDS APIs are reached
# through the NAT gateway (no interface endpoints in a lab VPC).
#trivy:ignore:AVD-AWS-0104
resource "aws_security_group" "rotation" {
  name        = "${local.name}-rotation"
  description = "Rotation function: PostgreSQL in the VPC and HTTPS out for the Secrets Manager and RDS APIs"
  vpc_id      = module.vpc.vpc_id

  egress {
    description = "PostgreSQL inside the lab VPC"
    from_port   = local.db_port
    to_port     = local.db_port
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    #checkov:skip=CKV_AWS_382:The Secrets Manager and RDS API endpoints are public AWS endpoints reached through the NAT gateway; no interface endpoints for them in a lab VPC.
    description = "HTTPS to the Secrets Manager and RDS APIs through the NAT gateway"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-rotation" }
}

# build/rotation is staged by rotation/build.sh (CI: platform-guardrails
# build_command), and the zip travels with the saved plan.
data "archive_file" "rotation" {
  type             = "zip"
  source_dir       = "${path.module}/build/rotation"
  output_path      = "${path.module}/build/rotation.zip"
  output_file_mode = "0644"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_group" "rotation" {
  #checkov:skip=CKV_AWS_158:Logs hold step names and usernames, never secret values; the default CloudWatch encryption applies.
  #checkov:skip=CKV_AWS_338:14 days covers a deploy, demo, destroy session.
  name              = "/aws/lambda/${local.name}-rotation"
  retention_in_days = 14
}

data "aws_iam_policy_document" "rotation" {
  statement {
    sid = "RotateTheAppSecret"
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      "secretsmanager:UpdateSecretVersionStage",
    ]
    resources = [aws_secretsmanager_secret.db_app.arn]
  }

  # Master credential: read only, to create the clone user and set passwords.
  statement {
    sid       = "ReadTheMasterSecret"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_db_instance.this.master_user_secret[0].secret_arn]
  }

  statement {
    #checkov:skip=CKV_AWS_356:GetRandomPassword does not support resource-level permissions.
    sid       = "GeneratePasswords"
    actions   = ["secretsmanager:GetRandomPassword"]
    resources = ["*"]
  }

  statement {
    sid       = "ResolveTheMasterHost"
    actions   = ["rds:DescribeDBInstances"]
    resources = [aws_db_instance.this.arn]
  }

  statement {
    sid       = "UseTheDataKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.rotation.arn}:*"]
  }

  statement {
    #checkov:skip=CKV_AWS_111:Lambda's VPC ENI actions do not support resource-level scoping on create.
    #checkov:skip=CKV_AWS_356:Same: the ENI actions need "*".
    sid = "AttachToTheVpc"
    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "rotation" {
  name               = "${local.name}-rotation"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "rotation" {
  name   = "rotate-db-app"
  role   = aws_iam_role.rotation.id
  policy = data.aws_iam_policy_document.rotation.json
}

resource "aws_lambda_function" "rotation" {
  #checkov:skip=CKV_AWS_115:The account Lambda concurrency quota is 10; a reservation would drop unreserved concurrency below the minimum. Secrets Manager serializes rotations per secret.
  #checkov:skip=CKV_AWS_116:Secrets Manager is the caller and records each failed step; a DLQ would hold nothing it does not.
  #checkov:skip=CKV_AWS_272:Code signing adds a signing pipeline for one function in a lab; the package is hash-pinned and rebuilt reproducibly.
  #checkov:skip=CKV_AWS_173:The environment holds an endpoint URL, no secrets.
  #checkov:skip=CKV_AWS_50:The four rotation steps are logged to CloudWatch; X-Ray adds nothing for a single-call function.
  function_name    = "${local.name}-rotation"
  description      = "Alternating-users rotation for the selfheal_app secret (AWS sample, psycopg2 port)"
  role             = aws_iam_role.rotation.arn
  runtime          = "python3.13"
  architectures    = ["x86_64"]
  handler          = "lambda_function.lambda_handler"
  filename         = data.archive_file.rotation.output_path
  source_code_hash = data.archive_file.rotation.output_base64sha256
  timeout          = 60
  memory_size      = 256

  vpc_config {
    subnet_ids         = module.vpc.private_subnets
    security_group_ids = [aws_security_group.rotation.id]
  }

  environment {
    variables = {
      SECRETS_MANAGER_ENDPOINT = "https://secretsmanager.${var.region}.amazonaws.com"
    }
  }

  depends_on = [aws_cloudwatch_log_group.rotation, aws_iam_role_policy.rotation]
}

resource "aws_lambda_permission" "rotation" {
  statement_id   = "AllowSecretsManager"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.rotation.function_name
  principal      = "secretsmanager.amazonaws.com"
  source_arn     = aws_secretsmanager_secret.db_app.arn
  source_account = local.account_id
}
