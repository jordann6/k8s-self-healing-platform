# ADR 0015: O1 operational readiness contract

Status: accepted before the first O1 live experiment on 2026-11-09.

## Context

O1 is the final Phase 3 capability. It must prove that a namespace has bounded
resource use, operators and controllers have distinct least-privilege roles,
an alert reaches an accountable receiver, a failed controller returns, and a
quarantined workload is released only by an operator after evidence capture.
The row must also distinguish a missing control from missing telemetry.

Primary Kubernetes documentation reviewed for this contract covers
ResourceQuota, LimitRange defaulting, RBAC resource names, Deployments,
NetworkPolicy, and Lease objects. The Kubernetes security checklist is the
production-readiness reference. O1 measures a deliberately small local
boundary and does not claim that the complete lab is production certified.

## Decision

Use one isolated `selfheal-o1` namespace and one focused kind run. A fixed Pod
named `o1-target` begins with the `o1.security/quarantined=true` label. A
NetworkPolicy denies ingress while that label is present.

The hardened profile adds:

- a ResourceQuota and LimitRange with CPU and memory defaults
- an `o1-operator` Role that can get and patch only the named `o1-release`
  Lease
- an `o1-controller` Role that can get and patch only the named target Pod and
  evidence ConfigMap, and get the named release Lease
- a controller Deployment and an alert-receiver Deployment

The operator requests release by patching the named Lease. The controller does
not release the target until the target has an incident marker and the evidence
ConfigMap contains the target UID, resource version, image, and capture time.
Evidence capture sends an alert containing the same target UID to the receiver.
Only then may the controller open the named quarantine NetworkPolicy.

The baseline has no quota or default limits, grants its operator broad
namespace access, and has no evidence controller or receiver. This deliberately
shows that an operator can read a synthetic Secret and directly open the
quarantine NetworkPolicy without evidence.

## Fixed thresholds and verdict contract

These thresholds are fixed before the first live experiment:

- ResourceQuota and LimitRange must both be present in hardened.
- A server-side dry-run Pod with no resources must receive the fixed default
  request and limit in hardened.
- A server-side dry-run Pod requesting 9 CPUs must be rejected in hardened.
- The hardened operator may patch the named release Lease, but may not read
  Secrets, delete Pods, or directly patch the quarantine NetworkPolicy.
- A deleted controller Pod must be replaced with a different UID and become
  Ready within 60 seconds.
- Evidence must be captured within 30 seconds of the incident marker.
- The matching alert must be delivered within 30 seconds of evidence capture.
- Release must occur within 30 seconds of the operator request becoming
  eligible, and strictly after evidence capture.
- Ingress to the quarantined target must fail before release and succeed after
  release.

Missing quota, limit, RBAC, controller identity, replacement, evidence, alert,
release, or connectivity telemetry is an error. A present measurement that
violates the contract is a miss.

## Boundaries and limitations

- The alert receiver is an in-cluster deterministic sink, not an external
  paging provider. Cloud notification delivery is part of the later cloud port.
- The controller is purpose-built for this lab. It is not a general incident
  response operator and does not introduce a custom resource definition.
- The operator request is a namespaced Lease update. Human authentication,
  approval workflows, and break-glass federation are outside the local proof.
- ResourceQuota and LimitRange bound this namespace only. They do not test node
  eviction, cluster capacity, or multi-tenant fairness across namespaces.
- Deleting the kind cluster remains the authorized teardown boundary.
