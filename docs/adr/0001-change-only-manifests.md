# ADR-0001: Change only manifests and policy between runs

- **Status:** accepted
- **Date:** 2026-11-03

## Context

The claim is "these controls heal these faults." That is only as good as the
experimental control. If anything besides manifests differs between the
baseline and hardened runs, a healed row can no longer be attributed to them.

## Decision

Both runs share the cluster, every platform component, the app image and its
code (including the fault toggles and the TLS hot reload), the prober and the
fault injection. A profile switch changes exactly two things:

1. The Argo CD root application's path (`apps/baseline` or `apps/hardened`),
   which selects `gitops/profiles/<profile>`. The diff between those two
   directories is the whole experiment.
2. Falco Talon's response rules (`platform/talon/<profile>.yaml`): baseline
   detects only, hardened quarantines.

The verdict rules in `scenarios/judge.py` enforce the consequence: a hardened
row can only be "healed" if the same run's baseline showed impact.

## Consequences

- Admission control for row A3 is the hardened manifest setting `MAX_INFLIGHT`;
  the shedding code ships in the one image both profiles run, off by default.

- The app hot-reloads its TLS pair in both runs, so row S2 measures who renews
  the certificate, not whether the app can load one.
- The baseline still has a network policy (allow-all) under the same name, so
  row S3 deletes the same object in both runs.

## Addendum: EKS (2026-11-04)

On EKS a profile switch changes the same two things, with the Argo CD path at
`targets/eks/apps/<profile>` (which composes `gitops/profiles/<profile>` with
the EKS overlay in `gitops/targets/eks/`), plus two AWS-side switches that are
policy, not infrastructure:

3. The EventBridge rule that routes GuardDuty runtime findings to the R2
   responder: disabled for the baseline, enabled for the hardened run.
4. The `Automatic` flag of the AWS Config remediation for row S4: off for the
   baseline, on for the hardened run.

Terraform creates both in their hardened state and ignores these two fields
(`lifecycle.ignore_changes`), so the runner owns them during a session and a
later plan does not fight it. Everything else on AWS (the Config rule, the
remediation document, the state machine, FIS, Karpenter, the node monitoring
agent and node repair) exists identically in both runs.

The EKS-only manifests that differ between profiles:

- The Karpenter NodePool's disruption budget (`100%` against `1`). Its template
  is identical, because any template change makes Karpenter treat every node
  as drifted.
- Topology spread for the web Rollout (hardened only). Without it Karpenter
  bin-packs the three replicas onto one node.
- How the S1b database client reads its credential: once from environment
  variables (baseline) or from both mounted secret versions, re-read per
  connection (hardened).

## Addendum: AKS (2026-11-05)

On AKS a profile switch changes the same two things, with the Argo CD path at
`targets/aks/apps/<profile>` (composing `gitops/profiles/<profile>` with the
AKS overlay in `gitops/targets/aks/`), plus two Azure-side switches that are
policy, not infrastructure:

3. The Defender for Cloud workflow automation that sends alerts to the R2
   Logic App: `isEnabled` false for the baseline, true for the hardened run
   (an ARM `PATCH` of that one property, so the Logic App trigger URI, which
   the API never returns, is never resent).
4. The enforcement mode of the S4 policy assignment: `DoNotEnforce` for the
   baseline (it still evaluates and reports, but never deploys), `Default`
   for the hardened run.

Terraform creates both in their hardened state and ignores these two fields,
so the runner owns them during a session. Everything else on Azure (the
policy definition, the Logic App, the queue, the in-cluster worker, the
Defender plan, the autoscaler) exists identically in both runs.

The AKS-only manifests that differ between profiles:

- Baseline: a required pod affinity puts all three web replicas on one node,
  so row A4's impact does not depend on how many nodes exist (on EKS it did,
  and A4 was inconclusive).
- Hardened: a hostname spread (`maxSkew` 1, `minDomains` 3,
  `nodeTaintsPolicy: Honor`) and 30s tolerations for `not-ready` and
  `unreachable` instead of the default 300s.

Shared by both profiles (`gitops/targets/aks/common`, not a control under
test): the secret store is Key Vault through workload identity, the web pods
run on the tainted workload pool, and the prober and partner on the system
pool.
