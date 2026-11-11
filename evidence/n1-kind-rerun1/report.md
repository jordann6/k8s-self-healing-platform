# Self-healing run `n1-kind-rerun1` (kind)

- Started 2026-11-11T01:12:59.722Z, finished 2026-11-11T01:14:28.869Z
- Rows: N1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| N1 | impact | **healed** | WireGuard 426 packets, VXLAN 0 attributed (0 total), marker visible False; peers match True, routes True/True, handshakes True/True; requests 20 with 0 failures, p95 1.877 ms; denied successes 0, policy drops 10 |

## Per row

### N1

**Steady-state hypothesis:** For Cilium-managed pods on different workers, hardened WireGuard replaces visible VXLAN underlay traffic with an established encrypted peer path while legitimate requests remain healthy and an unauthorized cross-node request remains attributable to policy enforcement.

Healing control: Cilium WireGuard replaces visible cross-node VXLAN traffic while legitimate requests and policy denial remain observable. Thresholds (kind): `{"min_requests": 20, "max_failures": 0, "p95_ms": 300, "min_denied": 5, "denied_successes": 0, "min_policy_drops": 1, "min_packets": 5, "interfaces": 1, "min_peers": 1}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 01:13:31 |  |  |  |  |  |  | - | impact: VXLAN 242 attributed packets (260 total), WireGuard 0, marker visible True; requests 20 with 0 failures, p95 3.332 ms; denied successes 0, policy drops 10 |
| hardened | 01:14:13 |  |  |  |  |  |  | - | healed: WireGuard 426 packets, VXLAN 0 attributed (0 total), marker visible False; peers match True, routes True/True, handshakes True/True; requests 20 with 0 failures, p95 1.877 ms; denied successes 0, policy drops 10 |

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
    "kube-system/hubble-relay": true,
    "selfheal/n1-server": true,
    "selfheal/n1-client": true,
    "selfheal/n1-denied": true,
    "n1_synced": true
  },
  "versions": {
    "repo_commit": "a003d52bab57bc5532f5be2b460d6319f3fa2819",
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
    "n1-server": "selfheal-n1:v1",
    "n1-client": "selfheal-n1:v1",
    "n1-denied": "selfheal-n1:v1",
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.37.0"
  }
}
```

N1 records worker-underlay packet metadata, Cilium WireGuard peer status, cross-node request results, and exact Hubble policy drops. Same-node and control-plane traffic are outside the claim.
