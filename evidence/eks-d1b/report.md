# Self-healing run `eks-d1b` (eks)

- Started 2026-11-11T03:56:55.438Z, finished 2026-11-11T04:02:01.600Z
- Rows: D1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| D1 | impact | **healed** | backup d1-backup-1794369609-kdgzv version iyyjH4WzQp045rhkSLMpl.KxHJekacI7 completed in 14s; restored 100 exact rows into selfheal-d1-restore/d1 in 43s; RPO 1 row and 14s; truncated restore failed atomically True; writer and WORM deletion denied True |

## Per row

### D1

**Steady-state hypothesis:** A completed PostgreSQL backup restores exactly 100 deterministic rows into a fresh target within 180 seconds, loses only the one intentional post-backup row, rejects a truncated archive atomically, and remains readable after identity and WORM deletion attempts.

Healing control: A retained logical PostgreSQL backup restores exact data into a fresh target and rejects deletion. Thresholds (eks): `{"backup_s": 120, "restore_s": 120, "rto_s": 180, "rpo_rows": 1, "rows": 100, "retention_days": 1, "admin_attribution": true}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:58:16 |  |  |  |  |  |  | - | impact: no backup recovery point; source PVC replacement removed the deterministic data and no restore was available |
| hardened | 04:00:02 | 14s | 43s |  |  |  |  | - | healed: backup d1-backup-1794369609-kdgzv version iyyjH4WzQp045rhkSLMpl.KxHJekacI7 completed in 14s; restored 100 exact rows into selfheal-d1-restore/d1 in 43s; RPO 1 row and 14s; truncated restore failed atomically True; writer and WORM deletion denied True |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "api_ready": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "d1_synced": true
  },
  "versions": {
    "repo_commit": "eaa5d2b84e008f8a60b64a9504c9be85d44402eb",
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
    "qworker": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/qworker:2bb4ee9",
    "q2-signal": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:2bb4ee9",
    "q2-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:2bb4ee9",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q3:2bb4ee9",
    "n1-server": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:2bb4ee9",
    "n1-client": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:2bb4ee9",
    "n1-denied": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:2bb4ee9",
    "o1-controller": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:2bb4ee9",
    "o1-alert-receiver": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:2bb4ee9",
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.36.5-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:2bb4ee9-v1",
    "workload_ami": [
      "ami-069787c4f4d0583ff"
    ]
  }
}
```

D1 records the deterministic source digest, backup Job identity and completion, retained object version and checksums, authorized source PVC replacement, exact fresh-target restore, RPO and RTO, an atomic malformed restore failure, and two deletion denials.
