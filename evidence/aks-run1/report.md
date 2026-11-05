# Self-healing run `aks-run1` (aks)

- Started 2026-11-05T15:29:23.429Z, finished 2026-11-05T17:51:31.330Z
- Rows: A1, A2, A3, A4, A5, S1, S2, S3, S4, R1, R2
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| A1 | impact | **healed** | error rate after detection 0.00%; hung pod restarted and serving at 31s |
| A2 | impact | **healed** | final error rate 0.00%, serving v1; canary aborted at 80s |
| A3 | impact | **miss** | sustain window p95 821.3 ms, errors 0.00%, replicas 3 -> 6 |
| A4 | impact | **healed** | stopped aks-work-37698655-vmss/0 (1 web pods), NotReady at 59s; errors from NotReady 0.00% (before NotReady, reported not judged: 29.49%); all replicas serving 172s, new node aks-work-37698655-vmss000003 |
| A5 | impact | **healed** | OOMKilled at 9s, recovered at 12s, alert at 21s, leaked pod memory at end None MiB, other restarts 0, errors 0.39% |
| S1 | impact | **healed** | 0 failed of 450; key synced at 11s, pods on the new key at 33s |
| S2 | impact | **healed** | 0 TLS errors of 193; renewed at 1s, new certificate served at 79s |
| S3 | impact | **error** | runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |
| S4 | impact | **error** | runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |
| R1 | impact | **error** | runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |
| R2 | error | **error** | runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |

## Per row

### A1

**Steady-state hypothesis:** Error rate stays under 1% once a hung pod is detected, and the hung pod is restarted and serving within 60s, while one pod deadlocks and Chaos Mesh kills a second.

Healing control: readiness removes the hung pod; liveness restarts it. Thresholds (aks): `{"recover_s": 60, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:30:27 |  |  | 359 | 121 | 33.70% | 67.4x | - | impact: error rate after detection 33.70%; hung pod never recovered |
| hardened | 17:25:07 | 9s | 31s | 361 | 0 | 0.00% | 0.0x | - | healed: error rate after detection 0.00%; hung pod restarted and serving at 31s |

### A2

**Steady-state hypothesis:** A release that fails 30% of requests is stopped within 180s and only v1 serves afterwards, with the error rate back under 1%.

Healing control: Argo Rollouts canary analysis aborts; stable keeps serving. Thresholds (aks): `{"abort_s": 180, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:32:20 |  |  | 258 | 77 | 29.84% | 59.7x | WebAvailabilityBudgetBurn | impact: final error rate 30.00%, serving v2; no abort |
| hardened | 17:27:01 | 80s | 80s | 579 | 23 | 3.97% | 7.9x | - | healed: final error rate 0.00%, serving v1; canary aborted at 80s |

### A3

**Steady-state hypothesis:** Once a 30 rps spike that saturates three pods is sustained (T0+100s on), p95 latency stays under 300 ms and errors under 1%. The ramp before the HPA reacts is reported, not judged.

Healing control: admission control sheds the excess fast; HPA scales the Rollout out. Thresholds (aks): `{"p95_ms": 300, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:34:03 |  |  | 318 | 70 | 22.01% | 44.0x | - | impact: sustain window p95 2173.1 ms, errors 22.01%, replicas 3 -> 3 |
| hardened | 17:29:40 | 69s | 69s | 323 | 0 | 0.00% | 0.0x | - | miss: sustain window p95 821.3 ms, errors 0.00%, replicas 3 -> 6 |

### A4

**Steady-state hypothesis:** When the scale set instance hosting the most web pods is stopped, the error rate stays under 1% from the moment its node is marked NotReady, and every web replica is serving again, off that node and with a new workload node, within 600s.

Healing control: the node controller marks the stopped node NotReady; pods leave after a 30s toleration; the spread (minDomains 3, nodeTaintsPolicy Honor) keeps them Pending until the cluster autoscaler adds a node. Thresholds (aks): `{"recover_s": 600, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:38:11 | 61s | 403s | 2703 | 1698 | 62.82% | 125.6x | - | impact: abort line hit at 70.1s: 100% of 45 http requests failed in 10s |
| hardened | 17:34:08 | 59s | 172s | 2707 | 0 | 0.00% | 0.0x | - | healed: stopped aks-work-37698655-vmss/0 (1 web pods), NotReady at 59s; errors from NotReady 0.00% (before NotReady, reported not judged: 29.49%); all replicas serving 172s, new node aks-work-37698655-vmss000003 |

### A5

**Steady-state hypothesis:** A leaking container is killed at its limit, restarted and serving within 60s, a human is paged with a runbook within 120s, and no other pod restarts.

Healing control: memory limit OOMKills the leak; kubelet restarts it; OOM alert pages with a runbook. Thresholds (aks): `{"recover_s": 60, "alert_s": 120, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:49:07 |  |  | 508 | 0 | 0.00% | 0.0x | - | impact: OOMKilled never, recovered never, alert never fired, leaked pod memory at end 336.4 MiB, other restarts 0, errors 0.00% |
| hardened | 17:45:33 | 9s | 12s | 507 | 2 | 0.39% | 0.8x | WebContainerOOMKilled | healed: OOMKilled at 9s, recovered at 12s, alert at 21s, leaked pod memory at end None MiB, other restarts 0, errors 0.39% |

### S1

**Steady-state hypothesis:** Rotating the partner API key in the secret backend causes 0 failed requests.

Healing control: ESO syncs the rotated key; Reloader restarts the pods. Thresholds (aks): `{"failed": 0, "grace_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:51:12 |  |  | 360 | 27 | 7.50% | 15.0x | - | impact: abort line hit at 71.8s: 52% of 50 http requests failed in 10s |
| hardened | 17:47:34 | 11s | 33s | 450 | 0 | 0.00% | 0.0x | - | healed: 0 failed of 450; key synced at 11s, pods on the new key at 33s |

### S2

**Steady-state hypothesis:** A web certificate reaching its renewal window causes 0 TLS errors.

Healing control: cert-manager renews; the app hot-reloads the mounted pair. Thresholds (aks): `{"tls_errors": 0, "renew_s": 60, "serve_s": 150}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:54:10 |  |  | 69 | 14 | 20.29% | 40.6x | - | impact: abort line hit at 34.7s: 65% of 20 tls requests failed in 10s |
| hardened | 17:49:24 | 1s | 79s | 193 | 0 | 0.00% | 0.0x | - | healed: 0 TLS errors of 193; renewed at 1s, new certificate served at 79s |

### S3

**Steady-state hypothesis:** A deleted network policy and a hand-edited Deployment are reverted to Git within 60s.

Healing control: Argo CD self-heal re-applies Git. Thresholds (aks): `{"revert_s": 60}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:55:47 |  |  |  |  |  |  | - | impact: policy restored never, hand edit reverted never |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |

### S4

**Steady-state hypothesis:** An inbound rule opening the node NSG to 0.0.0.0/0 is closed again by the Azure Policy deployIfNotExists deployment within 1200s.

Healing control: Azure Policy deployIfNotExists rewrites the open rule to Deny. Thresholds (aks): `{"revert_s": 1200}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:58:18 |  |  |  |  |  |  | - | impact: policy deployment none (-), rule closed never, non-compliant reported never |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |

### R1

**Steady-state hypothesis:** A shell in a web pod is isolated within 30s and evidence is preserved within 90s, with the pod kept for capture rather than killed.

Healing control: Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it. Thresholds (aks): `{"isolate_s": 30, "evidence_s": 90, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 16:19:51 | 1s |  | 391 | 0 | 0.00% | 0.0x | - | impact: detected 1s, isolated never (0 Hubble policy drops), evidence never, errors 0.00% |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |

### R2

**Steady-state hypothesis:** A threat Defender for Containers reports in a web pod is isolated (node cordoned, then pod relabelled into quarantine) within 300s of the alert and evidence (OS-disk snapshot, Run Command capture, manifest) is preserved within 600s of the alert, with the pod and node kept.

Healing control: Defender for Containers -> workflow automation -> Logic App -> queue -> worker cordons, then relabels; the Logic App snapshots the OS disk and runs the capture. Thresholds (aks): `{"alert_s": 3600, "isolate_s": 300, "evidence_s": 600, "error_rate": 0.01}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 16:21:29 |  |  | 18051 | 9 | 0.05% | 0.1x | - | error: no Defender alert naming the attacked pod within 3600s: the trigger never fired, so there was nothing to respond to |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: RuntimeError: az aks nodepool delete-machines: ERROR: (InvalidParameter) Cannot find any valid machines to delete. Please check your input machine names. The valid machines to delete in agent pool 'work' are: aks-work-37698655-vmss000001,aks-work-37698655-vmss000002,aks-work-37698655-vmss000003.
Code: InvalidParameter
Message: Cannot find any va |

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
