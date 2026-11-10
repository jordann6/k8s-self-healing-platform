# Row D1 on EKS (ADR-0022): the WORM backup store, in its own root so a lab
# destroy never waits on retention. COMPLIANCE mode means "a protected object
# version can't be overwritten or deleted by any user, including the root user",
# and the only early deletion is deleting the AWS account, so this root cannot be
# destroyed until the one-day retention has passed. Read the plan before applying
# it: the lock cannot be undone.

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.lab_account_id]

  assume_role {
    role_arn     = "arn:aws:iam::${var.lab_account_id}:role/${var.lab_role_name}"
    session_name = "selfheal-d1-store"
  }

  # Written out literally so the shared policy gate can read them.
  default_tags {
    tags = {
      Project     = "k8s-self-healing-platform"
      Environment = var.environment
      Owner       = "jordann6"
      ManagedBy   = "terraform"
      CostCenter  = var.cost_center
    }
  }
}

locals {
  bucket = "selfheal-d1-worm-${var.lab_account_id}"
}

resource "aws_s3_bucket" "worm" {
  #checkov:skip=CKV_AWS_18:No access logging bucket: a second bucket would also need to outlive the lab.
  #checkov:skip=CKV_AWS_144:One region, one session; replication would add a second locked store.
  #checkov:skip=CKV_AWS_145:SSE-S3 on purpose: a KMS key cannot be deleted immediately and would outlive the lab.
  #checkov:skip=CKV2_AWS_61:Retention, not a lifecycle rule, bounds the lifetime of every object.
  #checkov:skip=CKV2_AWS_62:No event notifications: the runner reads the object directly.
  #checkov:skip=CKV2_AWS_6:Public access is blocked by aws_s3_bucket_public_access_block.worm.
  bucket              = local.bucket
  object_lock_enabled = true
  # Lets the second destroy remove expired versions. It cannot remove a version
  # still under retention, which is the point.
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "worm" {
  bucket = aws_s3_bucket.worm.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "worm" {
  bucket = aws_s3_bucket.worm.id

  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.worm]
}

# SSE-S3 on purpose: a customer-managed key cannot be deleted immediately (7 to 30 day
# window), so it would outlive the lab and the second destroy (ADR-0022).
#trivy:ignore:AVD-AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "worm" {
  bucket = aws_s3_bucket.worm.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "worm" {
  bucket                  = aws_s3_bucket.worm.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.worm.arn, "${aws_s3_bucket.worm.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "worm" {
  bucket = aws_s3_bucket.worm.id
  policy = data.aws_iam_policy_document.tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.worm]
}
