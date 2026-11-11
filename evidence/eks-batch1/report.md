# Self-healing run `eks-batch1` (eks)

- Started 2026-11-11T03:07:54.494Z, finished 2026-11-11T03:25:16.324Z
- Rows: O1, Q3, P1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| O1 | impact | **error** | runner exception: TimeoutError: timed out after 60s waiting for O1 controller replacement |
| Q3 | impact | **healed** | 6 samples, CPU p90 184.124m, memory p90 108.672MiB; recommendation 224m/144645763 at 4s, ratios 1.22/1.27; bounded True, requests unchanged True, pod unchanged True, restart delta 0 |
| P1 | impact | **healed** | trusted and recovered images ran at sha256:c1a798de25a13a04124b3d2400420107b2e1d790a0684ee833354ae7c19325fa; denied unsigned, invalid, untrusted, tag_only, verifier_outage; policy metrics pass 2.0, fail or error 5.0; verifier recovery 16.589s |

## Per row

### O1

**Steady-state hypothesis:** The hardened namespace enforces a resource budget and default limits, confines the operator to a named release request, replaces a failed controller within 60 seconds, captures evidence and delivers a matching alert within 30 seconds, then releases quarantine only after evidence while restoring access.

Healing control: namespace budgets and least-privilege roles preserve evidence, deliver an alert, recover the controller, and gate operator release. Thresholds (eks): `{"controller_recover_s": 60, "evidence_s": 30, "alert_s": 30, "release_s": 30}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:09:47 |  |  |  |  |  |  | - | impact: no namespace budget or default limits; the broad operator could read the synthetic Secret, delete or patch the target, and directly release quarantine without evidence, a controller, or an alert receiver |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: TimeoutError: timed out after 60s waiting for O1 controller replacement |

### Q3

**Steady-state hypothesis:** Under sustained CPU and memory pressure, the hardened VPA publishes a bounded recommendation within 240s that is related to measured use, while Off mode leaves requests, the running pod, and restart count unchanged. The baseline has the same pressure and telemetry but no sizing recommendation.

Healing control: VPA recommender compares measured CPU and memory use with recommendation-only targets while requests and the running pod remain unchanged. Thresholds (eks): `{"recommend_s": 240, "min_samples": 6, "cpu_pressure_m": 100, "memory_pressure_mib": 64, "cpu_ratio_min": 0.7, "cpu_ratio_max": 2.0, "memory_ratio_min": 0.9, "memory_ratio_max": 2.0, "restarts": 0, "sample_window_s": 240, "baseline_window_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:10:36 |  |  |  |  |  |  | - | impact: 6 samples, CPU p90 186.805m, memory p90 108.672MiB; no VPA recommendation; requests 50m/32Mi |
| hardened | 03:21:36 | 4s | 4s |  |  |  |  | - | healed: 6 samples, CPU p90 184.124m, memory p90 108.672MiB; recommendation 224m/144645763 at 4s, ratios 1.22/1.27; bounded True, requests unchanged True, pod unchanged True, restart delta 0 |

### P1

**Steady-state hypothesis:** A digest-pinned artifact signed by the trusted local key is admitted and runs at the signed digest, while unsigned, invalid-signature, untrusted-signer, tag-only, and verifier-outage requests are denied with attributable telemetry and the verifier recovers within 60 seconds.

Healing control: Kyverno verifies a digest-pinned Cosign signature against the injected local trust root and fails closed. Thresholds (eks): `{"admission_s": 20, "ready_s": 120, "recovery_s": 60, "min_pass_decisions": 2, "min_reject_decisions": 5, "min_outage_drops": 1}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:12:30 | 2s |  |  |  |  |  | - | impact: no P1 policy; admitted signed, unsigned, copied-invalid, untrusted-signer, and tag-only references at sha256:c1a798de25a13a04124b3d2400420107b2e1d790a0684ee833354ae7c19325fa |
| hardened | 03:23:33 | 3s | 17s |  |  |  |  | - | healed: trusted and recovered images ran at sha256:c1a798de25a13a04124b3d2400420107b2e1d790a0684ee833354ae7c19325fa; denied unsigned, invalid, untrusted, tag_only, verifier_outage; policy metrics pass 2.0, fail or error 5.0; verifier recovery 16.589s |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "api_ready": true,
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
    "repo_commit": "2bb4ee9992c0f3a0e26a515d57cd1388cbfa6f6e",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": "docker.io/falcosecurity/falco:0.45.2",
    "falco-talon": "docker.io/falcosecurity/falco-talon:0.3.0",
    "argo-rollouts": "quay.io/argoproj/argo-rollouts:v1.10.1",
    "external-secrets": "ghcr.io/external-secrets/external-secrets:v2.14.0",
    "cert-manager": "quay.io/jetstack/cert-manager-controller:v1.21.3",
    "chaos-controller-manager": "ghcr.io/chaos-mesh/chaos-mesh:v2.8.7",
    "reloader-reloader": "ghcr.io/stakater/reloader:v1.4.23",
    "keda-operator": "ghcr.io/kedacore/keda:2.21.0",
    "rabbitmq": "rabbitmq:3.13.7-management",
    "qworker": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/qworker:2bb4ee9",
    "q2-signal": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:2bb4ee9",
    "q2-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:2bb4ee9",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q3:2bb4ee9",
    "n1-server": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:2bb4ee9",
    "n1-client": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:2bb4ee9",
    "n1-denied": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:2bb4ee9",
    "o1-controller": null,
    "o1-alert-receiver": null,
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.36.5-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:2bb4ee9-v1",
    "workload_ami": [
      "ami-069787c4f4d0583ff"
    ]
  }
}
```

Burn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`, the R2 bundle (manifest, capture, audit window, finding, execution history) in `R2/r2-hardened/`.
