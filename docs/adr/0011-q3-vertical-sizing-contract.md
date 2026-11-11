# ADR-0011: Q3 vertical sizing contract

- **Status:** accepted
- **Date:** 2026-11-08

## Context

Q1 proves queue-driven horizontal scaling. Q2 proves behavior when horizontal
scaling loses telemetry or reaches replica and node ceilings. Neither row asks
whether a workload's CPU and memory requests match measured use.

The upstream Vertical Pod Autoscaler documentation defines `Off` mode as a dry
run: the recommender writes recommendations to the VPA status but never changes
pod resources. The recommender reads current usage from the Kubernetes resource
Metrics API. VPA 1.8 supports Kubernetes 1.36 through 1.38, including this lab's
kind 1.37 cluster.

Primary references:

- <https://kubernetes.io/docs/concepts/workloads/autoscaling/vertical-pod-autoscale/>
- <https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/installation.md>
- <https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/components.md>
- <https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/api.md>

## Decision

Q3 uses a dedicated one-replica workload with fixed requests of 50 millicores
and 32 MiB. It consumes approximately 200 millicores and retains 96 MiB, making
the initial requests observably undersized without causing an OOM or CPU limit.
No HorizontalPodAutoscaler targets this workload.

The focused platform installs Metrics Server and the official VPA chart. Only
the recommender runs. The updater and admission controller are disabled, so no
VPA component capable of changing workload resources exists in the cluster.

The baseline profile has the workload but no VPA object. The hardened profile
adds one `autoscaling.k8s.io/v1` VPA with:

| Setting | Value |
|---|---|
| Update mode | `Off` |
| Controlled values | Requests only |
| Controlled resources | CPU and memory |
| CPU bounds | 10m to 1 core |
| Memory bounds | 16Mi to 256Mi |

## Experiment

Each profile proves the resource Metrics API is available, records timestamped
CPU and memory samples, and confirms the pod has no HPA. The runner records the
pod UID, restart count, and configured requests before and after observation.

Baseline impact requires measured CPU and memory use above the fixed pressure
thresholds and the expected absence of a VPA recommendation. Hardened healing requires a VPA
recommendation whose target lies within its lower and upper bounds, exceeds the
undersized request, and remains reasonably related to measured p90 usage. The
same pod must remain running with unchanged requests and zero restarts.

## Thresholds fixed before the first live run

| Observation | Threshold |
|---|---:|
| Recommendation available | 240 seconds |
| Resource samples | at least 6 |
| Measured CPU p90 | at least 100m |
| Measured memory p90 | at least 64Mi |
| CPU target divided by measured p90 | 0.70 to 2.00 |
| Memory target divided by measured p90 | 0.90 to 2.00 |
| Pod restarts | 0 |

These thresholds will not be relaxed to manufacture a pass. Any implementation
correction after a live run will be recorded here with the motivating evidence.

## Honesty rules

- Missing resource metrics is an error in either profile.
- Fewer than six measured samples is an error.
- A hardened run without a VPA recommendation is an error.
- A recommendation outside its reported bounds is an error.
- An active HPA makes the experiment invalid.
- A changed pod UID, changed requests, or restart is a miss.
- Recommendation-only mode is not an applied resize and will not be described
  as one.

## Scope

The kind proof validates recommendation generation and evidence integrity. It
does not validate eviction, in-place resize, production history retention, or
cloud-specific managed VPA behavior. Those remain outside Q3's local claim.
