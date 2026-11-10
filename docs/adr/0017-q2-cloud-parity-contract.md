# ADR 0017: Q2 cloud parity contract

Status: accepted before the first Q2 cloud experiment on 2026-11-10.

## Context

ADR-0010 fixed the Q2 contract on kind. Phase 4 runs the same sequence on EKS
and AKS. Seven kind assumptions do not survive the move, and each one was
checked against vendor documentation before any number below was written. No
threshold here comes from a run, as in ADR-0003.

## Facts that change the kind contract

1. HPA sync cadence. Kubernetes documents `--horizontal-pod-autoscaler-sync-period`
   with a default of 15s. Kind set it to 5s in `kind/q2-cluster.yaml`. Microsoft
   documents the AKS control plane, including kube-controller-manager, as
   Azure-managed and offers no cadence setting. AWS offers
   `horizontalPodAutoscalerSyncPeriod` (10s to 15s) only on EKS Provisioned
   Control Plane, which is billed hourly by tier, and setting it blocks the
   return to Standard mode. This lab runs Standard mode, so both clouds run the
   15s default. Provisioned Control Plane is rejected because it adds an hourly
   resource to a lab whose cost rule is to add minutes, not new hourly resources.

2. Node autoscalers can mask the scheduling ceiling. Karpenter documents that a
   pod whose nodeSelector names a label the NodePool does not define will not
   cause a node launch and stays Pending. The label approach is therefore sound
   on EKS. The Kubernetes Cluster Autoscaler documents that it builds its
   scale-up template node from an existing node of the group, and that nodes of
   autoscaled groups should not be modified by hand. A label added with kubectl
   may therefore appear in the template, and the label approach cannot be
   trusted on AKS. This corrects the handoff assumption. On AKS the Q2 session
   also pins the workload pool to its current size (see Decision 2).

3. Single-node assumption. `add_capacity_holder` requires one node. The clouds
   have three workload nodes, so the runner places one holder per labelled
   workload node.

4. Four replicas must fit. Each replica requests 250m CPU. The runner measures
   free requested CPU on every labelled node before the fault and records it.
   If the four-replica ceiling or the holder arithmetic cannot fit, the run is
   an error. The 250m request is not changed.

5. KEDA versions. EKS installs the pinned 2.21 chart. The AKS add-on version is
   set by the cluster's Kubernetes minor and is not upgraded within it. The AKS
   version table lists 2.16.1 on 1.33, 2.17.2 on 1.35 and 2.19.0 on 1.36, and
   its 1.34 row could not be read. The Q2 trigger uses `metricType: Value`.
   KEDA's changelog shows fallback support for `Value` triggers arrived in
   2.18.0, and the 2.17 reference states fallback applies only to `AverageValue`
   targets. The AKS 1.34 add-on is therefore expected to leave the hardened
   fallback disabled. The `behavior` field arrived in 2.17.0.

6. Hardcoded kind values in the runner (threshold table, image name, the
   kind-only guard, KEDA namespaces, footer text) become target-aware.

7. Argo CD. The cloud root applications own a separate `selfheal-q2`
   application, as Q1 does. Argo CD ignores only the Deployment replica count.

## Decision

1. Use the ADR-0010 sequence, workload, ScaledObject bounds and profiles on both
   clouds. The profiles still differ only by fallback.

2. Defeat node autoscaler masking in two layers and prove it.
   - Every current workload node gets `selfheal.lab/q2=true` before Argo CD
     creates the Q2 application. The `q2-target` pods select that label
     through the per-cloud overlay, together with the existing workload
     toleration. Holders also select the label. The load generator and
     `q2-signal` select nothing and stay off the tainted workload pool.
   - On AKS the Q2 session applies the workload pool with `workload_max_nodes=3`,
     equal to the minimum, so the cluster autoscaler cannot add a node. This is
     a Terraform variable on the existing module. Row A4 is not run in a Q2
     session.
   - The verdict adds a negative control for the masking risk. The node count
     and, on EKS, the NodeClaim count must be unchanged from the start of the
     Pending window to the removal of the holders. Any new node is an error, not
     a pass.

3. Run the AKS Q2 session on Kubernetes 1.36. Its add-on ships KEDA 2.19.0,
   which supports fallback on `Value` triggers and the `behavior` field. The
   trigger, bounds and profiles stay identical to kind and EKS, so ADR-0010 is
   not reopened. The session sets `kubernetes_version=1.36` on the existing
   module variable. Version parity with EKS (1.34) is deliberately given up for
   this session only. The runner reads the KEDA operator image tag on every
   target, records it in evidence, and treats a version below 2.18 as an error
   before the fault starts, so a disabled fallback can never be read as a
   failed hardening.

4. Thresholds fixed before the first cloud run. Kind keeps the ADR-0010 numbers.

   | Observation | kind | EKS and AKS | Derivation |
   |---|---:|---:|---|
   | Metric failure detected | 30s | 30s | The operator marks the ScaledObject not Ready within one 5s poll. The HPA condition follows within one 15s sync. About 20s worst case. |
   | Hardened fallback at 3 Ready replicas and 3 endpoints | 30s | 60s | 5s poll, up to 15s HPA sync, then pod creation, image pull from ECR or ACR on a node that has not run the image, container start and readiness. About 35s typical and 50s worst case. |
   | Metric source recovers | 45s | 45s | Readiness comes from the operator poll, not the HPA. Unchanged. |
   | Capacity recovers after holders are removed | 60s | 90s | The delete event requeues the Pending pods at once. Image pull on a node that never ran a replica and readiness add up to about 30s beyond kind. |
   | Scale-down after demand and load are withdrawn | 90s | 120s | The HPA sync is 10s longer than kind, plus pod termination. |
   | Healthy-window request p95 | 300ms | 300ms | Unchanged. |
   | Healthy-window request failure rate | under 1% | under 1% | Unchanged. |
   | Replica ceiling | 4 and `ScalingLimited=True` | same | Unchanged. |
   | Scheduling ceiling | `Insufficient cpu` | same | Unchanged. |

   These numbers are not relaxed to obtain a pass. Any later change is listed
   here with the run that motivated it.

5. The baseline impact verdict still holds at a 15s cadence. During the outage
   the baseline keeps its current single replica, so overload depends on the
   fixed replica count and the request slot, not on how often the HPA syncs. The
   hardened fallback load of 30 requests per second over three slots is the
   same as on kind.

6. Build and delivery follow Q1 (ADR-0016). Each target session builds the Q2
   image from the repository commit, pushes it to ECR or ACR and selects it
   through the generated Kustomize component. ECR gains one encrypted
   repository. ACR needs nothing new.

## Session mechanics

- AKS plans the Q2 session with
  `make aks-plan AKS_PLAN_ARGS="-var=kubernetes_version=1.36 -var=workload_max_nodes=3"`.
  The 1.36 version supplies KEDA 2.19 and the pinned pool size blocks the
  cluster autoscaler. EKS needs no extra variable.
- The runner labels the workload nodes (`selfheal.lab/q2=true`) before the Q2
  application is expected to be Healthy, and again at the start of the row for
  mixed runs where Karpenter may have replaced a node.
- The runner waits for the ceiling and for Pending pods for up to 120s and 90s
  on the clouds (60s on kind), and runs the outage and capacity loads for longer
  (140s and 260s against 80s and 100s). These bounds only stop a stuck run and
  are never judged. The thresholds above are the only verdict clocks.
- Q2 is a focused row on the clouds. A mixed cloud run that includes A4 or S5
  can replace labelled nodes, which the row-start relabel covers.

## Cost and session boundary

The source change adds one encrypted ECR repository and image storage. It adds
no hourly resource. The live proof reuses each lab's bounded session, so the
extra cost is the minutes of one Q2 run, which is a few minutes for the
sequence plus the session overhead already in the runbook. The operator reviews
a fresh saved plan and the projected cost before any apply, then destroys and
verifies the target. Never push before Phase 5.

## Consequences

Q2 on the clouds proves the same failure behavior on managed control planes and
real schedulers with a slower HPA, and it proves that a node autoscaler did not
hide the scheduling ceiling. It still does not prove multi-zone capacity or a
managed metrics service.

## References

- [Kubernetes, kube-controller-manager reference](https://kubernetes.io/docs/reference/command-line-tools-reference/kube-controller-manager/)
- [Amazon EKS, advanced control plane configuration](https://docs.aws.amazon.com/eks/latest/userguide/control-plane-configuration.html)
- [Microsoft Learn, AKS core concepts](https://learn.microsoft.com/azure/aks/core-aks-concepts)
- [Microsoft Learn, KEDA add-on for AKS](https://learn.microsoft.com/azure/aks/keda-about)
- [Microsoft Learn, supported Kubernetes versions in AKS](https://learn.microsoft.com/azure/aks/supported-kubernetes-versions)
- [Karpenter, scheduling](https://karpenter.sh/docs/concepts/scheduling/)
- [Kubernetes Cluster Autoscaler FAQ](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/FAQ.md)
- [KEDA, ScaledObject specification](https://keda.sh/docs/2.17/reference/scaledobject-spec/)
- [KEDA changelog, 2.17.0 fallback behavior](https://github.com/kedacore/keda/blob/main/CHANGELOG.md)
