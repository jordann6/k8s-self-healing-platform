#!/usr/bin/env bash
# Apply the R2 worker and its egress policy (cluster/aks/responder.yaml.tmpl),
# then prove the worker can poll its queue: no poll_error after the rollout
# (live checks L15, L16). Called by aks-platform; `make aks-worker` reapplies
# only this.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/azure"
: "${KUBECONFIG:?run through make}"
[[ "$KUBECONFIG" == "$root/.kube/aks" ]] || { echo "aks-worker: KUBECONFIG must be $root/.kube/aks" >&2; exit 1; }
gen="$root/gitops/targets/aks/generated"
[[ -f "$gen/RESPONDER_IMAGE" ]] || { echo "aks-worker: run make aks-images first (the R2 worker image)" >&2; exit 1; }

runner="$(terraform -chdir="$tf" output -json runner)"
r() { jq -r --arg k "$1" '.[$k]' <<<"$runner"; }

RESPONDER_CLIENT_ID="$(r responder_client_id)" RESPONDER_IMAGE="$(cat "$gen/RESPONDER_IMAGE")" \
	CLUSTER_NAME="$(terraform -chdir="$tf" output -raw cluster_name)" CLUSTER_ID="$(r cluster_id)" \
	NODE_RESOURCE_GROUP="$(r node_resource_group)" STORAGE_ACCOUNT="$(r storage_account)" \
	INCIDENTS_QUEUE="$(r incidents_queue)" EVIDENCE_CONTAINER="$(r evidence_container)" \
	envsubst <"$root/cluster/aks/responder.yaml.tmpl" | kubectl apply -f - >/dev/null
# A policy change alone does not restart the pod; restart it so the proof
# below reads a fresh process.
kubectl -n responder rollout restart deploy/r2-worker >/dev/null
kubectl -n responder rollout status deploy/r2-worker --timeout=180s >/dev/null

echo "  L15/L16: waiting 60s for the worker's first polls"
sleep 60
# An empty poll logs nothing, so the proof is: the fresh pod logged
# "started", never "poll_error", and has not restarted.
logs="$(kubectl -n responder logs deploy/r2-worker)"
restarts="$(kubectl -n responder get pods -l app=r2-worker -o jsonpath='{.items[*].status.containerStatuses[*].restartCount}')"
if ! grep -q '"started"' <<<"$logs"; then
	echo "aks-worker: FAIL, the worker never logged started" >&2
	exit 1
fi
if grep -q '"poll_error"' <<<"$logs"; then
	echo "aks-worker: FAIL, the worker cannot poll its queue:" >&2
	grep '"poll_error"' <<<"$logs" | tail -3 >&2
	exit 1
fi
[[ "$restarts" =~ ^0( 0)*$ ]] || { echo "aks-worker: FAIL, the worker restarted ($restarts)" >&2; exit 1; }
echo "  L15/L16: the worker started and polls its queue through its egress policy (no poll_error, no restart)"
