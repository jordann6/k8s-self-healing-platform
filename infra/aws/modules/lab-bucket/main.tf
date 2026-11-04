# A private, KMS-encrypted, versioned bucket for one purpose of the lab
# (Config delivery, R2 evidence). force_destroy because the lab is deployed,
# demonstrated and destroyed in one sitting; evidence worth keeping is copied
# into the repo's evidence/ directory by the runner before teardown.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

variable "name" {
  description = "Globally unique bucket name."
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for default encryption."
  type        = string
}

variable "purpose" {
  description = "What the bucket holds, stamped as a tag."
  type        = string
}

variable "expire_days" {
  description = "Objects (and old versions) expire after this many days."
  type        = number
  default     = 14
}

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_18:Access logging would need a second bucket per bucket; both buckets live for one session and CloudTrail records the API calls.
  #checkov:skip=CKV_AWS_144:Cross-region replication is out of scope for a same-session lab.
  #checkov:skip=CKV2_AWS_62:Nothing consumes event notifications here; the runner reads the objects directly.
  bucket        = var.name
  force_destroy = true

  tags = { Purpose = var.purpose }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "expire"
    status = "Enabled"
    filter {}

    expiration {
      days = var.expire_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.expire_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.this]
}

output "s3_bucket_id" {
  description = "Bucket name."
  value       = aws_s3_bucket.this.id
}

output "s3_bucket_arn" {
  description = "Bucket ARN."
  value       = aws_s3_bucket.this.arn
}
