# Self-healing run `eks-20261110-124524` (eks)

- Started 2026-11-10T18:45:48.740Z, finished 2026-11-10T19:07:18.982Z
- Rows: Q2 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q2 | impact | **healed** | metric error detected 6s, fallback 30s, metric recovered 22s; outage p95 149.6 ms, failures 0.0016666666666666668; ceiling 4 of 4 (limited True); Pending 3 (0/5 nodes are available: 2 node(s) didn't match Pod's node affinity/selector, 3 Insufficient cpu. no new claims to deallocate, preemption: 0/5 nodes are available: 2 Preemption is not helpful for scheduling, 3 No preemption victims found for incoming pod.); capacity recovered 14s, scale-down 23s |

## Per row

### Q2

**Steady-state hypothesis:** When the scaling metric fails, the failure is visible within 30s and the hardened fallback reaches three replicas within 30s while meeting the request SLO. Restored telemetry recovers within 45s, the maximum-replica and CPU scheduling ceilings are explicitly observed, restored capacity recovers within 60s, and withdrawn demand scales back to one within 90s.

Healing control: KEDA fallback preserves bounded capacity during metric failure; explicit autoscaler and scheduler evidence distinguishes metric, replica, and node ceilings. Thresholds (eks): `{"detect_s": 30, "fallback_s": 60, "metric_recover_s": 45, "capacity_recover_s": 90, "scale_down_s": 120, "p95_ms": 300, "error_rate": 0.01, "fallback_replicas": 3, "max_replicas": 4, "min_replicas": 1, "min_request_samples": 20, "node_autoscaler": true}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 18:47:44 | 14s | 20s |  |  |  |  | - | impact: metric error detected 14s, fallback never, metric recovered 20s; outage p95 219.7 ms, failures 0.5544041450777202; ceiling 4 of 4 (limited True); Pending 3 (0/5 nodes are available: 2 node(s) didn't match Pod's node affinity/selector, 3 Insufficient cpu. no new claims to deallocate, preemption: 0/5 nodes are available: 2 Preemption is not helpful for scheduling, 3 No preemption victims found for incoming pod.); capacity recovered 13s, scale-down 25s |
| hardened | 18:58:09 | 6s | 22s |  |  |  |  | - | healed: metric error detected 6s, fallback 30s, metric recovered 22s; outage p95 149.6 ms, failures 0.0016666666666666668; ceiling 4 of 4 (limited True); Pending 3 (0/5 nodes are available: 2 node(s) didn't match Pod's node affinity/selector, 3 Insufficient cpu. no new claims to deallocate, preemption: 0/5 nodes are available: 2 Preemption is not helpful for scheduling, 3 No preemption victims found for incoming pod.); capacity recovered 14s, scale-down 23s |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "keda/keda-operator": true,
    "keda/keda-operator-metrics-apiserver": true,
    "keda/keda-admission-webhooks": true,
    "q2_nodes_labelled": [
      "ip-10-30-12-120.ec2.internal",
      "ip-10-30-27-234.ec2.internal",
      "ip-10-30-9-196.ec2.internal"
    ],
    "selfheal/q2-signal": true,
    "selfheal/q2-target": true,
    "q2_synced": true
  },
  "versions": {
    "repo_commit": "6c1b2375675fd22c9ef482199fb4b53ade7012af-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "keda-operator": "ghcr.io/kedacore/keda:2.21.0",
    "rabbitmq": "rabbitmq:3.13.7-management",
    "qworker": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/qworker:841d4f9",
    "q2-signal": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:841d4f9",
    "q2-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:841d4f9",
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": null,
    "o1-alert-receiver": null,
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.34.12-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:841d4f9-v1",
    "workload_ami": [
      "ami-078ba44fb8cea0c34"
    ]
  }
}
```

Q2 records metric-source availability, ScaledObject and HPA conditions, replica and Pending-pod timelines, scheduler reasons, per-node CPU capacity, the node inventory around the Pending window, the KEDA version, and timestamped request samples.
