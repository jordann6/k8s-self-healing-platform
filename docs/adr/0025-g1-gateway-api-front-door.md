# ADR 0025: G1 Gateway API front door

Status: ACCEPTED 2026-11-17 by Jordan, with the recommended answer to each open
decision below. Kind build starts now, nothing is applied to any cloud.

## Context

Every row so far proves recovery behind the cluster edge. None of the three labs
has a north-south entrance, so there is no proof of the platform-team and
app-team split that Gateway API exists for, and no proof that a bad route change
by an app team cannot take the shared entrance down. Ingress NGINX is retired
(no fixes since March 2026), so Ingress is not a candidate. This ADR fixes the
implementation choice and the contract before any code. No threshold below comes
from a run.

## Decision 1, Envoy Gateway, not Cilium Gateway API

Cilium Gateway API was the first choice because Cilium is already the CNI on all
three targets. Its documentation (read 2026-11-17, v1.20) lists as a prerequisite
`kubeProxyReplacement=true`. Today kind, EKS and AKS all run with it off, and
`cilium/values-aks.yaml` states `false` on purpose because AKS keeps its own
kube-proxy under BYO CNI. Turning it on would change the data plane under every
proven row (A1 to S5, R1, R2, Q1 to Q3, P1, D1, O1, N1) and make their evidence
stale until rerun on both clouds.

Jordan chose Envoy Gateway on 2026-11-17. It is an independent controller and
data plane, so no existing row changes. The cost is a second north-south data
plane beside Cilium and one more chart to pin, and the README must say so.

Facts read 2026-11-17 from the Envoy Gateway compatibility matrix and install
page.

- Latest release v1.10 (Envoy 1.40, Gateway API v1.6.1, Kubernetes 1.34 to 1.37,
  end of life 2027-05-05). v1.9 also lists 1.36. Pin v1.10.0 at build time after
  rechecking, as every other chart pin was.
- Chart `oci://docker.io/envoyproxy/gateway-helm`. The default install applies
  Gateway API CRDs and Envoy Gateway CRDs. Install through Argo CD with the
  chart's CRD handling reviewed first, because the platform already uses Argo CD
  with a pinned chart pattern (`platform/*.yaml`).
- The Gateway API CRDs ship from the experimental channel by default. Decide
  whether to keep them or install the standard channel only, in the build step.

## Facts that change the kind contract on the clouds

1. Exposure. A Gateway gets a LoadBalancer Service by default. Kind has no cloud
   load balancer, so the kind run reaches the data plane through a NodePort
   service, set with an `EnvoyProxy` parametersRef on the GatewayClass. EKS creates
   a load balancer (the AWS Load Balancer Controller is not installed today, so
   either the in-tree provider makes a Classic or NLB or the row sets a
   documented internal-only NodePort). AKS creates an Azure load balancer and
   needs a public IP, and the `deny-public-ip` policy from ADR-0007 can block it.
   Both are open questions for the next ADR revision, not decided here. The
   default recommendation is an internal load balancer on both clouds and a
   runner that calls it from a pod inside the cluster, so no public IP exists
   and the teardown scripts only need an "no leftover load balancer" check.
2. Cilium policy. The lab runs default-deny `CiliumNetworkPolicy` in the app
   namespace. The Envoy data plane pods need an ingress path to the app and the
   app needs to admit them, and the Hubble drop evidence must show which side
   blocked a request.
3. Cost. Envoy Gateway adds a controller and at least one proxy pod per Gateway.
   Resource requests on the 4 GB kind budget must be measured, not assumed.

## Contract

Baseline, the app is reachable only through its own Service and no shared
entrance exists. Hardened, one `Gateway` in a platform namespace owns the
listener and TLS (certificate from the existing cert-manager CA), and the app
team owns an `HTTPRoute` in its own namespace attached by `allowedRoutes`
restricted to labelled namespaces.

Rows, each with a steady-state hypothesis and an abort condition.

| Check | Fault | Hypothesis |
|---|---|---|
| G1a | App team applies a route to a path outside its namespace's allowed hostname | Gateway rejects it (`Accepted=False`), other routes keep serving |
| G1b | App team applies a syntactically valid route to a deleted backend | Route reports `ResolvedRefs=False`, requests get a fail-closed 5xx, other routes keep serving |
| G1c | Request to an unrouted path or host | 404, no response from any backend |
| G1d | A route in another namespace tries to attach to the platform Gateway | Not attached, listener `AttachedRoutes` unchanged |
| G1e | Envoy proxy pod is killed under load | Replacement serves again inside a threshold fixed before the first run, failures counted not hidden |

Baseline expectation for the runner's honesty rule. Without the Gateway split
(one shared route object everyone edits), G1a and G1d must show impact in the
baseline, or the row is reported inconclusive, never healed, as N1 was on EKS.

## Thresholds

None are set. G1e's recovery bound and G1c's request counts are written here,
per target, in an addendum committed before the runner (the ADR-0003 rule).

## What this does not claim

It does not claim Envoy Gateway is better than Cilium Gateway. It claims the
role split and failure isolation Gateway API provides. Ingress is out of scope.

## Decisions (2026-11-17, recommended answers accepted)

1. Internal load balancer plus in-cluster caller (recommended) versus a public
   endpoint, given `deny-public-ip`.
2. Gateway API CRD channel, standard or experimental.
3. Whether G1e (proxy kill) is judged or informational, as N1 and node repair were.

Resolved as recommended. 1 internal load balancer and an in-cluster caller, no
public IP. 2 channel is settled in the build step by what the pinned chart
installs, and the choice is recorded in an addendum. 3 G1e is informational at
first (as N1 and node repair were) and becomes judged only after two clean runs.

## Addendum 2026-11-17, first kind run (kind-g1-run1)

- Result. Baseline impact, hardened healed. The baseline listener served the stray
  hostname and the cross-namespace route. The hardened listener rejected them with
  `NoMatchingListenerHostname` and `NotAllowedByListeners` and answered 404. The
  deleted-backend (500) and unrouted (404) results were the same in both profiles,
  so those two come from Gateway API itself, not from the hardening.
- CRD channel. The chart installed the experimental channel, bundle v1.6.1. Kept as
  installed, decision 2 is closed.
- TLS. The kind row is HTTP only. TLS from the cert-manager CA stays a cloud item.
- Argo CD. `resource.inclusions` silently excluded the Gateway kinds, so the app
  showed Synced and Healthy with nothing applied. GatewayClass, Gateway, HTTPRoute
  and EnvoyProxy are now included in the base and EKS lists.
- Thresholds. Route status arrived in about 0.05 s on quiet kind, so the 30 s bound
  is loose and is to be set from cloud numbers. The proxy kill lost 0 of 22
  requests in both profiles with two replicas, still informational.
- Not yet read. Cilium default-deny interaction with the Envoy data plane, which
  needs the Cilium clusters.
