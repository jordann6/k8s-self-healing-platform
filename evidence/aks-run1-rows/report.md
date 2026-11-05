# Self-healing run `aks-run1-rows` (aks)

- Started 2026-11-05T17:55:34.008Z, finished 2026-11-05T20:52:41.652Z
- Rows: S3, S4, R1, R2 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| S3 | impact | **healed** | policy restored 2s, hand edit reverted 10s |
| S4 | impact | **miss** | policy deployment none (-), rule closed never, non-compliant reported never |
| R1 | impact | **healed** | detected 1s, isolated 8s (22 Hubble policy drops), evidence 24s, errors 0.00% |
| R2 | error | **error** | no Defender alert naming the attacked pod within 3600s: the trigger never fired, so there was nothing to respond to |

## Per row

### S3

**Steady-state hypothesis:** A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.

Healing control: Argo CD self-heal re-applies Git. Thresholds (aks): `{"revert_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 17:58:37 |  |  |  |  |  |  | - | impact: policy restored never, hand edit reverted never |
| hardened | 19:28:55 | 2s | 10s |  |  |  |  | - | healed: policy restored 2s, hand edit reverted 10s |

### S4

**Steady-state hypothesis:** An inbound rule opening the node NSG to 0.0.0.0/0 is closed again by the Azure Policy deployIfNotExists deployment within 1200s.

Healing control: Azure Policy deployIfNotExists rewrites the open rule to Deny. Thresholds (aks): `{"revert_s": 1200}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 18:01:14 | 996s |  |  |  |  |  | - | impact: policy deployment none (-), rule closed never, non-compliant reported 996s |
| hardened | 19:29:26 |  |  |  |  |  |  | - | miss: policy deployment none (-), rule closed never, non-compliant reported never |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (aks): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 18:22:49 | 1s |  | 386 | 0 | 0.00% | 0.0x | - | impact: detected 1s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 19:51:02 | 1s | 8s | 300 | 0 | 0.00% | 0.0x | - | healed: detected 1s, isolated 8s (22 Hubble policy drops), evidence 24s, errors 0.00% |

### R2

**Steady-state hypothesis:** A threat Defender for Containers reports in a web pod is isolated (node cordoned, then pod relabelled into quarantine) within 300s of the alert and evidence (OS-disk snapshot, Run Command capture, manifest) is preserved within 600s of the alert, with the pod and node kept.

Healing control: Defender for Containers -> workflow automation -> Logic App -> queue -> worker cordons, then relabels; the Logic App snapshots the OS disk and runs the capture. Thresholds (aks): `{"alert_s": 3600, "isolate_s": 300, "evidence_s": 600, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 18:25:58 |  |  | 18056 | 0 | 0.00% | 0.0x | - | error: no Defender alert naming the attacked pod within 3600s: the trigger never fired, so there was nothing to respond to |
| hardened | 19:52:22 |  |  | 18070 | 0 | 0.00% | 0.0x | - | error: no Defender alert naming the attacked pod within 3600s: the trigger never fired, so there was nothing to respond to |

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
