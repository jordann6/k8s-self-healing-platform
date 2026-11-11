# Self-healing run `aks-20261110-q1` (aks)

- Started 2026-11-10T15:47:58.615Z, finished 2026-11-10T15:51:31.410Z
- Rows: Q1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q1 | impact | **miss** | published 240, distinct done 240 (0 duplicate completions), replicas 0 to 5 to 0, drained 33s, back to zero 60s, killed none (unacked None) |

## Per row

### Q1

**Steady-state hypothesis:** A backlog of 240 messages scales the worker from zero, drains within 120s with no lost work even when a busy worker is killed mid-message, and the worker returns to zero, while the fixed baseline never scales to zero.

Healing control: KEDA scales the worker from zero on queue depth and back to zero; manual-ack redelivery keeps a killed worker from losing work. Thresholds (aks): `{"drain_s": 120, "zero_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:48:34 |  | 38s |  |  |  |  | - | impact: published 240, distinct done 240 (0 duplicate completions), replicas 2 to 2 to 2, drained 38s, back to zero never, killed none (unacked None) |
| hardened | 15:50:14 | 14s | 33s |  |  |  |  | - | miss: published 240, distinct done 240 (0 duplicate completions), replicas 0 to 5 to 0, drained 33s, back to zero 60s, killed none (unacked None) |

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
    "selfheal/rabbitmq": true,
    "selfheal/qworker": true,
    "q1_synced": true
  },
  "versions": {
    "repo_commit": "96facb8c4d275b7f79a03f9ae6619bb7c388469a",
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
    "qworker": "acrselfhealzmouo2.azurecr.io/selfheal/qworker:96facb8",
    "q2-signal": null,
    "q2-target": null,
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.34.12",
    "web_image": "acrselfhealzmouo2.azurecr.io/selfheal/app:96facb8-v1",
    "node_images": [
      "AKSUbuntu-2204gen2containerd-202610.30.1"
    ]
  }
}
```

Q1 uses RabbitMQ queue telemetry and distinct completion IDs. The measured observations are in `report.json`.
