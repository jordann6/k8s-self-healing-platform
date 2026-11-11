# Self-healing run `eks-n1d` (eks)

- Started 2026-11-11T04:19:49.510Z, finished 2026-11-11T04:25:32.831Z
- Rows: N1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| N1 | no-impact | **inconclusive** | baseline was 'no-impact'; a heal can only be claimed against an impact observed in the same run |

## Per row

### N1

**Steady-state hypothesis:** For Cilium-managed pods on different nodes under ENI IPAM with native routing, the baseline sends the request as plaintext between the two pod addresses on the physical NICs. Hardened WireGuard replaces it with UDP 51871 between the node addresses, leaves no pod TCP and no plaintext marker on a physical NIC, establishes peers whose routes hold the remote pod address, keeps legitimate requests healthy and keeps an unauthorized cross-node request attributable to policy enforcement.

Healing control: Cilium WireGuard replaces visible cross-node VXLAN traffic while legitimate requests and policy denial remain observable. Thresholds (eks): `{"min_requests": 20, "max_failures": 0, "p95_ms": 300, "min_denied": 5, "denied_successes": 0, "min_policy_drops": 1, "min_packets": 5, "interfaces": 1, "min_peers": 1, "datapath": "native"}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 04:22:05 |  |  |  |  |  |  | - | no-impact: plaintext pod packets 202 on ['ens5', 'ens6', 'ens7'], WireGuard 0, marker visible True; requests 20 with 0 failures, p95 12.541 ms; denied successes 5, policy drops 0 |
| hardened | 04:25:01 |  |  |  |  |  |  | - | inconclusive: baseline was 'no-impact'; a heal can only be claimed against an impact observed in the same run |

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
    "kube-system/hubble-relay": true,
    "n1_slot_nodes": {
      "client": "ip-10-30-1-187.ec2.internal",
      "server": "ip-10-30-27-5.ec2.internal"
    },
    "selfheal/n1-server": true,
    "selfheal/n1-client": true,
    "selfheal/n1-denied": true,
    "n1_synced": true
  },
  "versions": {
    "repo_commit": "eaa5d2b84e008f8a60b64a9504c9be85d44402eb",
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
    "qworker": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/qworker:eaa5d2b",
    "q2-signal": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:eaa5d2b",
    "q2-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q2:eaa5d2b",
    "vpa-vertical-pod-autoscaler-recommender": "registry.k8s.io/autoscaling/vpa-recommender:1.8.1",
    "q3-target": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/q3:eaa5d2b",
    "n1-server": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:eaa5d2b",
    "n1-client": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:eaa5d2b",
    "n1-denied": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/n1:eaa5d2b",
    "o1-controller": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:eaa5d2b",
    "o1-alert-receiver": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/o1:eaa5d2b",
    "karpenter": "public.ecr.aws/karpenter/controller:1.15.1@sha256:8314ff9c6bca66372031e80532c18025bbe6d34eae2443849d9ff02796fb09c8",
    "kubernetes": "v1.36.5-eks-577a51e",
    "web_image": "<lab-account>.dkr.ecr.us-east-1.amazonaws.com/selfheal/app:eaa5d2b-v1",
    "workload_ami": [
      "ami-069787c4f4d0583ff"
    ]
  }
}
```

N1 records worker-underlay packet metadata, Cilium WireGuard peer status, cross-node request results, and exact Hubble policy drops. Same-node and control-plane traffic are outside the claim.
