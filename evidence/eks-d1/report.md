# Self-healing run `eks-d1` (eks)

- Started 2026-11-11T03:52:17.155Z, finished 2026-11-11T03:55:12.366Z
- Rows: D1 (partial run)
- Overall: **FAIL** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| D1 | impact | **error** | runner exception: RuntimeError: D1 Job d1-backup-1794369294 unexpectedly failed: AccessDenied: An error occurred (AccessDenied) when calling the PutObject operation: User: arn:aws:sts::<lab-account>:assumed-role/selfheal-d1-backup/eks-selfheal-d1-backup--6ab73b86-e66e-4dfd-a2f1-142747e9cdf6 is not authorized to perform: s3:PutObject on resource: "arn:aws:s3:::selfheal-d1-worm-<lab-account>/backups/d1-backup-1794369294-45xws.dump" with an explicit deny in a service control policy: arn:aws:organizations::692859913278:policy/o-rpfh5h9ite/service_control_policy/p-zepreoac
 |

## Per row

### D1

**Steady-state hypothesis:** A completed PostgreSQL backup restores exactly 100 deterministic rows into a fresh target within 180 seconds, loses only the one intentional post-backup row, rejects a truncated archive atomically, and remains readable after identity and WORM deletion attempts.

Healing control: A retained logical PostgreSQL backup restores exact data into a fresh target and rejects deletion. Thresholds (eks): `{"backup_s": 120, "restore_s": 120, "rto_s": 180, "rpo_rows": 1, "rows": 100, "retention_days": 1, "admin_attribution": true}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 03:53:23 |  |  |  |  |  |  | - | impact: no backup recovery point; source PVC replacement removed the deterministic data and no restore was available |
| hardened |  |  |  |  |  |  |  | - | error: runner exception: RuntimeError: D1 Job d1-backup-1794369294 unexpectedly failed: AccessDenied: An error occurred (AccessDenied) when calling the PutObject operation: User: arn:aws:sts::<lab-account>:assumed-role/selfheal-d1-backup/eks-selfheal-d1-backup--6ab73b86-e66e-4dfd-a2f1-142747e9cdf6 is not authorized to perform: s3:PutObject on resource: "arn:aws:s3:::selfheal-d1-worm-<lab-account>/backups/d1-backup-1794369294-45xws.dump" with an explicit deny in a service control policy: arn:aws:organizations::692859913278:policy/o-rpfh5h9ite/service_control_policy/p-zepreoac
 |

## Environment

```json
{
  "preconditions": {
    "context": "selfheal-eks",
    "api_ready": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "d1_synced": true
  },
  "versions": {
    "repo_commit": "295765894aa3cb843705dbeb87feb275dd0a8201",
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

D1 records the deterministic source digest, backup Job identity and completion, retained object version and checksums, authorized source PVC replacement, exact fresh-target restore, RPO and RTO, an atomic malformed restore failure, and two deletion denials.
