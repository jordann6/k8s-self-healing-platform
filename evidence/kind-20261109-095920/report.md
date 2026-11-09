# Self-healing run `kind-20261109-095920` (kind)

- Started 2026-11-09T15:59:24.844Z, finished 2026-11-09T16:01:08.348Z
- Rows: P1 (partial run)
- Overall: **PASS** (every hardened row must be healed; nothing is skipped)

## Verdicts

| Row | Baseline | Hardened | Hardened detail |
|---|---|---|---|
| P1 | impact | **healed** | trusted and recovered images ran at sha256:bc5f2c12ccfbd17e5ec6ff0af832359b1fd3989818994775e4b605dce2c6351b; denied unsigned, invalid, untrusted, tag_only, verifier_outage; policy metrics pass 2.0, fail or error 5.0; verifier recovery 10.135s |

## Per row

### P1

**Steady-state hypothesis:** A digest-pinned artifact signed by the trusted local key is admitted and runs at the signed digest, while unsigned, invalid-signature, untrusted-signer, tag-only, and verifier-outage requests are denied with attributable telemetry and the verifier recovers within 60 seconds.

Healing control: Kyverno verifies a digest-pinned Cosign signature against the injected local trust root and fails closed. Thresholds (kind): `{"admission_s": 20, "ready_s": 120, "recovery_s": 60, "min_pass_decisions": 2, "min_reject_decisions": 5}`

| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 15:59:25 | 0s |  |  |  |  |  | - | impact: no P1 policy; admitted signed, unsigned, copied-invalid, untrusted-signer, and tag-only references at sha256:bc5f2c12ccfbd17e5ec6ff0af832359b1fd3989818994775e4b605dce2c6351b |
| hardened | 16:00:26 | 0s | 10s |  |  |  |  | - | healed: trusted and recovered images ran at sha256:bc5f2c12ccfbd17e5ec6ff0af832359b1fd3989818994775e4b605dce2c6351b; denied unsigned, invalid, untrusted, tag_only, verifier_outage; policy metrics pass 2.0, fail or error 5.0; verifier recovery 10.135s |

## Environment

```json
{
  "preconditions": {
    "context": "kind-selfheal",
    "api_ready": true,
    "kyverno/kyverno-admission-controller": true,
    "kyverno/kyverno-background-controller": true,
    "kyverno/kyverno-reports-controller": true,
    "argocd/argocd-application-controller": true,
    "gitops/gitserver": true,
    "p1_synced": true
  },
  "versions": {
    "repo_commit": "f9dad9b65fdbe5de40c52146a8dfdf7fef8e127d-dirty",
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
    "kubernetes": "v1.37.0"
  }
}
```

P1 records the single artifact digest, signature-state preflight, exact admission responses, runtime image IDs, Kyverno image-policy metric deltas, a positive PolicyReport, and fail-closed verifier recovery.
