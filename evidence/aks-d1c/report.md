# Self-healing run `aks-d1c` (aks)

- Started 2026-11-11T15:19:02.221Z, finished 2026-11-11T15:22:39.431Z
- Rows: D1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| D1 | impact | **healed** | backup d1-backup-1794410462-8bc97 version 2026-11-11T15:21:06.6307678Z completed in 10s; restored 100 exact rows into selfheal-d1-restore/d1 in 48s; RPO 1 row and 10s; truncated restore failed atomically True; writer and WORM deletion denied True |

## Per row

### D1

**Steady-state hypothesis:** A completed PostgreSQL backup restores exactly 100 deterministic rows into a fresh target within 180 seconds, loses only the one intentional post-backup row, rejects a truncated archive atomically, and remains readable after identity and WORM deletion attempts.

Healing control: A retained logical PostgreSQL backup restores exact data into a fresh target and rejects deletion. Thresholds (aks): `{"backup_s": 120, "restore_s": 120, "rto_s": 180, "rpo_rows": 1, "rows": 100, "retention_days": 1, "admin_attribution": true, "retention_mode": "Locked"}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:19:42 |  |  |  |  |  |  | - | impact: no backup recovery point; source PVC replacement removed the deterministic data and no restore was available |
| hardened | 15:20:59 | 10s | 48s |  |  |  |  | - | healed: backup d1-backup-1794410462-8bc97 version 2026-11-11T15:21:06.6307678Z completed in 10s; restored 100 exact rows into selfheal-d1-restore/d1 in 48s; RPO 1 row and 10s; truncated restore failed atomically True; writer and WORM deletion denied True |

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
    "repo_commit": "02b9dbd7669f5661e985d0af92e3a32a1cba8fce-dirty",
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
    "qworker": "acrselfhealebab1v.azurecr.io/selfheal/qworker:20ce7a0",
    "q2-signal": "acrselfhealebab1v.azurecr.io/selfheal/q2:20ce7a0",
    "q2-target": "acrselfhealebab1v.azurecr.io/selfheal/q2:20ce7a0",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "acrselfhealebab1v.azurecr.io/selfheal/q3:20ce7a0",
    "n1-server": "acrselfhealebab1v.azurecr.io/selfheal/n1:20ce7a0",
    "n1-client": "acrselfhealebab1v.azurecr.io/selfheal/n1:20ce7a0",
    "n1-denied": "acrselfhealebab1v.azurecr.io/selfheal/n1:20ce7a0",
    "o1-controller": "acrselfhealebab1v.azurecr.io/selfheal/o1:20ce7a0",
    "o1-alert-receiver": "acrselfhealebab1v.azurecr.io/selfheal/o1:20ce7a0",
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealebab1v.azurecr.io/selfheal/app:20ce7a0-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

D1 records the deterministic source digest, backup Job identity and completion, retained object version and checksums, authorized source PVC replacement, exact fresh-target restore, RPO and RTO, an atomic malformed restore failure, and two deletion denials.
