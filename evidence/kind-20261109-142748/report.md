# Self-healing run `kind-20261109-142748` (kind)

- Started 2026-11-09T20:27:59.871Z, finished 2026-11-09T20:29:00.033Z
- Rows: D1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| D1 | impact | **healed** | backup d1-backup-1794256103-x8nmg version 4c4abcfe-15d8-40d3-be9e-8eefd4f3ef1d completed in 3s; restored 100 exact rows into selfheal-d1-restore/d1 in 14s; RPO 1 row and 3s; truncated restore failed atomically True; writer and WORM deletion denied True |

## Per row

### D1

**Steady-state hypothesis:** A completed PostgreSQL backup restores exactly 100 deterministic rows into a fresh target within 180 seconds, loses only the one intentional post-backup row, rejects a truncated archive atomically, and remains readable after identity and WORM deletion attempts.

Healing control: A retained logical PostgreSQL backup restores exact data into a fresh target and rejects deletion. Thresholds (kind): `{"backup_s": 120, "restore_s": 120, "rto_s": 180, "rpo_rows": 1, "rows": 100, "retention_days": 1}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:28:01 |  |  |  |  |  |  | - | impact: no backup recovery point; source PVC replacement removed the deterministic data and no restore was available |
| hardened | 20:28:23 | 3s | 14s |  |  |  |  | - | healed: backup d1-backup-1794256103-x8nmg version 4c4abcfe-15d8-40d3-be9e-8eefd4f3ef1d completed in 3s; restored 100 exact rows into selfheal-d1-restore/d1 in 14s; RPO 1 row and 3s; truncated restore failed atomically True; writer and WORM deletion denied True |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "selfheal-d1-store/d1-minio": true,
    "selfheal-d1-source/d1-source": true,
    "d1_synced": true
  },
  "versions": {
    "repo_commit": "908f75f075ca9d4b51d79aa0037a6acbbb44d1dc-dirty",
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
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "kubernetes": "v1.37.0"
  }
}
```

D1 records the deterministic source digest, backup Job identity and completion, retained object version and checksums, authorized source PVC replacement, exact fresh-target restore, RPO and RTO, an atomic malformed restore failure, and two deletion denials.
