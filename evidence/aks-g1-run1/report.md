# Self-healing run `aks-g1-run1` (aks)

- Started 2026-11-18T07:42:33.030Z, finished 2026-11-18T07:48:11.289Z
- Rows: G1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| G1 | impact | **miss** | stray host rejected True, cross-namespace route rejected True, deleted backend failed closed True, unrouted and unknown host 404 True, legitimate route served throughout True; proxy kill (informational) 0 of 6 failed; load balancer address in 759.0s and first 200 in 5.801s False, TLS from the lab CA True; backend policy drop seen in Hubble (informational) True |

## Per row

### G1

**Steady-state hypothesis:** A Gateway that admits routes only from labelled namespaces and one hostname rejects a stray hostname and a route from another namespace, fails a route to a deleted backend closed, answers unrouted requests with 404, and keeps the legitimate route serving throughout, where the baseline listener serves the stray and cross-namespace routes.

Healing control: Envoy Gateway splits the entrance (platform owns the Gateway, the app team owns its route), so a stray hostname, a route from another namespace, or a route to a deleted backend never reaches traffic and never takes the legitimate route down. Thresholds (aks): `{"route_status_s": 30, "legit_status": 200, "denied_status": 404, "failed_status": 500, "lb": true, "address_s": 300, "first_ok_s": 300, "cert_ready_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 07:43:34 | 1s | 48s |  |  |  |  | - | impact: baseline listener served the stray hostname and the route from another namespace through the shared entrance |
| hardened | 07:46:10 | 1s | 60s |  |  |  |  | - | miss: stray host rejected True, cross-namespace route rejected True, deleted backend failed closed True, unrouted and unknown host 404 True, legitimate route served throughout True; proxy kill (informational) 0 of 6 failed; load balancer address in 759.0s and first 200 in 5.801s False, TLS from the lab CA True; backend policy drop seen in Hubble (informational) True |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-aks",
    "api_ready": true,
    "envoy-gateway-system/envoy-gateway": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "g1_synced": true
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
    "keda-operator": null,
    "rabbitmq": "rabbitmq:3.13.7-management",
    "qworker": "acrselfhealirjygg.azurecr.io/selfheal/qworker:a15a195",
    "q2-signal": "acrselfhealirjygg.azurecr.io/selfheal/q2:a15a195",
    "q2-target": "acrselfhealirjygg.azurecr.io/selfheal/q2:a15a195",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "acrselfhealirjygg.azurecr.io/selfheal/q3:a15a195",
    "n1-server": "acrselfhealirjygg.azurecr.io/selfheal/n1:a15a195",
    "n1-client": "acrselfhealirjygg.azurecr.io/selfheal/n1:a15a195",
    "n1-denied": "acrselfhealirjygg.azurecr.io/selfheal/n1:a15a195",
    "o1-controller": "acrselfhealirjygg.azurecr.io/selfheal/o1:a15a195",
    "o1-alert-receiver": "acrselfhealirjygg.azurecr.io/selfheal/o1:a15a195",
    "kubernetes": "v1.36.5",
    "web_image": "acrselfhealirjygg.azurecr.io/selfheal/app:a15a195-v1",
    "node_images": [
      "AKSUbuntu-2404gen2containerd-202610.30.1"
    ]
  }
}
```

G1 records the listener shape, the Gateway's own Accepted and ResolvedRefs conditions per faulted route, the response each faulted host and path received through the Envoy proxy Service, the legitimate route during every fault, and an informational proxy pod kill. The caller is a pod inside the cluster, so no public address exists.
