# ADR 0024: Integrated adversarial proof on one AKS cluster

Status: ACCEPTED 2026-11-11 by Jordan. Design only. Nothing is built or applied, and it
cannot start until both AKS ports (eks-network-segmentation ADR-0006 and
eks-attack-range ADR-0004) have passed their own paid sessions.

## Context

Each capability has been proven alone. The attack simulation and the
segmentation matrix passed on EKS on 2026-11-11 (their AKS ports are designed,
not yet run). Encryption (N1), signed-image admission (P1), autoscaling (KEDA,
A3), runtime detection (Falco, R1), containment (O1), recovery (D1) and
operations passed on AKS 1.36.5 on 2026-11-11 in this repo.

A control that passes alone can still fail beside another one. This repo has
already hit that twice. A shared allow-all policy silently overrode the N1
deny (Cilium policies are additive, ADR-0023 root cause), and diagnostic
watchers that touched the cluster during N1 perturbed the row. An integrated
proof exists to find that class of failure, so its value is in the
interactions, not in repeating the individual rows.

Constraints that bind. The Azure subscription has 10 regional vCPUs and
deprecated DSv3 cannot be raised, so only one cluster can exist at a time. O1,
Q2 and N1 must each run alone because node labels apply only in focused mode.
N1 restarts every Cilium agent, so it cannot overlap anything. Nothing else may
touch the cluster while a demo runs. The Azure D1 store is locked until about
09:30 local on 2026-11-12 and is unrelated to this session's cluster.

## Decision

1. **Home.** This repo, on the AKS cluster module it already owns, since it
   carries O1, P1, D1, N1, Q and the Argo-managed profiles. The attack
   simulation and segmentation lab are brought in as workloads at a pinned
   commit of their own repos. Their runners drive them, and this repo records
   the pinned commits in the evidence.
2. **One narrative, run in phases, not one batch.** A single scenario
   (an attacker foothold in an internet-facing workload) walks through the
   legs in a fixed order. Each phase is a separate run so the rows that must run
   alone still do. Every phase runs twice, baseline then hardened, with the
   same per-row verdicts and thresholds the individual proofs used.
3. **Phase order.** Order is chosen so that disruptive legs come last.
   1. Preflight, plus server version, quota, node labels and a clean policy
      listing (no stray allow-all).
   2. Admission (P1) and attack simulation. Unsigned images are refused, the
      signed attack-range workload runs, and its stages are judged.
   3. Segmentation matrix, then runtime detection and containment (R1, O1)
      against the same foothold.
   4. Autoscaling under load while contained (A3), then recovery (D1) from the
      contained state.
   5. Operations row.
   6. N1 last and alone, because it restarts every Cilium agent.
4. **What counts as integration evidence, beyond each row's own verdict.**
   - Policy interaction check. Before and after each phase, the effective
     Cilium policy per endpoint is captured, and no workload that a deny
     targets may show an allow-all.
   - Control-order checks. Detection must name the attacked pod and containment
     must act on that same pod, so a Falco hit on a different pod does not
     count. Admission must refuse the unsigned image even while the cluster is
     scaling. Recovery must restore from a backup taken before the containment
     action.
   - Collateral checks. Each hardened control is measured for harm to another
     leg, for example whether the segmentation policy breaks the legitimate
     flows the autoscaler and prober depend on.
   - Telemetry honesty. Lost events, missed alerts and platform detector
     misses (Defender) are recorded as findings with the window unchanged.
5. **Differences from the individual proofs are recorded, not hidden.** The
   attack-range ADR picked the managed Cilium dataplane for its own cluster.
   Here the engine is this repo's bring-your-own Cilium, so the attack-range
   metadata control is expressed in that engine and re-verified, with the
   difference stated in the report.
6. **Rules carried forward.** Thresholds do not move to force a pass.
   Misses are kept beside reruns. Synthetic events never count as a detection.
   The operator runs every credentialed apply and destroy from saved plans
   after the cost estimate, one command at a time. The API server is limited to
   the operator's single address. Evidence is redacted of account identifiers
   and addresses and grepped before commit, large captures are truncated with a
   hash and the full file kept outside the repo.
7. **Teardown.** Node pools carry a drain timeout, the pre-destroy step removes
   disruption budgets before destroy begins, and the operator's cluster role
   must outlive the workloads. `verify-gone` must report nothing left except
   the intended locked D1 store, whose later destroy is a separate step.
8. **Cost control.** One session, and a phase that fails for a harness reason
   is rerun alone, not the whole sequence. A hard stop after a stated number of
   unplanned reruns is set in the plan before apply.

## Alternatives rejected

| Option | Why not |
| ------ | ------- |
| Run every leg in one batched run | Several rows require running alone (O1, Q2, N1), and a batch hides which control caused a failure. |
| Separate clusters per leg | Already done, and it cannot find interaction failures. Quota also allows one cluster. |
| A new standalone repo for the integrated proof | The legs, profiles and runner already live here. A new repo would duplicate them. |
| Re-prove each row only | Repeats existing evidence and misses the additive-policy class of failure that has already occurred. |

## Consequences

This is the longest paid session in the effort, so its plan needs a firm
budget and the phase-level rerun rule. The attack and segmentation workloads
must be runnable as pinned, self-contained packages from this repo, which is
work the ports must leave ready. Any honest miss (Defender, audit visibility,
lost Hubble events) will appear in the final report and in the Phase 5 docs
pass, so those write-ups must be written to hold up with the misses in them.

Preconditions to start.
- AKS port sessions for the segmentation lab and the attack range both pass,
  with evidence committed and clusters destroyed and verified gone.
- This ADR is accepted, and a follow-up contract ADR (or an addendum here)
  fixes the exact cross-leg thresholds before any code, using values taken from
  the individual runs and not from this one.

Open items.
- Exact policy-interaction assertion format, and where the per-endpoint policy
  capture runs without perturbing a row.
- Whether containment (O1) and the attack range's own stages need a joint
  fixture or can share the foothold as designed.
- Session budget and the unplanned rerun cap.
