# Self-healing run `eks-x1-run1` (eks)

- Started 2026-11-18T07:42:24.365Z, finished 2026-11-18T07:46:51.047Z
- Rows: X1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| X1 | impact | **healed** | isolated in 4.563s without a restart True, release before evidence refused True, release after evidence True, resumed after controller kill in 6.963s True, self-isolation refused and role has no delete or Secret read True |

## Per row

### X1

**Steady-state hypothesis:** A Quarantine resource makes the controller isolate the named pod without restarting it, refuse a release requested before evidence exists, release only on a later request after the evidence, resume from status after the controller is killed, and refuse to isolate its own pod, with a role that cannot delete pods or read Secrets.

Healing control: A Quarantine custom resource makes a controller isolate the named pod without restarting it, refuse a release that comes before evidence, release only on a later request, resume from status after a controller kill, and refuse to isolate its own pod. Thresholds (eks): `{"isolate_s": 10, "blocked_s": 30, "resume_s": 30, "cilium": true}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:43:12 |  |  |  |  |  |  | - | impact: manual flow isolated the pod, then released it with no evidence recorded and no status anywhere |
| hardened | 07:45:15 |  |  |  |  |  |  | - | healed: isolated in 4.563s without a restart True, release before evidence refused True, release after evidence True, resumed after controller kill in 6.963s True, self-isolation refused and role has no delete or Secret read True |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "api_ready": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "x1_node_labelled": "ip-10-30-20-162.ec2.internal",
    "x1_synced": true
  },
  "versions": {
    "repo_commit": "a15a1953e1a517f842c05b71f9bec5a399ff5e94-dirty",
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
    "qworker": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/qworker:a15a195",
    "q2-signal": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:a15a195",
    "q2-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:a15a195",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q3:a15a195",
    "n1-server": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:a15a195",
    "n1-client": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:a15a195",
    "n1-denied": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:a15a195",
    "o1-controller": null,
    "o1-alert-receiver": null,
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.36.5-eks-91f89a9",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:a15a195-v1",
    "workload_ami": [
      "ami-0551d0e876b76cec6"
    ]
  }
}
```

X1 records, per Quarantine, the status phase and conditions the controller wrote, isolation time, direct pod-IP reachability from an in-cluster client before and after, pod UID and restart count, the evidence record, label-patch counts from the replacement controller's log, and `kubectl auth can-i` for the controller's service account.
