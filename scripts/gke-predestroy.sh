#!/usr/bin/env bash
# gke-predestroy.sh: clear what Terraform does not own, or what would stall
# its destroy, so the destroy can finish and nothing survives it (ADR-0027
# row 11). Run by the operator (make gke-predestroy) BEFORE the destroy plan:
#
#   1. make gke-predestroy        this script
#   2. make gke-destroy-plan, apply it
#   3. make gke-verify-gone
#
# In order:
#   - the Argo CD application controller is scaled to zero first, then the
#     apps are deleted: on EKS (2026-11-18) deleting only the parent apps
#     left children with selfHeal on, which re-created what was being removed
#   - every Service of type LoadBalancer and every PersistentVolumeClaim, and
#     a wait for them to go: "GKE retains persistent disk volumes during
#     cluster deletion", and LoadBalancer Services should be deleted "before
#     deleting your cluster" (Deleting a cluster). The GKE rows create none;
#     this is the fail-safe
#   - every PodDisruptionBudget in the lab namespace and the web Rollout, so
#     no drain waits on them (the EKS lesson: a PDB stranded nodes)
# Cilium stays: on GKE it uses the pod alias ranges and owns no Google Cloud
# resource, and removing it would only break networking for the drain.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${KUBECONFIG:?run through make}"
[[ "$KUBECONFIG" == "$root/.kube/gke" ]] || { echo "gke-predestroy: KUBECONFIG must be $root/.kube/gke" >&2; exit 1; }
[[ "$(kubectl config current-context)" == "selfheal-gke" ]] || { echo "gke-predestroy: wrong kube context" >&2; exit 1; }

if ! kubectl version --request-timeout=10s >/dev/null 2>&1; then
	echo "gke-predestroy: the API server does not answer; nothing to clear in-cluster (run gke-verify-gone after the destroy)"
	exit 0
fi

echo "gke-predestroy: stopping the Argo CD application controller, then deleting the apps"
kubectl -n argocd scale statefulset argocd-application-controller --replicas=0 >/dev/null 2>&1 || true
kubectl -n argocd wait --for=delete pod -l app.kubernetes.io/name=argocd-application-controller --timeout=120s >/dev/null 2>&1 || true
kubectl -n argocd delete application --all --wait=false >/dev/null 2>&1 || true

echo "gke-predestroy: LoadBalancer Services and PVCs"
lbs="$(kubectl get svc -A -o json | jq -r '.items[] | select(.spec.type == "LoadBalancer") | "\(.metadata.namespace) \(.metadata.name)"')"
while read -r ns name; do
	[[ -n "$ns" ]] && kubectl -n "$ns" delete svc "$name" --wait=true --timeout=300s >/dev/null
done <<<"$lbs"
kubectl delete pvc -A --all --wait=true --timeout=300s >/dev/null 2>&1 || true
left="$(kubectl get pv -o name 2>/dev/null | wc -l | tr -d ' ')"
[[ "$left" == "0" ]] || echo "gke-predestroy: $left PersistentVolume(s) remain; gke-verify-gone will list their disks" >&2

echo "gke-predestroy: PodDisruptionBudgets and the web Rollout"
kubectl -n selfheal delete pdb --all --wait=true --timeout=60s >/dev/null 2>&1 || true
kubectl -n selfheal delete rollout --all --wait=false >/dev/null 2>&1 || true
echo "gke-predestroy: done (next: make gke-destroy-plan)"
