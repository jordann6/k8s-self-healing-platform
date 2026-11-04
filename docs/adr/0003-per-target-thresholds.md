# ADR-0003: Thresholds per target, written in the report

- **Status:** accepted
- **Date:** 2026-11-03

## Context

Repair clocks differ by design: AKS waits about 5 minutes of NotReady before
node auto-repair, EKS node repair has toleration windows up to about 30
minutes. One global threshold would be either meaningless on kind or
impossible on the clouds.

## Decision

`scenarios/judge.py` holds a threshold table per target (`kind` now; `eks`,
`aks`, `gke` in later phases). Every report prints the thresholds it judged
against, next to each row.

## Threshold changes made after observing a run (disclosed)

- **A2 abort: 120s -> 180s** (2026-11-03, after run 3 measured 128s). The abort
  clock runs from the Git push and includes Argo CD's sync, the canary pod
  starting, the analysis' deliberate 45s wait for data (added after an empty
  result aborted a healthy canary) and two failing measurements 10s apart. The
  user-facing criteria did not change: afterwards only v1 serves and the error
  rate is under 1%.

## A3 latency on kind (decided 2026-11-04, no threshold change)

The A3 latency bar stays at 300 ms on every target. On kind, hardened A3 met its
error bar (0.3%) and the HPA scaled 3 to 8 and 3 to 10 pods in the two
reference runs, but p95 was 758 ms and 697 ms. The kind node shares eight
vCPUs between every pod and the control plane, and at-rest p95 there is
already 130-230 ms, so the bar is not reachable on that hardware.

The kind result is recorded as **errors healed, latency not reachable on one
shared node**, and the runner keeps reporting it as a miss. The latency claim is
proven on EKS, where worker nodes do not host the control plane. A kind-only
relative bar was considered and rejected: it would be a second post-observation
change, and a bar relative to an already degraded node proves little.

## EKS thresholds (set 2026-11-04, before the first EKS run)

The rows shared with kind keep the kind numbers, A3's 300 ms included. The
AWS-native rows carry service latency the lab does not control, so their
clocks are longer, and the numbers were chosen from documented behaviour, not
from a run:

| Row | Threshold | Why this number |
|---|---|---|
| A4 | all replicas serving within 300s, errors under 1% | a Karpenter replacement node is Ready in about 1 to 2 minutes; 300s leaves room for the pods to start and pass readiness |
| S1b | 0 failed queries, 0 dropped connections | the point of alternating users is that nothing fails |
| S4 | revoked within 600s | Config evaluates on change, typically within minutes, then the remediation retries every 60s |
| S5 | 0 failed requests, every node replaced within 1200s, at most 1 disrupted at a time | two or three nodes, one at a time, each launch, drain and terminate within a few minutes |
| R2 | isolated within 600s, evidence within 900s | GuardDuty runtime findings reach EventBridge within minutes; the state machine itself takes about a minute |

Like A2 on kind, any change after a run has been observed will be listed here
with the run that prompted it.

## Fixes during the first EKS session (2026-11-04, no threshold changed)

No EKS threshold was changed. These runner and manifest fixes were made after
the first runs had been observed, so they are listed here with the run that
prompted each one:

- **S1b, runner** (`eks-run1`): a rotation was treated as finished only once
  no version carried `AWSPENDING`. Secrets Manager leaves that label on the
  version that has just become `AWSCURRENT`, so the runner waited out its 240s
  on a rotation that had finished in 4s. A rotation is now finished when
  `AWSCURRENT` has moved and no other version is pending (unit tested).
- **S1b, hardened manifest** (`eks-run1-s1b`): the database probe could not
  read its own mounted credential files (`root:root`, mode `0440`, probe
  running as UID 10001), so every query failed with "no credential
  available". The pod now sets `fsGroup: 10001`.
- **R2, runner** (`eks-run1`): the runner stopped polling once the responder
  had isolated the pod and stored evidence (170s), before `ListFindings`
  returned a finding GuardDuty had created at 155s and already delivered to
  EventBridge. The runner now waits for the finding itself, and times
  detection from the finding's `CreatedAt`.

Three runs from that session are not reference evidence and are not
committed: `eks-run1`, `eks-run1-s1b` and `eks-run1-rerun` overlapped on the
same cluster (two runners switching profiles under each other), and an
in-cluster Git re-seed landed during `eks-run1`'s hardened A2. The rows they
measured before the overlap agree with the reference run.
