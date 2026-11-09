# Self-healing run `kind-20261109-o1-run4` (kind)

- Started 2026-11-09T21:11:24.658Z, finished 2026-11-09T21:12:22.341Z
- Rows: O1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| O1 | impact | **miss** | quota and defaults enforced True; operator least privilege True; controller replaced in 1s; evidence 0s, alert 1s, release 1s; evidence-gated sequence False; connectivity restored True |

## Per row

### O1

**Steady-state hypothesis:** The hardened namespace enforces a resource budget and default limits, confines the operator to a named release request, replaces a failed controller within 60 seconds, captures evidence and delivers a matching alert within 30 seconds, then releases quarantine only after evidence while restoring access.

Healing control: namespace budgets and least-privilege roles preserve evidence, deliver an alert, recover the controller, and gate operator release. Thresholds (kind): `{"controller_recover_s": 60, "evidence_s": 30, "alert_s": 30, "release_s": 30}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 21:12:00 |  |  |  |  |  |  | - | impact: no namespace budget or default limits; the broad operator could read the synthetic Secret, delete or patch the target, and directly release quarantine without evidence, a controller, or an alert receiver |
| hardened | 21:12:12 | 0s | 1s |  |  |  |  | - | miss: quota and defaults enforced True; operator least privilege True; controller replaced in 1s; evidence 0s, alert 1s, release 1s; evidence-gated sequence False; connectivity restored True |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "o1_synced": true
  },
  "versions": {
    "repo_commit": "e73d20133488a04949501106339800ed3efe45a0-dirty",
    "cilium": "quay.io/cilium/cilium:v1.20.4@sha256:20fe92e4c86641233a450098c158d2d29085f3419931ab6bf681d2485dedf16e",
    "falco": null,
    "falco-talon": null,
    "argo-rollouts": null,
    "external-secrets": null,
    "cert-manager": null,
    "chaos-controller-manager": null,
    "reloader-reloader": null,
    "keda-operator": null,
    "rabbitmq": null,
    "qworker": null,
    "q2-signal": null,
    "q2-target": null,
    "vpa-vertical-pod-autoscaler-recommender": null,
    "q3-target": null,
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": "selfheal-o1:v1",
    "o1-alert-receiver": "selfheal-o1:v1",
    "kubernetes": "v1.37.0"
  }
}
```

O1 records namespace admission defaults and quota denial, exact operator and controller permissions, controller replacement identity, evidence and alert correlation, release ordering, and quarantine connectivity before and after release.
