# Runbook: the GKE session

One bounded session: plan, apply, platform, demo, teardown, verify. The
operator runs every apply and destroy from a saved plan they have read. The
design is ADR-0027; the thresholds are ADR-0003 "GKE thresholds".

GKE runs the in-cluster rows only: A1, A2, A3, A4, A5, S2, S3, R1. The
GCP-native responses (S1 source rotation, S4 firewall revert, R2 Pub/Sub
response) and the native SLO burn rate are proven in the GCP landing zone
repository, `docs/completion.md`, "Proof, 2026-11-18".

## 0. Before the session (once)

1. The landing zone teardown has finished: no `jn-lz-*` project is ACTIVE
   except the seed (`make gke-preflight` checks this and fails closed).
2. Billing cap. Five projects were linked to the billing account on
   2026-11-18. A deleted project unlinks from billing at once but counts
   toward the project quota for 30 days. `make gke-project-plan` prints the
   current link count before it plans.
3. Fill `infra/gcp-project/lab.auto.tfvars` from the example, then:

   ```sh
   make gke-project-plan
   terraform -chdir=infra/gcp-project apply infra/gcp-project/tfplan
   terraform -chdir=infra/gcp-project output -raw project_id   # into infra/gcp/lab.auto.tfvars
   ```

   The project is kept between sessions (D1). `make gke-project-park` plans
   unlinking its billing after the last session.
4. Tools: gcloud (signed in as the operator), terraform, helm, cilium CLI,
   kubectl, docker with buildx, jq, infracost.

## 1. Preflight and infrastructure

```sh
make gke-cost          # Infracost on the saved plan's resources
make gke-plan          # runs gke-preflight first; writes infra/gcp/tfplan
terraform -chdir=infra/gcp apply infra/gcp/tfplan
```

Preflight fails closed on: project not ACTIVE or billing off, a landing zone
project still ACTIVE, org policies beyond Google's six defaults, a custom
constraint, a lien, under 16 free regional CPUs, the REGULAR channel not
offering the pinned minor, or any Eventarc trigger, push subscription, Cloud
Run service or Cloud Function in the lab project. It writes the
before-picture verify-gone compares with.

Billing starts at the apply: about $0.52 per hour (Infracost, $450 per month
list with the $73 cluster fee the zonal free tier credit covers), plus NAT
and logging usage.

## 2. Platform, images, bootstrap (right after the apply)

```sh
make gke-up            # gke-images, gke-platform, gke-bootstrap
export KUBECONFIG=$PWD/.kube/gke
```

### Live checks before the demo (the docs do not settle these)

| Check | How | Pass |
|---|---|---|
| L1 BYO Cilium | `make gke-platform` output | every node Ready, the agent-not-ready taint lifted, readiness gate passes |
| L2 quarantine policy | row R1 hardened | pod isolated, kubelet probes still pass, Hubble shows the drops |
| L3 Falco on COS | `make gke-platform` output | modern eBPF probe on every node |
| L4 stopped VM | row A4 trace and report | NotReady time, shutdown taint, whether the MIG restarts it, any repair operation |
| L5 port-forwards | the demo log | no forward lost over the run |
| L6 HPA input | `make gke-platform` output | recorded, not judged |
| L7 CPU quota | preflight | 16 free |
| L8 NAT egress | `make gke-platform` (charts pull) | every chart image pulled |
| L9 system pool fit | `kubectl top nodes`, no Pending pod | system nodes under 80% requested |
| L10 teardown | `make gke-verify-gone` | nothing LEFT |

## 3. Experiment

One run command at a time; keep the laptop awake with the lid open.

```sh
make demo TARGET=gke RUN_ID=gke-run1
```

Read `evidence/gke-run1/report.md`. Redact before committing:

```sh
python3 scripts/redact-evidence.py --cloud gcp evidence/gke-run1
```

Add a `.gitignore` whitelist line for the run before `git add`.

## 4. Teardown (three steps, in order)

```sh
make gke-predestroy
make gke-destroy-plan
terraform -chdir=infra/gcp apply infra/gcp/destroy.tfplan
make gke-verify-gone
```

Every line of verify-gone must say `gone`. The project itself stays (D1).
