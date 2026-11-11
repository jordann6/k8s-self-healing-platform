# Self-healing run `eks-o1b` (eks)

- Started 2026-11-11T03:31:22.329Z, finished 2026-11-11T03:34:48.014Z
- Rows: O1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| O1 | impact | **healed** | quota and defaults enforced True; operator least privilege True; controller replaced in 7s; evidence 1s, alert 4s, release 5s; evidence-gated sequence True; connectivity restored True |

## Per row

### O1

**Steady-state hypothesis:** The hardened namespace enforces a resource budget and default limits, confines the operator to a named release request, replaces a failed controller within 60 seconds, captures evidence and delivers a matching alert within 30 seconds, then releases quarantine only after evidence while restoring access.

Healing control: namespace budgets and least-privilege roles preserve evidence, deliver an alert, recover the controller, and gate operator release. Thresholds (eks): `{"controller_recover_s": 60, "evidence_s": 30, "alert_s": 30, "release_s": 30}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:32:49 |  |  |  |  |  |  | - | impact: no namespace budget or default limits; the broad operator could read the synthetic Secret, delete or patch the target, and directly release quarantine without evidence, a controller, or an alert receiver |
| hardened | 03:34:01 | 1s | 5s |  |  |  |  | - | healed: quota and defaults enforced True; operator least privilege True; controller replaced in 7s; evidence 1s, alert 4s, release 5s; evidence-gated sequence True; connectivity restored True |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "api_ready": true,
    "kube-system/cilium": true,
    "kube-system/cilium-operator": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "o1_node_labelled": "ip-10-30-1-187.ec2.internal",
    "o1_synced": true
  },
  "versions": {
    "repo_commit": "2bb4ee9992c0f3a0e26a515d57cd1388cbfa6f6e-dirty",
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
    "o1-controller": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:2bb4ee9",
    "o1-alert-receiver": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:2bb4ee9",
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.36.5-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:2bb4ee9-v1",
    "workload_ami": [
      "ami-069787c4f4d0583ff"
    ]
  }
}
```

O1 records namespace admission defaults and quota denial, exact operator and controller permissions, controller replacement identity, evidence and alert correlation, release ordering, and quarantine connectivity before and after release.
