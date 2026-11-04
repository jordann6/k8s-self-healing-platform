# Self-healing run `kind-run5` (kind)

- Started 2026-11-04T05:49:47.570Z, finished 2026-11-04T06:25:04.915Z
- Rows: A1, A2, A3, A5, S1, S2, S3, R1
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A1 | impact | **healed** | error rate after detection 0.00%; hung pod restarted and serving at 33s |
| A2 | impact | **healed** | final error rate 0.00%, serving v1; canary aborted at 100s |
| A3 | impact | **miss** | sustain window p95 758.4 ms, errors 0.32%, replicas 3 -> 8 |
| A5 | impact | **healed** | OOMKilled at 11s, recovered at 18s, alert at 35s, leaked pod memory at end None MiB, other restarts 0, errors 0.79% |
| S1 | impact | **healed** | 0 failed of 451; key synced at 6s, pods on the new key at 30s |
| S2 | impact | **healed** | 0 TLS errors of 177; renewed at 2s, new certificate served at 73s |
| S3 | impact | **healed** | policy restored 2s, hand edit reverted 18s |
| R1 | impact | **healed** | detected 0s, isolated 22s (20 Hubble policy drops), evidence 35s, errors 0.00% |

## Per row

### A1

**Steady-state hypothesis:** Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving within 60s, while one pod deadlocks and Chaos Mesh kills a second.

Healing control: readiness removes the hung pod; liveness restarts it. Thresholds (kind): `{"recover_s": 60, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 05:50:09 |  |  | 352 | 116 | 32.95% | 65.9x | - | impact: error rate after detection 32.95%; hung pod never recovered |
| hardened | 06:10:04 | 9s | 33s | 357 | 0 | 0.00% | 0.0x | - | healed: error rate after detection 0.00%; hung pod restarted and serving at 33s |

### A2

**Steady-state hypothesis:** A release that fails 30% of requests is stopped within 180s and only v1 serves afterwards, with the error rate back under 1%.

Healing control: Argo Rollouts canary analysis aborts; stable keeps serving. Thresholds (kind): `{"abort_s": 180, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 05:52:11 |  |  | 351 | 139 | 39.60% | 79.2x | WebAvailabilityBudgetBurn | impact: final error rate 32.67%, serving v2; no abort |
| hardened | 06:11:39 | 100s | 100s | 633 | 22 | 3.48% | 7.0x | - | healed: final error rate 0.00%, serving v1; canary aborted at 100s |

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (kind): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 05:54:29 |  |  | 21 | 15 | 71.43% | 142.9x | - | impact: abort line hit at 106.5s: 71% of 21 http requests failed in 10s |
| hardened | 06:14:39 | 28s | 28s | 310 | 1 | 0.32% | 0.6x | - | miss: sustain window p95 758.4 ms, errors 0.32%, replicas 3 -> 8 |

### A5

**Steady-state hypothesis:** A leaking container is killed at its limit, restarted and serving within 60s, a human is paged with a runbook within 120s, and no other pod restarts.

Healing control: memory limit OOMKills the leak; kubelet restarts it; OOM alert pages with a runbook. Thresholds (kind): `{"recover_s": 60, "alert_s": 120, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 05:57:30 |  |  | 505 | 0 | 0.00% | 0.0x | - | impact: OOMKilled never, recovered never, alert never fired, leaked pod memory at end 337.9 MiB, other restarts 0, errors 0.00% |
| hardened | 06:18:31 | 11s | 18s | 507 | 4 | 0.79% | 1.6x | WebContainerOOMKilled | healed: OOMKilled at 11s, recovered at 18s, alert at 35s, leaked pod memory at end None MiB, other restarts 0, errors 0.79% |

### S1

**Steady-state hypothesis:** Rotating the partner API key in the secret backend causes 0 failed requests.

Healing control: ESO syncs the rotated key; Reloader restarts the pods. Thresholds (kind): `{"failed": 0, "grace_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 05:59:33 |  |  | 345 | 29 | 8.41% | 16.8x | - | impact: abort line hit at 69.0s: 58% of 50 http requests failed in 10s |
| hardened | 06:20:17 | 6s | 30s | 451 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 451; key synced at 6s, pods on the new key at 30s |

### S2

**Steady-state hypothesis:** A web certificate reaching its renewal window causes 0 TLS errors.

Healing control: cert-manager renews; the app hot-reloads the mounted pair. Thresholds (kind): `{"tls_errors": 0, "renew_s": 60, "serve_s": 150}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 06:02:38 |  |  | 69 | 13 | 18.84% | 37.7x | - | impact: abort line hit at 34.8s: 65% of 20 tls requests failed in 10s |
| hardened | 06:21:50 | 2s | 73s | 177 | 0 | 0.00% | 0.0x | - | healed: 0 TLS errors of 177; renewed at 2s, new certificate served at 73s |

### S3

**Steady-state hypothesis:** A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.

Healing control: Argo CD self-heal re-applies Git. Thresholds (kind): `{"revert_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 06:03:57 |  |  |  |  |  |  | - | impact: policy restored never, hand edit reverted never |
| hardened | 06:23:21 | 2s | 18s |  |  |  |  | - | healed: policy restored 2s, hand edit reverted 18s |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (kind): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 06:06:14 | 0s |  | 312 | 0 | 0.00% | 0.0x | - | impact: detected 0s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 06:24:04 | 0s | 22s | 299 | 0 | 0.00% | 0.0x | - | healed: detected 0s, isolated 22s (20 Hubble policy drops), evidence 35s, errors 0.00% |

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
