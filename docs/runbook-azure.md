# Runbook: one bounded live AKS session

Goal: run every AKS row (A1 to A5, S1 to S4, R1, R2) against real AKS,
baseline then hardened, keep the evidence, and leave nothing running. Facts
behind every step: ADR-0007; design: ADR-0008; thresholds: ADR-0003.

The `terraform apply` steps are run by the operator from saved plans, with
absolute paths, so a human reads every plan before it executes. Nothing in
the Makefile applies or destroys infrastructure. Run one command at a time,
and start the next only when the last has finished. Keep the laptop lid open
during `make demo` (it holds an idle-sleep assertion; a closed lid still
sleeps).

Cost while up: about $0.85 to $1.00 an hour. Budget five to six hours; R2
alone can take two (Defender documents alerts "up to an hour", once per
profile). `make aks-cost` gives the Infracost figure.

## 0. Before the session (once)

```bash
brew install Azure/kubelogin/kubelogin
infracost auth login
# Decision D1 (revised 2026-11-05): the Dsv6 family (and with it the region) to 16 vCPUs.
# DSv3 quota can no longer be raised (DeprecatedQuotaType, ADR-0008).
az provider register -n Microsoft.Quota --wait
az quota update --resource-name StandardDsv6Family \
  --scope "/subscriptions/$(az account show --query id -o tsv)/providers/Microsoft.Compute/locations/eastus2" \
  --limit-object value=16 --resource-type dedicated
cp infra/azure/lab.auto.tfvars.example infra/azure/lab.auto.tfvars   # the lab subscription ID
```

## 1. Preflight and infrastructure

```bash
make check            # unit tests, renders (needs the network), Terraform gates, hooks
make aks-cost         # Infracost estimate; stop if it fails
make aks-preflight    # read-only, fails closed; records the before-picture for verify-gone
make aks-plan         # runs the preflight again, writes infra/azure/tfplan, prints the apply command
terraform -chdir=$PWD/infra/azure apply $PWD/infra/azure/tfplan
```

The preflight fails on: the wrong subscription; a policy assignment nobody
has reviewed, a deny assignment or a lock on the subscription or a lab group;
an unregistered provider or `EncryptionAtHost`; Dsv6 or regional quota under
16; a session that would overlap the weekly node image window (Sunday 08:00
to 12:00 UTC by default, `SESSION_HOURS=6`); another workflow automation; or
anything left from an earlier session (groups, soft-deleted vault, custom
role, policy definition, snapshots, a Containers plan not on Free).

## 2. Platform, images, bootstrap (right after the apply)

```bash
make aks-up     # = aks-images aks-platform aks-bootstrap
```

- `aks-images`: linux/amd64 builds to ACR (`<sha>-v1`, `<sha>-v2`, git
  server, R2 worker) and the generated overlay with the vault URL.
- `aks-platform`: `.kube/aks` through kubelogin, Cilium (nodes are NotReady
  until it is in), the readiness gate, the charts in the kind order without
  metrics-server, the R2 worker, then a wait of up to 35 minutes for remote
  write to be accepted.
- `aks-bootstrap`: lab CA, ESO's identity, the partner key in Key Vault
  (retried while the role assignment propagates), the Git server, Argo CD on
  the AKS baseline, and proof that ESO synced the vault's latest version.

### Live checks before the demo (the docs do not settle these)

```bash
export KUBECONFIG=$PWD/.kube/aks
```

| Check | How | If it fails |
|---|---|---|
| L20 Cilium's dataplane on the Dsv6 MANA NIC | `kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg status \| grep -iE 'devices\|kubeproxy\|cluster health'`; `make aks-platform` must pass its readiness gate | stop; record it (D2) |
| L1 Cilium on BYO CNI; no node rebooted by auto-repair before Cilium | `kubectl get nodes`; `kubectl get events -A --field-selector source=aks-auto-repair` | stop; record it |
| L3 Defender sensor on the tainted workload nodes | printed by `aks-platform` ("L3: ... N of 3") | R2 cannot be detected: record R2 as not runnable (a miss with a reason), never skip it |
| L5, L13 ESO reads the latest version through the firewall | printed by `aks-bootstrap` | wait for the service endpoint (up to 15 minutes), rerun `make aks-bootstrap` |
| L9 remote write accepted | printed by `aks-platform` | check the Prometheus log for 403 |
| L10 the worker's RBAC with Azure RBAC enabled | `kubectl auth can-i patch nodes --as=system:serviceaccount:responder:responder` | stop |
| L14 Grafana plugin | `kubectl -n monitoring exec deploy/kps-grafana -c grafana -- grafana cli plugins ls \| grep azureprometheus` | Grafana only; not a judged row |
| L15, L16 worker egress policy | `kubectl -n responder get cnp r2-worker-egress`; `kubectl -n responder logs deploy/r2-worker \| tail` (no token or queue errors) | stop |

Checked by the rows themselves and read from their evidence afterwards: L2
and L4 (the R2 alert's fields and delay, `R2/r2-*/alert-arm.json`), L6 and L18
(the DINE deployment, `S4/s4-hardened-trace.json`), L7 (the shutdown taint,
`shutdown_taint_s` in A4), L8 (port-forwards survive the run), L11, L12 and
L17 (the Logic App run, `R2/r2-hardened/logic-app-runs.json` and the
manifest), L19 (the hardened R2 Logic App run exists at all after the
`isEnabled` PATCH).

## 3. Experiment

```bash
make demo TARGET=aks RUN_ID=aks-run1
```

A subset: `make demo TARGET=aks ROWS=A4,S4 RUN_ID=aks-a4s4`.

Evidence: `evidence/<run-id>/report.md`, per-row prober samples, the A4 and
S4 traces, and R2's bundle (ARM's copy of the alert, the worker's
`isolated.json`, the Logic App's manifest with the snapshot and capture, its
runs). Before committing a reference run, redact it and check nothing is left:

```bash
scripts/redact-evidence.py --cloud azure --ip "$(curl -4 -s ifconfig.me)" evidence/aks-run1
```

It replaces the subscription, the tenant and every other GUID, and fails if
any GUID, SAS signature or token survives. Add the run to `.gitignore`'s
reference list, then commit (locally).

## 4. Teardown (three steps, in order)

```bash
make aks-predestroy
make aks-destroy-plan
terraform -chdir=$PWD/infra/azure apply $PWD/infra/azure/destroy.tfplan
make aks-verify-gone
```

`aks-predestroy` removes the lab app and its PDB (a node pool delete honours
PDBs, so it would stall the destroy) and the evidence snapshots (the lab
group refuses to be deleted with resources Terraform did not create). The
destroy removes the cluster with its node group, the Containers plan (back to
Free), the automation, the Logic App, the policy, the workspace with its
`MA_` group, and purges the vault. `aks-verify-gone` must print only "gone";
it compares Defender's footprint with the preflight's before-picture.

If the destroy stops on "the resource group contains resources": list them
(`az resource list -g rg-selfheal-lab -o table`); anything there that
Terraform did not create is the thing to understand before deleting it.
