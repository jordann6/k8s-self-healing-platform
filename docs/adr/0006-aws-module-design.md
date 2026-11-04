# ADR-0006: The AWS module

- **Status:** accepted
- **Date:** 2026-11-04

## Context

The AWS module runs every row of the kind core on EKS and adds five rows that
only a cloud can host: a node dies (A4), a database password rotates (S1b), a
firewall opens (S4), every node is replaced (S5), and a runtime threat is
answered by the cloud's own detector (R2). ADR-0005 records the facts checked
before this was written. This ADR records the choices.

## Decisions

### Two accounts, one Terraform root

The lab lives in an isolated workload account. GuardDuty in that account is a
member of the organization's delegated administrator, and a member cannot turn
Runtime Monitoring on for itself (ADR-0005). The root therefore has a second
provider that assumes a role in the security account, and uses it for exactly
one resource, `aws_guardduty_member_detector_feature`. Both providers start
from the operator's management-account credentials, and `allowed_account_ids`
fails a plan that would land anywhere else. Account IDs live in a gitignored
`lab.auto.tfvars`.

### Teardown is two applies, not one

The member-feature resource's delete is a no-op in the AWS provider: it only
removes the resource from state. A plain destroy would leave Runtime
Monitoring enabled on the account, and the agent's VPC endpoint (with a
GuardDuty-created security group) would block the VPC delete. Teardown is:

1. a saved plan with `runtime_monitoring=false`, applied by the operator;
2. `make eks-predestroy`: lift termination protection, drain the workload pool
   through Karpenter, remove Karpenter and Cilium, delete whatever the GuardDuty
   agent left, delete the evidence snapshots;
3. the destroy plan, applied by the operator;
4. `make eks-verify-gone`, read-only, every line must say "gone" or "kept".

"Kept" is the lab's AWS Config recorder, rule, delivery bucket and the data
key its delivery channel encrypts with. The organization's SCP
(`deny-disable-detective`) denies deleting Config recorders, delivery
channels and rules in member accounts, so the destroy stops at them (found in
the first live session). They stay in Terraform state and the next session
reuses them; about $1 a month, for the key. After a destroy, re-apply the
bucket's hardening, which the destroy removes:
`terraform plan -target=module.config_bucket`, then apply that plan.
Removing them for good needs an SCP exemption made in the landing zone's own
code, not a change from this lab.

The drain in step 2 deletes the lab app and its PDB first. With the NodePool
deleted and the PDB still in place, no replacement replica can schedule, the
PDB allows 0 disruptions and Karpenter's drain blocks; the first session
removed Karpenter at that point and stranded two running nodes. Step 2 now
stops with an error, Karpenter still installed, if any node survives.

### Node pools

Two system nodes in a managed node group run the platform and are never a
fault target. Workload nodes come from Karpenter, carry a taint only the web
pods tolerate, and are the only nodes A4, S4, S5 and R2 act on. The prober and
the partner run on the system nodes, so a workload node fault never takes out
the measurement. Every automation that can act on an instance or a security
group (FIS, the R2 state machine, the S4 document) is limited by IAM to
resources tagged `selfheal.lab/managed=true`, and checks the tag itself.

### A4: the node with the most web pods, not a random one

FIS selects targets by tag. Left alone it could terminate a node with no web
pod, and the row would test nothing. The runner tags the node hosting the most
web pods just before starting the experiment, and the judge treats a kill that
hit no web pod as an error. FIS stops on a CloudWatch alarm; the SLO lives in
Prometheus, so the runner relays the independent prober's error rate as a
1-second metric. Missing data counts as breaching: if the runner dies, FIS
stops.

### S4: detective on purpose

The managed `AWS-DisablePublicAccessForSecurityGroup` runbook only revokes 22
and 3389, so the remediation is a custom Automation document that revokes
`0.0.0.0/0` and `::/0` on any port. The Config rule is scoped by tag to the
lab's groups. Production puts prevention first (an SCP or a deny policy); this
row proves the layer that catches what prevention misses. The judge only
accepts a revert that a successful remediation execution performed.

### S5: AMI rotation through drift, never expiry

`expireAfter` (720h) is configured as a compliance maximum age, but expiry can
bypass disruption budgets, so routine replacement is drift. The workload
EC2NodeClass is pinned one AMI release behind the newest at deploy time; the
row moves the alias to `@latest` (and back, for the second run), and Karpenter
replaces the nodes under the profile's budget.

### R2: isolate, preserve, investigate

The responder follows the EKS incident response order. It cordons the node
before it relabels the pod out of the Service: relabelling makes the Rollout
create a replacement at once, and an uncordoned node can receive it just
before its security groups are swapped (found in the first EKS session). It never deletes the
pod or the node; eradication is a human decision, which the runner takes only
after the evidence is in the repo. Run Command output comes back through the
SSM API, so nodes need no write access to the evidence bucket, and the
quarantine security group allows only HTTPS to the SSM endpoints.

Sessions are revoked for the compromised workload's Pod Identity role, not the
node role. Every node shares the node role: denying its sessions issued before
the incident would cut kubelet, image pulls and Cilium's ENI operator on the
healthy nodes too. The web pods carry no Pod Identity association and cannot
reach IMDS (hop limit 1), so on this lab the revocation step records that
there was nothing to revoke, which is the least-privilege result it should be.

The trigger is a DNS lookup of GuardDuty's documented test domain from the web
container's own process (python3, not a shell, so R1's Falco rule stays out
of it). The judge requires a real, non-sample finding.

### Node auto-repair: configured, not a judged row

Karpenter's `NodeRepair` gate and the node monitoring agent are on, and the
system node group has repair enabled. A real repair waits 30 minutes of an
unhealthy condition, too long to judge inside a session; the configuration is
recorded in each report's environment section instead.

### Observability

The in-cluster Prometheus still evaluates the Sloth rules and the canary
analysis, so the PromQL is the same as on kind. It remote-writes only the
series the lab reads to Amazon Managed Prometheus, which bills per sample;
the in-cluster Grafana reads AMP with SigV4. Amazon Managed Grafana is an
opt-in variable, off by default.

## Consequences

- One live session in the workload account; the operator applies and
  destroys from saved plans (`docs/runbook-aws.md`).
- Rows that depend on AWS service latency (S4, R2) have longer clocks, set
  before the first run (ADR-0003).
- The R2 fallback that forwards findings from the administrator account is
  built but off; the first session records which path delivers.
