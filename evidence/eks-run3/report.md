# Self-healing run `eks-run3` (eks)

- Started 2026-11-06T23:38:22.174Z, finished 2026-11-06T23:59:32.142Z
- Rows: A4, R1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A4 | impact | **healed** | killed i-073e318eae1babf84 (1 web pods), errors 0.00%, all replicas serving 72s, replacement node ip-10-30-1-193.ec2.internal |
| R1 | impact | **healed** | detected 1s, isolated 11s (26 Hubble policy drops), evidence 30s, errors 0.00% |

## Per row

### A4

**Steady-state hypothesis:** When the node hosting the most web pods is terminated, the error rate stays under 1% and every web replica is serving again, on a replacement node, within 300s.

Healing control: Karpenter replaces the node; topology spread and the PDB keep replicas serving; FIS stops on the error-rate alarm. Thresholds (eks): `{"recover_s": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 23:40:15 | 60s | 77s | 1500 | 307 | 20.47% | 40.9x | - | impact: abort line hit at 24.1s: 100% of 50 http requests failed in 10s |
| hardened | 23:52:56 | 48s | 72s | 1500 | 0 | 0.00% | 0.0x | - | healed: killed i-073e318eae1babf84 (1 web pods), errors 0.00%, all replicas serving 72s, replacement node ip-10-30-1-193.ec2.internal |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (eks): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 23:45:46 | 1s |  | 422 | 0 | 0.00% | 0.0x | - | impact: detected 1s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 23:58:30 | 1s | 11s | 299 | 0 | 0.00% | 0.0x | - | healed: detected 1s, isolated 11s (26 Hubble policy drops), evidence 30s, errors 0.00% |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "kube-system/karpenter": true,
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
    "repo_commit": "66a190fbe6f77385523bae95dbe0f99c5cda5453",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.34.12-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:66a190f-v1",
    "workload_ami": [
      "ami-09696b17488e356ab"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`, the R2 bundle (manifest, capture, audit window, finding, execution history) in `R2/r2-hardened/`.
