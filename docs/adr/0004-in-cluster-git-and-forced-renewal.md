# ADR-0004: In-cluster Git server, and how the lab reaches the renewal window

- **Status:** accepted
- **Date:** 2026-11-03

## Context

Phase 1 is local only (no GitHub), and two rows need time to pass faster than
it does.

## Decision

- **Git:** a `git daemon` pod serves `gitops/` to Argo CD, re-seeded at the
  start of every run. A bad release (row A2) is a real commit pushed to it, so
  Argo CD and Argo Rollouts see exactly what a CI release would produce.
- **SLO windows:** the SRE Workbook windows divided by 12 (slo/windows). Burn
  rate thresholds are unchanged.
- **Certificate renewal (row S2):** the hardened certificate lives 1h and renews
  at 40 minutes. Instead of waiting, the runner sets the Certificate's
  `Issuing` condition, which is what cert-manager's own trigger controller does
  at `renewalTime` (and what `cmctl renew` does). The baseline has no renewer,
  so its hand-made certificate is issued with 90 seconds left and simply
  reaches expiry.

## Consequences

The S2 baseline and hardened runs inject the same event, "the certificate
enters its renewal window," by the only means each profile has.
