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
