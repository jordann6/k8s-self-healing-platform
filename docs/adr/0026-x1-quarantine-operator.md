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
