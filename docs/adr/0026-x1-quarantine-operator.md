# ADR 0026: X1 Quarantine operator (CRD plus controller)

Status: ACCEPTED 2026-11-17 by Jordan. Build starts after G1 is proven on kind.
Nothing is built.

## Context

The platform consumes operators (Karpenter, External Secrets, KEDA, Kyverno,
cert-manager, Argo Rollouts) but has never authored one, and no CRD of its own
exists. The incident response flow is spread across three pieces today.

- Falco Talon relabels a pod out of its Service and ReplicaSet selector
  (`platform/talon/hardened.yaml`, ADR-0002, isolate then preserve, never kill first).
- A pre-deployed `CiliumNetworkPolicy` selecting `quarantine=true` cuts traffic
  while still admitting the host so kubelet probes do not restart the container.
- O1's controller (`app/o1/app.py`) releases only after evidence exists, gated
  by an `o1-release` Lease and an evidence ConfigMap (ADR-0019).

A `Quarantine` custom resource and a controller turn that sequence into the
desired-state loop Kubernetes is built on, with a status a human and the runner
can read. This is the first CRD the lab authors.

## Decision

Jordan chose the Quarantine CRD on 2026-11-17 over a backup CRD or a partner-key
CRD. It reuses logic already proven live, so X1 is a refactor into an operator,
not new behaviour.

- Group `selfhealing.lab`, version `v1alpha1`, kind `Quarantine`, namespaced.
- `spec` has `podSelector` (or a pod name and UID), `reason`, `evidenceRef` (the
  ConfigMap or object that must exist before release), and `release` (policy,
  `onEvidence` or `manual`).
- `status` has `phase` (`Pending`, `Isolated`, `Preserved`, `Released`, `Failed`),
  `observedGeneration`, the pod UID it acted on, and conditions with timestamps.
- Controller written in Python with kopf, packaged like the R2 worker
  (`app/r2worker/`). Version, image digest and RBAC are fixed in the build step
  after reading the kopf and Kubernetes client docs against the pinned Python
  image. Nothing about kopf is verified yet, so this is a reading task first.
- The controller does not duplicate Talon. Talon (or a human) still detects.
  Creating a `Quarantine` is the action. The controller applies the same label
  patch and relies on the existing quarantine policy, then records evidence status.

## Contract

| Check | Fault | Hypothesis |
|---|---|---|
| X1a | `Quarantine` created for a running pod | Pod relabelled out of the selector, not restarted (same UID, restart count 0), status `Isolated` within a bound fixed before the first run |
| X1b | Release requested before evidence exists | Refused, status stays `Preserved`, pod stays isolated |
| X1c | Release after evidence exists | Pod released only strictly after the evidence timestamp |
| X1d | Controller pod killed mid-reconcile | A new controller resumes from `status`, no duplicate action, end state the same |
| X1e | `Quarantine` created for a pod the controller's role must not touch | Refused by RBAC, status `Failed` with a reason, nothing changed |

Baseline is the manual flow (a person runs the label patch and the release), so
the baseline impact is the missing status and the unguarded release. If the
baseline shows no impact the runner reports inconclusive, not healed.

## Risks and facts still to read

1. Least privilege. The controller needs patch on pods in one namespace only and
   no Secret reads, no pod delete. O1's role table (ADR-0019) is the model.
2. CRD lifecycle. Argo CD must apply the CRD before any `Quarantine` and
   handle deletion safely (a finalizer that never clears is the classic failure).
3. Admission. P1's Kyverno policy verifies image signatures, so the operator
   image must be signed and pinned by digest like the other lab images.
4. kopf behaviour on restart and on a status-patch conflict has not been read.
5. Cloud parity is expected to be cheap (no cloud API calls), but the EKS and AKS
   image push scripts (`eks-images.sh`, `aks-images.sh`) need the new image.

## Thresholds

None are set. X1a's isolation bound and X1d's resume bound are written in an
addendum per target before the runner is written.

## Out of scope

Replacing Talon, adding a webhook to Cilium, forensic capture automation, and any
second GitOps controller.

## Addendum 2026-11-17, build decisions

1. No kopf. Reading result. kopf 1.45.2 (2026-10-23, Python 3.11 or newer) is current,
   but it adds an async framework and its transitive packages to an image whose siblings
   (`app/o1`, `app/r2worker`) use only the standard library, and the lab would have to
   hash-pin and scan that tree. The controller is standard library only. This deviates
   from the first draft, which said kopf. The part that matters for the claim is kept, a
   CRD with a status subresource and a controller that reconciles from status.
2. Shape. `spec.podName` (one pod, not a selector), `spec.evidenceRef`, and
   `spec.release.requestId`. Status carries `phase`, `targetUID`, `isolatedAt`,
   `preservedAt`, `evidenceCapturedAt`, `handledReleaseId` and conditions. The decision is
   a pure function (`reconcile` in `app/x1/controller.py`) so every refusal is unit tested.
3. Release is request-id based. A request made before evidence is consumed and refused
   (`ReleaseRefused`, `NoEvidence`) and never turns into a release when evidence later
   arrives. A release needs a new request id, and it is refused in the same pass that
   first sees the evidence, so release is always strictly after the preserve step.
4. Evidence is bound to a pod UID. A record naming another UID is ignored, and a
   replacement pod with the same name is never acted on (`PodReplaced`). This is the
   stale-record lesson from the Azure S4 row.
5. No finalizer. Deleting a Quarantine leaves the pod isolated, the safe default, and
   removes the finalizer-that-never-clears failure from the risk list.
6. Protected pods. A pod labelled `selfheal.lab/protected=true` (the controller itself)
   fails with `Protected` and nothing is changed.
7. Isolation is the same label-keyed NetworkPolicy in both profiles, so the only
   difference between baseline and hardened is who applies and removes the label. The
   baseline is the manual flow, and its impact is a release with no evidence and no
   status anywhere.
8. Kind only, on a Cilium cluster (as O1), so NetworkPolicy is enforced. The evidence is
   direct pod-IP reachability from an in-cluster client, not the Service.

Thresholds, fixed before the first run. Status Isolated within 10 s, the pod unreachable
within 15 s, a replacement controller reaches Isolated within 30 s of the kill, exactly
one label patch for the Quarantine across the kill, and the role may patch pods but not
delete them or read Secrets.
