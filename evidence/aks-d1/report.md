# Self-healing run `aks-d1` (aks)

- Started 2026-11-11T15:05:01.092Z, finished 2026-11-11T15:07:26.400Z
- Rows: D1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| D1 | impact | **error** | runner exception: RuntimeError: D1 Job d1-backup-1794409632 unexpectedly failed: AttributeError: 'BlobClient' object has no attribute 'get'
 |

## Per row

### D1

**Steady-state hypothesis:** A completed PostgreSQL backup restores exactly 100 deterministic rows into a fresh target within 180 seconds, loses only the one intentional post-backup row, rejects a truncated archive atomically, and remains readable after identity and WORM deletion attempts.

Healing control: A retained logical PostgreSQL backup restores exact data into a fresh target and rejects deletion. Thresholds (aks): `{"backup_s": 120, "restore_s": 120, "rto_s": 180, "rpo_rows": 1, "rows": 100, "retention_days": 1, "admin_attribution": true, "retention_mode": "Locked"}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:05:46 |  |  |  |  |  |  | - | impact: no backup recovery point; source PVC replacement removed the deterministic data and no restore was available |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: RuntimeError: D1 Job d1-backup-1794409632 unexpectedly failed: AttributeError: 'BlobClient' object has no attribute 'get'
 |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-aks",
    "api_ready": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "d1_synced": true
  },
  "versions": {
    "repo_commit": "fa37786aa8006849eedf1b34ab84df7eeb99df83-dirty",
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
    "qworker": "acrselfhealebab1v.azurecr.io/selfheal/qworker:fa37786",
    "q2-signal": "acrselfhealebab1v.azurecr.io/selfheal/q2:fa37786",
    "q2-target": "acrselfhealebab1v.azurecr.io/selfheal/q2:fa37786",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "acrselfhealebab1v.azurecr.io/selfheal/q3:fa37786",
    "n1-server": "acrselfhealebab1v.azurecr.io/selfheal/n1:fa37786",
    "n1-client": "acrselfhealebab1v.azurecr.io/selfheal/n1:fa37786",
    "n1-denied": "acrselfhealebab1v.azurecr.io/selfheal/n1:fa37786",
    "o1-controller": "acrselfhealebab1v.azurecr.io/selfheal/o1:fa37786",
    "o1-alert-receiver": "acrselfhealebab1v.azurecr.io/selfheal/o1:fa37786",
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealebab1v.azurecr.io/selfheal/app:fa37786-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

D1 records the deterministic source digest, backup Job identity and completion, retained object version and checksums, authorized source PVC replacement, exact fresh-target restore, RPO and RTO, an atomic malformed restore failure, and two deletion denials.
