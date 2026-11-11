# Self-healing run `aks-n1b` (aks)

- Started 2026-11-11T15:31:27.002Z, finished 2026-11-11T15:35:33.063Z
- Rows: N1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| N1 | impact | **error** | runner exception: TimeoutExpired: Command '['kubectl', '-n', 'selfheal', 'exec', 'n1-client-7995cc7b45-kjmzb', '--', 'python3', '/app/app.py', 'probe', 'http://10.244.2.128:8084/n1-hardened-3ade231e5187d64d', '20', '2']' timed out after 60 seconds |

## Per row

### N1

**Steady-state hypothesis:** For Cilium-managed pods on different workers, hardened WireGuard replaces visible VXLAN underlay traffic with an established encrypted peer path while legitimate requests remain healthy and an unauthorized cross-node request remains attributable to policy enforcement.

Healing control: Cilium WireGuard replaces visible cross-node VXLAN traffic while legitimate requests and policy denial remain observable. Thresholds (aks): `{"min_requests": 20, "max_failures": 0, "p95_ms": 300, "min_denied": 5, "denied_successes": 0, "min_policy_drops": 1, "min_packets": 5, "interfaces": 1, "min_peers": 1}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:32:58 |  |  |  |  |  |  | - | impact: VXLAN 200 attributed packets (415 total), WireGuard 0, marker visible True; requests 20 with 0 failures, p95 6.63 ms; denied successes 0, policy drops 12 |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: TimeoutExpired: Command '['kubectl', '-n', 'selfheal', 'exec', 'n1-client-7995cc7b45-kjmzb', '--', 'python3', '/app/app.py', 'probe', 'http://10.244.2.128:8084/n1-hardened-3ade231e5187d64d', '20', '2']' timed out after 60 seconds |

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
    "kube-system/hubble-relay": true,
    "n1_slot_nodes": {
      "client": "aks-system-13980686-vmss000000",
      "server": "aks-system-13980686-vmss000001"
    },
    "selfheal/n1-server": true,
    "selfheal/n1-client": true,
    "selfheal/n1-denied": true,
    "n1_synced": true
  },
  "versions": {
    "repo_commit": "ca155463b265ced5ccbe05581ac4ed88afa5756e-dirty",
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
    "qworker": "acrselfhealebab1v.azurecr.io/selfheal/qworker:20ce7a0",
    "q2-signal": "acrselfhealebab1v.azurecr.io/selfheal/q2:20ce7a0",
    "q2-target": "acrselfhealebab1v.azurecr.io/selfheal/q2:20ce7a0",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "acrselfhealebab1v.azurecr.io/selfheal/q3:20ce7a0",
    "n1-server": "acrselfhealebab1v.azurecr.io/selfheal/n1:20ce7a0",
    "n1-client": "acrselfhealebab1v.azurecr.io/selfheal/n1:20ce7a0",
    "n1-denied": "acrselfhealebab1v.azurecr.io/selfheal/n1:20ce7a0",
    "o1-controller": "acrselfhealebab1v.azurecr.io/selfheal/o1:20ce7a0",
    "o1-alert-receiver": "acrselfhealebab1v.azurecr.io/selfheal/o1:20ce7a0",
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealebab1v.azurecr.io/selfheal/app:20ce7a0-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

N1 records worker-underlay packet metadata, Cilium WireGuard peer status, cross-node request results, and exact Hubble policy drops. Same-node and control-plane traffic are outside the claim.
