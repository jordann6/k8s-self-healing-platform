#!/usr/bin/env bash
# bootstrap.sh: everything outside GitOps, after the platform charts are up.
# Idempotent. Creates the lab CA, the backend key, the partner's keys, the
# CA bundle the prober trusts, the baseline certificate, seeds Git and points
# Argo CD at the baseline profile.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
kubectl apply -k "$root/bootstrap" >/dev/null
kubectl apply -f "$root/bootstrap/ca.yaml" >/dev/null
kubectl -n cert-manager wait --for=condition=Ready certificate/lab-ca --timeout=120s >/dev/null

if ! kubectl -n secret-backend get secret partner-api-key >/dev/null 2>&1; then
	kubectl -n secret-backend create secret generic partner-api-key \
		--from-literal=api-key="$(openssl rand -hex 24)" >/dev/null
fi
key="$(kubectl -n secret-backend get secret partner-api-key -o jsonpath='{.data.api-key}' | base64 -d)"
kubectl -n selfheal create secret generic partner-keys --from-literal=current="$key" \
	--from-literal=previous= --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.ca\.crt}' | base64 -d |
	kubectl -n selfheal create configmap lab-ca --from-file=ca.crt=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null

kubectl -n selfheal get secret web-tls >/dev/null 2>&1 || "$root/scripts/static-cert.sh" 3600 >/dev/null

"$root/scripts/seed-git.sh"
kubectl apply -f "$root/bootstrap/root-app.yaml" >/dev/null
echo "bootstrap: done (Argo CD root app -> apps/baseline)"
