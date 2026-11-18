# Self-healing run `gke-run1` (gke)

- Started 2026-11-18T19:57:24.040Z, finished 2026-11-18T20:53:39.827Z
- Rows: A1, A2, A3, A4, A5, S2, S3, R1
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A1 | impact | **healed** | error rate after detection 0.00%; hung pod restarted and serving at 36s |
| A2 | impact | **healed** | final error rate 0.00%, serving v1; canary aborted at 70s |
| A3 | impact | **miss** | sustain window p95 319.0 ms, errors 0.00%, replicas 3 -> 10 |
| A4 | impact | **healed** | stopped us-central1-a/gke-selfheal-workload-13368d94-tcdz (1 web pods), NotReady at 56s; errors from NotReady 0.07% (before NotReady, reported not judged: 35.61%); all replicas serving 98s; VM started again by something else at 134s |
| A5 | impact | **miss** | OOMKilled at 14s, recovered at 17s, alert at 20s, leaked pod memory at end None MiB, other restarts 0, errors 1.17% |
| S2 | impact | **healed** | 0 TLS errors of 153; renewed at 1s, new certificate served at 60s |
| S3 | impact | **healed** | policy restored 2s, hand edit reverted 4s |
| R1 | impact | **miss** | detected 2s, isolated 32s (25 Hubble policy drops), evidence 53s, errors 0.00% |

## Per row

### A1

**Steady-state hypothesis:** Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving within 60s, while one pod deadlocks and Chaos Mesh kills a second.

Healing control: readiness removes the hung pod; liveness restarts it. Thresholds (gke): `{"recover_s": 60, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 19:58:58 |  |  | 144 | 60 | 41.67% | 83.3x | - | impact: abort line hit at 49.2s: 54% of 46 http requests failed in 10s |
| hardened | 20:28:09 | 11s | 36s | 356 | 0 | 0.00% | 0.0x | - | healed: error rate after detection 0.00%; hung pod restarted and serving at 36s |

### A2

**Steady-state hypothesis:** A release that fails 30% of requests is stopped within 180s and only v1 serves afterwards, with the error rate back under 1%.

Healing control: Argo Rollouts canary analysis aborts; stable keeps serving. Thresholds (gke): `{"abort_s": 180, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:00:14 |  |  | 65 | 29 | 44.62% | 89.2x | - | impact: abort line hit at 21.6s: 52% of 50 http requests failed in 10s |
| hardened | 20:30:05 | 70s | 70s | 521 | 22 | 4.22% | 8.4x | - | healed: final error rate 0.00%, serving v1; canary aborted at 70s |

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (gke): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:01:26 |  |  | 142 | 56 | 39.44% | 78.9x | - | impact: abort line hit at 130.1s: 52% of 42 http requests failed in 10s |
| hardened | 20:32:39 | 35s | 35s | 318 | 0 | 0.00% | 0.0x | - | miss: sustain window p95 319.0 ms, errors 0.00%, replicas 3 -> 10 |

### A4

**Steady-state hypothesis:** When the VM hosting the most web pods is stopped, the error rate stays under 1% from the moment its node is marked NotReady, and every web replica is serving again on the remaining fixed workload nodes within 600s.

Healing control: the node controller marks the stopped node NotReady; pods leave after a 30s toleration; the spread (minDomains 3, nodeTaintsPolicy Honor) places them on the three remaining fixed workload nodes. Thresholds (gke): `{"recover_s": 600, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:05:02 | 46s | 85s | 2790 | 208 | 7.46% | 14.9x | - | impact: abort line hit at 54.9s: 100% of 43 http requests failed in 10s |
| hardened | 20:37:50 | 56s | 98s | 2725 | 2 | 0.07% | 0.1x | - | healed: stopped us-central1-a/gke-selfheal-workload-13368d94-tcdz (1 web pods), NotReady at 56s; errors from NotReady 0.07% (before NotReady, reported not judged: 35.61%); all replicas serving 98s; VM started again by something else at 134s |

### A5

**Steady-state hypothesis:** A leaking container is killed at its limit, restarted and serving within 60s, a human is paged with a runbook within 120s, and no other pod restarts.

Healing control: memory limit OOMKills the leak; kubelet restarts it; OOM alert pages with a runbook. Thresholds (gke): `{"recover_s": 60, "alert_s": 120, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:15:32 |  |  | 509 | 0 | 0.00% | 0.0x | - | impact: OOMKilled never, recovered never, alert never fired, leaked pod memory at end 336.2 MiB, other restarts 0, errors 0.00% |
| hardened | 20:48:18 | 14s | 17s | 514 | 6 | 1.17% | 2.3x | WebContainerOOMKilled | miss: OOMKilled at 14s, recovered at 17s, alert at 20s, leaked pod memory at end None MiB, other restarts 0, errors 1.17% |

### S2

**Steady-state hypothesis:** A web certificate reaching its renewal window causes 0 TLS errors.

Healing control: cert-manager renews; the app hot-reloads the mounted pair. Thresholds (gke): `{"tls_errors": 0, "renew_s": 60, "serve_s": 150}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:18:40 |  |  | 68 | 14 | 20.59% | 41.2x | - | impact: abort line hit at 34.3s: 70% of 20 tls requests failed in 10s |
| hardened | 20:50:24 | 1s | 60s | 153 | 0 | 0.00% | 0.0x | - | healed: 0 TLS errors of 153; renewed at 1s, new certificate served at 60s |

### S3

**Steady-state hypothesis:** A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.

Healing control: Argo CD self-heal re-applies Git. Thresholds (gke): `{"revert_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:20:50 |  |  |  |  |  |  | - | impact: policy restored never, hand edit reverted never |
| hardened | 20:52:02 | 2s | 4s |  |  |  |  | - | healed: policy restored 2s, hand edit reverted 4s |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (gke): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 20:23:31 | 2s |  | 390 | 0 | 0.00% | 0.0x | - | impact: detected 2s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened | 20:52:37 | 2s | 32s | 300 | 0 | 0.00% | 0.0x | - | miss: detected 2s, isolated 32s (25 Hubble policy drops), evidence 53s, errors 0.00% |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-gke",
    "api_ready": true,
    "metrics_api": true,
    "keda/keda-operator": true,
    "keda/keda-operator-metrics-apiserver": true,
    "keda/keda-admission-webhooks": true,
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
    "repo_commit": "f00f387cee1834647208f0407add47eafb9d4bf4-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "keda-operator": "ghcr.io/kedacore/keda:2.21.0",
    "rabbitmq": null,
    "qworker": null,
    "q2-signal": null,
    "q2-target": null,
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.36.5-gke.1982000",
    "web_image": "us-central1-docker.pkg.dev/<gcp-project>/selfheal/app:fb0bea5-v1",
    "node_os_images": [
      "Container-Optimized OS from Google"
    ],
    "node_kernels": [
      "6.12.113+"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`.
