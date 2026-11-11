# Self-healing run `aks-batch1` (aks)

- Started 2026-11-11T14:47:32.610Z, finished 2026-11-11T15:03:55.878Z
- Rows: Q3, P1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| Q3 | impact | **healed** | 6 samples, CPU p90 162.619m, memory p90 108.73MiB; recommendation 203m/144645763 at 2s, ratios 1.25/1.27; bounded True, requests unchanged True, pod unchanged True, restart delta 0 |
| P1 | impact | **healed** | trusted and recovered images ran at sha256:c1a798de25a13a04124b3d2400420107b2e1d790a0684ee833354ae7c19325fa; denied unsigned, invalid, untrusted, tag_only, verifier_outage; policy metrics pass 2.0, fail or error 5.0; verifier recovery 16.156s |

## Per row

### Q3

**Steady-state hypothesis:** Under sustained CPU and memory pressure, the hardened VPA publishes a bounded recommendation within 240s that is related to measured use, while Off mode leaves requests, the running pod, and restart count unchanged. The baseline has the same pressure and telemetry but no sizing recommendation.

Healing control: VPA recommender compares measured CPU and memory use with recommendation-only targets while requests and the running pod remain unchanged. Thresholds (aks): `{"recommend_s": 240, "min_samples": 6, "cpu_pressure_m": 100, "memory_pressure_mib": 64, "cpu_ratio_min": 0.7, "cpu_ratio_max": 2.0, "memory_ratio_min": 0.9, "memory_ratio_max": 2.0, "restarts": 0, "sample_window_s": 420, "baseline_window_s": 420}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 14:49:21 |  |  |  |  |  |  | - | impact: 6 samples, CPU p90 169.964m, memory p90 108.73MiB; no VPA recommendation; requests 50m/32Mi |
| hardened | 14:57:01 | 2s | 2s |  |  |  |  | - | healed: 6 samples, CPU p90 162.619m, memory p90 108.73MiB; recommendation 203m/144645763 at 2s, ratios 1.25/1.27; bounded True, requests unchanged True, pod unchanged True, restart delta 0 |

### P1

**Steady-state hypothesis:** A digest-pinned artifact signed by the trusted local key is admitted and runs at the signed digest, while unsigned, invalid-signature, untrusted-signer, tag-only, and verifier-outage requests are denied with attributable telemetry and the verifier recovers within 60 seconds.

Healing control: Kyverno verifies a digest-pinned Cosign signature against the injected local trust root and fails closed. Thresholds (aks): `{"admission_s": 20, "ready_s": 120, "recovery_s": 60, "min_pass_decisions": 2, "min_reject_decisions": 5, "min_outage_drops": 1}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 14:52:25 | 1s |  |  |  |  |  | - | impact: no P1 policy; admitted signed, unsigned, copied-invalid, untrusted-signer, and tag-only references at sha256:c1a798de25a13a04124b3d2400420107b2e1d790a0684ee833354ae7c19325fa |
| hardened | 15:02:30 | 3s | 16s |  |  |  |  | - | healed: trusted and recovered images ran at sha256:c1a798de25a13a04124b3d2400420107b2e1d790a0684ee833354ae7c19325fa; denied unsigned, invalid, untrusted, tag_only, verifier_outage; policy metrics pass 2.0, fail or error 5.0; verifier recovery 16.156s |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-aks",
    "api_ready": true,
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
    "repo_commit": "fa37786aa8006849eedf1b34ab84df7eeb99df83-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "keda-operator": null,
    "rabbitmq": "rabbitmq:3.13.7-management",
    "qworker": "acrselfhealebab1v.azurecr.io/selfheal/qworker:fa37786",
    "q2-signal": "acrselfhealebab1v.azurecr.io/selfheal/q2:fa37786",
    "q2-target": "acrselfhealebab1v.azurecr.io/selfheal/q2:fa37786",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "acrselfhealebab1v.azurecr.io/selfheal/q3:fa37786",
    "n1-server": "acrselfhealebab1v.azurecr.io/selfheal/n1:fa37786",
    "n1-client": "acrselfhealebab1v.azurecr.io/selfheal/n1:fa37786",
    "n1-denied": "acrselfhealebab1v.azurecr.io/selfheal/n1:fa37786",
    "o1-controller": "acrselfhealebab1v.azurecr.io/selfheal/o1:fa37786",
    "o1-alert-receiver": "acrselfhealebab1v.azurecr.io/selfheal/o1:fa37786",
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealebab1v.azurecr.io/selfheal/app:fa37786-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`, the R2 bundle (the Defender alert as ARM returned it, the worker's isolated.json, the Logic App's manifest with the snapshot and Run Command capture, its runs) in `R2/r2-hardened/`.
