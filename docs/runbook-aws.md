# Runbook: one bounded live EKS session

Goal: run every row (A1 to A5, S1, S1b, S2 to S5, R1, R2) against real EKS,
baseline then hardened, keep the evidence, and leave nothing running. The
kind proof (`make up && make demo`) comes first.

The credentialed `terraform apply` steps are run by the operator from saved
plans, with absolute paths, so a human reads every plan before it executes.
Nothing in the Makefile applies or destroys infrastructure.

Cost while up: about $0.85 to $1.00 an hour (EKS control plane, two t3.large
system nodes, two or three Karpenter workload nodes, one NAT gateway, three
SSM interface endpoints, a db.t4g.micro, CloudWatch logs and metrics). AMP,
GuardDuty Runtime Monitoring, Config, FIS, Step Functions and Lambda are cents
for a session. Budget four hours. `make eks-cost` gives the Infracost figure.

## 0. Preflight

```bash
cp infra/aws/lab.auto.tfvars.example infra/aws/lab.auto.tfvars   # fill in both account IDs
export MGMT_PROFILE=<management-account profile>   # Terraform assumes from here
export LAB_PROFILE=<lab-account profile>           # AWS CLI and the runner
make check            # unit tests, Terraform gates, renders, hooks
make eks-cost         # Infracost estimate (needs `infracost auth login` once)
make eks-preflight    # read-only: accounts, Config recorder, GuardDuty admin, leftovers, quota, IPv4
```

The preflight fails if another Config recorder exists in the lab account and
region (one per region; the lab owns its own), if GuardDuty is not a member of
the security account, or if anything from an earlier session is still there.

## 1. Infrastructure

```bash
make eks-plan
# read the plan: VPC, NAT, endpoints, EKS, system nodes, Karpenter IAM, RDS,
# secrets, rotation, Config, FIS, GuardDuty member feature (security account),
# responder, AMP, ECR
AWS_PROFILE=$MGMT_PROFILE terraform -chdir=$PWD/infra/aws apply $PWD/infra/aws/tfplan
```

RDS takes the longest (about 10 minutes).

## 2. Platform, images, bootstrap

```bash
make eks-up     # = eks-platform eks-images eks-bootstrap
```

- `eks-platform`: lab kubeconfig (`.kube/eks`), VPC CNI off, Cilium in ENI
  mode and the readiness gate, Karpenter with NodeRepair, the workload
  EC2NodeClass one AMI release behind the newest, the platform charts in the
  kind order, the responder's RBAC.
- `eks-images`: linux/amd64 builds, immutable `<sha>-v1` / `<sha>-v2` tags.
- `eks-bootstrap`: lab CA, the partner key in Secrets Manager, the S1b
  database user and its rotation, the Git server, Argo CD on the EKS baseline.

### Verify live before the demo (one-time unknowns)

```bash
export KUBECONFIG=$PWD/.kube/eks
# SSM agent on the AL2023 workload AMI (ADR-0005): Online for every workload node
aws ssm describe-instance-information --query 'InstanceInformationList[].[InstanceId,PingStatus]' --output table
# GuardDuty agent add-on running on the workload nodes, despite their taint
kubectl -n amazon-guardduty get pods -o wide
# Prometheus remote write to AMP through Pod Identity (no 403s)
kubectl -n monitoring logs sts/prometheus-kps-prometheus -c prometheus | grep -i "remote_write\|403" | tail
# ESO reads AWSCURRENT and AWSPREVIOUS (hardened profile only; after the switch)
kubectl -n selfheal get externalsecret db-app
```

If the GuardDuty agent is not on the workload nodes, R2 cannot be detected:
stop and record it rather than running the row.

## 3. Experiment

```bash
make demo TARGET=eks RUN_ID=eks-run1
```

About two and a half hours for every row, both profiles: S4 and R2 wait on
AWS (Config evaluation, GuardDuty delivery), S5 replaces every workload node
twice. A subset: `make demo TARGET=eks ROWS=A4,S5 RUN_ID=eks-a4s5`.

The R2 finding normally reaches the lab account's own event bus. If a
hardened R2 shows a finding (`finding_s`) but no execution, the bus did not
receive it: set `forward_findings_from_security = true` in `lab.auto.tfvars`,
plan and apply (it adds a forwarding rule in the security account), and
rerun R2 only.

Evidence: `evidence/<run-id>/report.md`, per-row prober samples, the S1b and
S4 traces, the S5 node trace, and R2's manifest, capture, audit window,
finding and execution history. Commit the reference runs before teardown.

## 4. Teardown (four steps, in order)

```bash
make eks-runtime-off-plan
AWS_PROFILE=$MGMT_PROFILE terraform -chdir=$PWD/infra/aws apply $PWD/infra/aws/runtime-off.tfplan
make eks-predestroy
make eks-destroy-plan
AWS_PROFILE=$MGMT_PROFILE terraform -chdir=$PWD/infra/aws apply $PWD/infra/aws/destroy.tfplan
make eks-verify-gone
```

Why four: destroying the GuardDuty member feature does not turn Runtime
Monitoring off (the provider's delete only leaves state), and the agent's
VPC endpoint blocks the VPC delete (ADR-0006). `eks-verify-gone` checks the
cluster, instances (Karpenter's included), NAT gateways, the VPC, Cilium
ENIs, Runtime Monitoring and its endpoint and security group, the lab's
Config recorder, rule and document, AMP, Grafana, FIS templates and running
experiments, evidence snapshots, RDS, secrets, functions, state machines, ECR,
buckets, and KMS keys not pending deletion. Every line must say "gone"; the
two KMS keys stay in their 7-day deletion window, which is expected.
