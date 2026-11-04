# Self-healing run `eks-run2-rows` (eks)

- Started 2026-11-04T16:44:06.897Z, finished 2026-11-04T17:35:59.997Z
- Rows: A4, S1b, R1, R2 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A4 | error | **inconclusive** | baseline was 'error'; a heal can only be claimed against an impact observed in the same run |
| S1b | impact | **healed** | 0 failed of 120 queries, 0 dropped connections, 2 rotations, users seen selfheal_app,selfheal_app_clone |
| R1 | impact | **miss** | detected 1s, isolated 35s (30 Hubble policy drops), evidence 53s, errors 0.00% |
| R2 | impact | **healed** | Backdoor:Runtime/C&CActivity.B!DNS seen 144s, isolated 155s (pod relabelled, node cordoned, quarantine group), evidence 172s (1 snapshots, 24195 B captured), errors 0.00% |

## Per row

### A4

**Steady-state hypothesis:** When the node hosting the most web pods is terminated, the error rate stays under 1% and every web replica is serving again, on a replacement node, within 300s.

Healing control: Karpenter replaces the node; topology spread and the PDB keep replicas serving; FIS stops on the error-rate alarm. Thresholds (eks): `{"recover_s": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 16:47:06 |  |  | 2109 | 3 | 0.14% | 0.3x | - | error: the FIS experiment did not terminate a node ({'status': 'completed', 'reason': 'Experiment completed.'}) |
| hardened | 17:23:15 | 55s | 78s | 1500 | 0 | 0.00% | 0.0x | - | inconclusive: baseline was 'error'; a heal can only be claimed against an impact observed in the same run |

### S1b

**Steady-state hypothesis:** Rotating the database password twice (both alternating users) causes 0 failed queries and 0 dropped connections.

Healing control: both credential versions mounted and re-read per connection; alternating users keep the previous one valid. Thresholds (eks): `{"failed": 0, "dropped": 0}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 16:54:37 | 6s | 168s | 228 | 64 | 28.07% | 56.1x | - | impact: 64 failed of 228 queries, 0 dropped connections, 2 rotations, users seen selfheal_app_clone |
| hardened | 17:28:43 | 6s | 60s | 120 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 120 queries, 0 dropped connections, 2 rotations, users seen selfheal_app,selfheal_app_clone |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (eks): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 16:58:54 | 1s |  | 396 | 0 | 0.00% | 0.0x | - | impact: detected 1s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 17:31:13 | 1s | 35s | 300 | 0 | 0.00% | 0.0x | - | miss: detected 1s, isolated 35s (30 Hubble policy drops), evidence 53s, errors 0.00% |

### R2

**Steady-state hypothesis:** A runtime threat GuardDuty reports in a web pod is isolated at pod and node level within 600s and evidence is preserved within 900s, with the pod and node kept for investigation.

Healing control: GuardDuty Runtime Monitoring -> EventBridge -> Step Functions: isolate pod and node, preserve, investigate. Thresholds (eks): `{"isolate_s": 600, "evidence_s": 900, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 17:00:43 | 160s |  | 5147 | 0 | 0.00% | 0.0x | - | impact: Backdoor:Runtime/C&CActivity.B!DNS seen 160s, isolated never (pod relabelled, node cordoned, quarantine group), evidence never (0 snapshots, 0 B captured), errors 0.00% |
| hardened | 17:32:46 | 144s | 155s | 876 | 0 | 0.00% | 0.0x | - | healed: Backdoor:Runtime/C&CActivity.B!DNS seen 144s, isolated 155s (pod relabelled, node cordoned, quarantine group), evidence 172s (1 snapshots, 24195 B captured), errors 0.00% |

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
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:2aac581-v1",
    "workload_ami": [
      "ami-09696b17488e356ab"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`, the R2 bundle (manifest, capture, audit window, finding, execution history) in `R2/r2-hardened/`.
