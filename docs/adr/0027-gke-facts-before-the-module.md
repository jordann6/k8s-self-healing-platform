# ADR-0027: GKE facts verified before the GCP module is written

- **Status:** proposed (2026-11-18). Nothing is built or applied. Decisions
  D1 to D5 at the end wait for Jordan.
- **Date:** 2026-11-18

## Context

The EKS session hit about fifteen live bugs that a docs-first pass would have
caught, and ADR-0007 did the same pass for AKS. This ADR does it for GKE. Each
row takes one AWS misstep, names the GKE mechanism, quotes the official
documentation (Google Cloud docs unless noted, read 2026-11-18; the pages
carry no visible update date), and records the decision it forces. What the
docs do not settle goes on the live-check list at the end.

### Scope

GKE runs the in-cluster rows only: A1 to A5, S2, S3 and R1 (Falco, then Talon,
then the quarantine CiliumNetworkPolicy). The GCP-native responses are already
proven live in the GCP landing zone repository and are referenced, not rebuilt:

| Row | GCP-native proof (landing zone, 2026-11-18) |
|---|---|
| S1 source rotation | `make test-rotation`, 10 passed. Rotated 1s after schedule, one enabled version, client reconnected over TLS with the new value |
| S4 firewall revert | `make test-remediation`, 9 passed. An open firewall rule disabled 22s after creation through Cloud Asset feed, Pub/Sub, handler |
| R2 Pub/Sub response | `make test-incident` live, 24 passed (2026-10-06), and the event remediation above. Finding to Pub/Sub to a private handler that tags, labels, snapshots, stops and detaches |
| Native SLO burn rate | Cloud Run SLO, slow-burn ticket and fast-burn page opened and closed on the Monitoring alerts API |

Source of record is `docs/completion.md`, section "Proof, 2026-11-18", in the
landing zone repository (its PR #6 is still open, so the README link points to
the file on that branch until it merges). The README states plainly that
these ran on a different GKE cluster and project, not on this lab's cluster.

## What the organization holds today (read-only, 2026-11-18)

| Check | Result | Consequence |
|---|---|---|
| Org policies in force | Only Google's six secure-by-default constraints (`iam.disableServiceAccountKeyCreation`, `iam.disableServiceAccountKeyUpload`, `iam.automaticIamGrantsForDefaultServiceAccounts`, `storage.uniformBucketLevelAccess`, `compute.restrictProtocolForwardingCreationForTypes`, `compute.setNewProjectDefaultToZonalDNSOnly`) | The landing zone's own constraints (no external IPs, Shielded VM, OS Login, private-nodes custom constraint, `cost_center` label) are already gone. The lab meets them anyway (hardening section) so it still applies cleanly if the landing zone is deployed again |
| Custom constraints | None | Nothing rejects a cluster shape |
| Folders | `workloads` still ACTIVE | Landing zone teardown is not finished |
| Landing zone projects | `net-prod` and `app-prod` ACTIVE, `logging` and `images` DELETE_REQUESTED | Teardown is in progress (the Cloud Run subnet address blocks the network destroy, completion.md item 10). **No GCP apply until Jordan confirms it finished** |
| Projects linked to the billing account | 5 (`idp-platform`, `jn-gitops-seed`, `jn-lz-seed`, `jn-lz-net-prod`, `jn-lz-app-prod`) | See the billing cap section |

## Billing and project cap (flag before any live session)

Two different limits, and they behave differently:

- **Project quota.** "Until a project is permanently deleted (after the 30-day
  window), it continues to count toward your project quota." "The project
  remains in a `DELETE_REQUESTED` state for 30 days."
  [Delete and restore projects](https://docs.cloud.google.com/resource-manager/docs/delete-restore-projects)
  So all four landing zone projects keep counting against project creation
  until about 2026-12-18, whether or not they are linked to billing.
- **Billing link count.** On shutdown "Any associated Cloud Billing account is
  disconnected" (same page), so a deleted project frees its billing link at
  once. Five projects are linked today. The billing quotas page
  ([Cloud Billing quotas](https://docs.cloud.google.com/billing/quotas)) does
  not document a link limit; five is a common default, so the safe reading is
  that a sixth link would fail until `net-prod` and `app-prod` are deleted.

Decision D1 picks the project. Nothing creates a project or a billing link
until Jordan answers it.

## Cluster hardening (the landing zone paved road, carried over)

Reused from `gcp-landing-zone/workload/gke.tf`, which was proven live. Every
control is either kept or dropped with the reason.

| Control | Lab | Why |
|---|---|---|
| Private nodes, no external IPs | Kept | Matches the landing zone custom constraint. Egress through Cloud NAT (charts pull from quay.io, ghcr.io, docker.io) |
| Private IP endpoint, operator access through the DNS endpoint | Kept | No authorized-network list to chase when the public IP changes (the EKS lockout in the 11/10 session). Needs `container.clusters.connect` (landing zone live finding 8) |
| Workload Identity, `GKE_METADATA` on every pool, legacy endpoints off | Kept | Pods never read node credentials |
| Least-privilege node service account (log writer, metric writer, registry reader) | Kept | Not the default compute account |
| Shielded nodes, secure boot, integrity monitoring | Kept | Matches `compute.requireShieldedVm` |
| COS containerd image | Kept | Falco's documented path on GKE (row 16) |
| No client certificate, no basic auth | Kept | IAM only |
| Release channel REGULAR, auto-upgrade, auto-repair, surge 1 / unavailable 0 | Kept, with a maintenance exclusion over the session | An upgrade mid-run would be an unrecorded fault |
| Security posture BASIC, vulnerability scanning BASIC | Kept | Free tier of the feature |
| Intranode visibility | Dropped | It forces traffic through the VPC for flow logs; BYO Cilium (D2) owns the datapath and Hubble covers flow visibility |
| Dataplane V2 | Replaced by pinned Cilium | D2 |
| Application-layer secrets encryption with Cloud KMS | Off by default, a variable | A key ring can never be deleted and a key only schedules version destruction, so verify-gone would report a permanent leftover. The production answer is CMEK, stated in the README |
| Binary Authorization enforce | Off | The lab pulls upstream chart images nobody here attests; P1 (signed images) is out of GKE scope. Production answer in the README |
| Shared VPC | Dropped | Own VPC in the lab project, so no Shared VPC lien can block teardown (row 2) |

## Findings, row by row

### 1. CNI and what GKE installs

**AWS:** EKS module v21 installed no CNI, kube-proxy or CoreDNS; nodes stayed
NotReady and the node group timed out after 30 minutes.

**GKE mechanism:** Dataplane V2 (managed Cilium), legacy datapath with
optional Calico, or a third-party CNI on the legacy datapath.

**Docs:**
- "GKE Dataplane V2 is implemented using Cilium." "GKE Dataplane V2 uses
  `cilium` instead of `kube-proxy` to implement Kubernetes Services." "GKE
  Dataplane V2 can only be enabled when creating a new cluster."
  [About Dataplane V2](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dataplane-v2)
- Cilium policy on Dataplane V2 is the cluster-wide CRD: "Your cluster must
  use GKE Dataplane V2." "Layer 7 policies are not supported." "Node selectors
  are not supported." The page shows `endpointSelector`, `fromEndpoints`,
  `toPorts` and `toCIDR` and says deny rules "override allow rules in
  namespace-scoped policies", with no deny example; entities (`host`,
  `world`, `all`) are not covered.
  [Configure CiliumClusterwideNetworkPolicy](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-cilium-network-policy)
- Hubble on Dataplane V2 is a separate feature, "disabled by default"; the
  page does not state its price.
  [About Dataplane V2 observability](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-dpv2-observability)
- Third-party CNI: "Although other third-party custom CNIs might function in
  your cluster, GKE doesn't officially support them."
  [Learn GKE networking architecture](https://docs.cloud.google.com/kubernetes-engine/docs/learn/learn-gke-net-architecture)
- Cilium's own GKE install (Cilium 1.20.5 docs): "The cluster should be
  created with the taint `node.cilium.io/agent-not-ready=true:NoExecute`";
  Helm values `nodeinit.enabled=true`, `nodeinit.reconfigureKubelet=true`,
  `nodeinit.removeCbrBridge=true`, `cni.binPath=/home/kubernetes/bin`,
  `gke.enabled=true`, `ipam.mode=kubernetes`, `ipv4NativeRoutingCIDR` set to
  the cluster's `clusterIpv4Cidr`. "The NodeInit DaemonSet is required to
  prepare the GKE nodes as nodes are added to the cluster."
  [Cilium Helm install, GKE](https://docs.cilium.io/en/stable/installation/k8s-install-helm/)

**Decision (D2, recommended):** legacy datapath, GKE network policy off,
pinned Cilium 1.20.4 with the GKE values above, both pools created with the
agent-not-ready taint. The R1 quarantine policy uses `ingressDeny`,
`egressDeny` and the `host` entity, none of which the Dataplane V2 page
confirms, and the lab's rule is that only manifests change between targets
(ADR-0001); with BYO Cilium the same quarantine manifest runs unchanged on
kind, EKS, AKS and GKE, and Hubble is the same open-source relay. The cost of the
choice is that Google does not support the CNI, exactly as Microsoft does not on AKS
BYO CNI (ADR-0007 row 1). GKE's legacy datapath keeps kube-proxy, so
`kubeProxyReplacement: "false"` as on AKS. The alternative (Dataplane V2 plus a
cluster-wide quarantine policy rewritten for it) is recorded, not chosen.

### 2. Org guardrails that block teardown

**AWS:** an org SCP kept AWS Config, so teardown could not finish.

**GKE mechanism:** org policy constraints, project liens (Shared VPC adds
one), VPC Service Controls.

**Docs:** the landing zone itself documents that a Shared VPC host "cannot
have its lien removed" while `compute.restrictXpnProjectLienRemoval` is
enforced (landing zone `terraform/org_policies.tf`).

**Decision:** own VPC, no Shared VPC, no lien. Preflight re-reads org
policies and custom constraints, and lists liens on the lab project, and
fails closed if anything new appears. No VPC Service Controls perimeter
exists today (none created by the landing zone).

### 3. Managed metrics authorization

**AWS:** AMP with a CMK needed caller KMS rights, and a cached denial
outlived the fix.

**GKE mechanism:** Managed Service for Prometheus. "GKE Standard clusters
running GKE version 1.27 or greater" have managed collection on by default.
It scrapes through PodMonitoring CRs, and the page offers only a manual
conversion for prometheus-operator objects ("You can convert a ServiceMonitor
CR to a PodMonitoring CR by mapping the fields"); it does not say it reads
PrometheusRule.
[Managed collection](https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/setup-managed)

**Decision:** managed collection off at create. The in-cluster rows use the
kind stack (kube-prometheus-stack, Sloth PrometheusRules, the A2
AnalysisTemplate) unchanged. No managed metrics path means no metric writer
grant to propagate and no CMEK. The native SLO with burn-rate alerts is the
landing zone proof (Scope).

### 4, 5, 6. Secret versions, detector lag, stale secrets

S1 and R2 are out of GKE scope (landing zone proofs). S2 uses the in-cluster
CA and cert-manager exactly as kind does. No row reads Secret Manager or a
detector API, so these three AWS missteps have no GKE counterpart in this
module. ESO is still installed because the base profile's SecretStore uses the
Kubernetes provider on kind, unchanged.

### 7. Responder race and stateful firewalls

**AWS:** the R2 replacement pod landed on the node being quarantined;
security groups kept the existing kubelet connection.

**GKE:** R2 node isolation is a landing zone proof. R1 is pod-level only and
runs on the same Cilium as kind, so this module adds no firewall-based
isolation. Carried over from the ADR-0002 addendum, Cilium's identity grace period
(5s) and identity jitter (up to 30s) can push R1 past its 30s bound; the bound
is not changed, and a miss is reported.

### 8. Topology spread and the autoscaler

**AWS:** spread without minDomains spread nothing; the autoscaler reacts only
to Pending pods.

**Docs:** "The cluster autoscaler does not support strict Pod topology spread
constraints when the `whenUnsatisfiable` field is set to the `DoNotSchedule`
value. You can soften the spread requirements by setting the
`whenUnsatisfiable` field to the `ScheduleAnyway` value." (Limitations).
"If Pods fail to be scheduled on any of the current nodes, the cluster
autoscaler adds nodes." Scale-down can be blocked when "The node's deletion
would exceed the configured PodDisruptionBudget."
[Cluster autoscaler](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/cluster-autoscaler)

**Decision (D3, recommended):** the workload pool is fixed at four nodes with
the autoscaler off, and keeps the AKS hardened spread (`DoNotSchedule`,
`minDomains: 3`, `nodeTaintsPolicy: Honor`). A4 stops one node, three domains
remain, the evicted replicas schedule at once and no autoscaler decision sits
in the measured path. The alternative (three nodes, autoscaler on,
`ScheduleAnyway`) follows the doc but makes the spread best-effort, which is
the EKS bug again.

### 9. Chaos targeting

**AWS:** a stale tag made FIS stop the wrong node.

**Docs:** "When you delete managed instances, the MIG reduces the targetSize
of the group and deletes the corresponding VM instances." Acting on a VM
outside the group manager means "the MIG might automatically attempt to
recreate or revert that VM according to the MIG's configuration."
[Working with managed instances](https://docs.cloud.google.com/compute/docs/instance-groups/working-with-managed-instances)

**Decision:** A4 resolves the VM from the chosen node's `spec.providerID`
(`gce://<project>/<zone>/<instance>`) at injection time and runs
`gcloud compute instances stop` on that name, matching the AKS stop-by-ID and
its clock (errors judged from NotReady on, ADR-0007 D3). Never
`delete-instances` (shrinks the pool) and never a label or tag selector. What
the MIG and auto-repair do to a stopped VM is live check L4.

### 10. Long port-forwards through the managed API

**AWS:** port-forwards died over two hours; a public IP change locked the
runner out.

**Docs:** the DNS-based endpoint gives "a unique and immutable DNS or fully
qualified domain name (FQDN) for each cluster control plane", reachable from
"any network reachable by Google Cloud APIs", authorized by IAM. The page does
not mention streaming, port-forward or timeouts.
[Network isolation](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/network-isolation)

**Decision:** DNS endpoint only, so an IP change cannot lock the runner out.
The runner already restarts port-forwards on failure (kind and AKS); whether
they survive a multi-hour run over the DNS endpoint is live check L5.

### 11. Cloud-created leftovers at teardown

**AWS:** a PDB stranded nodes; resources the cluster created outlived
Terraform.

**Docs:** on delete GKE removes "Any firewalls and routes created by GKE at the
time of cluster creation". "GKE retains persistent disk volumes during cluster
deletion." "GKE attempts to delete all load balancer resources", and Services
of type LoadBalancer should be deleted "before deleting your cluster."
[Deleting a cluster](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/deleting-a-cluster)
Repair and autoscaler drains wait one hour, and the autoscaler respects PDBs
(row 8, row 17).

**Decision:** the in-scope rows create no PVC and no LoadBalancer Service
(checked in `gitops/` and `platform/`). `gke-predestroy` still stops the Argo
CD application controller first (the 11/18 EKS bug, where child apps
recreated a Gateway), deletes the apps, then any Service of type
LoadBalancer and any PVC, and removes PDBs from the lab namespace so a drain
cannot stall. `gke-verify-gone` lists, in the lab project, instances, MIGs,
disks, firewall rules, routes, forwarding rules, NEGs, Cloud NAT and router,
Artifact Registry repos, and service accounts, and prints "gone" or "LEFT"
per line.

### 12. Pricing

**Docs:** "$0.10 per cluster per hour" for all modes; the "$74.40 monthly
credit" is "equivalent to one free Autopilot or zonal Standard cluster per
month", per billing account, and "cannot be applied to ... the cluster fee for
Regional clusters."
[GKE pricing](https://cloud.google.com/kubernetes-engine/pricing) (page did not
render in full here; figures from Google's own search snippet, to recheck)

**Decision:** one zonal Standard cluster in us-central1, so the fee is
covered. Seven e2-standard-2 nodes (three system, four workload), pd-balanced
50 GB boot disks, one Cloud NAT. Rough figure about $0.55 per hour before the
Infracost run (`make gke-cost` against the saved plan, Jordan runs
`! infracost auth login` if needed). No Dataplane V2 observability, no managed
Prometheus ingestion, no Container Threat Detection (SCC Premium, skipped per
the plan).

### 13. IAM eventual consistency

**Docs:** policy changes take "Typically 2 minutes, potentially 7 minutes or
longer".
[Access change propagation](https://docs.cloud.google.com/iam/docs/access-change-propagation)

**Decision:** node service account roles and the registry reader grant are
created before the node pools (explicit `depends_on` plus a `time_sleep` of
120s), so the first image pull does not race the grant. The runner's
identity needs `container.clusters.connect` and the cluster role it already
uses; no grant is made mid-run.

### 14. Public access defaults

`storage.publicAccessPrevention` was a landing zone constraint and is gone;
only `storage.uniformBucketLevelAccess` remains (read above). The module
creates no bucket. If one is ever added it sets
`public_access_prevention = "enforced"` itself instead of relying on org
policy.

### 15. An existing responder acting on lab resources

**AWS:** `aws-incident-forensics` reacted to lab findings.

**GKE:** the landing zone incident handler subscribes to the SCC topic in the
landing zone's own projects, which are being deleted, and SCC is not
activated for the organization (completion.md). Preflight confirms no Eventarc
trigger, Pub/Sub push subscription, Cloud Run service or Cloud Function in the
lab project, and no org-level SCC notification config, and fails closed if one
exists. Reading SCC needs a quota project on the caller's credentials (seen
2026-11-18), so preflight sets `--billing-project` to the lab project.

### 16. Agent DaemonSets on tainted nodes

**Docs (falco.org, Specific Environments, GKE):** "Falco cannot insert its
Kernel Module" on COS. "To use Falco on GKE, you need to deploy using the
Modern eBPF." "The Modern eBPF is the default driver for Falco 0.38.0 and
later."
[Falco on GKE](https://falco.org/docs/setup/enviroments/)

**Decision:** `driver.kind: modern_ebpf`, already the lab's value
(`platform/falco.yaml`). Falco must tolerate the workload taint the lab sets
(same as AKS). The Cilium agent-not-ready taint is NoExecute and lifts when
Cilium is ready, so Falco is installed after Cilium. Whether the COS kernel in
the 1.36 node image loads the probe is live check L3.

### 17. Node repair interplay

**Docs:** a node is unhealthy if it "reports a `NotReady` status on
consecutive checks over the given time threshold (approximately 10 minutes)";
then "the node is drained and re-created. This process preserves the original
name of the node." "If the drain doesn't complete, the node is shut down and a
new node is created." Repairs show in `gcloud container operations list` with
`operationReason` such as `AUTO_REPAIR_LONG_UNHEALTHY`.
[Node auto-repair](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/node-auto-repair)

**Decision:** auto-repair stays on. A4's window is 600s from NotReady, inside
the 10 minute repair threshold, so repair is not part of the judged heal; the
runner records any repair operation that lands during or after the window
and starts the VM itself afterwards. Not a judged row (same call as EKS N1
node auto-repair).

### Also checked: HPA input on GKE (A3)

AKS's managed metrics-server is fixed at 60s and A3 was a near miss until it
moved to KEDA on Prometheus. GKE's HPA page documents a Performance HPA
profile ("improves the reaction time of horizontal Pod autoscaling", Standard
clusters on 1.33 or later get it automatically) and does not state the
metrics-server resolution.
[Horizontal Pod autoscaling](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/horizontal-pod-autoscaling)

**Decision (D4, recommended):** use the AKS A3 shape (KEDA ScaledObject on the
in-cluster Prometheus cAdvisor CPU, HPA named `web`), installed from the
pinned KEDA chart because GKE has no KEDA add-on. One A3 implementation on
both managed clouds. Live check L6 records the metrics-server resolution for
the README.

## Decisions for Jordan

- **D1, project.** (a) A new dedicated project `jn-selfheal-<suffix>`, created
  only after `net-prod` and `app-prod` are deleted (frees two billing links);
  at the end the module is destroyed and the empty project kept with billing
  unlinked, so the lab adds no further pending-deletion project to the quota.
  (b) Reuse an already linked project (`jn-gitops-seed` or `idp-platform`),
  which needs no new link or project but mixes the lab with that project's
  IAM and leftovers. Recommended (a).
- **D2, CNI.** BYO Cilium 1.20.4 on the legacy datapath (recommended, row 1)
  or Dataplane V2 with a rewritten cluster-wide quarantine policy.
- **D3, A4 pool.** Four fixed workload nodes, strict spread (recommended,
  row 8), or three with the autoscaler and `ScheduleAnyway`.
- **D4, A3.** KEDA on Prometheus as on AKS (recommended) or plain HPA on GKE's
  metrics-server.
- **D5, CMEK.** Application-layer secrets encryption off with the production
  answer in the README (recommended, hardening section), or on, accepting a
  key ring that verify-gone always reports.

## Proposed GKE thresholds (into ADR-0003 and `THRESHOLDS["gke"]` before the first run)

Shared rows keep their kind and EKS numbers (A3 p95 300 ms, R1 30s). A4 keeps the AKS bound, every web replica
serving within 600s and errors under 1% from NotReady on, because the mechanism (stop one VM, evict on a 30s hardened
toleration, reschedule onto remaining nodes) is the same and no node has to be
created.

## Live checks (runbook-gcp.md section 2; the docs do not settle these)

- **L1** BYO Cilium on GKE 1.36 legacy datapath: pools create with the
  NoExecute taint, NodeInit reconfigures kubelet, kube-dns and GKE system
  pods come up once Cilium is ready, no auto-repair fires during bootstrap.
- **L2** The quarantine CiliumNetworkPolicy (`ingressDeny`, `egressDeny`,
  `host` entity) isolates the pod on GKE and kubelet probes still pass.
- **L3** Falco modern eBPF loads on the node image's COS kernel.
- **L4** A stopped node VM: does the MIG restart or recreate it, when the
  shutdown taint and NotReady appear, and whether auto-repair acts at about
  10 minutes (`operationReason`).
- **L5** Hubble, Prometheus and prober port-forwards across a multi-hour run
  over the DNS endpoint.
- **L6** The GKE metrics-server resolution and whether the Performance HPA
  profile is on (recorded, not judged).
- **L7** Regional CPU quota in the lab project covers 14 vCPUs plus one
  surge node.
- **L8** Cloud NAT egress reaches every chart registry; Artifact Registry
  pulls work with the node service account.
- **L9** GKE's own DaemonSets (`gke-metrics-agent`, logging agent) fit beside
  the stack on e2-standard-2 system nodes.
- **L10** After destroy, verify-gone prints nothing LEFT.

## Gaps stated in the README, not hidden

- S1, S4 and R2 on GCP ran in the landing zone, on a different cluster.
- The CNI is not supported by Google (D2), as on AKS.
- Container Threat Detection is skipped (SCC Premium).
- CMEK and Binary Authorization are off in the lab; both are the production
  answer.

## Sources (read 2026-11-18)

Google Cloud docs: About Dataplane V2; Configure CiliumClusterwideNetworkPolicy;
About Dataplane V2 observability; Learn GKE networking architecture; Network
isolation; Cluster autoscaler; Node auto-repair; Horizontal Pod autoscaling;
Deleting a cluster; Working with managed instances; Managed Service for
Prometheus managed collection; Access change propagation; Delete and restore
projects; Cloud Billing quotas; GKE pricing. Also: Cilium 1.20.5 Helm install
(GKE); Falco Specific Environments (GKE). Landing zone repository:
`workload/gke.tf`, `terraform/org_policies.tf`, `docs/completion.md`.
