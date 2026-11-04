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
