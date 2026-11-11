# Self-healing run `eks-20261109-211212` (eks)

- Started 2026-11-10T03:12:43.391Z, finished 2026-11-10T03:17:53.848Z
- Rows: Q1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q1 | impact | **error** | runner exception: TimeoutError: timed out after 180s waiting for the Q1 ScaledObject Ready |

## Per row

### Q1

**Steady-state hypothesis:** A backlog of 240 messages scales the worker from zero, drains within 120s with no lost work even when a busy worker is killed mid-message, and the worker returns to zero, while the fixed baseline never scales to zero.

Healing control: KEDA scales the worker from zero on queue depth and back to zero; manual-ack redelivery keeps a killed worker from losing work. Thresholds (eks): `{"drain_s": 120, "zero_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:13:24 |  | 44s |  |  |  |  | - | impact: published 240, distinct done 240 (0 duplicate completions), replicas 2 to 2 to 2, drained 44s, back to zero never, killed none (unacked None) |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: TimeoutError: timed out after 180s waiting for the Q1 ScaledObject Ready |

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
    "selfheal/rabbitmq": true,
    "selfheal/qworker": true,
    "q1_synced": true
  },
  "versions": {
    "repo_commit": "e9d83417255de4755571d45e93c2ef4d7f2d0e35-dirty",
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
    "qworker": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/qworker:e9d8341",
    "q2-signal": null,
    "q2-target": null,
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": null,
    "o1-alert-receiver": null,
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.34.12-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:e9d8341-v1",
    "workload_ami": [
      "ami-09696b17488e356ab"
    ]
  }
}
```

Q1 uses RabbitMQ queue telemetry and distinct completion IDs. The measured observations are in `report.json`.
