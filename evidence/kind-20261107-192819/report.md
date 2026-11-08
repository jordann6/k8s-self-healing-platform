# Self-healing run `kind-20261107-192819` (kind)

- Started 2026-11-08T01:28:20.950Z, finished 2026-11-08T01:30:25.024Z
- Rows: Q1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q1 | impact | **healed** | published 240, distinct done 240 (0 duplicate completions), replicas 0 to 5 to 0, drained 30s, back to zero 56s, killed qworker-598766ff55-8tmwq (unacked 1) |

## Per row

### Q1

**Steady-state hypothesis:** A backlog of 240 messages scales the worker from zero, drains within 120s with no lost work even when a busy worker is killed mid-message, and the worker returns to zero, while the fixed baseline never scales to zero.

Healing control: KEDA scales the worker from zero on queue depth and back to zero; manual-ack redelivery keeps a killed worker from losing work. Thresholds (kind): `{"drain_s": 120, "zero_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 01:28:26 |  | 34s |  |  |  |  | - | impact: published 240, distinct done 240 (0 duplicate completions), replicas 2 to 2 to 2, drained 34s, back to zero never, killed none (unacked None) |
| hardened | 01:29:19 | 10s | 30s |  |  |  |  | - | healed: published 240, distinct done 240 (0 duplicate completions), replicas 0 to 5 to 0, drained 30s, back to zero 56s, killed qworker-598766ff55-8tmwq (unacked 1) |

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
    "selfheal/rabbitmq": true,
    "selfheal/qworker": true,
    "q1_synced": true
  },
  "versions": {
    "repo_commit": "cf9a6377c5de88da1d62fa880b58192e85c2fe62-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": null,
    "falco-talon": null,
    "argo-rollouts": null,
    "external-secrets": null,
    "cert-manager": null,
    "chaos-controller-manager": null,
    "reloader-reloader": null,
    "keda-operator": "ghcr.io/kedacore/keda:2.21.0",
    "rabbitmq": "rabbitmq:3.13.7-management",
    "qworker": "selfheal-qworker:v1",
    "kubernetes": "v1.37.0"
  }
}
```

Q1 uses RabbitMQ queue telemetry and distinct completion IDs. The measured observations are in `report.json`.
