# Self-healing run `kind-g1-run2` (kind)

- Started 2026-11-18T06:13:42.145Z, finished 2026-11-18T06:15:36.231Z
- Rows: G1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| G1 | impact | **healed** | stray host rejected True, cross-namespace route rejected True, deleted backend failed closed True, unrouted and unknown host 404 True, legitimate route served throughout True; proxy kill (informational) 0 of 19 failed |

## Per row

### G1

**Steady-state hypothesis:** A Gateway that admits routes only from labelled namespaces and one hostname rejects a stray hostname and a route from another namespace, fails a route to a deleted backend closed, answers unrouted requests with 404, and keeps the legitimate route serving throughout, where the baseline listener serves the stray and cross-namespace routes.

Healing control: Envoy Gateway splits the entrance (platform owns the Gateway, the app team owns its route), so a stray hostname, a route from another namespace, or a route to a deleted backend never reaches traffic and never takes the legitimate route down. Thresholds (kind): `{"route_status_s": 30, "legit_status": 200, "denied_status": 404, "failed_status": 500}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 06:13:43 | 0s | 31s |  |  |  |  | - | impact: baseline listener served the stray hostname and the route from another namespace through the shared entrance |
| hardened | 06:14:42 | 0s | 31s |  |  |  |  | - | healed: stray host rejected True, cross-namespace route rejected True, deleted backend failed closed True, unrouted and unknown host 404 True, legitimate route served throughout True; proxy kill (informational) 0 of 19 failed |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "envoy-gateway-system/envoy-gateway": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "g1_synced": true
  },
  "versions": {
    "repo_commit": "bfa013b812ebaf7603983b03517692e9b88a08da-dirty",
    "cilium": null,
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
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.37.0"
  }
}
```

G1 records the listener shape, the Gateway's own Accepted and ResolvedRefs conditions per faulted route, the response each faulted host and path received through the Envoy proxy Service, the legitimate route during every fault, and an informational proxy pod kill. The caller is a pod inside the cluster, so no public address exists.
