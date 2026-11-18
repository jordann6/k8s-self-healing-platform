# ADR-0002: Pinned Falco Talon, quarantine by relabel, never kill first

- **Status:** accepted
- **Date:** 2026-11-03

## Context

The EKS incident response best practice is isolate, preserve, investigate,
eradicate. Deleting the pod first destroys the evidence. Falco Talon is the
in-cluster responder, and it is alpha.

## Decision

- Pin Talon (chart 0.5.0, app 0.3.0). The label actionner in this release is
  `kubernetes:label`; older docs and the original plan say
  `kubernetes:labelize`, which is the pre-0.1 name.
- The response is one label patch: `quarantine=true` plus `lab/pool=quarantine`.
  The pool label takes the pod out of the Service and the ReplicaSet selector,
  so it stops serving and a fresh replica restores capacity. The pod is orphaned,
  not deleted.
- A pre-deployed CiliumNetworkPolicy selects `quarantine=true` and denies all
  ingress from pods and the world and all egress, DNS included. The host stays
  allowed in, so kubelet probes pass and the container is not restarted (a
  restart would kill the attacker's process).
- The runner captures the pod spec, logs, process list, sockets, the Falco event
  and Hubble drop flows, and only then deletes the pod.
- Fallback if Talon breaks: a small Falcosidekick webhook responder doing the
  same label patch.

## Consequences

- Talon's `kubernetes:networkpolicy` actionner is not used: the policy is
  reviewed in Git ahead of time instead of generated at incident time.
- Cilium enforces the label change through an identity update, so isolation is
  only as fast as the agent's policy regeneration. The runner proves it with
  Hubble policy-drop flows, not just failed curls.

## Addendum: the quarantine identity is pre-allocated (phase 1 finding)

Cilium enforces a relabel by moving the pod to the security identity of its
new label set. When that identity does not exist yet, the agent allocates it
through the API server (CRD identity mode). On the single kind node under load
this took 16s, 22s and 38s across runs 1, 3 and 4, with "Key allocation attempt
failed" retries in the agent log, while Talon had labelled the pod within 2s.
That measured the API server, not the response chain.

The runner now starts a standby pod with exactly the quarantined label set
before the attack (both profiles), so the identity exists and isolation costs
only the endpoint regeneration. The 30s threshold is unchanged. Production
equivalent: a kvstore-backed identity allocator, or a quarantine label set that
is pre-provisioned, so incident response never waits on identity creation.

## Addendum 2026-11-18, R1's isolation time matches the Cilium identity jitter

ADR-0026's Cilium investigation found that Cilium v1.20.4 delays applying a pod label change to the endpoint
identity by `identity-change-grace-period` (default 5 s) plus a random jitter of up to `identity-max-jitter`
(default 30 s). Pre-allocating the quarantine identity, the remedy above, did not remove that wait, the identity
already existed in every later trial of that investigation and the delay stayed.

The `isolated_s` values for R1 hardened across the committed evidence are 8.4 (aks-run1-rows), 16.1, 18.7, 34.6
(eks-run2-a4r1, eks-run2, eks-run2-rows), 11.0 (eks-run3), and on kind 22.5, 43.2, 15.6, 38.2, 22.3, 22.1 and 24.9
(kind-run1 to run7). That spread, 8 to 43 s, is the shape the default settings predict (5 s plus a uniform jitter
up to 30 s, plus detection), and three of the runs are over the 30 s bound. The reference runs (kind-run5 and
run7) were within it. No bound and no verdict is changed here. R1's rows are not wrong, but "the pre-allocated
identity makes isolation cost only the endpoint regeneration" is not supported, and a pass on a single run is
partly luck against the jitter. Whether to set the two Cilium options, or to say so on the R1 claim, is the open
decision recorded in ADR-0026's 2026-11-18 addendum.
