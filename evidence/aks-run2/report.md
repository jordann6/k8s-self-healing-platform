# Self-healing run `aks-run2` (aks)

- Started 2026-11-07T01:13:13.464Z, finished (running)
- Rows: A3, R2 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A3 | impact | **-** |  |
| R2 | error | **-** |  |

## Per row

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (aks): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 01:14:32 |  |  | 328 | 103 | 31.40% | 62.8x | - | impact: sustain window p95 2051.2 ms, errors 31.40%, replicas 3 -> 3 |

### R2

**Steady-state hypothesis:** A threat Defender for Containers reports in a web pod is isolated (node cordoned, then pod relabelled into quarantine) within 300s of the alert and evidence (OS-disk snapshot, Run Command capture, manifest) is preserved within 600s of the alert, with the pod and node kept.

Healing control: Defender for Containers -> workflow automation -> Logic App -> queue -> worker cordons, then relabels; the Logic App snapshots the OS disk and runs the capture. Thresholds (aks): `{"alert_s": 3600, "isolate_s": 300, "evidence_s": 600, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 01:18:44 |  |  | 18088 | 0 | 0.00% | 0.0x | - | error: no Defender alert naming the attacked pod within 3600s: the trigger never fired, so there was nothing to respond to |

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
    "repo_commit": "24516a3df5eea109b5f0ab305e4811895a941c47",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "kubernetes": "v1.34.12",
    "web_image": "acrselfhealxmzuyg.azurecr.io/selfheal/app:24516a3-v1",
    "node_images": [
      "AKSUbuntu-2204gen2containerd-202610.30.1"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`, the R2 bundle (the Defender alert as ARM returned it, the worker's isolated.json, the Logic App's manifest with the snapshot and Run Command capture, its runs) in `R2/r2-hardened/`.
