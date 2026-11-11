output "bucket" {
  description = "The WORM bucket. Pass it to the EKS root as -var=d1_store_bucket=..."
  value       = aws_s3_bucket.worm.bucket
}

output "region" {
  description = "Region of the bucket."
  value       = var.region
}

output "retention" {
  description = "The default retention the bucket enforces."
  value       = { mode = "COMPLIANCE", days = 1 }
}
