# Self-healing run `aks-20261110-151150` (aks)

- Started 2026-11-10T21:12:11.273Z, finished 2026-11-10T21:24:31.374Z
- Rows: Q2 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q2 | impact | **error** | runner exception: TimeoutError: timed out after 30s waiting for Q2 autoscaler metric error |

## Per row

### Q2

**Steady-state hypothesis:** When the scaling metric fails, the failure is visible within 30s and the hardened fallback reaches three replicas within 30s while meeting the request SLO. Restored telemetry recovers within 45s, the maximum-replica and CPU scheduling ceilings are explicitly observed, restored capacity recovers within 60s, and withdrawn demand scales back to one within 90s.

Healing control: KEDA fallback preserves bounded capacity during metric failure; explicit autoscaler and scheduler evidence distinguishes metric, replica, and node ceilings. Thresholds (aks): `{"detect_s": 30, "fallback_s": 60, "metric_recover_s": 45, "capacity_recover_s": 90, "scale_down_s": 120, "p95_ms": 300, "error_rate": 0.01, "fallback_replicas": 3, "max_replicas": 4, "min_replicas": 1, "min_request_samples": 20, "node_autoscaler": true}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 21:13:43 | 5s | 8s |  |  |  |  | - | impact: metric error detected 5s, fallback never, metric recovered 8s; outage p95 224.9 ms, failures 0.5165562913907285; ceiling 4 of 4 (limited True); Pending 3 (0/6 nodes are available: 3 Insufficient cpu, 3 node(s) didn't match Pod's node affinity/selector. no new claims to deallocate, preemption: 0/6 nodes are available: 3 No preemption victims found for incoming pod, 3 Preemption is not helpful for scheduling.); capacity recovered 11s, scale-down 18s |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: TimeoutError: timed out after 30s waiting for Q2 autoscaler metric error |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-aks",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "kube-system/keda-operator": true,
    "kube-system/keda-operator-metrics-apiserver": true,
    "kube-system/keda-admission-webhooks": true,
    "q2_nodes_labelled": [
      "aks-work-38857769-vmss000000",
      "aks-work-38857769-vmss000001",
      "aks-work-38857769-vmss000002"
    ],
    "selfheal/q2-signal": true,
    "selfheal/q2-target": true,
    "q2_synced": true
  },
  "versions": {
    "repo_commit": "59272b24754aa9ef3e7ee2e20deebd2711cb5932-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "keda-operator": null,
    "rabbitmq": "rabbitmq:3.13.7-management",
    "qworker": "acrselfhealzjuigy.azurecr.io/selfheal/qworker:59272b2",
    "q2-signal": "acrselfhealzjuigy.azurecr.io/selfheal/q2:59272b2",
    "q2-target": "acrselfhealzjuigy.azurecr.io/selfheal/q2:59272b2",
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealzjuigy.azurecr.io/selfheal/app:59272b2-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

Q2 records metric-source availability, ScaledObject and HPA conditions, replica and Pending-pod timelines, scheduler reasons, per-node CPU capacity, the node inventory around the Pending window, the KEDA version, and timestamped request samples.
