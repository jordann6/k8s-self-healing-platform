# Self-healing run `kind-run7` (kind)

- Started 2026-11-04T07:19:33.159Z, finished 2026-11-04T07:54:39.758Z
- Rows: A1, A2, A3, A5, S1, S2, S3, R1
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A1 | impact | **healed** | error rate after detection 0.00%; hung pod restarted and serving at 42s |
| A2 | impact | **healed** | final error rate 0.00%, serving v1; canary aborted at 119s |
| A3 | impact | **miss** | sustain window p95 696.9 ms, errors 0.31%, replicas 3 -> 10 |
| A5 | impact | **healed** | OOMKilled at 14s, recovered at 18s, alert at 16s, leaked pod memory at end None MiB, other restarts 0, errors 0.98% |
| S1 | impact | **healed** | 0 failed of 453; key synced at 10s, pods on the new key at 42s |
| S2 | impact | **healed** | 0 TLS errors of 94; renewed at 2s, new certificate served at 30s |
| S3 | impact | **healed** | policy restored 7s, hand edit reverted 7s |
| R1 | impact | **healed** | detected 0s, isolated 25s (28 Hubble policy drops), evidence 38s, errors 0.33% |

## Per row

### A1

**Steady-state hypothesis:** Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving within 60s, while one pod deadlocks and Chaos Mesh kills a second.

Healing control: readiness removes the hung pod; liveness restarts it. Thresholds (kind): `{"recover_s": 60, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:19:54 |  |  | 355 | 119 | 33.52% | 67.0x | - | impact: error rate after detection 33.52%; hung pod never recovered |
| hardened | 07:40:21 | 10s | 42s | 360 | 0 | 0.00% | 0.0x | - | healed: error rate after detection 0.00%; hung pod restarted and serving at 42s |

### A2

**Steady-state hypothesis:** A release that fails 30% of requests is stopped within 180s and only v1 serves afterwards, with the error rate back under 1%.

Healing control: Argo Rollouts canary analysis aborts; stable keeps serving. Thresholds (kind): `{"abort_s": 180, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:21:44 |  |  | 74 | 31 | 41.89% | 83.8x | WebAvailabilityBudgetBurn | impact: abort line hit at 25.0s: 57% of 49 http requests failed in 10s |
| hardened | 07:41:59 | 119s | 119s | 709 | 20 | 2.82% | 5.6x | - | healed: final error rate 0.00%, serving v1; canary aborted at 119s |

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (kind): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:23:21 |  |  | 22 | 12 | 54.55% | 109.1x | - | impact: abort line hit at 106.3s: 55% of 22 http requests failed in 10s |
| hardened | 07:45:19 | 31s | 31s | 321 | 1 | 0.31% | 0.6x | - | miss: sustain window p95 696.9 ms, errors 0.31%, replicas 3 -> 10 |

### A5

**Steady-state hypothesis:** A leaking container is killed at its limit, restarted and serving within 60s, a human is paged with a runbook within 120s, and no other pod restarts.

Healing control: memory limit OOMKills the leak; kubelet restarts it; OOM alert pages with a runbook. Thresholds (kind): `{"recover_s": 60, "alert_s": 120, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:26:11 |  |  | 510 | 0 | 0.00% | 0.0x | - | impact: OOMKilled never, recovered never, alert never fired, leaked pod memory at end 338.5 MiB, other restarts 0, errors 0.00% |
| hardened | 07:49:07 | 14s | 18s | 509 | 5 | 0.98% | 2.0x | WebContainerOOMKilled | healed: OOMKilled at 14s, recovered at 18s, alert at 16s, leaked pod memory at end None MiB, other restarts 0, errors 0.98% |

### S1

**Steady-state hypothesis:** Rotating the partner API key in the secret backend causes 0 failed requests.

Healing control: ESO syncs the rotated key; Reloader restarts the pods. Thresholds (kind): `{"failed": 0, "grace_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:28:22 |  |  | 343 | 31 | 9.04% | 18.1x | - | impact: abort line hit at 68.6s: 62% of 50 http requests failed in 10s |
| hardened | 07:50:52 | 10s | 42s | 453 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 453; key synced at 10s, pods on the new key at 42s |

### S2

**Steady-state hypothesis:** A web certificate reaching its renewal window causes 0 TLS errors.

Healing control: cert-manager renews; the app hot-reloads the mounted pair. Thresholds (kind): `{"tls_errors": 0, "renew_s": 60, "serve_s": 150}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:31:12 |  |  | 71 | 14 | 19.72% | 39.4x | - | impact: abort line hit at 35.6s: 70% of 20 tls requests failed in 10s |
| hardened | 07:52:26 | 2s | 30s | 94 | 0 | 0.00% | 0.0x | - | healed: 0 TLS errors of 94; renewed at 2s, new certificate served at 30s |

### S3

**Steady-state hypothesis:** A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.

Healing control: Argo CD self-heal re-applies Git. Thresholds (kind): `{"revert_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:32:39 |  |  |  |  |  |  | - | impact: policy restored never, hand edit reverted never |
| hardened | 07:53:22 | 7s | 7s |  |  |  |  | - | healed: policy restored 7s, hand edit reverted 7s |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (kind): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:35:11 | 1s |  | 304 | 0 | 0.00% | 0.0x | - | impact: detected 1s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 07:53:39 | 0s | 25s | 299 | 1 | 0.33% | 0.7x | WebAvailabilityBudgetBurn | healed: detected 0s, isolated 25s (28 Hubble policy drops), evidence 38s, errors 0.33% |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
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
    "kubernetes": "v1.37.0"
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`.
