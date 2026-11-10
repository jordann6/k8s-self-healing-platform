# ADR 0019: O1 cloud parity contract

Status: ACCEPTED 2026-11-10 by Jordan, before the first O1 cloud experiment.
Source is built (unit tests and `make check` green); nothing is applied to any cloud yet.

## Context

ADR-0015 fixed the O1 contract on kind. ADR-0018 decided that the remaining
cloud rows run on Kubernetes 1.36 (Decision A), that the in-cluster receiver
stays the judged alert sink (Decision C), and that O1 is the first row of each
cloud session (Decision D). This ADR lists what in the kind O1 implementation
does not survive the move and fixes the cloud contract before any code. No
threshold below comes from a run.

## Facts that change the kind contract

1. Image delivery. Every O1 manifest uses `selfheal-o1:v1` with
   `imagePullPolicy: Never`, and `o1_row._server_dry_run` hardcodes the same
   name. On the clouds the image must come from ECR or ACR, selected through
   the generated Kustomize component as Q1 and Q2 do (ADR-0016, ADR-0017).
2. Runner gating. `o1_only()` is true only for `TARGET == "kind"`,
   `ROWS_BY_TARGET` has no O1 for eks or aks, and `apply_o1_profile()` patches
   the Argo CD path to `o1/{profile}`, not a per-cloud path.
3. Bootstrap. `eks-bootstrap.sh` and `aks-bootstrap.sh` do not create the
   `selfheal-o1` namespace (Pod Security `restricted` label) or apply
   `bootstrap/o1-runtime.yaml` (synthetic Secret, `o1-release` Lease,
   `quarantine-ingress` NetworkPolicy). Argo CD excludes Secrets and Leases,
   so these must stay outside Git.
4. Root applications. Cloud root apps hold only `q1.yaml` and `q2.yaml`.
5. Impersonation. The runner checks the operator with
   `kubectl auth can-i --as system:serviceaccount:selfheal-o1:o1-operator` and
   patches with `--as`.
   - EKS. The module sets `authentication_mode = "API"` and
     `enable_cluster_creator_admin_permissions = true`
     (`infra/aws/eks.tf`), so the runner identity holds cluster admin by
     access entry. Impersonation is expected to work.
   - AKS. The module sets `azure_rbac_enabled = true` with local accounts
     disabled and assigns the runner "Azure Kubernetes Service RBAC Cluster
     Admin" (`infra/azure/aks.tf`). Microsoft Learn (read 2026-11-10) lists
     that role as `dataActions: ["Microsoft.ContainerService/managedClusters/*"]`
     with no `notDataActions`. The page does not name an impersonate action,
     so whether the wildcard covers Kubernetes `impersonate` is not settled by
     the docs. This stays live check L11.
6. Placement. The O1 pods have no tolerations, so on both clouds they land on
   the untainted system pool. Cilium enforces NetworkPolicy on both clouds, so
   the quarantine policy needs no change.
7. Replacement clock. The 60s controller replacement includes an image pull
   if the new pod lands on a node that never pulled the image.
8. Versions. EKS standard support for 1.34 ends 2026-12-02. Both modules still
   default to 1.34.

## Decision

1. Contract and thresholds. The ADR-0015 contract, workload, profiles and every
   threshold stay as written, on both clouds. Nothing is relaxed. The 60s
   replacement clock is kept (Decision 3).
2. Image and overlays. `eks-images.sh` and `aks-images.sh` gain `push o1`, the
   generated component gains a `selfheal-o1` image entry, and the scripts write
   an `O1_IMAGE` file the runner reads, as for `Q2_IMAGE`. The dry-run image
   reads the target's image. New overlays `gitops/targets/<cloud>/o1/{baseline,hardened}`
   wrap the kind profile, add the generated component and patch the pull policy
   to `IfNotPresent`. Each cloud's `apps/{baseline,hardened}/o1.yaml` points at
   its overlay, and `apply_o1_profile()` patches that path, as
   `apply_q2_profile()` does.
3. Placement. The runner labels one system-pool node `selfheal.lab/o1=true`
   before the profile is applied, and the overlays pin the target, receiver and
   controller to that label through a nodeSelector. The replacement then pulls
   from a warm cache and the 60s clock measures the controller, not a registry.
   The cold-pull case is not hidden. The runner records the image pull time of
   the first start from the pod events in evidence. A cold pull that breaks 60s
   would be recorded as a fact in a later ADR change, not pre-relaxed.
4. Bootstrap. Both bootstrap scripts create `selfheal-o1` with the restricted
   Pod Security labels and apply `bootstrap/o1-runtime.yaml`, as `bootstrap.sh`
   lines 23 to 28 do on kind.
5. Impersonation is a precondition, not a verdict. Before the row's fault, the
   runner runs `kubectl auth can-i get leases -n selfheal-o1 --as
   system:serviceaccount:selfheal-o1:o1-operator`. If the call is Forbidden
   for the caller, the row is an error with the message, never a miss, so a
   missing runner permission cannot be read as a failed hardening. On AKS an
   error here means L11 failed and the role is changed before a retry.
6. Alert delivery stays the in-cluster receiver (ADR-0018 Decision C). No SNS
   topic or action group.
7. Versions. The session plans pass the version, not the module defaults.
   EKS uses `-var=cluster_version=1.36` and AKS uses
   `-var=kubernetes_version=1.36`, as ADR-0017 did for AKS. Defaults change in
   Phase 5 once L1 (EKS 1.36 readiness) has passed, so the proven kind and
   earlier cloud rows are not re-based by a source edit.
8. Row mechanics. O1 is a focused row on the clouds, run first in each session
   (ADR-0018 Decision D). It has its own run id and evidence directory.

## Thresholds

Identical to ADR-0015 on kind, EKS and AKS.

| Observation | kind | EKS and AKS |
|---|---:|---:|
| ResourceQuota and LimitRange present (hardened) | yes | yes |
| Defaults applied to a Pod with no resources (dry-run) | yes | yes |
| 9 CPU Pod rejected (dry-run) | yes | yes |
| Operator may patch the release Lease, may not read Secrets, delete Pods or patch the NetworkPolicy | yes | yes |
| Controller Pod replaced with a new UID and Ready | 60s | 60s |
| Evidence after incident marker | 30s | 30s |
| Alert after evidence | 30s | 30s |
| Release after eligible request, strictly after evidence | 30s | 30s |
| Ingress fails before release and succeeds after | yes | yes |

Missing telemetry stays an error. A present measurement that violates the
contract stays a miss.

## Verification plan

- Unit tests. The cloud image name flows into the dry-run, the O1 row is
  selectable on eks and aks, the overlay paths resolve, and the impersonation
  precondition returns an error, not a miss, on Forbidden. `make check` and
  `make test` stay green.
- Overlays render with `kustomize build` for both clouds and both profiles, and
  contain no `imagePullPolicy: Never` and no kind-only image name.
- Local proof stays kind. The paid proof waits for the batched session
  (ADR-0018 Decision D).

## Live checks

| # | Check |
|---|---|
| L11 | `kubectl auth can-i --as system:serviceaccount:selfheal-o1:o1-operator` succeeds for the runner's Entra identity under Azure RBAC |
| L12 | The first O1 pod pull time from ECR through NAT and from ACR, recorded in evidence |
| L13 | The replacement controller reaches Ready inside 60s on the pinned node on both clouds |

## Cost and session boundary

The change adds no hourly resource and no new repository beyond one ECR
repository for the O1 image. The proof reuses the session's bounded minutes.
Never push before Phase 5.

## References

- [Azure built-in roles, Containers](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/containers) (read 2026-11-10)
- ADR-0015, ADR-0016, ADR-0017, ADR-0018
