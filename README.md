# Kubernetes Self-Healing Platform

A cluster that recovers on its own from outages, security drift and an active
threat, proven with before/after experiments. The third lab in the arc after
the EKS Attack Range (gain access, detect) and the Kubernetes Network
Segmentation Lab (contain): this one responds and recovers.

**Status:** phase 1 (portable core on kind, zero cloud spend) and phase 2 (the
AWS module, `infra/aws/`, every row on EKS) have both run live. On EKS 11 of 13
hardened rows healed, one missed and one was inconclusive (see below); the
cluster was then destroyed. Azure and GCP come after.

## The experiment

Every row states a steady-state hypothesis, injects one fault, and runs twice:
a **baseline** profile and a **hardened** profile. Only manifests and policy
differ between the runs ([ADR-0001](docs/adr/0001-change-only-manifests.md)).
User impact is measured by an independent prober, never by the app's own
metrics.

| Row | Fault | Injected with | Healing control |
|---|---|---|---|
| A1 | App deadlocks, then a second pod is killed | app toggle, Chaos Mesh PodChaos | readiness + liveness probes, replicas, preStop |
| A2 | Bad release (30% 5xx) | a Git commit of a new image tag | Argo Rollouts canary + Prometheus analysis on the canary's own pods, auto-abort |
| A3 | 4x traffic spike | k6 | HPA |
| A5 | Memory leak | app toggle | memory limit, kubelet restart, OOM alert with runbook |
| S1 | API key rotated in the secret backend | backend write + partner revocation after a grace window | ESO 10s refresh + Reloader restart |
| S2 | Certificate reaches its renewal window | see [ADR-0004](docs/adr/0004-in-cluster-git-and-forced-renewal.md) | cert-manager renewal + TLS hot reload |
| S3 | Network policy deleted, Deployment hand-edited | kubectl | Argo CD self-heal |
| R1 | Shell spawned in a web pod | kubectl exec | Falco -> Falcosidekick -> Talon relabel -> quarantine CiliumNetworkPolicy; evidence captured before the pod is deleted |

On EKS (phase 2) the same eight rows run unchanged, plus five that need a cloud:

| Row | Fault | Injected with | Healing control |
|---|---|---|---|
| A4 | The node hosting the most web pods dies | AWS FIS instance terminate, stopped by a CloudWatch alarm on the prober's error rate | Karpenter replacement, PDB, topology spread across zones |
| S1b | Database password rotated twice | Secrets Manager alternating-users rotation | both secret versions mounted and re-read per connection |
| S4 | Lab security group opened to 0.0.0.0/0 | an ingress rule | AWS Config rule + custom SSM Automation revoke (any port) |
| S5 | Every workload node replaced for a new AMI | EC2NodeClass AMI alias change | Karpenter drift, one node at a time, PDB |
| R2 | Runtime threat in a web pod | DNS lookup of GuardDuty's C2 test domain | GuardDuty Runtime Monitoring -> EventBridge -> Step Functions: quarantine pod and node, snapshot, Run Command capture, evidence to SSE-KMS S3 |

The AWS design is in [ADR-0006](docs/adr/0006-aws-module-design.md), the facts
it rests on in [ADR-0005](docs/adr/0005-aws-build-time-verification.md), and
the live session (apply from saved plans, a four-step teardown, verify-gone)
in [docs/runbook-aws.md](docs/runbook-aws.md).

The runner exits non-zero unless every hardened row is healed. A hardened row
counts as healed only if the same run's baseline showed impact; a row that
cannot be measured is an error, never a skip (`scenarios/judge.py`, unit
tested by `make test`).

## Run it

```
make up      # kind + Cilium + platform charts + bootstrap
make demo    # baseline then hardened, every row -> evidence/<run-id>/report.md
make down
```

Needs docker (about 4 GB free for the cluster), kind, kubectl, helm, cilium,
hubble, jq, openssl 3.4+ and python3. The lab uses its own kubeconfig
(`.kube/config`), so it never touches other clusters on the machine.

## Stack (pinned in the Makefile)

Cilium 1.20.4, Argo CD v3.5.7, Argo Rollouts v1.10.1, External Secrets
v2.14.0, Reloader v1.4.23, cert-manager v1.21.3, Falco 0.45.2 + Falcosidekick
2.35.1 + Falco Talon 0.3.0, kube-prometheus-stack 92.17.1, Sloth v0.13.0,
Chaos Mesh 2.8.7, k6 v2.3.0. CI: platform-guardrails v1.10.0.

## Phase 1 result (kind)

Two reference runs on the same commit (`evidence/kind-run5`, `evidence/kind-run7`):
every baseline row showed impact, and hardened A1, A2, A5, S1, S2, S3 and R1
healed in both. Hardened A3 met its error bar (0.3%) and scaled 3 to 8 and 3 to
10 pods, but missed its 300 ms p95 bar (758 ms, 697 ms). The runner reports
that as a FAIL and is meant to: the 300 ms bar is not lowered for kind, and
A3 latency is proven on EKS instead.

## Phase 2 result (EKS)

One bounded live session on 2026-11-04 (EKS 1.34, two t3.large system nodes,
Karpenter c6a.large workload nodes, about $6 for roughly ten hours up). Reference evidence:
`evidence/eks-run2` (every row), `evidence/eks-run2-rows` and
`evidence/eks-run2-a4r1` (the rows rerun after fixes). Every fix made after a
run was observed is listed in [ADR-0003](docs/adr/0003-per-target-thresholds.md)
with the run that prompted it; no threshold was changed.

| Row | Hardened result on EKS | Run |
|---|---|---|
| A1 | healed: 0.00% errors after detection, hung pod serving again at 35s | eks-run2 |
| A2 | healed: canary aborted at 67s on measured errors, only v1 after, 0.00% | eks-run2 |
| A3 | healed: sustained-spike p95 **267 ms** (bar 300), 0.00% errors, HPA 3 to 8 | eks-run2 |
| A4 | **inconclusive**: hardened met every criterion twice (0.00% and 0.13% errors, every replica serving at 78s), but neither same-run baseline showed impact (one errored on a stale FIS tag, one had its pods spread already) | eks-run2-rows, eks-run2-a4r1 |
| A5 | healed: OOMKilled at 10s, serving and paged at 14s | eks-run2 |
| S1 | healed: 0 failed of 1856 across a key rotation | eks-run2 |
| S1b | healed: 0 failed queries, 0 dropped connections across two alternating-user rotations; the original password was rejected | eks-run2-rows |
| S2 | healed: 0 TLS errors, renewed certificate served at 86s | eks-run2 |
| S3 | healed: policy restored at 2s, hand edit reverted at 4s | eks-run2 |
| S4 | healed: Config flagged the open group at 108s, the SSM document revoked it at 132s (bar 600) | eks-run2 |
| S5 | healed: all 3 workload nodes replaced for a new AMI in 256s, 0 failed, never more than 1 disrupted | eks-run2 |
| R1 | **miss**: isolated at 34.6s against 30s (`eks-run2-rows`); 16.1s in `eks-run2-a4r1` | eks-run2-rows |
| R2 | healed: GuardDuty finding at 144s, pod and node isolated at 155s, evidence (snapshot, Run Command capture, manifest) at 172s, 0.00% errors | eks-run2-rows |

A3 is the row kind could not prove: on worker nodes that do not host the
control plane, the same 300 ms bar holds. A4's hardened behaviour is measured
and recorded, but the runner only claims a heal against a baseline impact in
the same run, and the baseline has no spread rule, so whether its replicas
share a node depends on how many workload nodes already exist; `eks-run2`'s
baseline is the one that shows the failure (all three replicas on the killed
node, 56% errors). R1 met 30s once and missed once on EKS: Talon labels the pod
within 2s, and the rest is Cilium applying the quarantine identity plus the
runner proving unreachability through `kubectl exec` over the internet.

## What broke (and what it taught)

### On AWS (phase 2)

- **Module v21 installs no CNI.** terraform-aws-modules/eks v21 creates
  clusters with `bootstrap_self_managed_addons = false`, so the VPC CNI,
  kube-proxy and CoreDNS never appeared, the nodes stayed NotReady and the node
  group failed after 30 minutes. They are now explicit add-ons (CoreDNS
  tolerates the Cilium startup taint, and pods it started are restarted under
  Cilium).
- **A replacement pod landed on the node being quarantined.** The R2 responder
  relabelled the pod out of the Service, the Rollout created a replacement at
  once, and the scheduler bound it to the same node 0.2s before the cordon.
  The security group swap then cut it off while it still reported Ready
  (security groups are stateful, so the kubelet's API connection survived),
  and a third of requests failed for 100s. Cordon first, then relabel.
- **Topology spread without `minDomains` spreads nothing.** With one workload
  node, `DoNotSchedule` on hostname saw one domain and a skew of 0, so all
  three replicas sat on it and a node kill took every one. `minDomains` makes
  the missing nodes count, so the extra replicas go Pending and Karpenter
  launches a node for each.
- **AMP on a customer-managed key needs the caller's KMS permission.** Remote
  write returned 403 on `kms:GenerateDataKey`; after the fix AMP kept serving
  a cached denial for about four minutes.
- **Secrets Manager leaves `AWSPENDING` on the new current version.** Waiting
  for the label to disappear waited out every rotation.
- **ListFindings lags EventBridge.** The responder had acted on the GuardDuty
  event before the finding was listable, so the runner now waits for the
  finding itself and times detection from its `CreatedAt`.
- **A baseline can be broken before the fault.** Earlier runs had rotated the
  database password, so the baseline probe was failing for 30 minutes before
  S1b began. The runner now requires a clean window before T0, and the judge
  rejects an impact that was already there.
- **The laptop is part of the experiment.** It slept during a rerun and froze
  the runner while the cluster carried on; port-forwards through the EKS API
  also die over a two-hour run. `make demo` now holds an idle-sleep assertion
  and the Hubble query reconnects and fails loudly. Two runners on one cluster
  (run by hand, by mistake) contaminated each other; those runs are not
  evidence.
- **A PDB can strand nodes at teardown.** With the NodePool deleted, the web
  PDB allowed 0 disruptions, Karpenter's drain blocked, and the teardown
  removed Karpenter with two nodes still running. Predestroy now deletes the
  app first and stops if any node survives.
- **The organization keeps Config.** The landing zone's SCP denies deleting
  Config recorders, delivery channels and rules in member accounts, so the
  lab's recorder, rule, delivery bucket and its key stay after teardown (about
  $1 a month) and `eks-verify-gone` reports them as kept.

### On kind (phase 1)

- **Reloader silently never restarted the Rollout.** Its chart grants RBAC on
  `rollouts.argoproj.io` only if that API exists at install time. Installed
  before Argo Rollouts, it logged `forbidden` forever and the rotated key never
  reached the pods. Install order is now fixed and commented.
- **An empty metric result aborted healthy canaries.** The analysis judged new
  canary pods before Prometheus had two samples for them. It now waits 45s, and
  the judge only accepts an abort driven by a measured error rate.
- **A strict readiness probe starved the autoscaler.** Busy pods failed
  readiness, left the Service, pushed their load onto the rest, and the HPA
  ignored their CPU. Then a 1s liveness probe killed busy, healthy pods and
  turned a spike into a restart storm. Fix: forgiving probes plus load
  shedding (`MAX_INFLIGHT`), so overload gets fast 503s instead of a queue.
- **`ignoreDifferences` outlived the HPA.** With the replica count ignored for
  the HPA's sake, switching to a profile without an HPA froze the Rollout at
  the HPA's last count. Only the profile that has an HPA ignores replicas now.
- **Cilium identity allocation dominated time-to-isolate.** Talon labelled the
  pod within 2s, but a brand-new identity took 16-38s to allocate through a
  loaded API server. The quarantine identity is now pre-allocated (ADR-0002).
- **A single laptop node is the ceiling.** The full stack starved its own API
  server until the platform was trimmed (ESO CRDs and webhook, Argo CD watch
  scope, Hubble ring, no cAdvisor scrape), and the A3 latency bar is not
  reachable when every pod shares eight vCPUs with the control plane: even at
  rest p95 is 130-230 ms. A3 keeps its 300 ms bar everywhere: on kind it is
  recorded as errors healed, latency not reachable on one shared node, and
  the latency claim is proven on EKS ([ADR-0003](docs/adr/0003-per-target-thresholds.md)).
