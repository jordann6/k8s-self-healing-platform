# Self-healing run `eks-g1-run1` (eks)

- Started 2026-11-18T08:00:22.235Z, finished 2026-11-18T08:07:20.055Z
- Rows: G1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| G1 | impact | **healed** | stray host rejected True, cross-namespace route rejected True, deleted backend failed closed True, unrouted and unknown host 404 True, legitimate route served throughout True; proxy kill (informational) 0 of 4 failed; load balancer address in 4.0s and first 200 in 11.54s True, TLS from the lab CA True; backend policy drop seen in Hubble (informational) True |

## Per row

### G1

**Steady-state hypothesis:** A Gateway that admits routes only from labelled namespaces and one hostname rejects a stray hostname and a route from another namespace, fails a route to a deleted backend closed, answers unrouted requests with 404, and keeps the legitimate route serving throughout, where the baseline listener serves the stray and cross-namespace routes.

Healing control: Envoy Gateway splits the entrance (platform owns the Gateway, the app team owns its route), so a stray hostname, a route from another namespace, or a route to a deleted backend never reaches traffic and never takes the legitimate route down. Thresholds (eks): `{"route_status_s": 30, "legit_status": 200, "denied_status": 404, "failed_status": 500, "lb": true, "address_s": 300, "first_ok_s": 300, "cert_ready_s": 120}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 08:01:25 | 1s | 51s |  |  |  |  | - | impact: baseline listener served the stray hostname and the route from another namespace through the shared entrance |
| hardened | 08:04:40 | 1s | 62s |  |  |  |  | - | healed: stray host rejected True, cross-namespace route rejected True, deleted backend failed closed True, unrouted and unknown host 404 True, legitimate route served throughout True; proxy kill (informational) 0 of 4 failed; load balancer address in 4.0s and first 200 in 11.54s True, TLS from the lab CA True; backend policy drop seen in Hubble (informational) True |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
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
    "o1-controller": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:a15a195",
    "o1-alert-receiver": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:a15a195",
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.36.5-eks-91f89a9",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:a15a195-v1",
    "workload_ami": [
      "ami-0551d0e876b76cec6"
    ]
  }
}
```

G1 records the listener shape, the Gateway's own Accepted and ResolvedRefs conditions per faulted route, the response each faulted host and path received through the Envoy proxy Service, the legitimate route during every fault, and an informational proxy pod kill. The caller is a pod inside the cluster, so no public address exists.
