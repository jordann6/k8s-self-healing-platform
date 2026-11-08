# ADR-0010: Q2 scaling failure contract

- **Status:** accepted
- **Date:** 2026-11-08

## Context

Q1 proves that KEDA can scale a queue worker from zero, drain a backlog, and
return to zero without losing work. It does not show what happens when the
metric source fails, the replica limit is reached, or the scheduler cannot
place the replicas the autoscaler requests.

The autoscaling and security videos reviewed for this program remain reference
inputs:

- <https://www.youtube.com/watch?v=hsJ2qtwoWZw>
- <https://www.youtube.com/watch?v=oBf5lrmquYI>

They are not proof. Q2 is proven only by code, negative tests, telemetry failure
tests, and a measured local run.

KEDA 2.21 supports a Metrics API scaler and a fallback replica count after a
fixed number of consecutive metric failures. Kubernetes exposes HPA conditions
for metric errors and for scaling limited by the configured minimum or maximum.
Q2 records those conditions rather than inferring autoscaler behavior from a
replica count.

## Decision

Q2 uses a dedicated HTTP workload and a separate in-cluster metric source. The
metric source exposes a numeric demand value and can deliberately return an
error while remaining available to the experiment controller. The workload
has one bounded request slot per replica, so one replica is measurably
overloaded by the test rate while three replicas can serve it.

Both profiles use a KEDA `ScaledObject` with these bounds:

| Setting | Value |
|---|---:|
| Minimum replicas | 1 |
| Maximum replicas | 4 |
| Polling interval | 5 seconds |
| Cooldown | 30 seconds |
| Metric target | 1 demand unit per replica |

The baseline has no fallback. During a metric-source error, KEDA and the HPA
retain the current replica count. The hardened profile adds this fallback:

| Setting | Value |
|---|---:|
| Consecutive failures | 1 |
| Fallback replicas | 3 |
| Fallback behavior | static |

The profile difference is only the fallback. Both profiles run the same
workload, signal source, load, ceilings, and recovery sequence.

## Experiment sequence

Each profile runs these phases in order:

1. Prove the metric source, ScaledObject conditions, and HPA conditions are
   present while demand is healthy.
2. Raise demand and prove normal scaling before any fault.
3. Return to one replica, start the fixed request load, then make the metric
   endpoint return an error.
4. Record the metric-source error, autoscaler conditions, desired and actual
   replicas, request latency, failures, and time to detect the failure.
5. Restore the metric endpoint and measure the return to a Ready scaler.
6. Set demand above four. Require four actual replicas and an HPA
   `ScalingLimited=True` condition. Four replicas alone do not prove that the
   ceiling was observed.
7. Return to one replica. Create a temporary capacity holder whose CPU request
   leaves less free CPU than one workload replica requires. Raise demand to
   four and require at least one Pending workload pod with a scheduler message
   containing `Insufficient cpu`.
8. Delete the capacity holder and measure the time until all four requested
   replicas are Ready and no workload pod is Pending.
9. Stop the request load, set demand to zero, and measure scale-down to the
   configured minimum of one replica.

The runner records the node's allocatable CPU, scheduled CPU requests before
the capacity fault, the capacity holder's CPU request, queue or workload
demand, every Pending pod and its scheduler reason, condition snapshots, and a
replica timeline.

## Thresholds fixed before the first live run

| Observation | Threshold |
|---|---:|
| Metric failure detected | 30 seconds |
| Hardened fallback reaches 3 Ready replicas | 30 seconds |
| Metric source recovers after restoration | 45 seconds |
| Capacity recovers after the holder is removed | 60 seconds |
| Scale-down after demand and load are withdrawn | 90 seconds |
| Healthy-window request p95 | at most 300 ms |
| Healthy-window request failure rate | under 1 percent |
| Replica ceiling | exactly 4 actual replicas with `ScalingLimited=True` |
| Scheduling ceiling | at least 1 Pending pod with `Insufficient cpu` |

These thresholds will not be relaxed to obtain a pass. Any later change must
be disclosed here with the run that motivated it.

## Post-observation implementation corrections

The first hardened implementation required three consecutive metric failures.
Runs `kind-20261108-083600` and `kind-20261108-085200` measured fallback readiness
at 39 and 34 seconds, beyond the fixed 30-second threshold. The fallback control
was changed to activate after the first failed metric poll. The three-replica
fallback and every verdict threshold remain unchanged.

The focused Q2 kind configuration also changes the HPA reconciliation cadence
from the Kubernetes default to five seconds. That setting is isolated to
`q2-kind-up`; normal kind and Q1 runs retain the shared cluster configuration.
Fallback readiness additionally requires three ready Service endpoints so pod
readiness cannot be mistaken for usable service capacity.

Run `kind-20261108-130138` reached fallback in 10 seconds but missed the request
failure threshold with 17 HTTP 503 responses in 903 judged requests. The
failures were spread across the complete post-fallback window and every one
occurred at the bounded work-slot acquisition timeout. The timeout was raised
from 100 to 150 milliseconds to absorb brief per-endpoint request imbalance.
The workload still has one slot per replica, takes 60 milliseconds per request,
and uses the same 30 request-per-second load. The latency and failure thresholds
remain unchanged.

## Verdict contract

The baseline shows impact when the planned metric outage leaves one replica
overloaded and either request p95 exceeds 300 ms or failures reach 1 percent.

The hardened profile heals only when all of the following are measured:

- The planned metric error is observed, not inferred.
- ScaledObject and HPA conditions are present throughout the fault.
- KEDA fallback reaches three Ready replicas within 30 seconds.
- Hardened fallback activates after the first failed metric poll.
- The focused kind profile uses a five-second HPA reconciliation cadence, and fallback is ready only when three Service endpoints are ready.
- The post-fallback request window meets the latency and failure thresholds.
- Restored metrics become Ready within 45 seconds.
- The replica ceiling is attributed to `ScalingLimited=True`.
- Pending pods are attributed to an observed `Insufficient cpu` scheduler
  reason.
- Removing the capacity holder makes every requested replica Ready within 60
  seconds.
- With load and demand withdrawn, the workload scales back to one within 90
  seconds.

Missing metric-source evidence, missing autoscaler conditions, missing request
samples, or missing scheduler reasons is an error. A replica count at the
maximum without the HPA condition is an error. A Pending pod without a
scheduler reason is an error. Recovery times are measured from the restoration
actions and are never inferred from the final state.

## Scope

The first proof runs on local kind at zero cloud cost. It demonstrates
autoscaler and scheduler failure behavior on one node. It does not prove cloud
node provisioning, multi-zone capacity, or a managed metrics service. EKS and
AKS parity belongs to the bounded paid sessions in the next phase.
