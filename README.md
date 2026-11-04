# Kubernetes Self-Healing Platform

A cluster that recovers on its own from outages, security drift and an active
threat, proven with before/after experiments. The third lab in the arc after
the EKS Attack Range (gain access, detect) and the Kubernetes Network
Segmentation Lab (contain): this one responds and recovers.

**Status:** phase 1 (portable core on kind, zero cloud spend). The AWS, Azure
and GCP modules come next.

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

## What broke (and what it taught)

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
