# ADR-0005: AWS facts verified before the AWS module was written

- **Status:** accepted
- **Date:** 2026-11-04

## Context

The plan listed AWS behaviour to verify at build time instead of trusting
memory or older blog posts. Each answer below changed, or confirmed, a design
choice in `infra/aws/`. Prices are US East (N. Virginia), read from the AWS
pricing pages on the date above.

## Findings

### Pricing

| Service | Price | What it means for a 3 to 4 hour session |
|---|---|---|
| GuardDuty Runtime Monitoring | $1.50 per vCPU per month for the first 500 vCPUs, $0.75 for the next 4,500; vCPUs are prorated by active hours. 30-day free trial per protection plan not yet enabled in the account and Region | A few cents for a handful of nodes; the account has never enabled Runtime Monitoring, so the trial should apply |
| Amazon Managed Service for Prometheus | $0.90 per 10M samples ingested (first 2B per month), $0.03 per GB-month stored, $0.10 per billion query samples. Free tier: 40M samples ingested, 10 GB stored, 200B query samples | Inside the free tier |
| Amazon Managed Grafana | $9 per active editor or admin per workspace per month, $5 per viewer; every workspace needs at least one editor license. 90-day free trial for up to five users per account | Billed per month, not per hour, and needs Identity Center sign-in. Off by default (see Decision) |
| AWS FIS | $0.10 per action-minute | Cents per run |
| AWS Config | $0.003 per configuration item (continuous), $0.001 per rule evaluation (first 100,000) | Cents, with recording scoped to security groups |

### EKS node monitoring agent and node auto-repair with Karpenter

- Both work with self-managed Karpenter, not just Auto Mode or managed node
  groups. The agent is an EKS add-on; repair is the Karpenter feature gate
  `NodeRepair=true`, which still **defaults to false** in Karpenter v1.15.
- Karpenter v1.15.1 is current; Kubernetes 1.34 needs Karpenter 1.6 or later.
- Repair waits 30 minutes for `Ready`, `KernelReady`, `NetworkingReady`,
  `StorageReady` and `ContainerRuntimeReady`, then replaces the node. It stops
  when more than 20% of a NodePool is unhealthy and records a
  `NodeRepairBlocked` event.
- From Karpenter v1.15.0, repair is a disruption method: it applies a 10 minute
  termination grace period, respects PodDisruptionBudgets within it, and honours
  `karpenter.sh/do-not-repair`. `karpenter.sh/do-not-disrupt` does **not**
  exclude a node from repair. Older versions delete pods with a minimal grace
  period and bypass PDBs.

### SSM agent on the EKS optimized AL2023 AMI

- The AL2023 EKS AMI release notes list `amazon-ssm-agent` in the package set
  (for example 3.3.3050.0 in v20251209). AWS docs do not state it in prose, so
  the live session confirms it with `systemctl status amazon-ssm-agent` through
  Run Command before any row depends on it. Bottlerocket stays the documented
  alternative.

### External Secrets Operator with EKS Pod Identity

- Supported. With Pod Identity, the `SecretStore` omits the `auth` block and ESO
  uses the controller's own identity. `serviceAccountRef` cannot be combined with
  Pod Identity. Per-store scoping is done by role chaining (`role` on the
  provider), not by impersonating a service account. No IRSA is needed.

### GuardDuty in an AWS Organizations member account

- The lab account's detector is a **member** of the organization's delegated
  GuardDuty administrator (the security account). Only the delegated
  administrator can enable or disable Runtime Monitoring for a member; the
  member cannot change it from its own account.
- Terraform models this as `aws_guardduty_member_detector_feature`, applied from
  the administrator account against the member account ID.
- `RUNTIME_MONITORING` (with `EKS_ADDON_MANAGEMENT`) is the current feature;
  `EKS_RUNTIME_MONITORING` is the older EKS-only name. The API rejects requests
  that set both.
- The automated agent creates a VPC endpoint (and a security group) in each
  monitored VPC. Both must be gone before the VPC can be deleted, so Runtime
  Monitoring is turned off in a separate step before the destroy plan.
- The lab account had no AWS Config recorder when this was checked (the landing
  zone's recorder lives in the security account), so the lab creates and removes
  its own.

## Decision

- Runtime Monitoring is enabled for the lab account from the security account
  (`aws_guardduty_member_detector_feature`, `RUNTIME_MONITORING` +
  `EKS_ADDON_MANAGEMENT`), and turned off in its own apply before teardown.
- Karpenter v1.15.x with `NodeRepair=true`, plus the node monitoring agent add-on.
- Amazon Managed Prometheus for storage; dashboards from the in-cluster Grafana
  reading AMP with SigV4. Managed Grafana is an opt-in variable, off by default,
  because it bills per user per month and needs Identity Center sign-in.
- ESO authenticates with Pod Identity, no IRSA anywhere.

## Sources

- https://aws.amazon.com/guardduty/pricing/
- https://aws.amazon.com/prometheus/pricing/
- https://aws.amazon.com/grafana/pricing/
- https://aws.amazon.com/fis/pricing/
- https://aws.amazon.com/config/pricing/
- https://docs.aws.amazon.com/eks/latest/userguide/node-health.html
- https://docs.aws.amazon.com/eks/latest/userguide/node-repair.html
- https://karpenter.sh/docs/reference/settings/
- https://karpenter.sh/docs/upgrading/compatibility/
- https://github.com/awslabs/amazon-eks-ami/releases (v20251209 package list)
- https://external-secrets.io/latest/provider/aws-access/
- https://docs.aws.amazon.com/guardduty/latest/ug/eks-runtime-monitoring-configuration-multiple-accounts.html
- https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_findings_eventbridge.html
