# ADR 0021: P1 cloud parity contract

Status: ACCEPTED 2026-11-10 by Jordan, before the first P1 cloud experiment.
Source is built (unit tests and gates green); nothing is applied to any cloud yet.

## Context

ADR-0013 fixed the P1 contract on kind: Kyverno 1.19.2 `ImageValidatingPolicy`,
Cosign 2.6.3 with a local ECDSA key pair, one artifact digest copied into five
repositories, fail-closed admission, and an outage proven by stopping a local
registry container. ADR-0018 decided the cloud design in outline (ECR and ACR,
per-policy credentials, an egress allow-list as the outage, no insecure
registry) and ADR-0019 and ADR-0020 ported O1 and Q3. This ADR reads the kind
implementation against the cloud modules and vendor documentation (read
2026-11-10), lists what does not survive the move, and fixes the cloud contract
before any code. No threshold below comes from a run.

## Facts that change the kind contract

1. Registry credentials. Kyverno documents `spec.credentials` on each policy as
   "the only way to configure registry authentication for IVPOL". Its
   `providers` field enables ambient cloud credential helpers, and `secrets`
   takes dockerconfigjson Secrets in the Kyverno namespace. Pod
   `imagePullSecrets` and the global credential-helper flag are not used.
2. Amazon provider. The documented path is IRSA: the admission controller's
   service account needs `ecr:GetAuthorizationToken` and `ecr:BatchGetImage`,
   and `AWS_ROLE_ARN` and `AWS_WEB_IDENTITY_TOKEN_FILE` are injected into the
   pod. EKS Pod Identity injects container credentials, not those variables, and
   the Kyverno page does not mention it. This lab's own workloads use Pod
   Identity ("No IRSA" in `infra/aws/eks.tf`), but the EKS module leaves
   `enable_irsa` at its default of true, so the OIDC provider already exists and
   IRSA needs an IAM role and no new infrastructure.
3. Azure provider. The page lists Azure Container Registry with "Workload
   Identity / managed identity" and gives no setup steps. The AKS module already
   enables the OIDC issuer and workload identity and federates one identity per
   service account (`infra/azure/identity.tf`).
4. Which controllers verify images is not documented. The kind values pass
   `allowInsecureRegistry` to both the admission and the reports controller, and
   P1's hardened verdict needs a positive PolicyReport from background
   evaluation. So the reports controller reads the registry too, and the
   admission controller alone is not enough. This is live check L6.
5. ECR tags here are immutable (`aws_ecr_repository.lab`). Two kind fixtures
   break on that. Cosign writes the signature to the tag
   `sha256-<digest>.sig`, so a second signing of the same digest rewrites an
   existing tag and ECR refuses it. `p1-images.sh` also tampers a signature
   manifest and writes it back to the same `.sig` tag it just copied. ACR tags
   are mutable, but both clouds use one flow. The ECR repositories are destroyed
   with the lab, so every session starts empty and a rerun inside a session is
   the only collision.
6. The kind fixtures talk to a local plain-HTTP registry on localhost with no
   authentication (`p1_registry.py`, `registry_ready`, `docker stop`). ECR and
   ACR are TLS and need tokens.
7. The outage cannot be a stopped registry. A managed registry cannot be
   stopped, and revoking the identity's access would measure IAM and Entra
   propagation (ADR-0007 row 13), not the verifier. Cilium enforces Kubernetes
   and Cilium network policy on both clouds, and its deny rules cannot use
   `toFQDNs`, so the outage is an egress allow-list on the Kyverno admission
   controller. Kind runs the default CNI, so egress policy is new territory for
   this row.
8. Kubernetes 1.36. Kyverno publishes a Kubernetes range only for 1.20
   (1.34 to 1.37), which is still a release candidate. For 1.19 the Releases page
   lists none. This repo's own kind P1 evidence ran Kyverno 1.19.2 on
   Kubernetes v1.37.0 and passed, so the pin stays, and Kyverno on 1.36 becomes
   live check L16.
9. The signing keys and passwords live outside Git (`~/.config/k8s-self-healing-platform/p1`),
   and only the trusted public key is injected as a ConfigMap in the Kyverno
   namespace. That mechanism carries over unchanged.
10. Kind-only couplings in the runner: `p1_only()` is kind-only, the policy glob
    `selfheal-p1-registry:5000/p1/*`, `.p1/images.json` and `.p1/signatures.json`
    are single-target files, and `FOCUSED_P1_PRECONDITIONS` has no Hubble
    dependency. The cloud platform scripts install no Kyverno.

## Decision

1. Contract. The ADR-0013 sequence, cases, profiles and every observation
   threshold stay as written. One artifact digest is copied into five
   repositories per cloud, so every case exercises the same bytes.
2. Registries. EKS uses five encrypted ECR repositories, `selfheal/p1-trusted`,
   `p1-unsigned`, `p1-invalid`, `p1-untrusted` and `p1-recovery`. AKS uses the
   existing ACR with `selfheal/p1/<case>`, which needs no new resource. The
   artifact is the same `app/p1` build, pushed by the P1 image step.
3. Policy match. The cloud overlays patch the hardened policy to the cloud
   registry host pattern, `*.dkr.ecr.*.amazonaws.com/selfheal/p1-*` on EKS and
   `*.azurecr.io/selfheal/p1/*` on AKS, so no account or registry name is
   committed. `failurePolicy: Fail`, `verifyDigest`, `required` and
   `mutateDigest: false` are unchanged. The trust root is still the injected
   ConfigMap.
4. Credentials per policy.
   - EKS. `spec.credentials.providers: [amazon]`. One IAM role trusts the OIDC
     provider for exactly two service accounts, the Kyverno admission controller
     and the reports controller, and allows `ecr:GetAuthorizationToken` plus
     `BatchGetImage`, `GetDownloadUrlForLayer` and `BatchCheckLayerAvailability`
     scoped to the five P1 repositories. The role ARN is passed to the Kyverno
     install from a Terraform output, not committed. This is the one place the
     lab uses IRSA, because it is the only documented Kyverno path, and it is
     stated in the runbook. Whether Pod Identity also works is live check L6b,
     not a dependency.
   - AKS. `spec.credentials.providers: [azure]`. Two user-assigned identities
     (admission and reports) follow the existing one-identity-per-service-account
     pattern, each federated to its service account, each holding `AcrPull`
     scoped to the registry, assigned directly and in Terraform long before the
     row. The chart gets the client ids, the `azure.workload.identity/use` pod
     label and the service account annotation.
   - `allowInsecureRegistry` is off on both clouds. The cloud Kyverno values
     live in a new `platform/kyverno-cloud.yaml`, and the kind values stay as
     they are. Verification caching stays off so the outage cannot be masked.
5. Install. Both platform scripts install the pinned Kyverno chart (3.9.2, app
   1.19.2) with the cloud values and the identity settings.
6. Fixture preparation. `p1-images.sh` takes the target. It builds once, pushes
   the digest to the five repositories, signs with Cosign 2.6.3 using a
   temporary Docker config in a 0700 directory so the registry token never
   appears on a command line, and skips signing when the `.sig` tag already
   exists. The invalid fixture is built once: copy the trusted signature
   manifest to a temporary tag in the invalid repository, then write the
   tampered manifest to the `.sig` tag, which is new there. The signature-state
   preflight (`signatures.json`) is unchanged and fails the run before any
   cluster call if a state is not what P1 needs. Kind keeps its fixture files in
   `.p1/` and each cloud writes `.p1/<target>/`, so the three never overwrite each
   other.
7. Outage is a CiliumNetworkPolicy on the Kyverno admission controller that
   allows egress to the API server entity and to cluster DNS only. Applying it is
   the outage and deleting it is the restoration, so the recovery clock keeps
   its meaning (restoration to a Ready recovery pod, 60s).
   - The runner waits a fixed 10s for enforcement and records it.
   - Attribution. The outage case needs Hubble policy drops from the admission
     controller pod to a registry address inside the window. If the request is
     denied the drops attribute it to the policy. If the request is admitted and
     no drop was observed, the policy was not enforced (a pooled connection, or
     a slow rollout) and the row is an error, never a miss. If it is admitted
     and drops exist, that is a miss.
   - The policy is removed in a `finally`, as the kind runner restarts the
     registry in a `finally`.
8. Runner. P1 is selectable on EKS and AKS as a focused row. It reads
   `THRESHOLDS[target]["P1"]`, `.p1/<target>/`, the target registry check
   (none, the outage is the policy), and adds the Hubble relay to the
   preconditions on the clouds. The cloud bootstrap scripts inject the trusted
   public key as the `p1-cosign-trust-root` ConfigMap.
9. Versions. Session plans pass 1.36 as in ADR-0019 Decision 7. Kyverno stays
   1.19.2 (L16).

## Amendment after review, 2026-11-10

Found by rereading the code, no run motivated it, and no threshold changed.

1. Only Hubble drops to TCP 443 attribute the outage. The runner recorded every policy drop from
   the admission controller pod, so a drop to an unrelated port could have counted as proof the
   registry was cut off. It now counts TCP 443 drops (ECR, ACR and the token exchange) for the
   verdict and records the total beside it as `all_policy_drops`.
2. A network-policy outage drops packets without answering, unlike the stopped registry on kind
   that refuses at once. The verifier can then hang until the 15s webhook timeout, and with
   `failurePolicy: Fail` the API server reports a failed webhook call that names neither the
   policy nor the digest. For the verifier-outage case only, on a cloud only, a reason that names
   a failed webhook call with a deadline or timeout is now an attributable denial. The judge has
   already required Hubble drops by then, so the denial is still tied to the policy. Every other
   negative case still needs the policy or digest wording, and kind is unchanged. The 20s request
   bar still applies, and the 15s webhook timeout leaves room under it.
3. The cloud globs are `*.dkr.ecr.*.amazonaws.com/selfheal/p1-*` and `*.azurecr.io/selfheal/p1/*`.
   The Kyverno docs do not say whether `*` crosses `.` or `/`. These patterns never need it to
   cross either, each `*` stands for one dot-free, slash-free segment, so the question does not
   arise. Keep it that way if the layout changes.

Added live check L24 below.

## Thresholds

Identical to ADR-0013 on kind, EKS and AKS.

| Observation | kind | EKS and AKS |
|---|---:|---:|
| Admission request completes | 20s | 20s |
| Valid signed Pod Ready | 120s | 120s |
| Verifier recovers after restoration | 60s | 60s |
| Trusted Pods admitted, runtime digest equals signed digest | 1 | 1 |
| Unsigned, invalid, untrusted, tag-only and outage requests | denied with a reason | same |
| Image-policy pass decisions | at least 2 | at least 2 |
| Image-policy fail or error decisions | at least 5 | at least 5 |
| Positive PolicyReport for the running trusted Pod within 60s | yes | yes |

The first trusted request on a cloud includes a token exchange and registry
round trips that kind never made. It stays under the same 20s bar, and a miss
would be recorded, not relaxed. Missing readiness, reachability, digest, metrics,
reasons or reports stay errors, and the outage attribution rule in Decision 7
adds one more.

## Verification plan

- Unit tests. Signature-state preflight, fixture idempotency (the `.sig` tag
  check), the cloud policy patch, the outage attribution rule (admitted with no
  drops is an error, admitted with drops is a miss, denied with drops is the
  pass), the per-target fixture paths and the thresholds. `make check` and
  `make test` stay green.
- Overlays render for both clouds and the hardened policy contains the cloud
  glob and credentials provider, no insecure-registry setting and no kind
  registry name.
- Terraform validate, tflint and Checkov pass for the new ECR repositories, the
  IAM role and the AKS identities.
- Local proof stays kind. The paid proof waits for the batched session
  (ADR-0018 Decision D).

## Live checks

| # | Check |
|---|---|
| L6 | The Kyverno `amazon` provider with IRSA verifies in both the admission and the reports controllers (positive PolicyReport appears) |
| L6b | Whether Pod Identity also works for the `amazon` provider (information only) |
| L7 | The Kyverno `azure` provider with workload identity against ACR, in both controllers |
| L8 | Cosign 2.6.3 legacy signature tags in ECR and ACR are found by Kyverno 1.19.2, and ECR accepts the signature manifest and the tampered `.sig` write |
| L16 | Kyverno 1.19.2 reaches Ready on Kubernetes 1.36 on both clouds |
| L17 | The egress allow-list produces attributable Hubble drops and the first outage request is denied on both clouds, and deletion restores admission inside 60s |
| L24 | The text the API server returns for the outage denial on each cloud (a policy-named error or a webhook timeout), and that it arrives inside 20s |

## Cost and session boundary

No hourly resource. Five ECR repositories, one IAM role and two AKS managed
identities, all free to hold, plus Kyverno's requests on the system pool
(admission 100m, background and reports controllers). The AKS system pool is
sized at about 67 percent of requests, so the first AKS session checks free
CPU before the install. The ECR repositories are destroyed with the lab. Never
push before Phase 5.

## References

- [Kyverno ImageValidatingPolicy](https://kyverno.io/docs/policy-types/image-validating-policy/) (read 2026-11-10)
- [Kyverno releases](https://kyverno.io/docs/installation/releases/)
- [Cilium deny policies](https://docs.cilium.io/en/latest/security/policy/deny/)
- ADR-0007, ADR-0013, ADR-0018, ADR-0019, ADR-0020
