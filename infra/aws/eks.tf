# EKS for the self-healing lab. Two kinds of node:
#
#   system    a two-node managed group for the platform (Argo CD, Prometheus,
#             Falco, Karpenter itself). Never a fault target.
#   workload  launched by Karpenter (cluster/karpenter/), tainted so only the
#             lab app lands there. Rows A4, S4, S5 and R2 act on these nodes.
#
# The VPC CNI is switched off by `make eks-cilium` (aws-node pinned to a node
# label nothing carries) and Cilium takes over with AWS ENI IPAM, as in the
# network segmentation lab.

# Public endpoint on purpose, scoped to the operator's /32 (variables.tf refuses
# 0.0.0.0/0); the runner drives the cluster from the laptop. Node egress is open
# so nodes pull images and reach AWS APIs through the NAT gateway.
#trivy:ignore:AVD-AWS-0040
#trivy:ignore:AVD-AWS-0041
#trivy:ignore:AVD-AWS-0104
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.29"

  name               = local.name
  kubernetes_version = var.cluster_version

  # Public endpoint scoped to the operator's /32 for the runner; private
  # endpoint for the R2 responder Lambda, which runs in the private subnets.
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.cluster_public_access_cidrs
  endpoint_private_access      = true

  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = true

  # The audit log is evidence for R2 (the responder exports its window).
  # The verbose api log is left off: nothing reads it, and log ingestion is
  # one of the larger line items of a session.
  enabled_log_types                      = ["audit", "authenticator"]
  cloudwatch_log_group_retention_in_days = 14

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  addons = {
    # Pod Identity is how every workload gets AWS access (Karpenter, ESO,
    # Prometheus remote write, the prober's CloudWatch metric). No IRSA.
    eks-pod-identity-agent = {
      before_compute = true
    }
    # KernelReady, NetworkingReady, StorageReady and ContainerRuntimeReady node
    # conditions; Karpenter's NodeRepair acts on them (ADR-0005).
    eks-node-monitoring-agent = {}
  }

  # Under Cilium ENI IPAM, pod addresses live on ENIs that carry the node
  # security group, so that group filters every cross-node pod-to-pod packet.
  # The module's recommended rules miss cross-node UDP (found in the network
  # segmentation lab); pod-to-pod filtering is Cilium's job, so the node group
  # trusts itself on all ports.
  node_security_group_additional_rules = {
    ingress_self_all = {
      description = "Node to node, all ports and protocols (pod-to-pod under Cilium ENI IPAM)"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "ingress"
      self        = true
    }
  }

  # Karpenter selects the node security group by this tag.
  node_security_group_tags = {
    "karpenter.sh/discovery" = local.name
  }

  eks_managed_node_groups = {
    system = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = [var.system_instance_type]
      min_size       = 2
      max_size       = 2
      desired_size   = 2
      capacity_type  = "ON_DEMAND"

      # Node pool label; pods use their own lab/pool label for Service
      # membership, so the node label lives under a separate key.
      labels = { "selfheal.lab/nodes" = "system" }

      # Nothing but Cilium schedules until the agent is ready on the node and
      # removes this taint: the node half of the readiness gate.
      taints = {
        cilium = {
          key    = "node.cilium.io/agent-not-ready"
          value  = "true"
          effect = "NO_EXECUTE"
        }
      }

      # IMDSv2 only, hop limit 1: pods cannot reach node credentials. Pod
      # Identity does not use IMDS.
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 1
      }

      node_repair_config = {
        enabled = true
      }

      iam_role_additional_policies = {
        ssm        = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
        cilium_eni = aws_iam_policy.cilium_eni.arn
      }
    }
  }

  tags = local.tags
}

# Cilium's operator allocates ENIs and secondary IPs with the node role (it runs
# host-networked). AmazonEKS_CNI_Policy covers the mutations; these read-only
# lookups are the rest.
resource "aws_iam_policy" "cilium_eni" {
  name        = "${local.name}-cilium-eni"
  description = "Read-only EC2 lookups Cilium ENI IPAM needs beyond AmazonEKS_CNI_Policy."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "CiliumEniDescribe"
      Effect = "Allow"
      Action = [
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcs",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeRouteTables",
      ]
      Resource = "*" # Describe* calls do not support resource-level scoping.
    }]
  })
}

# --- Karpenter ------------------------------------------------------------------
# Controller role (Pod Identity), node role, instance profile, interruption
# queue and its EventBridge rules. The NodePool and EC2NodeClass are cluster
# manifests (cluster/karpenter/), applied by `make eks-platform`.
module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 21.29"

  cluster_name = module.eks.cluster_name

  namespace                       = "kube-system"
  service_account                 = "karpenter"
  create_pod_identity_association = true

  node_iam_role_use_name_prefix = false
  node_iam_role_name            = "${local.name}-karpenter-node"
  node_iam_role_additional_policies = {
    ssm        = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
    cilium_eni = aws_iam_policy.cilium_eni.arn
  }

  tags = local.tags
}
