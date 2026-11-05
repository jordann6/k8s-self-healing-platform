#!/usr/bin/env bash
# aks-predestroy.sh: clear what Terraform does not own, or what would stall
# its destroy, so the destroy can finish and nothing survives it. Run by the
# operator (make aks-predestroy) BEFORE the destroy plan:
#
#   1. make aks-predestroy        this script
#   2. make aks-destroy-plan, apply it
#   3. make aks-verify-gone
#
# What it removes, in order:
#   - the Argo CD apps (so nothing re-creates what follows)
#   - the lab app and its PodDisruptionBudget. Terraform deletes the workload
#     node pool before the cluster, and a node pool delete considers PDBs
#     unless told otherwise (`az aks nodepool delete --ignore-pdb`: "Delete an
#     existing nodepool without considering Pod Disruption Budget"), so
#     minAvailable 2 on a draining pool would stall the destroy (the EKS
#     lesson: a PDB stranded nodes at teardown)
#   - the R2 evidence snapshots (snap-r2-*) in the lab group: the provider
#     refuses to delete a group that still holds resources it did not create
#     (prevent_deletion_if_contains_resources), which is the fail-closed
#     behaviour we want for anything else
#
# Not done, unlike EKS: Cilium stays installed. On AKS it runs in overlay
# mode and owns no Azure resources (no ENIs to release), and removing it
# first would only break networking for the drain. The cluster delete removes
# the Defender sensor with the cluster (ADR-0007 row 11).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/azure"
: "${KUBECONFIG:?run through make}"
[[ "$KUBECONFIG" == "$root/.kube/aks" ]] || { echo "aks-predestroy: KUBECONFIG must be $root/.kube/aks" >&2; exit 1; }
runner="$(terraform -chdir="$tf" output -json runner 2>/dev/null || echo '{}')"
rg="$(jq -r '.resource_group // empty' <<<"$runner")"
rg="${rg:-rg-selfheal-lab}"

if kubectl version --request-timeout=10s >/dev/null 2>&1; then
	echo "aks-predestroy: stopping Argo CD sync"
	kubectl -n argocd delete application selfheal-root selfheal-workload --ignore-not-found --wait=false >/dev/null || true
	echo "aks-predestroy: removing the lab app and its PodDisruptionBudget"
	kubectl -n selfheal delete rollout,deployment,pdb --all --wait=false >/dev/null 2>&1 || true
	for _ in $(seq 1 30); do
		[[ -z "$(kubectl -n selfheal get pdb -o name 2>/dev/null)" ]] && break
		sleep 5
	done
	# Any other PDB that allows no disruption would stall the workload pool's
	# delete if it covers a pod there. (The system pool goes with the cluster
	# and is not drained.) A selector this cannot read counts as covering.
	wnodes="$(kubectl get nodes -l selfheal.lab/nodes=workload -o json | jq -c '[.items[].metadata.name]')"
	stuck=""
	while IFS=$'\t' read -r ns name sel; do
		[[ -n "$name" ]] || continue
		if [[ "$sel" == "?" ]]; then
			stuck="$stuck $ns/$name"
			continue
		fi
		on="$(kubectl -n "$ns" get pods -l "$sel" -o json | jq --argjson w "$wnodes" \
			'[.items[] | select(.spec.nodeName as $n | $w | index($n))] | length')"
		((on == 0)) || stuck="$stuck $ns/$name"
	done < <(kubectl get pdb -A -o json | jq -r '.items[] | select(.status.disruptionsAllowed == 0)
		| [.metadata.namespace, .metadata.name,
		   (if (.spec.selector.matchExpressions // []) == [] and (.spec.selector.matchLabels // {}) != {}
		    then (.spec.selector.matchLabels | to_entries | map("\(.key)=\(.value)") | join(",")) else "?" end)] | @tsv')
	if [[ -n "$stuck" ]]; then
		echo "aks-predestroy: FAIL, PodDisruptionBudgets on workload-pool pods that allow no disruption:$stuck" >&2
		exit 1
	fi
else
	echo "aks-predestroy: cluster unreachable; skipping the in-cluster steps"
fi

echo "aks-predestroy: evidence snapshots in $rg"
for id in $(az snapshot list -g "$rg" --query "[?starts_with(name, 'snap-r2-')].id" -o tsv 2>/dev/null); do
	az snapshot delete --ids "$id" >/dev/null
	echo "  deleted ${id##*/}"
done
left="$(az snapshot list --query "[?starts_with(name, 'snap-r2-')].name" -o tsv 2>/dev/null || true)"
[[ -z "$left" ]] || { echo "aks-predestroy: FAIL, snapshots remain: $left" >&2; exit 1; }

echo "aks-predestroy: done (next: make aks-destroy-plan)"
echo "Copy any evidence you still need first: the evidence account goes with the destroy."
