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

## Addendum 2026-11-17, cloud contract (EKS and AKS), written before any overlay or runner change

Status, ACCEPTED 2026-11-17 by Jordan, all three decisions as recommended. Overlays are built from
this addendum, nothing is applied to any cloud. Vendor pages were read on 2026-11-17 and are named per fact.
No threshold below comes from a cloud run.

### Facts read

1. AKS internal load balancer (Microsoft Learn, "Use an internal load balancer with AKS", updated
   2026-07-31). The annotation `service.beta.kubernetes.io/azure-load-balancer-internal: "true"`
   on a `LoadBalancer` Service creates a Standard load balancer in the node resource group whose
   frontend takes a private address from the node subnet, with no public IP. The cluster identity
   needs Network Contributor on the virtual network, or `subnets/join/action` and `subnets/read`.
   The lab module already gives the control plane identity a role on the node subnet
   (`infra/azure/aks.tf`, `control_plane_subnet`). Deleting the Service deletes the load balancer.
   Not read, whether the existing `deny-public-ip` policy treats an internal frontend differently,
   it should not because there is no public IP object, and this stays live check L1.
2. EKS provisioner (AWS EKS user guide "Route TCP and UDP traffic with Network Load Balancers", and
   the cloud-provider-aws Service controller page). With no AWS Load Balancer Controller installed,
   the in-tree cloud provider makes the load balancer. It creates a Classic Load Balancer unless
   `service.beta.kubernetes.io/aws-load-balancer-type: nlb` is set, and
   `service.beta.kubernetes.io/aws-load-balancer-internal: "true"` makes it internal. AWS says that
   controller now receives only critical bug fixes and recommends the AWS Load Balancer Controller.
   The same AWS page says that with an alternate CNI plugin the Load Balancer Controller can only use
   instance targets, and this lab replaces the VPC CNI with Cilium, so installing it would not buy
   IP targets. The lab VPC already tags private subnets `kubernetes.io/role/internal-elb=1`
   (`infra/aws/vpc.tf`), so internal subnet discovery needs no new tag.
3. Envoy Gateway (docs "Customize EnvoyProxy", v1.10). `envoyService.annotations` or a patch on the
   `EnvoyProxy` sets Service annotations, and `envoyService.type` takes `LoadBalancer`. Service
   annotations cannot be edited after creation on AWS ("delete the service object and create it again"),
   so the annotations go in the first apply, never added later.
4. Repo facts. `aks-verify-gone.sh` already checks load balancers and public IPs in the lab resource
   groups. `eks-verify-gone.sh` has no load balancer check at all. The only Cilium policies in the
   lab select `app` labels in the `selfheal` namespace (`gitops/profiles/hardened/policy.yaml`).
   No `CiliumNetworkPolicy` exists in `selfheal-g1`, `selfheal-g1-other` or `selfheal-g1-platform`.
   The Envoy proxy pods run in `envoy-gateway-system`. P1's admission policy applies only to
   namespaces labelled `selfheal.lab/p1=true`.

### Decisions proposed

(a) Load balancer. Internal on both clouds, no public IP, as decision 1 already said. EKS uses the
in-tree provider with `aws-load-balancer-type: nlb` and `aws-load-balancer-internal: "true"`, and
the Load Balancer Controller is NOT installed (one more IAM role and chart, and no IP targets under
Cilium, for a row that only needs a private address). AKS uses `azure-load-balancer-internal: "true"`.
The runner calls the load balancer address from a pod in the cluster, so the judged checks cross the
cloud load balancer, which is what makes the row more than a repeat of kind. Kind keeps ClusterIP.
The cloud overlay patches `envoyService.type` to `LoadBalancer` and adds the annotations. A guard in
`scripts/render-g1-profiles.sh` fails the render if a cloud overlay lacks the internal annotation.

(b) TLS. The cloud hardened profile adds an HTTPS listener (443) on the existing cert-manager CA
(`bootstrap/ca.yaml`, the same issuer the S2 row uses) with a `Certificate` in the platform
namespace, and the runner verifies the served certificate against that CA. The baseline listener
stays HTTP. TLS is therefore a hardened-only difference and a new check, G1f, a plain request
over HTTPS validates against the lab CA and a request over HTTP is refused or redirected. No claim
is made for the kind row, which stays HTTP only. If cert-manager cannot issue in time the row
records G1f as an error, not a miss.

(c) Cilium. The premise in the first draft ("default-deny in the app namespace") does not hold for
the G1 namespaces, no policy exists there, so today nothing blocks the proxy. Proposed, both
profiles carry one `CiliumNetworkPolicy` in `selfheal-g1` and `selfheal-g1-other` that selects the
backend and admits only the Envoy proxy pods (namespace `envoy-gateway-system`, the
`gateway.envoyproxy.io/owning-gateway-name` label) on 8080, so the policy is parity and the only
baseline versus hardened difference stays the listener. A new informational check G1g labels the
runner pod as a stranger, calls a backend directly, and requires a Hubble `DROPPED` flow with a
policy-denied reason on the destination, which shows the backend side blocked it. Which side blocks
is therefore answered by the destination endpoint's ingress verdict in Hubble. Not read, that the
Envoy Gateway proxy pods carry the same `owning-gateway` labels the Service and Deployment docs
show, and whether Hubble reports the L7 proxy's source identity as the pod or the node. Both are
live checks L2 and L3 and must be answered on the cluster before the policy selector is final.

(d) Thresholds, per target, written now and not changed after a run without a disclosed addendum.

| Check | kind | EKS | AKS | Why |
|---|---|---|---|---|
| route status (Accepted, ResolvedRefs) | 30 s | 30 s | 30 s | Controller only, no cloud call, kind took 0.05 s |
| legit route | 200 | 200 | 200 | unchanged |
| denied host or namespace | 404 | 404 | 404 | unchanged |
| deleted backend | 500 | 500 | 500 | unchanged |
| Gateway Programmed with an address | n/a | 300 s | 300 s | Cloud load balancer provisioning, chosen from documented behaviour, not a run |
| first 200 through the load balancer | n/a | 300 s after address | 300 s after address | Target registration and health checks, same reason |
| TLS check G1f | n/a | cert Ready 120 s | cert Ready 120 s | cert-manager issue, S2 uses 60 s on kind |
| Hubble drop G1g | n/a | informational | informational | Reading the datapath, not a recovery claim |
| proxy kill G1e | informational | informational | informational | Decision 3, judged only after two clean runs on that target |

If a cloud number is missed the row reports the miss. The 300 s provisioning bounds are loose on
purpose, and they are retuned only by a disclosed addendum after a run, as ADR-0026 did once.

(e) Teardown. `eks-verify-gone.sh` gains a leftover check for load balancers, Classic and
NLB, filtered on the lab's cluster tag (`kubernetes.io/cluster/selfheal`) and on load balancer
security groups, since a Service left behind on destroy orphans an NLB and its security group in
the VPC and blocks the VPC delete. `aks-verify-gone.sh` already covers load balancers and public IPs
in the lab groups, so it only gains a comment naming G1. Both `*-predestroy.sh` scripts delete the
Gateway namespace first and wait for the Service to disappear, before Argo CD or the cluster goes.

(f) Proxy-kill bar. The kind run is a single run. A second kind run (kind-g1-run2) happens before
the cloud session, so decision 3 can be applied to kind. The cloud bar needs two clean runs per
target and cannot be satisfied in one paid session, so G1e is informational on EKS and AKS in the
first session whatever it shows. Both paths are recorded, the ClusterIP path and the load balancer
path, since load balancer deregistration time is a separate number.

### Build items this addendum creates (after acceptance)

`gitops/targets/{eks,aks}/g1/{baseline,hardened}`, a cloud `EnvoyProxy` patch, the HTTPS listener
and `Certificate`, the two `CiliumNetworkPolicy` objects in both profiles, `push g1` and a
`selfheal-g1` image entry in `eks-images.sh` and `aks-images.sh`, namespace creation in both
bootstrap scripts, `proxy_address` accepting `LoadBalancer`, G1f and G1g in `g1_row.py` and
`judge_g1`, G1 in the cloud row lists as a focused row, and the `eks-verify-gone.sh` check.

### Decisions for Jordan

1. Internal NLB through the in-tree provider on EKS, no Load Balancer Controller (recommended), or
   install the controller.
2. Add G1f (TLS) and G1g (Hubble drop) as new checks, or keep the cloud row to the kind checks only.
3. Add the parity `CiliumNetworkPolicy` to both profiles (recommended), or leave the G1 namespaces
   without policy and say so.

### Build notes 2026-11-17, where the build differs from the addendum above

- G1f. The HTTP half ("refused or redirected") is dropped. The hardened Gateway keeps its HTTP listener
  and the route attaches to both listeners, so no refusal exists to test. G1f is now, the lab-CA
  certificate Ready within 120 s, HTTPS served and verifying against the lab CA, and the same request
  without the lab CA failing verification.
- First 200 clock. It is measured from the start of the row, so it is never shorter than the wait after
  the address. The address clock is taken from Kubernetes timestamps (proxy Service created to Gateway
  Programmed), so it holds when the Gateway was provisioned before the row began. Both bounds are 300 s.
- Where G1 and X1 run. Both are added to each cloud's root application directory
  (`gitops/targets/{eks,aks}/apps/{baseline,hardened}`), as O1 is, so the runner moves the root path.
  Their rows stay focused only (`FOCUSED_ONLY_ROWS`), never in the default cloud row list. Envoy Gateway is
  installed by `eks-platform.sh` and `aks-platform.sh` from the pinned chart.
- Infrastructure change, not applied. `infra/aws/data.tf` gains two ECR repositories, `g1` and `x1`. The next
  `make eks-plan` shows them as additions. AKS needs none, ACR creates repositories on push.
- Not verified, because nothing ran on a cloud. That the in-tree provider tags its load balancers and security
  groups `kubernetes.io/cluster/selfheal` (the new `eks-verify-gone.sh` checks rely on it, and a check that
  finds nothing for the wrong reason would read as "gone"), live checks L1 to L3, and the Envoy
  proxy pod labels. Verify the tag on the first EKS session before trusting a clean result.

## Addendum 2026-11-18, second kind run (kind-g1-run2)

Baseline impact, hardened healed, same as run1. The proxy kill lost 0 of 19 requests in both profiles, so with
run1 (0 of 22) there are two clean runs on kind, which meets the condition of decision 3 for promoting G1e from
informational. No judged bound for G1e is written here, that needs your decision and a number fixed before the
next run. The recorded `proxy_recovery_s` (30.6 to 30.9 s in both runs) is not a recovery time. It is measured after
the 30 s kill window ends, so it can never be shorter than that window, and it should not be quoted or used as a bar.
The meaningful number is the failed request count. On the clouds G1e stays informational (cloud addendum, f).

## Addendum 2026-11-18, first AKS run (aks-g1-run1) and the address clock

Written after aks-g1-run1 and before any rerun.

aks-g1-run1 is kept as recorded. Baseline impact, hardened **miss**. Every functional check healed (stray host
`NoMatchingListenerHostname`, cross-namespace route `NotAllowedByListeners`, deleted backend 500 with
`BackendNotFound`, unrouted and unknown host 404, legitimate route served throughout, lab-CA TLS ready in 1.5 s and
refused without the lab CA, backend policy drop seen in Hubble). The miss is the load balancer address clock, 759 s
against the 300 s bound. First 200 was 5.8 s.

The clock was wrong, not the load balancer. It read the Gateway's `Programmed` `lastTransitionTime`, which moves
every time the listener changes. G1 was already deployed by the baseline root at bootstrap, so each profile switch
reset it. Baseline read 608 s and hardened 759 s, and the 151 s between them is the time between the two profile
switches. The Service events show the real provisioning. Service created 07:33:24Z, first `EnsuredLoadBalancer`
07:34:41Z, which is 77 s. The provider re-synced the Service on each Gateway change (four `EnsuredLoadBalancer`
events, one object, `firstTimestamp` unchanged).

Change. `lb_provisioning_s` now measures from the proxy Service `creationTimestamp` to the `firstTimestamp` of its
first `EnsuredLoadBalancer` event, selected by the Service UID. When the event is missing (events expire) the judge
errors, it never passes or misses on a clock it could not read. The 300 s bound is unchanged. Unit tests cover the
first event against a later re-sync, a missing event, and the judge error.

Limit. The rerun uses the same Service, so it times the same provisioning (77 s) through the corrected clock. It
proves the clock on a real cluster, not a second provisioning.

Live checks answered on AKS. L1, `deny-public-ip` accepted the internal frontend (`EnsuredLoadBalancer`, no public
IP on the `kubernetes-internal` frontend). The proxy Service took `10.42.0.10` from the node subnet `10.42.0.0/22`.
L2, both proxy pods carry `gateway.envoyproxy.io/owning-gateway-name=g1` and
`gateway.envoyproxy.io/owning-gateway-namespace=selfheal-g1-platform`, so the policy selector stands. L3, Hubble
reports proxy to backend flows with the proxy pod as source (its own Cilium identity, verdict FORWARDED), not the
host or node. EKS has the same clock and must not run G1 on the old one.
