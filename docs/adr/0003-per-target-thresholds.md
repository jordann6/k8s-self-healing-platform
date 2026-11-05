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
- **S1b, steady state and judge** (`eks-run2`): the baseline probe had failed
  every login for 30 minutes before T0. Earlier runs had rotated the
  password, and in the baseline ESO refreshes the probe's Secret only hourly,
  so a restarted probe started on an invalidated credential. The runner now
  requires 30s of clean logins before T0 (forcing an ESO sync and restarting
  the probe on proof of failures), and the judge returns an error, never
  impact, when the probe was failing before the fault.
- **A4, hardened manifest** (`eks-run2`): the hardened topology spread
  (`DoNotSchedule` on zone and hostname) only counted the nodes that existed.
  With one workload node the skew was 0, all three replicas stayed on it, and
  the node kill took every replica (a miss, 56% errors at 24.6s). The spread
  now sets `minDomains` (3 nodes, 2 zones), so missing nodes count as empty
  domains and Karpenter launches one for each extra replica.
- **A4, runner** (`eks-run2`): after the abort line the row stopped
  recording, so the evidence showed no node deletion, replacement or recovery
  (Karpenter had deleted the node at 35s). The row now keeps recording them
  until its window ends; the verdict still comes from the abort line.
- **A4, runner** (`eks-run2-rows`): the runner tagged the target node for
  FIS but never removed the tag, and FIS picks one of the tagged running
  instances at random. A tag left by an interrupted run made FIS terminate a
  different node (judged an error). The runner now clears every A4 target
  tag before tagging and after the experiment.
- **R1, runner** (`eks-run2`): the Hubble relay port-forward was opened once
  per run and did not survive two hours through the EKS API, and a failed
  query was read as "no drops", so a working quarantine was judged an error
  (Hubble had recorded the prober's `POLICY_DENY` drops). Every query now
  re-opens the port-forward if needed, and a failed query raises instead of
  returning nothing.
- **R2, responder order** (`eks-run2`): the responder relabelled the pod out
  of the Service before cordoning its node, and in the 0.2s between the two
  the scheduler put the replacement pod on that same node. The security
  group swap then cut the replacement off while it still reported Ready, so
  about a third of requests failed for 100s (12.65%, a miss). The state
  machine now cordons first.
- **R2, runner** (`eks-run1`): the runner stopped polling once the responder
  had isolated the pod and stored evidence (170s), before `ListFindings`
  returned a finding GuardDuty had created at 155s and already delivered to
  EventBridge. The runner now waits for the finding itself, and times
  detection from the finding's `CreatedAt`.

Three runs from that session are not reference evidence and are not
committed: `eks-run1`, `eks-run1-s1b` and `eks-run1-rerun` overlapped on the
same cluster (two runners switching profiles under each other), and an
in-cluster Git re-seed landed during `eks-run1`'s hardened A2. A fourth,
`eks-run2-rerun`, is not reference evidence either: the operator's laptop slept
from 25 minutes in, freezing the runner (its port-forwards died and its polls
stopped) while the cluster kept going. `make demo` now holds a macOS idle-sleep
assertion for the length of a run.

The prober records every request inside the cluster, so a paused runner
cannot hide errors; it only observes recoveries late. Where the laptop slept
during `eks-run2` (06:25 to 06:28, 07:32 to 07:38 and 07:54 to 07:57 local
time, during baseline S1, hardened S1 and hardened R2), the measured times
are upper bounds.

## AKS thresholds (set 2026-11-05, before the first AKS run)

Written before any AKS run, from documented behaviour (ADR-0007), not from a
measurement. The rows shared with kind and EKS keep their numbers, A3's 300 ms
included, and R1 keeps 30s (same Cilium, same pre-allocated identity).

| Row | Threshold | Why this number |
|---|---|---|
| A4 | every web replica serving, off the stopped node, within 600s of T0; a new workload node; errors under 1% **from NotReady on** | NotReady after the node controller's 50s grace, eviction after the hardened 30s toleration, an autoscaled D2s_v3 node in about 3 to 5 minutes, then the pod starts |
| S4 | the rule closed by the policy's own deployment within 1200s | DINE runs once the write has succeeded (`AfterProvisioningSuccess`), then deploys a template; policy compliance data lags "around 15 minutes" |
| R2 | a Defender alert within 3600s (Defender's clock, reported); the pod isolated within 300s **of the alert**; evidence within 600s **of the alert**; errors under 1% | detection is documented as "up to an hour" and is not the lab's to tune; the response (automation, Logic App, queue, worker, snapshot, Run Command) is |

**Decision D3 (A4's error clock).** A stopped scale set VM is not deleted the
way a terminated EC2 instance is: its Node object stays and its endpoints keep
receiving traffic until the node controller marks it NotReady (50s by
default). With three replicas that is about a third of requests failing for
that minute in both profiles, which alone would break a 1% bar. A4's error
rate on AKS is therefore judged from the moment the node is marked NotReady
(the detection), the way A1 is judged after detection and A3 after its ramp.
The gap from T0 to NotReady is measured and printed in the report, never
dropped. The NotReady time is the earlier of the runner's observation and the
condition's own `lastTransitionTime`, so the judged window can only grow. On
AWS the instance is terminated and A4 keeps its EKS rule (errors from T0).

**Honesty rules added for AKS (each one has a unit test):**

- A4: the stop must be confirmed (the instance reports `PowerState/stopped`)
  and the stopped node must have run a web pod, or the row is an error. A node
  never marked NotReady in the window is an error: the error clock has no
  start. An `aks-auto-repair` event on the stopped node inside the window
  voids the row as an error (ADR-0007 row 17): the repair, not the lab's
  controls, would have acted. The abort line only reads samples after
  NotReady.
- S4: a closed rule only counts when the policy closed it: the rule reads
  `Deny` with the description the policy's template writes, and a deployment
  in the lab group that started after T0 and succeeded names that rule.
  Anything else that closed or deleted it is an error, never a heal. The
  deployment's name is recorded, not assumed (the DINE docs do not name it).
- R2: only a real alert counts: generated after T0, naming the lab cluster
  AND the attacked pod (a portal sample alert names neither); a display name
  with the `[SAMPLE ALERT]` prefix (third-party documented) is refused as
  well. No alert within 3600s is an error (the trigger never fired). Isolation
  is the pod relabelled AND its node cordoned AND the prober no longer
  reaching it; isolation reported while the pod still answers is an error.
  A replacement web pod scheduled onto the quarantined node is a miss (the
  EKS race, ADR above). Response times measured before the alert's own
  generation time are an error (clock skew), never a fast heal. Evidence is
  the Logic App's `manifest.json` for that alert with a `Succeeded` snapshot
  and a non-empty Run Command capture; `failed.json` means no evidence.
- Every target: a row whose observations carry a `void` reason is an error,
  before the abort line is considered.

There is no node-level network quarantine on AKS (decision D4): R2 isolates
by cordon plus the pod's quarantine policy, and the README lists the gap. The
node is protected from autoscaler scale-down by annotation, the AKS-supported
stand-in for EC2 termination protection.
