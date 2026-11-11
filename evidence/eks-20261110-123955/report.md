# Self-healing run `eks-20261110-123955` (eks)

- Started 2026-11-10T18:40:12.534Z, finished 2026-11-10T18:44:27.007Z
- Rows: Q1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q1 | impact | **healed** | published 240, distinct done 240 (0 duplicate completions), replicas 0 to 5 to 0, drained 37s, back to zero 64s, killed qworker-6d4c5579cc-gmhlk (unacked 1) |

## Per row

### Q1

**Steady-state hypothesis:** A backlog of 240 messages scales the worker from zero, drains within 120s with no lost work even when a busy worker is killed mid-message, and the worker returns to zero, while the fixed baseline never scales to zero.

Healing control: KEDA scales the worker from zero on queue depth and back to zero; manual-ack redelivery keeps a killed worker from losing work. Thresholds (eks): `{"drain_s": 120, "zero_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 18:41:09 |  | 52s |  |  |  |  | - | impact: published 240, distinct done 240 (0 duplicate completions), replicas 2 to 2 to 2, drained 52s, back to zero never, killed qworker-6d4c5579cc-m4z9r (unacked 1) |
| hardened | 18:42:57 | 16s | 37s |  |  |  |  | - | healed: published 240, distinct done 240 (0 duplicate completions), replicas 0 to 5 to 0, drained 37s, back to zero 64s, killed qworker-6d4c5579cc-gmhlk (unacked 1) |

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

Q1 uses RabbitMQ queue telemetry and distinct completion IDs. The measured observations are in `report.json`.
