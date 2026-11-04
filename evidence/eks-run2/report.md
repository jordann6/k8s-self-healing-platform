# Self-healing run `eks-run2` (eks)

- Started 2026-11-04T12:05:22.716Z, finished 2026-11-04T13:57:50.604Z
- Rows: A1, A2, A3, A4, A5, S1, S1b, S2, S3, S4, S5, R1, R2
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A1 | impact | **healed** | error rate after detection 0.00%; hung pod restarted and serving at 35s |
| A2 | impact | **healed** | final error rate 0.00%, serving v1; canary aborted at 67s |
| A3 | impact | **healed** | sustain window p95 267.0 ms, errors 0.00%, replicas 3 -> 8 |
| A4 | impact | **miss** | abort line hit at 24.6s: 56% of 50 http requests failed in 10s |
| A5 | impact | **healed** | OOMKilled at 10s, recovered at 14s, alert at 14s, leaked pod memory at end None MiB, other restarts 0, errors 0.79% |
| S1 | impact | **healed** | 0 failed of 1856; key synced at 4s, pods on the new key at 20s |
| S1b | impact | **healed** | 0 failed of 84 queries, 0 dropped connections, 2 rotations, users seen selfheal_app,selfheal_app_clone |
| S2 | impact | **healed** | 0 TLS errors of 205; renewed at 1s, new certificate served at 86s |
| S3 | impact | **healed** | policy restored 2s, hand edit reverted 4s |
| S4 | impact | **healed** | non-compliant 108s, remediation SUCCEEDED, rule revoked 132s |
| S5 | impact | **healed** | 0 failed of 1461; 3 nodes replaced 256s, at most 1 disrupted at once, AMI ami-078ba44fb8cea0c34 -> ami-09696b17488e356ab |
| R1 | impact | **error** | reachability failed but Hubble shows no policy drop: not proven to be the policy |
| R2 | impact | **miss** | Backdoor:Runtime/C&CActivity.B!DNS seen 157s, isolated 251s (pod relabelled, node cordoned, quarantine group), evidence 251s (1 snapshots, 24195 B captured), errors 12.65% |

## Per row

### A1

**Steady-state hypothesis:** Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving within 60s, while one pod deadlocks and Chaos Mesh kills a second.

Healing control: readiness removes the hung pod; liveness restarts it. Thresholds (eks): `{"recover_s": 60, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:07:01 |  |  | 362 | 98 | 27.07% | 54.1x | - | impact: error rate after detection 27.07%; hung pod never recovered |
| hardened | 13:14:50 | 8s | 35s | 360 | 0 | 0.00% | 0.0x | - | healed: error rate after detection 0.00%; hung pod restarted and serving at 35s |

### A2

**Steady-state hypothesis:** A release that fails 30% of requests is stopped within 180s and only v1 serves afterwards, with the error rate back under 1%.

Healing control: Argo Rollouts canary analysis aborts; stable keeps serving. Thresholds (eks): `{"abort_s": 180, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:09:11 |  |  | 324 | 94 | 29.01% | 58.0x | - | impact: final error rate 24.67%, serving v2; no abort |
| hardened | 13:16:48 | 67s | 67s | 513 | 30 | 5.85% | 11.7x | - | healed: final error rate 0.00%, serving v1; canary aborted at 67s |

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (eks): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:11:19 |  |  | 310 | 95 | 30.65% | 61.3x | - | impact: sustain window p95 2330.5 ms, errors 30.65%, replicas 3 -> 3 |
| hardened | 13:19:28 | 32s | 32s | 329 | 0 | 0.00% | 0.0x | - | healed: sustain window p95 267.0 ms, errors 0.00%, replicas 3 -> 8 |

### A4

**Steady-state hypothesis:** When the node hosting the most web pods is terminated, the error rate stays under 1% and every web replica is serving again, on a replacement node, within 300s.

Healing control: Karpenter replaces the node; topology spread and the PDB keep replicas serving; FIS stops on the error-rate alarm. Thresholds (eks): `{"recover_s": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:16:16 |  |  | 1500 | 308 | 20.53% | 41.1x | - | impact: abort line hit at 19.4s: 54% of 50 http requests failed in 10s |
| hardened | 13:24:16 |  |  | 1499 | 279 | 18.61% | 37.2x | - | miss: abort line hit at 24.6s: 56% of 50 http requests failed in 10s |

### A5

**Steady-state hypothesis:** A leaking container is killed at its limit, restarted and serving within 60s, a human is paged with a runbook within 120s, and no other pod restarts.

Healing control: memory limit OOMKills the leak; kubelet restarts it; OOM alert pages with a runbook. Thresholds (eks): `{"recover_s": 60, "alert_s": 120, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:21:50 |  |  | 518 | 0 | 0.00% | 0.0x | - | impact: OOMKilled never, recovered never, alert never fired, leaked pod memory at end 337.2 MiB, other restarts 0, errors 0.00% |
| hardened | 13:29:40 | 10s | 14s | 504 | 4 | 0.79% | 1.6x | WebContainerOOMKilled | healed: OOMKilled at 10s, recovered at 14s, alert at 14s, leaked pod memory at end None MiB, other restarts 0, errors 0.79% |

### S1

**Steady-state hypothesis:** Rotating the partner API key in the secret backend causes 0 failed requests.

Healing control: ESO syncs the rotated key; Reloader restarts the pods. Thresholds (eks): `{"failed": 0, "grace_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:24:15 |  |  | 701 | 344 | 49.07% | 98.1x | WebAvailabilityBudgetBurn | impact: 344 failed of 701; key synced never, pods on the new key never |
| hardened | 13:31:49 | 4s | 20s | 1856 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 1856; key synced at 4s, pods on the new key at 20s |

### S1b

**Steady-state hypothesis:** Rotating the database password twice (both alternating users) causes 0 failed queries and 0 dropped connections.

Healing control: both credential versions mounted and re-read per connection; alternating users keep the previous one valid. Thresholds (eks): `{"failed": 0, "dropped": 0}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:28:28 | 10s | 179s | 239 | 239 | 100.00% | 200.0x | - | impact: 239 failed of 239 queries, 0 dropped connections, 2 rotations, users seen selfheal_app_clone |
| hardened | 13:38:42 | 6s | 24s | 84 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 84 queries, 0 dropped connections, 2 rotations, users seen selfheal_app,selfheal_app_clone |

### S2

**Steady-state hypothesis:** A web certificate reaching its renewal window causes 0 TLS errors.

Healing control: cert-manager renews; the app hot-reloads the mounted pair. Thresholds (eks): `{"tls_errors": 0, "renew_s": 60, "serve_s": 150}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:33:56 |  |  | 64 | 12 | 18.75% | 37.5x | - | impact: abort line hit at 32.1s: 60% of 20 tls requests failed in 10s |
| hardened | 13:40:40 | 1s | 86s | 205 | 0 | 0.00% | 0.0x | - | healed: 0 TLS errors of 205; renewed at 1s, new certificate served at 86s |

### S3

**Steady-state hypothesis:** A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.

Healing control: Argo CD self-heal re-applies Git. Thresholds (eks): `{"revert_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:36:18 |  |  |  |  |  |  | - | impact: policy restored never, hand edit reverted never |
| hardened | 13:42:46 | 2s | 4s |  |  |  |  | - | healed: policy restored 2s, hand edit reverted 4s |

### S4

**Steady-state hypothesis:** A lab security group opened to 0.0.0.0/0 is closed again by the remediation automation within 600s.

Healing control: AWS Config flags the group; the custom SSM document revokes the public rule. Thresholds (eks): `{"revert_s": 600}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:38:56 | 178s |  |  |  |  |  | - | impact: non-compliant 178s, remediation none, rule revoked never |
| hardened | 13:43:18 | 108s | 132s |  |  |  |  | - | healed: non-compliant 108s, remediation SUCCEEDED, rule revoked 132s |

### S5

**Steady-state hypothesis:** Replacing every workload node for a new AMI causes 0 failed requests and never takes more than one node out at a time.

Healing control: Karpenter drift replaces nodes one at a time under the budget and the PDB. Thresholds (eks): `{"failed": 0, "replaced_s": 1200, "max_disrupted": 1}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:50:26 | 8s | 104s | 701 | 43 | 6.13% | 12.3x | - | impact: 43 failed of 701; 1 nodes replaced 104s, at most 1 disrupted at once, AMI ami-09696b17488e356ab -> ami-078ba44fb8cea0c34 |
| hardened | 13:46:09 | 8s | 256s | 1461 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 1461; 3 nodes replaced 256s, at most 1 disrupted at once, AMI ami-078ba44fb8cea0c34 -> ami-09696b17488e356ab |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (eks): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:53:15 | 1s |  | 318 | 0 | 0.00% | 0.0x | - | impact: detected 1s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 13:51:31 | 1s | 19s | 300 | 0 | 0.00% | 0.0x | - | error: reachability failed but Hubble shows no policy drop: not proven to be the policy |

### R2

**Steady-state hypothesis:** A runtime threat GuardDuty reports in a web pod is isolated at pod and node level within 600s and evidence is preserved within 900s, with the pod and node kept for investigation.

Healing control: GuardDuty Runtime Monitoring -> EventBridge -> Step Functions: isolate pod and node, preserve, investigate. Thresholds (eks): `{"isolate_s": 600, "evidence_s": 900, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 12:54:47 | 154s |  | 5128 | 0 | 0.00% | 0.0x | - | impact: Backdoor:Runtime/C&CActivity.B!DNS seen 154s, isolated never (pod relabelled, node cordoned, quarantine group), evidence never (0 snapshots, 0 B captured), errors 0.00% |
| hardened | 13:53:01 | 157s | 251s | 1344 | 170 | 12.65% | 25.3x | - | miss: Backdoor:Runtime/C&CActivity.B!DNS seen 157s, isolated 251s (pod relabelled, node cordoned, quarantine group), evidence 251s (1 snapshots, 24195 B captured), errors 12.65% |

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
