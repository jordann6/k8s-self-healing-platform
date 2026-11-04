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
