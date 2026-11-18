# Self-healing run `kind-x1-run8-cilium` (kind)

- Started 2026-11-18T06:36:31.831Z, finished 2026-11-18T06:37:15.003Z
- Rows: X1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| X1 | impact | **healed** | isolated in 1.321s without a restart True, release before evidence refused True, release after evidence True, resumed after controller kill in 2.958s True, self-isolation refused and role has no delete or Secret read True |

## Per row

### X1

**Steady-state hypothesis:** A Quarantine resource makes the controller isolate the named pod without restarting it, refuse a release requested before evidence exists, release only on a later request after the evidence, resume from status after the controller is killed, and refuse to isolate its own pod, with a role that cannot delete pods or read Secrets.

Healing control: A Quarantine custom resource makes a controller isolate the named pod without restarting it, refuse a release that comes before evidence, release only on a later request, resume from status after a controller kill, and refuse to isolate its own pod. Thresholds (kind): `{"isolate_s": 10, "blocked_s": 30, "resume_s": 30}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 06:36:33 |  |  |  |  |  |  | - | impact: manual flow isolated the pod, then released it with no evidence recorded and no status anywhere |
| hardened | 06:36:49 |  |  |  |  |  |  | - | healed: isolated in 1.321s without a restart True, release before evidence refused True, release after evidence True, resumed after controller kill in 2.958s True, self-isolation refused and role has no delete or Secret read True |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "x1_synced": true
  },
  "versions": {
    "repo_commit": "bfa013b812ebaf7603983b03517692e9b88a08da-dirty",
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
    "n1-server": null,
    "n1-client": null,
    "n1-denied": null,
    "o1-controller": null,
    "o1-alert-receiver": null,
    "kubernetes": "v1.37.0"
  }
}
```

X1 records, per Quarantine, the status phase and conditions the controller wrote, isolation time, direct pod-IP reachability from an in-cluster client before and after, pod UID and restart count, the evidence record, label-patch counts from the replacement controller's log, and `kubectl auth can-i` for the controller's service account.
