# Self-healing run `kind-20261108-150850` (kind)

- Started 2026-11-08T21:08:54.427Z, finished 2026-11-08T21:11:49.638Z
- Rows: Q3 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q3 | impact | **healed** | 6 samples, CPU p90 200.807m, memory p90 108.562MiB; recommendation 247m/144645763 at 41s, ratios 1.23/1.27; bounded True, requests unchanged True, pod unchanged True, restart delta 0 |

## Per row

### Q3

**Steady-state hypothesis:** Under sustained CPU and memory pressure, the hardened VPA publishes a bounded recommendation within 240s that is related to measured use, while Off mode leaves requests, the running pod, and restart count unchanged. The baseline has the same pressure and telemetry but no sizing recommendation.

Healing control: VPA recommender compares measured CPU and memory use with recommendation-only targets while requests and the running pod remain unchanged. Thresholds (kind): `{"recommend_s": 240, "min_samples": 6, "cpu_pressure_m": 100, "memory_pressure_mib": 64, "cpu_ratio_min": 0.7, "cpu_ratio_max": 2.0, "memory_ratio_min": 0.9, "memory_ratio_max": 2.0, "restarts": 0}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 21:09:00 |  |  |  |  |  |  | - | impact: 6 samples, CPU p90 242.326m, memory p90 108.566MiB; no VPA recommendation; requests 50m/32Mi |
| hardened | 21:10:16 | 41s | 41s |  |  |  |  | - | healed: 6 samples, CPU p90 200.807m, memory p90 108.562MiB; recommendation 247m/144645763 at 41s, ratios 1.23/1.27; bounded True, requests unchanged True, pod unchanged True, restart delta 0 |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "kube-system/metrics-server": true,
    "vpa/vpa-vertical-pod-autoscaler-recommender": true,
    "selfheal/q3-target": true,
    "q3_synced": true
  },
  "versions": {
    "repo_commit": "d193139e25d3f53812ce971b19000ebb4ccda227-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": null,
    "falco-talon": null,
    "argo-rollouts": null,
    "external-secrets": null,
    "cert-manager": null,
    "chaos-controller-manager": null,
    "reloader-reloader": null,
    "keda-operator": null,
    "rabbitmq": null,
    "qworker": null,
    "q2-signal": null,
    "q2-target": null,
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "selfheal-q3:v1",
    "kubernetes": "v1.37.0"
  }
}
```

Q3 records resource Metrics API samples and the VPA status recommendation. The VPA runs in Off mode, so the report also verifies unchanged requests, pod identity, and restart count.
