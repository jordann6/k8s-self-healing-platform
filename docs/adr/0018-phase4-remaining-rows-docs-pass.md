# ADR 0018: Phase 4 remaining rows, docs pass before the cloud ports

Status: PROPOSED 2026-11-10. Nothing is built or applied. Jordan reviews the
open decisions before any per-row ADR, overlay or plan is written.

## Context

Q1 and Q2 pass on kind, EKS and AKS (ADR-0016, ADR-0017). Q3, N1, P1, D1 and
O1 have only been proven on kind (ADR-0011 to ADR-0015). This pass reads each
kind contract against the cloud modules as they stand in this repo and against
vendor documentation, row by row, with the AWS missteps of handoff section 10
in mind. Every fact below was read on 2026-11-10. Anything the docs do not
settle is on the live-check list at the end. Nothing is assumed from AWS
behaviour carrying over to AKS, or from kind carrying over to either.

## Facts that apply to every remaining row

1. Kubernetes versions. EKS standard support now offers 1.34, 1.35, 1.36 and
   1.37. Standard support for 1.34 ends on 2026-12-02, three weeks from now.
   Cilium 1.20.4 lists support for 1.33 to 1.36. Karpenter needs 1.13 or later
   for 1.36, so the pinned 1.15.1 qualifies. The AKS Q2 session already ran the
   full platform on 1.36 (ADR-0017 Decision 3). Both modules default to 1.34
   today.
2. Cluster create time. The AKS 1.36 BYO CNI create took 43m9s on 2026-11-10.
   One session per row per cloud would pay that wait five times.
3. Registries. ECR has no VPC interface endpoint in this module (only SSM,
   SSM messages and EC2 messages, plus the S3 gateway), so ECR traffic leaves
   through NAT. ACR is reached over its public endpoint as in Phase 3.

## Row Q3, VPA recommendation (ADR-0011)

Facts
- The pinned VPA chart 0.16.0 ships app 1.8.1. The upstream compatibility
  table lists 1.8.x for Kubernetes 1.36 to 1.38, 1.7.x for 1.35 to 1.37 and
  1.6.x for 1.34 to 1.36. On the 1.34 default, the pin is out of support.
- The AKS managed VPA add-on (`--enable-vpa`) always installs the recommender,
  the updater and the admission controller, two replicas each from AKS 1.34.
  Microsoft states that VPA "doesn't support running your own implementation
  of VPA alongside it", though an extra recommender is supported. The ADR-0011
  rule that no component able to change workload resources exists in the
  cluster cannot hold with the add-on enabled.
- The AKS module keeps the managed metrics-server at its fixed 60s resolution
  (comment in `infra/azure/aks.tf`). EKS installs the chart at 15s. VPA reads
  the resource Metrics API, so on AKS the recommender sees a new sample at
  most once a minute.
- The kind proof already installs metrics-server and VPA from charts, so EKS
  needs no new infrastructure.

Proposed design
- Install the upstream chart, recommender only, on both clouds. Leave the AKS
  add-on off.
- Pin by cluster version (see Decision A). On 1.36 the existing 0.16.0 pin
  serves kind, EKS and AKS.
- Keep the ADR-0011 thresholds. Write the AKS sampling question on the
  live-check list rather than pre-relaxing anything. If six samples inside the
  240s window cannot exist at 60s resolution, the per-row ADR records that as
  a fact and states an AKS observation window derived from it before the run.

## Row N1, WireGuard between nodes (ADR-0012)

Facts
- AKS runs Cilium in `routingMode: tunnel` with VXLAN (`cilium/values-aks.yaml`),
  the same shape as kind. The kind contract (VXLAN UDP 8472 in baseline,
  WireGuard UDP 51871 in hardened, capture on `eth0` filtered to the two node
  IPs) can transfer as written. Ubuntu 24.04 runs a 6.8 kernel, above the 5.6
  WireGuard requirement.
- EKS runs Cilium in ENI IPAM with `routingMode: native`
  (`cilium/values-eks.yaml`). There is no VXLAN. Cross-node pod traffic leaves
  with pod source and destination addresses straight on the VPC fabric. The
  kind baseline check ("VXLAN UDP 8472 with decoded inner addresses") cannot
  pass on EKS, and the capture filter in `scenarios/n1_row.py`, which matches
  only UDP 8472 or 51871 between the node IPs, would capture nothing in the
  baseline.
- Neither the Cilium WireGuard page nor the ENI IPAM page documents WireGuard
  with ENI IPAM. The only AWS walkthrough (AWS Containers blog, 2022-11-25)
  uses CNI chaining on Bottlerocket, not ENI mode. No AWS source states the
  AL2023 `CONFIG_WIREGUARD` value. Cilium documents that WireGuard needs UDP
  51871 open between all nodes. The EKS node security group already trusts
  itself on all protocols, so no rule is needed.
- Pod IPs under ENI IPAM may sit on a secondary ENI, so `eth0` may not carry
  the pod's packets.
- Hardened N1 needs a Cilium Helm upgrade on a running cluster, which restarts
  every agent. Any row sharing the session must run before it, or after Cilium
  is returned to the baseline values.

Proposed design
- AKS keeps the kind contract unchanged.
- EKS gets its own hypothesis, as A4, S4 and R2 have for AKS. Baseline proves
  plaintext HTTP between the two pod IPs on the wire. Hardened proves UDP 51871
  between the two node IPs, the same peer, route and handshake telemetry, and
  no plaintext marker. The capture runs on `any` with a pod-IP or node-IP
  filter, not `eth0` alone.
- Switching EKS to tunnel mode for N1 is rejected. It would change the
  datapath every other EKS row was proven on.

## Row P1, signed image admission (ADR-0013)

Facts
- Kyverno 1.19.2 is still the latest stable release. 1.20.0 is at rc.4
  (2026-11-10). The 1.19.2 notes fix annotation verification for Cosign v2 and
  v3 but say nothing about the OCI referrer bundle regression, so the Cosign
  2.6.3 pin and its legacy signature layout stay.
- `ImageValidatingPolicy` reads registry auth only from `spec.credentials` on
  the policy. The global `--registryCredentialHelpers` flag has no effect, and
  Pod `imagePullSecrets` are not used. Providers include `amazon` (documented
  with IRSA, the admission controller needs `ecr:GetAuthorizationToken` and
  `ecr:BatchGetImage`, and `AWS_ROLE_ARN` plus `AWS_WEB_IDENTITY_TOKEN_FILE` in
  the pod) and `azure` (Workload Identity or managed identity, no setup steps
  documented).
- The lab defaults to EKS Pod Identity, which injects container credentials,
  not the IRSA variables the Kyverno doc names. AKS already has the OIDC
  issuer and Workload Identity on.
- The kind outage case runs `docker stop selfheal-p1-registry`. A managed
  registry cannot be stopped. Revoking the identity's access instead would hit
  IAM and Entra propagation delay (section 10 row 13) and make the 60s
  recovery clock measure propagation, not the verifier.
- On the clouds the registry is TLS, so `allowInsecureRegistry` goes away.

Proposed design
- ECR and ACR replace the local registry. Push the same digest into separate
  repositories, as on kind, and keep the local ECDSA key pair as trust root.
- EKS uses IRSA for the Kyverno admission controller only, because that is the
  documented path. AKS uses a user-assigned identity with AcrPull and a
  federated credential for the Kyverno service account.
- The outage is a CiliumNetworkPolicy that limits Kyverno's egress to the API
  server and DNS. Restoring it is deleting that policy. It is immediate,
  attributable through Hubble drops, and costs nothing. Deny rules cannot use
  `toFQDNs`, so the policy is an allow list, not a deny of the registry name.

## Row D1, backup, restore and WORM (ADR-0014)

Facts
- S3 Object Lock needs versioning. In COMPLIANCE mode "a protected object
  version can't be overwritten or deleted by any user, including the root
  user", and "the only way to delete an object under the compliance mode
  before its retention date expires is to delete the associated AWS account".
  A bucket holding a locked version cannot be emptied, so `terraform destroy`
  fails on it until retention expires. In GOVERNANCE mode a principal with
  `s3:BypassGovernanceRetention` can delete with the bypass header.
- Azure time-based retention has a one-day minimum. An unlocked policy blocks
  deletes but can be shortened or deleted. A locked policy cannot be deleted
  or shortened. With version-level WORM on a container or account, it "can be
  deleted only if it is empty". A storage account inside `rg-selfheal-lab`
  holding retained blobs would block deleting the lab resource group, the same
  class of failure as the 11/10 ResourceGroupDeletionBlocked.
- The EKS module installs no EBS CSI driver (add-ons are vpc-cni, kube-proxy,
  coredns, the Pod Identity agent and the node monitoring agent). D1's
  PostgreSQL PVCs would stay Pending on EKS. AKS ships the Azure Disk CSI
  driver.
- ADR-0014 deferred transport encryption and external identity federation to
  the cloud proof. Keeping MinIO in-cluster on the clouds would prove nothing
  new.

Proposed design
- Replace MinIO with S3 Object Lock on EKS and version-level immutable Blob
  storage on AKS. The backup Job reaches them over TLS with Pod Identity and
  Workload Identity. The backup identity gets put, get, list and version reads,
  never delete.
- Put the WORM store in its own small Terraform root, apart from `infra/aws`
  and `infra/azure`, and on Azure in its own resource group. The lab destroy
  then never waits on retention.
- Add the `aws-ebs-csi-driver` add-on with its own Pod Identity role and a gp3
  StorageClass to the EKS module.
- Retention mode is Decision B.

## Row O1, operational readiness (ADR-0015)

Facts
- Everything O1 judges is Kubernetes API behaviour (ResourceQuota, LimitRange,
  RBAC resource names, Lease, NetworkPolicy). Cilium enforces NetworkPolicy on
  both clouds.
- The runner checks the operator with `kubectl auth can-i ... --as
  system:serviceaccount:selfheal-o1:o1-operator` and patches with `--as`.
  Impersonation needs the caller to hold `impersonate` on service accounts. On
  EKS the runner is mapped to cluster admin by access entry. On AKS the module
  sets `azure_rbac_enabled = true` with local accounts disabled, so the
  runner's Entra role must grant the impersonate data action. That is not
  settled by what was read today.
- ADR-0015 left cloud notification delivery to the cloud port.
- The controller replacement clock (60s) includes an image pull if the
  replacement lands on a node that never pulled the image.

Proposed design
- Same contract and thresholds on both clouds. Pin the controller to one
  labelled node per session so the replacement pulls from a warm cache, or
  record a cold pull and keep 60s. No threshold is changed before evidence.
- Alert delivery is Decision C.

## Open decisions for Jordan

A. Kubernetes version for the remaining cloud runs. Recommended is 1.36 on
   both clouds. It is inside Cilium 1.20.4 and Karpenter 1.15 support, keeps
   the existing VPA pin on every target, matches the AKS Q2 session, and moves
   off 1.34 before its 2026-12-02 standard-support end. The alternative keeps
   1.34 and adds a VPA 1.6.x pin for the clouds only. EKS on 1.36 has not run
   yet, so that becomes a live check.

B. D1 retention mode. Recommended is COMPLIANCE on S3 and a locked one-day
   policy on Blob, in the separate roots above. That proves what kind proved,
   that even an administrator cannot delete the version. The cost is a store
   that outlives the session by at least 24 hours (cents) and a second destroy
   the next day. The alternative is GOVERNANCE and an unlocked policy, which
   tears down the same day. The administrator check would then have to be
   restated as "rejected without an explicit bypass", which is a weaker claim.

C. O1 alert delivery. Recommended is to keep the in-cluster receiver as the
   judged sink on both clouds and leave cloud paging out of this row. The
   alternative adds SNS and an Azure Monitor action group, which adds IAM and
   a second delivery clock to a row that has none today.

D. Session shape. Recommended is one EKS session and one AKS session, each
   running O1, Q3, P1 and D1 in that order, then N1 last because it
   reconfigures Cilium. Cluster create time and the shared platform make that
   much cheaper than ten sessions. Each row keeps its own run id, evidence
   directory and verdict. The alternative is one row per session, which
   isolates failures but pays the 43-minute AKS create every time.

## Suggested order

1. O1. No new infrastructure, the smallest port, and it settles the AKS
   impersonation question for later rows.
2. Q3. Chart and version only.
3. P1. Identity wiring and the CNP outage.
4. D1. New Terraform roots, EBS CSI, and the retention decision.
5. N1. A new EKS hypothesis and capture path, and it must run last in a
   session.

Each row still gets its own ADR (0019 onward) fixing thresholds before its
first cloud run, as ADR-0016 and ADR-0017 did.

## Live-check list (the docs do not settle these)

| # | Row | Check | Why |
|---|---|---|---|
| L1 | all | EKS 1.36 with Cilium 1.20.4 ENI, Karpenter 1.15.1 and every pinned chart reaches the readiness gate | Only AKS has run 1.36 |
| L2 | Q3 | Distinct metrics-server samples per minute on AKS and the time to the first VPA recommendation | 60s resolution against a 240s clock and six samples |
| L3 | N1 | `modprobe wireguard` and `cilium status` on AL2023 for the pinned AMI | No AWS source states CONFIG_WIREGUARD for AL2023 |
| L4 | N1 | Which interface carries pod traffic under ENI IPAM, and that WireGuard peers use the node primary IPs | Secondary ENIs, and no doc covers WireGuard with ENI IPAM |
| L5 | N1 | MTU after enabling WireGuard on 9001-byte ENIs, with no fragmentation of the probe | Cilium docs mention MTU only for chaining |
| L6 | P1 | The Kyverno `amazon` provider with IRSA, and whether it also works with Pod Identity | The doc names IRSA only |
| L7 | P1 | The Kyverno `azure` provider with Workload Identity against ACR | No setup steps in the doc |
| L8 | P1 | Cosign 2.6.3 legacy signature tags in ECR and ACR are found by Kyverno 1.19.2 | The kind proof used Distribution 3.1.2 |
| L9 | D1 | Object Lock delete by the backup role returns AccessDenied, and the administrator version delete returns 403 | Error shape the runner parses |
| L10 | D1 | Blob delete error codes under a locked version-level policy | Error shape the runner parses |
| L11 | O1 | `kubectl auth can-i --as system:serviceaccount:...` works for the runner's Entra identity under Azure RBAC | Impersonation data action |

## References (read 2026-11-10)

- VPA compatibility, <https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/installation.md>
- AKS VPA overview, <https://learn.microsoft.com/en-us/azure/aks/vertical-pod-autoscaler>
- AKS VPA how-to, <https://learn.microsoft.com/en-us/azure/aks/use-vertical-pod-autoscaler>
- EKS Kubernetes versions, <https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html>
- Cilium Kubernetes compatibility, <https://docs.cilium.io/en/stable/network/kubernetes/compatibility/>
- Karpenter compatibility, <https://karpenter.sh/docs/upgrading/compatibility/>
- Cilium WireGuard, <https://docs.cilium.io/en/stable/security/network/encryption-wireguard/>
- Cilium ENI IPAM, <https://docs.cilium.io/en/stable/network/concepts/ipam/eni/>
- AWS Containers blog, WireGuard and Cilium on EKS (2022-11-25), <https://aws.amazon.com/blogs/containers/transparent-encryption-of-node-to-node-traffic-on-amazon-eks-using-wireguard-and-cilium>
- Cilium deny policies (limitations list toFQDNs), <https://docs.cilium.io/en/latest/security/policy/deny/>
- Kyverno releases, <https://github.com/kyverno/kyverno/releases>
- Kyverno ImageValidatingPolicy, <https://kyverno.io/docs/policy-types/image-validating-policy/>
- S3 Object Lock, <https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html>
- Azure immutable blob storage, <https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview>
