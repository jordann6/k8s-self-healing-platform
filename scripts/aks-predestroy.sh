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
#     (this CLI has no --ignore-pdb), so minAvailable 2 on a draining pool
#     would stall the destroy (the EKS lesson: a PDB stranded nodes at
#     teardown). Every PDB that selects a pool pod is deleted, the pool is
#     cordoned and its pods are deleted, all BEFORE the destroy: afterwards
#     the operator role assignments go and kubectl is Forbidden
#   - the R2 evidence snapshots (snap-r2-*) in the lab group: the provider
#     refuses to delete a group that still holds resources it did not create
#     (prevent_deletion_if_contains_resources), which is the fail-closed
#     behaviour we want for anything else
#   - any platform rolling upgrade still running on a node scale set. Azure
#     can start a VM Agent Rolling Upgrade on its own mid-session; while it
#     runs the scale set delete returns OperationNotAllowed, and the 11/10
#     destroy failed twice with ResourceGroupDeletionBlocked on the load
#     balancer, NSG and public IP the scale set still held. The real cause was
#     only in the Activity Log
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
node_rg="$(jq -r '.node_resource_group // empty' <<<"$runner")"
node_rg="${node_rg:-rg-selfheal-lab-nodes}"

if kubectl version --request-timeout=10s >/dev/null 2>&1; then
	echo "aks-predestroy: stopping Argo CD sync"
	kubectl -n argocd delete application selfheal-root selfheal-workload --ignore-not-found --wait=false >/dev/null || true
	echo "aks-predestroy: removing the lab app and its PodDisruptionBudget"
	kubectl -n selfheal delete rollout,deployment,pdb --all --wait=false >/dev/null 2>&1 || true
	for _ in $(seq 1 30); do
		[[ -z "$(kubectl -n selfheal get pdb -o name 2>/dev/null)" ]] && break
		sleep 5
	done
	# Empty the workload pool in-cluster NOW. Once the destroy starts, Terraform
	# removes the operator's role assignments with the cluster, kubectl goes
	# Forbidden, and nothing in-cluster can be fixed while the pool delete
	# drains (the 11/7 destroy hung ~45 minutes this way). The workload pool
	# is deleted before the operator roles (depends_on in aks.tf), but this
	# script must not rely on kubectl after the destroy begins.
	#   1. stop every autoscaler that would put pods back (KEDA, HPA)
	#   2. delete every PodDisruptionBudget that selects a pod on the pool
	#      (the pool delete honours PDBs and this CLI has no --ignore-pdb)
	#   3. cordon the pool and delete its pods, then wait until none remain
	echo "aks-predestroy: stopping autoscalers"
	kubectl delete scaledobject,triggerauthentication -A --all --wait=false >/dev/null 2>&1 || true
	kubectl delete hpa -A --all --wait=false >/dev/null 2>&1 || true
	wnames="$(kubectl get nodes -l selfheal.lab/nodes=workload -o name 2>/dev/null || true)"
	wnodes="$(kubectl get nodes -l selfheal.lab/nodes=workload -o json | jq -c '[.items[].metadata.name]')"
	if [[ -n "$wnames" ]]; then
		# shellcheck disable=SC2086
		kubectl cordon $wnames >/dev/null
	fi
	echo "aks-predestroy: removing PodDisruptionBudgets that cover workload-pool pods"
	while IFS=$'\t' read -r ns name sel; do
		[[ -n "$name" ]] || continue
		# A selector this cannot read counts as covering.
		if [[ "$sel" != "?" ]]; then
			on="$(kubectl -n "$ns" get pods -l "$sel" -o json | jq --argjson w "$wnodes" \
				'[.items[] | select(.spec.nodeName as $n | $w | index($n))] | length')"
			((on > 0)) || continue
		fi
		kubectl -n "$ns" delete pdb "$name" --wait=false >/dev/null
		echo "  deleted pdb $ns/$name"
	done < <(kubectl get pdb -A -o json | jq -r '.items[]
		| [.metadata.namespace, .metadata.name,
		   (if (.spec.selector.matchExpressions // []) == [] and (.spec.selector.matchLabels // {}) != {}
		    then (.spec.selector.matchLabels | to_entries | map("\(.key)=\(.value)") | join(",")) else "?" end)] | @tsv')
	echo "aks-predestroy: draining the workload pool"
	for n in $(jq -r '.[]' <<<"$wnodes"); do
		kubectl get pods -A --field-selector "spec.nodeName=$n" -o json \
			| jq -r '.items[] | select(([.metadata.ownerReferences[]?.kind] | index("DaemonSet")) | not)
				| [.metadata.namespace, .metadata.name] | @tsv' \
			| while IFS=$'\t' read -r ns name; do
				kubectl -n "$ns" delete pod "$name" --wait=false >/dev/null 2>&1 || true
			done
	done
	left=""
	for _ in $(seq 1 36); do
		left=""
		for n in $(jq -r '.[]' <<<"$wnodes"); do
			left+="$(kubectl get pods -A --field-selector "spec.nodeName=$n" -o json \
				| jq -r '.items[] | select(([.metadata.ownerReferences[]?.kind] | index("DaemonSet")) | not)
					| "\(.metadata.namespace)/\(.metadata.name) "')"
		done
		[[ -z "$left" ]] && break
		sleep 5
	done
	if [[ -n "$left" ]]; then
		echo "aks-predestroy: FAIL, pods still on the workload pool: $left" >&2
		echo "  Known recovery if the destroy still stalls: az group delete -n rg-selfheal-lab-nodes, then az aks delete, then re-plan and apply the destroy." >&2
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

echo "aks-predestroy: rolling upgrades on the scale sets in $node_rg"
running=""
for vmss in $(az vmss list -g "$node_rg" --query "[].name" -o tsv 2>/dev/null); do
	code="$(az vmss rolling-upgrade get-latest -g "$node_rg" -n "$vmss" \
		--query "runningStatus.code" -o tsv 2>/dev/null || true)"
	if [[ "$code" == "RollingForward" ]]; then
		echo "  cancelling the rolling upgrade on $vmss"
		az vmss rolling-upgrade cancel -g "$node_rg" -n "$vmss" >/dev/null
		running+="$vmss "
	fi
done
for vmss in $running; do
	code=""
	for _ in $(seq 1 24); do
		code="$(az vmss rolling-upgrade get-latest -g "$node_rg" -n "$vmss" \
			--query "runningStatus.code" -o tsv 2>/dev/null || true)"
		[[ "$code" != "RollingForward" ]] && break
		sleep 5
	done
	[[ "$code" != "RollingForward" ]] || { echo "aks-predestroy: FAIL, rolling upgrade still running on $vmss" >&2; exit 1; }
	echo "  $vmss: $code"
done

echo "aks-predestroy: done (next: make aks-destroy-plan)"
echo "Copy any evidence you still need first: the evidence account goes with the destroy."
