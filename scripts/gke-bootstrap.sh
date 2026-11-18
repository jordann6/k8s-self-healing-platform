#!/usr/bin/env bash
# gke-bootstrap.sh: everything outside GitOps on GKE, after gke-images and
# gke-platform. The kind bootstrap, with the Git server from Artifact Registry
# and the root app pointed at targets/gke. Idempotent.
#
#   the lab CA and the prober's CA bundle         (same as kind)
#   the partner API key in the kind backend       (row S1 on GCP is the landing
#                                                  zone proof, ADR-0027 Scope;
#                                                  the steady state still reads it)
#   the in-cluster Git server, seeded from ./gitops
#   Argo CD's root app, pointed at targets/gke/apps/baseline
#
# No secret value touches a command line: the key is generated in Python and
# travels over stdin.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${KUBECONFIG:?run through make}"
[[ "$KUBECONFIG" == "$root/.kube/gke" ]] || { echo "gke-bootstrap: KUBECONFIG must be $root/.kube/gke" >&2; exit 1; }
gen="$root/gitops/targets/gke/generated"
[[ -f "$gen/GITSERVER_IMAGE" ]] || { echo "gke-bootstrap: run make gke-images first" >&2; exit 1; }

echo "gke-bootstrap: namespaces, secret backend, Git server"
kubectl kustomize "$root/bootstrap" | sed "s|image: selfheal-gitserver:dev|image: $(cat "$gen/GITSERVER_IMAGE")|" |
	kubectl apply -f - >/dev/null
kubectl apply -f "$root/bootstrap/ca.yaml" >/dev/null
kubectl -n cert-manager wait --for=condition=Ready certificate/lab-ca --timeout=180s >/dev/null

echo "gke-bootstrap: partner API key"
if ! kubectl -n secret-backend get secret partner-api-key >/dev/null 2>&1; then
	python3 -I -c 'import secrets, sys; sys.stdout.write("api-key=" + secrets.token_hex(24) + "\n")' |
		kubectl -n secret-backend create secret generic partner-api-key --from-env-file=/dev/stdin >/dev/null
fi
kubectl -n secret-backend get secret partner-api-key -o jsonpath='{.data.api-key}' | base64 -d |
	python3 -I -c 'import sys; k = sys.stdin.read().strip(); sys.stdout.write(f"current={k}\nprevious=\n")' |
	kubectl -n selfheal create secret generic partner-keys --from-env-file=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.ca\.crt}' | base64 -d |
	kubectl -n selfheal create configmap lab-ca --from-file=ca.crt=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
kubectl -n selfheal get secret web-tls >/dev/null 2>&1 || "$root/scripts/static-cert.sh" 3600 >/dev/null

echo "gke-bootstrap: Git"
kubectl -n gitops rollout status deploy/gitserver --timeout=180s >/dev/null
"$root/scripts/seed-git.sh"
sed 's|    path: apps/baseline|    path: targets/gke/apps/baseline|' "$root/bootstrap/root-app.yaml" | kubectl apply -f - >/dev/null

echo "gke-bootstrap: waiting for the workload app to sync"
deadline=$(($(date +%s) + 600))
until [[ "$(kubectl -n argocd get application selfheal-workload -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null)" == "Synced/Healthy" ]]; do
	if (($(date +%s) > deadline)); then
		echo "gke-bootstrap: FAIL, selfheal-workload not Synced and Healthy after 10 minutes" >&2
		kubectl -n argocd get application selfheal-workload -o jsonpath='{.status.conditions}{"\n"}' >&2 || true
		exit 1
	fi
	sleep 10
done
echo "gke-bootstrap: done (Argo CD root app -> targets/gke/apps/baseline; next: make demo TARGET=gke)"
