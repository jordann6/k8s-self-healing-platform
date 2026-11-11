# Self-healing run `kind-20261108-131854` (kind)

- Started 2026-11-08T19:18:59.628Z, finished 2026-11-08T19:28:50.918Z
- Rows: Q2 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q2 | impact | **healed** | metric error detected 3s, fallback 10s, metric recovered 3s; outage p95 154.3 ms, failures 0.0033259423503325942; ceiling 4 of 4 (limited True); Pending 3 (0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.); capacity recovered 8s, scale-down 21s |

## Per row

### Q2

**Steady-state hypothesis:** When the scaling metric fails, the failure is visible within 30s and the hardened fallback reaches three replicas within 30s while meeting the request SLO. Restored telemetry recovers within 45s, the maximum-replica and CPU scheduling ceilings are explicitly observed, restored capacity recovers within 60s, and withdrawn demand scales back to one within 90s.

Healing control: KEDA fallback preserves bounded capacity during metric failure; explicit autoscaler and scheduler evidence distinguishes metric, replica, and node ceilings. Thresholds (kind): `{"detect_s": 30, "fallback_s": 30, "metric_recover_s": 45, "capacity_recover_s": 60, "scale_down_s": 90, "p95_ms": 300, "error_rate": 0.01, "fallback_replicas": 3, "max_replicas": 4, "min_replicas": 1, "min_request_samples": 20}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 19:19:30 | 5s | 4s |  |  |  |  | - | impact: metric error detected 5s, fallback never, metric recovered 4s; outage p95 214.2 ms, failures 0.4652711703139867; ceiling 4 of 4 (limited True); Pending 3 (0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.); capacity recovered 3s, scale-down 16s |
| hardened | 19:24:24 | 3s | 3s |  |  |  |  | - | healed: metric error detected 3s, fallback 10s, metric recovered 3s; outage p95 154.3 ms, failures 0.0033259423503325942; ceiling 4 of 4 (limited True); Pending 3 (0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 No preemption victims found for incoming pod.); capacity recovered 8s, scale-down 21s |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "keda/keda-operator": true,
    "keda/keda-operator-metrics-apiserver": true,
    "keda/keda-admission-webhooks": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "selfheal/q2-signal": true,
    "selfheal/q2-target": true,
    "q2_synced": true
  },
  "versions": {
    "repo_commit": "d38552b6489dfd6d10bc05598a295c0ad04a4f70-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": null,
    "falco-talon": null,
    "argo-rollouts": null,
    "external-secrets": null,
    "cert-manager": null,
    "chaos-controller-manager": null,
    "reloader-reloader": null,
    "keda-operator": "ghcr.io/kedacore/keda:2.21.0",
    "rabbitmq": null,
    "qworker": null,
    "q2-signal": "selfheal-q2:v1",
    "q2-target": "selfheal-q2:v1",
    "kubernetes": "v1.37.0"
  }
}
```

Q2 records metric-source availability, ScaledObject and HPA conditions, replica and Pending-pod timelines, scheduler reasons, node CPU capacity, and timestamped request samples.
