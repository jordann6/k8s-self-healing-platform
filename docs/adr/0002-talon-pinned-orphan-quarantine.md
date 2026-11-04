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
