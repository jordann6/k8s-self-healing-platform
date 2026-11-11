# Self-healing run `aks-o1` (aks)

- Started 2026-11-11T14:44:20.946Z, finished 2026-11-11T14:46:39.027Z
- Rows: O1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| O1 | impact | **healed** | quota and defaults enforced True; operator least privilege True; controller replaced in 3s; evidence 0s, alert 2s, release 2s; evidence-gated sequence True; connectivity restored True |

## Per row

### O1

**Steady-state hypothesis:** The hardened namespace enforces a resource budget and default limits, confines the operator to a named release request, replaces a failed controller within 60 seconds, captures evidence and delivers a matching alert within 30 seconds, then releases quarantine only after evidence while restoring access.

Healing control: namespace budgets and least-privilege roles preserve evidence, deliver an alert, recover the controller, and gate operator release. Thresholds (aks): `{"controller_recover_s": 60, "evidence_s": 30, "alert_s": 30, "release_s": 30}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 14:44:51 |  |  |  |  |  |  | - | impact: no namespace budget or default limits; the broad operator could read the synthetic Secret, delete or patch the target, and directly release quarantine without evidence, a controller, or an alert receiver |
| hardened | 14:46:06 | 0s | 2s |  |  |  |  | - | healed: quota and defaults enforced True; operator least privilege True; controller replaced in 3s; evidence 0s, alert 2s, release 2s; evidence-gated sequence True; connectivity restored True |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-aks",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "o1_node_labelled": "aks-system-13980686-vmss000000",
    "o1_synced": true
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
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealebab1v.azurecr.io/selfheal/app:fa37786-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

O1 records namespace admission defaults and quota denial, exact operator and controller permissions, controller replacement identity, evidence and alert correlation, release ordering, and quarantine connectivity before and after release.
