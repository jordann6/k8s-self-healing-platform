# Self-healing run `aks-run1-s4` (aks)

- Started 2026-11-05T20:53:45.082Z, finished 2026-11-05T21:22:03.757Z
- Rows: S4 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| S4 | impact | **healed** | policy deployment PolicyDeployment_12368233620176165940 (Succeeded), rule closed 121s, non-compliant reported 3s |

## Per row

### S4

**Steady-state hypothesis:** An inbound rule opening the node NSG to 0.0.0.0/0 is closed again by the Azure Policy deployIfNotExists deployment within 1200s.

Healing control: Azure Policy deployIfNotExists rewrites the open rule to Deny. Thresholds (aks): `{"revert_s": 1200}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:55:35 | 293s |  |  |  |  |  | - | impact: policy deployment none (-), rule closed never, non-compliant reported 293s |
| hardened | 21:19:54 | 3s | 121s |  |  |  |  | - | healed: policy deployment PolicyDeployment_12368233620176165940 (Succeeded), rule closed 121s, non-compliant reported 3s |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-aks",
    "responder/r2-worker": true,
    "kube-system/metrics-server": true,
    "monitoring/kps-operator": true,
    "cert-manager/cert-manager": true,
    "external-secrets/external-secrets": true,
    "reloader/reloader-reloader": true,
    "argo-rollouts/argo-rollouts": true,
    "argocd/argocd-application-controller": true,
    "chaos-mesh/chaos-controller-manager": true,
    "falco/falco": true,
    "falco/falco-falcosidekick": true,
    "falco/falco-talon": true,
    "gitops/gitserver": true,
    "workload_synced": true,
    "cilium_gate": true
  },
  "versions": {
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "kubernetes": "v1.34.12",
    "web_image": "acrselfhealdqd4ug.azurecr.io/selfheal/app:8b7df6f-v1",
    "node_images": [
      "AKSUbuntu-2204gen2containerd-202610.30.1"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`, the R2 bundle (the Defender alert as ARM returned it, the worker's isolated.json, the Logic App's manifest with the snapshot and Run Command capture, its runs) in `R2/r2-hardened/`.
