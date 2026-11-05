# Self-healing run `aks-run1-a3` (aks)

- Started 2026-11-05T23:05:21.853Z, finished 2026-11-05T23:19:32.287Z
- Rows: A3 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A3 | impact | **miss** | sustain window p95 320.2 ms, errors 0.00%, replicas 3 -> 7 |

## Per row

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (aks): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 23:08:11 |  |  | 328 | 77 | 23.48% | 47.0x | - | impact: sustain window p95 2215.9 ms, errors 23.48%, replicas 3 -> 3 |
| hardened | 23:15:44 | 42s | 42s | 316 | 0 | 0.00% | 0.0x | - | miss: sustain window p95 320.2 ms, errors 0.00%, replicas 3 -> 7 |

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
