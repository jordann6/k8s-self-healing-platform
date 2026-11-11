# ADR 0013: P1 signed-image admission contract

Status: accepted before the first P1 live experiment on 2026-11-09.

## Context

P1 must prove an admission decision against an OCI signature, not merely the
presence of a policy controller. The proof must admit a trusted artifact,
reject unsigned, invalid, untrusted, and malformed inputs, record the exact
reasons, verify the running digest, and fail when verifier telemetry is absent.

The existing secure-software-factory uses keyless GitHub Actions signatures.
That is good cloud prior art, but a local kind proof must not depend on a hosted
identity provider, public transparency service, or paid registry.

Primary documentation reviewed on 2026-11-09:

- Kyverno 1.19 release and migration guidance for the CEL based policy types
- Kyverno ImageValidatingPolicy key verification, digest verification, metrics,
  policy reports, fail-closed behavior, and insecure-registry test exception
- Sigstore Cosign self-managed keys, registry signing, and verification claims
- kind local-registry guidance
- CNCF Distribution 3.1.2 release information

Kyverno 1.19.2 is the current stable security release. Kyverno 1.20 is still a
release candidate. Kyverno 1.19.2 also has a recorded regression when reading
Cosign 3 default OCI referrer bundles. Cosign 2.6.3, the latest patched v2
release, is therefore pinned for its legacy signature layout until a stable
Kyverno release fixes the referrer path. This compatibility limitation is
tested rather than hidden.

## Decision

Use Kyverno 1.19.2 chart 3.9.2 and its CEL based
`ImageValidatingPolicy`. Use Cosign 2.6.3 with a local ECDSA key pair and
Distribution Registry 3.1.2. Use one built application digest copied into
separate registry repositories so every positive and negative case exercises
the same deterministic artifact bytes.

The trusted and untrusted encrypted private keys, plus their passwords, live
under the user configuration directory outside Git and evidence. Only the
trusted public key is injected at runtime into a ConfigMap in the Kyverno
namespace. The policy reads that ConfigMap as its trust root.

The policy applies only to Pods in the labelled `selfheal-p1` namespace and
only to `selfheal-p1-registry:5000/p1/*`. It uses `failurePolicy: Fail`, requires
validation, and verifies digest references. Background evaluation runs every
10 seconds so the admitted trusted Pod must produce a current-state positive
PolicyReport within the fixed 60-second telemetry window. Kyverno's
verified-image cache is disabled so a stopped registry cannot be masked by a
previous successful decision.

The baseline has the same registry, artifact, Kyverno deployment, namespace,
and admission requests, but no P1 ImageValidatingPolicy. The hardened profile
adds only the policy.

## Fixed thresholds and verdict contract

These thresholds were fixed before the first live experiment:

- Each API admission request completes within 20 seconds. The policy webhook
  timeout remains 15 seconds, leaving five seconds for API client overhead.
- A valid signed Pod becomes Ready within 120 seconds.
- Verifier service recovers within 60 seconds after the registry is restored.
- Exactly one trusted signed Pod is admitted and its runtime image digest equals
  the signed expected digest.
- Unsigned, invalid-signature, untrusted-signer, tag-only, and verifier-outage
  requests produce no Pod.
- Every negative request returns a nonzero API result and a nonempty exact
  admission reason naming the P1 policy or digest requirement.
- Hardened telemetry contains at least one image-policy pass and four failed or
  error decisions for the P1 policy, plus a positive PolicyReport for the
  running trusted Pod.
- Policy readiness, registry reachability, expected digest, admission metrics,
  rejection reasons, or PolicyReport evidence missing from the hardened run is
  an error, never a pass.

Baseline impact requires unsigned, invalid-signature, untrusted-signer, and
tag-only Pods to be admitted. Hardened healing requires the valid image and
recovery image to run while every negative case is denied and all telemetry
requirements are met.

## Limitations

- The trust root is a local public key, not workload identity, Fulcio, Rekor,
  hardware-backed KMS, or organizational PKI.
- There is no revocation service. Rotation means replacing the runtime public
  key and re-signing trusted artifacts.
- The registry is unauthenticated HTTP on a local Docker network. Kyverno's
  insecure-registry option is enabled only for this zero-cost lab.
- Transparency-log and signed-certificate-timestamp checks are disabled because
  the proof is offline and key based.
- Registry availability remains a dependency. The fail-closed outage case proves
  denial and recovery, not highly available registry design.
- A digest and signature prove artifact identity and signer authorization. They
  do not prove vulnerability status, provenance quality, or safe application
  behavior. Those require separate attestations and policies.
