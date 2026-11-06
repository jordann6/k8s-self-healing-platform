# Evidence

`make demo` writes `evidence/<run-id>/`: `report.md`, `report.json`, the raw
prober samples for every row window (`<row>/prober-<profile>-<kind>.json.gz`)
and the R1 evidence bundle (`R1/r1-hardened/`). Only curated reference runs are
committed; ad-hoc runs stay local (see .gitignore).

## Reference runs (kind, phase 1)

`kind-run5` and `kind-run7` ran the same commit on fresh clusters. Both runs:
every baseline row showed impact; hardened A1, A2, A5, S1, S2, S3 and R1 healed;
hardened A3 missed on latency only (p95 758 ms and 697 ms against 300 ms, errors
0.32% and 0.31%, HPA 3 -> 8 and 3 -> 10). Overall FAIL, by design: the runner
fails on any hardened miss. See the README "what broke" section for why A3's
latency bar is not met on one shared laptop node.

## Reference runs (EKS, phase 2)

One live session, 2026-11-04. Account IDs and the operator's IP are replaced
with `<lab-account>`, `<security-account>` and `<operator-ip>` by
`scripts/redact-evidence.py` before commit.

- `eks-run2`: every row, both profiles. Every baseline showed impact except
  S1b, whose baseline was invalid (the probe was already failing before T0).
  Hardened A1, A2, A3 (p95 267 ms), A5, S1, S2, S3, S4 and S5 healed; A4 and
  R2 missed and R1 errored, each on a cause fixed afterwards (ADR-0003).
- `eks-run2-rows`: A4, S1b, R1 and R2 after those fixes. Hardened S1b and R2
  healed (R2: isolated 155s, evidence 172s, 0.00% errors); R1 missed
  (isolated 34.6s against 30s); A4 inconclusive (hardened 0.00% errors and
  every replica serving at 78s, but its baseline errored on a stale FIS tag,
  fixed afterwards).
- `eks-run2-a4r1`: A4 and R1 once more. Hardened R1 healed (isolated 16.1s);
  A4 inconclusive again (hardened 0.13% errors, serving at 78s, but its
  baseline showed no impact: the baseline has no spread rule, and with three
  workload nodes already up the scheduler spread the pods on its own).

Not reference evidence, kept local: `eks-run1`, `eks-run1-s1b` and
`eks-run1-rerun` (overlapping runners), `eks-run2-rerun` (the operator's
laptop slept and froze the runner). ADR-0003 lists every fix with the run that
prompted it.

## Reference runs (AKS, phase 3)

One live session, 2026-11-05. The subscription, the tenant, every other GUID
and the operator's IP are replaced by `scripts/redact-evidence.py --cloud
azure` before commit.

- `aks-run1`: every row, both profiles. Every baseline showed impact except
  R2, whose trigger raised no Defender alert (an error, not an impact).
  Hardened A1, A2, A4, A5, S1 and S2 healed; A3 missed (p95 821 ms); S3, S4,
  R1 and R2 errored because the steady-state check took a cluster autoscaler
  scale-down for an R2 leftover (fixed afterwards, ADR-0008).
- `aks-run1-rows`: S3, S4, R1 and R2 after that fix. Hardened S3 and R1
  healed (R1 isolated at 8s); S4 missed (the policy's remediation template
  failed validation); R2 errored again (no Defender alert in 3600s).
- `aks-run1-s4`: S4 after the template fix. Healed: the rule was rewritten to
  Deny at 121s by the policy's own deployment. Its "non-compliant reported
  3s" matched the previous run's compliance record; the row now counts only
  records after T0 (ADR-0008 has the real times).
- `aks-run1-a3`: A3 with KEDA scaling the HPA from Prometheus. Missed: p95
  320 ms against 300, 0.00% errors, HPA 3 to 7, scaled at 41.5s.
