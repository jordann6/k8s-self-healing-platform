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

- The app hot-reloads its TLS pair in both runs, so row S2 measures who renews
  the certificate, not whether the app can load one.
- The baseline still has a network policy (allow-all) under the same name, so
  row S3 deletes the same object in both runs.
