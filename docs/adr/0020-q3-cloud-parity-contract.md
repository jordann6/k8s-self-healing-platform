# ADR 0020: Q3 cloud parity contract

Status: ACCEPTED 2026-11-10 by Jordan, before the first Q3 cloud experiment.
Source is built (unit tests and `make check` green); nothing is applied to any cloud yet.

## Context

ADR-0011 fixed the Q3 contract on kind. ADR-0018 decided that the remaining
cloud rows run on Kubernetes 1.36 (Decision A) and that one session per cloud
runs the rows in the order O1, Q3, P1, D1, then N1 (Decision D). ADR-0019 ported
O1. This ADR lists what in the kind Q3 implementation does not survive the move
to EKS and AKS, checks each point against vendor documentation (read
2026-11-10), and fixes the cloud contract before any code. No threshold below
comes from a run.

## Facts that change the kind contract

1. The recommender reads usage once a minute everywhere. The upstream VPA flags
   list `--recommender-interval` with a default of 1m0s ("How often metrics
   should be fetched"). Kind, EKS and AKS all run the pinned chart with that
   default, so the recommender sees at most one new sample per minute on every
   target. The 240s recommendation clock (ADR-0011) already sits on top of
   that, so it does not change.
2. The runner's own sample count is the part that breaks on AKS. ADR-0011
   requires at least 6 distinct Metrics API samples, and `q3_row.collect` stops
   after 240s (hardened) or 120s (baseline). Kind and EKS run metrics-server at
   `--metric-resolution=15s` (`platform/metrics-server.yaml`), so 6 distinct
   samples fit in 90s. Microsoft documents that the AKS Metrics API retrieves
   data from the kubelet every 60 seconds, and the managed metrics-server runs
   two pods, so at best one new timestamp per minute is guaranteed. Six
   distinct samples then need five full intervals after the first sample plus
   up to one interval of alignment, so a 240s or 120s window cannot hold them.
   Left alone the AKS row would end in a telemetry error every run.
3. AKS already has a "VPA" for the metrics-server, but it is not the VPA
   custom resource. The Microsoft page shows it as a `metrics-server-vpa`
   container running the pod nanny (`pod_nanny --deployment=metrics-server`)
   fed by a `metrics-server-config` ConfigMap. It creates no
   `VerticalPodAutoscaler` object, so it does not touch Q3's object.
4. The AKS managed VPA add-on is wrong for this row. Microsoft documents that
   it installs the recommender, updater and admission controller and that
   running your own implementation of VPA alongside it is unsupported (an extra
   recommender is supported). ADR-0011 requires that no component able to change
   workload resources exists, which the add-on cannot satisfy. The module sets
   no `vertical_pod_autoscaler` block, so the add-on is off.
5. Whether the VerticalPodAutoscaler CRD already exists on an AKS Standard
   cluster with the add-on off is not stated in the docs read. Helm fails with
   an ownership error if the CRD exists and is not Helm-owned, so a collision
   is loud, not silent. This is live check L14.
6. Kind-only couplings in the repo. The Q3 manifests use `selfheal-q3:v1`
   loaded by `kind load`. `q3_row.run` reads `THRESHOLDS["kind"]["Q3"]`.
   `q3_only()` is true only for kind, `ROWS_BY_TARGET` has no Q3 for eks or aks,
   `apply_q3_profile` patches `q3/{profile}`, the cloud root apps hold no Q3
   application, and the cloud platform scripts install metrics-server but no
   VPA. The Makefile `q3-platform` target is the only place the chart is
   installed.
7. System pool headroom. The Q3 workload has no toleration, so it and the
   recommender land on the system pool. The AKS system pool is sized at about
   67 percent of requests (`infra/azure/variables.tf`), and Q3 adds 100m CPU of
   requests in total (recommender 50m, target 50m). That fits.
8. Versions. The VPA compatibility table lists app 1.8.x for Kubernetes 1.36 to
   1.38, so the existing 0.16.0 chart pin (app 1.8.1) serves kind, EKS and AKS on
   1.36 (ADR-0018 Decision A). EKS on 1.36 stays live check L1.

## Decision

1. Contract and thresholds. The ADR-0011 workload, VPA object, profiles and every
   observation threshold stay as written on both clouds. Nothing is relaxed:
   recommendation within 240s, at least 6 samples, CPU p90 at least 100m, memory
   p90 at least 64Mi, ratios 0.70 to 2.00 and 0.90 to 2.00, zero restarts.
2. Install. The upstream chart `autoscalers/vertical-pod-autoscaler` at the
   existing `VPA_VERSION`, release `vpa` in namespace `vpa`, with
   `platform/vpa.yaml` unchanged (recommender only, updater and admission
   controller disabled). Both `eks-platform.sh` and `aks-platform.sh` install
   it right after the metrics source. The AKS add-on stays off. On AKS the
   platform script keeps using the managed metrics-server and installs no
   second one.
3. Derived sampling window. The sample-count requirement is a quality gate, not
   a performance clock, so the runner separates the two.
   - The recommendation clock stays `recommend_s = 240`, measured from the first
     Metrics API sample to the first recommendation, and is judged on its own.
   - A new threshold `sample_window_s` bounds how long the runner collects
     distinct samples. A new `baseline_window_s` does the same for baseline.
     Derived as `(min_samples + 1) x metrics resolution`:

     | Target | Resolution | `baseline_window_s` | `sample_window_s` |
     |---|---:|---:|---:|
     | kind | 15s | 120 | 240 |
     | EKS | 15s | 120 | 240 |
     | AKS | 60s | 420 | 420 |

     Kind and EKS keep the windows the runner uses today. AKS gets
     `(6 + 1) x 60 = 420s`. The hardened collection ends at the later of the
     two clocks, but a recommendation first seen after 240s is still a miss.
   - Fewer than 6 distinct samples inside the derived window is still an error.
     The window is never extended after a run to rescue a count.
4. Evidence for the open questions. The observation records the target node, the
   median spacing between distinct Metrics API timestamps, the metrics-server
   replica count and its `--metric-resolution` argument. These settle L2 from
   the run itself, with no extra probe.
5. Delivery. `eks-images.sh` and `aks-images.sh` push `q3`, the generated
   component gains a `selfheal-q3` entry, and ECR gains one `q3` repository
   (ACR needs nothing new). New overlays `gitops/targets/<cloud>/q3/{baseline,hardened}`
   wrap the kind profile and add the generated component. Each cloud's
   `apps/{baseline,hardened}/q3.yaml` points at its overlay, and
   `apply_q3_profile()` patches that path, as for Q1, Q2 and O1.
6. Runner. Q3 is selectable on EKS and AKS as a focused row, reads the target's
   thresholds, and uses the same preconditions (metrics-server and the VPA
   recommender Ready).
7. Placement. The workload is not pinned. Nothing in Q3 depends on a warm cache
   or a specific node because the pod must not restart. The node name is
   recorded.
8. Versions. Session plans pass 1.36 (`cluster_version` on EKS,
   `kubernetes_version` on AKS) as in ADR-0019 Decision 7.

## Thresholds

| Observation | kind | EKS | AKS |
|---|---:|---:|---:|
| Recommendation available | 240s | 240s | 240s |
| Distinct resource samples | at least 6 | at least 6 | at least 6 |
| Sample collection window, hardened | 240s | 240s | 420s |
| Sample collection window, baseline | 120s | 120s | 420s |
| Measured CPU p90 | at least 100m | same | same |
| Measured memory p90 | at least 64Mi | same | same |
| CPU target divided by measured p90 | 0.70 to 2.00 | same | same |
| Memory target divided by measured p90 | 0.90 to 2.00 | same | same |
| Pod restarts | 0 | 0 | 0 |

Missing telemetry stays an error. A present measurement that violates the
contract stays a miss. Recommendation-only mode is still not described as an
applied resize.

## Verification plan

- Unit tests. The cloud thresholds exist and equal kind except the AKS windows,
  the collection loop honours the per-target window, the recommendation clock
  is judged separately from the window, and the sample spacing helper. `make
  check` and `make test` stay green.
- Overlays render with `kustomize build` for both clouds and both profiles, and
  the generated component rewrites the image.
- Local proof stays kind. The paid proof waits for the batched session
  (ADR-0018 Decision D).

## Live checks

| # | Check |
|---|---|
| L2 | Distinct Metrics API samples per minute on AKS (two metrics-server pods) and the time to the first VPA recommendation. Recorded by the run. |
| L14 | The `vpa` chart installs on AKS with the add-on off, with no CRD ownership conflict |
| L15 | EKS holds 6 distinct samples inside the 120s baseline window at 15s resolution on 1.36 |

## Cost and session boundary

Adds no hourly resource. One encrypted ECR repository, image storage, and about
100m CPU of requests on the system pool. The proof reuses the session's bounded
minutes. Never push before Phase 5.

## References

- [VPA recommender flags](https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/flags.md) (read 2026-11-10)
- [VPA installation and compatibility](https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/installation.md)
- [Microsoft Learn, AKS vertical pod autoscaling](https://learn.microsoft.com/en-us/azure/aks/vertical-pod-autoscaler)
- [Microsoft Learn, AKS Metrics Server VPA](https://learn.microsoft.com/en-us/azure/aks/use-metrics-server-vertical-pod-autoscaler)
- [Microsoft Learn, AKS scaling concepts](https://learn.microsoft.com/azure/aks/concepts-scale)
- ADR-0011, ADR-0017, ADR-0018, ADR-0019
