# Pinned by name to the region's a and b zones, so the AZ set (and the subnet
# layout with it) cannot change silently when AWS adds a zone. Two zones are
# enough for row A4: topology spread keeps a replica in the zone that survives.
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "zone-name"
    values = ["${var.region}a", "${var.region}b"]
  }
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.6"

  name = "${local.name}-vpc"
  cidr = var.vpc_cidr
  azs  = local.azs

  # /20 private subnets: Cilium ENI IPAM hands pods real VPC addresses.
  private_subnets = [cidrsubnet(var.vpc_cidr, 4, 0), cidrsubnet(var.vpc_cidr, 4, 1)]
  public_subnets  = [cidrsubnet(var.vpc_cidr, 8, 101), cidrsubnet(var.vpc_cidr, 8, 102)]

  # One NAT keeps the hourly footprint small. Image pulls and the GuardDuty
  # test-domain lookup leave through it; SSM uses the endpoints below.
  enable_nat_gateway = true
  single_nat_gateway = true

  # Nodes never get public addresses (no SSH, no port 22, SSM only).
  map_public_ip_on_launch = false

  # Karpenter discovers the subnets it may launch into by this tag.
  private_subnet_tags = {
    "karpenter.sh/discovery"          = local.name
    "kubernetes.io/role/internal-elb" = "1"
  }

  tags = local.tags
}

# --- SSM without the internet ----------------------------------------------
# Session Manager and Run Command reach nodes through these endpoints, so a
# node under quarantine (whose security group only allows 443 to this group)
# can still hand over its evidence. Run Command output comes back through the
# SSM API, so nodes never need write access to the evidence bucket. The S3
# gateway endpoint is free and keeps image layer pulls off the NAT gateway.

resource "aws_security_group" "endpoints" {
  name        = "${local.name}-endpoints"
  description = "Interface endpoints: HTTPS from inside the VPC only"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS from the lab VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = { Name = "${local.name}-endpoints" }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(["ssm", "ssmmessages", "ec2messages"])

  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "${local.name}-${each.key}" }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = { Name = "${local.name}-s3" }
}

# --- quarantine (R2) ----------------------------------------------------------
# The responder moves every ENI of a compromised node onto this group. No
# ingress at all; egress only to the SSM endpoints, so evidence capture still
# works after isolation. Pod ENIs carry
# the node's security groups under Cilium ENI IPAM, so this isolates the pods
# on that node too, which is the point: a host-level compromise is not
# contained by a NetworkPolicy.
resource "aws_security_group" "quarantine" {
  #checkov:skip=CKV2_AWS_5:Attached at incident time by the R2 responder, never at create time.
  name        = "${local.name}-quarantine"
  description = "R2 node quarantine: no ingress, egress only to the SSM endpoints"
  vpc_id      = module.vpc.vpc_id

  egress {
    description     = "HTTPS to the SSM interface endpoints"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.endpoints.id]
  }

  tags = merge(local.lab_tag, { Name = "${local.name}-quarantine", "selfheal.lab/quarantine" = "true" })
}

# --- S4 target ----------------------------------------------------------------
# A security group attached to every workload node (the EC2NodeClass selects
# it by tag) that starts with no ingress. Row S4 opens it to 0.0.0.0/0 at run
# time; Terraform never declares that rule, and the shared policy gate would
# refuse it if it did. lifecycle ignores ingress so the drift the row injects
# is not "fixed" by a later plan.
resource "aws_security_group" "exposure" {
  #checkov:skip=CKV2_AWS_5:Attached to Karpenter-launched nodes through the EC2NodeClass selector, which checkov cannot see.
  name        = "${local.name}-exposure"
  description = "S4 drift target on workload nodes. Starts closed; the runner opens it"
  vpc_id      = module.vpc.vpc_id

  tags = merge(local.lab_tag, { Name = "${local.name}-exposure", "selfheal.lab/s4-target" = "true" })

  lifecycle {
    ignore_changes = [ingress]
  }
}
