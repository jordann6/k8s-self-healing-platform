#!/usr/bin/env bash
# aks-bootstrap.sh: everything outside GitOps on AKS, after aks-images and
# aks-platform. The AKS counterpart of bootstrap.sh. Idempotent.
#
#   the lab CA and the prober's CA bundle        (same as kind)
#   ESO's identity toward Key Vault: service account eso-backend, annotated
#     with the workload identity client and tenant (Argo CD does not watch
#     ServiceAccounts, so it lives here, as the kind reader does)
#   the partner API key, now in Key Vault        (row S1's backend)
#   the in-cluster Git server from ACR, seeded from ./gitops
#   Argo CD's root app, pointed at targets/aks/apps/baseline
#   proof that ESO reads the vault (the synced Secret exists)
#
# No secret value touches a command line: the key travels through a 0600
# file to `az keyvault secret set --file`, and over stdin into the cluster.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/azure"
: "${KUBECONFIG:?run through make}"
[[ "$KUBECONFIG" == "$root/.kube/aks" ]] || { echo "aks-bootstrap: KUBECONFIG must be $root/.kube/aks" >&2; exit 1; }
gen="$root/gitops/targets/aks/generated"
[[ -f "$gen/GITSERVER_IMAGE" ]] || { echo "aks-bootstrap: run make aks-images first" >&2; exit 1; }
runner="$(terraform -chdir="$tf" output -json runner)"
r() { jq -r --arg k "$1" '.[$k]' <<<"$runner"; }
vault="$(r key_vault)"
secret=partner-api-key

echo "aks-bootstrap: namespaces, CA, Git server"
kubectl kustomize "$root/bootstrap" | sed "s|image: selfheal-gitserver:dev|image: $(cat "$gen/GITSERVER_IMAGE")|" |
	kubectl apply -f - >/dev/null
"$root/scripts/q1-credentials.sh"
# The kind backend namespace and its reader are not used on AKS (Key Vault is
# the backend) but are harmless; the SecretStore no longer references them.
kubectl apply -f "$root/bootstrap/ca.yaml" >/dev/null
kubectl -n cert-manager wait --for=condition=Ready certificate/lab-ca --timeout=180s >/dev/null

echo "aks-bootstrap: ESO's workload identity (selfheal/eso-backend)"
kubectl apply -f - >/dev/null <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: eso-backend
  namespace: selfheal
  annotations:
    azure.workload.identity/client-id: "$(r eso_client_id)"
    azure.workload.identity/tenant-id: "$(r tenant_id)"
automountServiceAccountToken: false
EOF

echo "aks-bootstrap: partner API key in Key Vault ($vault)"
# The operator's Key Vault Secrets Officer assignment was made in the apply,
# but role assignments "can take up to 10 minutes" (ADR-0007 row 13), so a
# 403 right after the apply is retried, for up to 15 minutes. Anything that
# is not a 403 or a missing secret stops the script.
keyfile="$(mktemp)"
chmod 600 "$keyfile"
trap 'rm -f "$keyfile"' EXIT
deadline=$(($(date +%s) + 900))
while :; do
	if out="$(az keyvault secret show --vault-name "$vault" --name "$secret" --query id -o tsv 2>&1)"; then
		echo "  present"
		break
	fi
	if grep -q 'SecretNotFound' <<<"$out"; then
		python3 -I -c 'import secrets, sys; sys.stdout.write(secrets.token_hex(24))' >"$keyfile"
		if out="$(az keyvault secret set --vault-name "$vault" --name "$secret" --file "$keyfile" --encoding utf-8 \
			--query id -o tsv 2>&1)"; then
			echo "  written"
			break
		fi
	fi
	if ! grep -qE 'Forbidden|403|not authorized|ForbiddenByRbac|ForbiddenByFirewall' <<<"$out"; then
		echo "aks-bootstrap: Key Vault call failed: $(head -3 <<<"$out")" >&2
		exit 1
	fi
	if (($(date +%s) > deadline)); then
		echo "aks-bootstrap: FAIL, still forbidden after 15 minutes: $(head -2 <<<"$out")" >&2
		exit 1
	fi
	echo "  forbidden (role or firewall still propagating); retrying in 30s"
	sleep 30
done
rm -f "$keyfile"

# The partner accepts the vault's current key; the value goes over stdin.
az keyvault secret show --vault-name "$vault" --name "$secret" --query value -o tsv |
	python3 -I -c 'import sys; k = sys.stdin.read().strip(); sys.stdout.write(f"current={k}\nprevious=\n")' |
	kubectl -n selfheal create secret generic partner-keys --from-env-file=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
kubectl -n cert-manager get secret lab-ca -o jsonpath='{.data.ca\.crt}' | base64 -d |
	kubectl -n selfheal create configmap lab-ca --from-file=ca.crt=/dev/stdin --dry-run=client -o yaml |
	kubectl apply -f - >/dev/null
kubectl -n selfheal get secret web-tls >/dev/null 2>&1 || "$root/scripts/static-cert.sh" 3600 >/dev/null

echo "aks-bootstrap: Git"
kubectl -n gitops rollout status deploy/gitserver --timeout=180s >/dev/null
"$root/scripts/seed-git.sh"
sed 's|    path: apps/baseline|    path: targets/aks/apps/baseline|' "$root/bootstrap/root-app.yaml" | kubectl apply -f - >/dev/null

# Live checks L5 and L13 in one: ESO, as eso-backend, exchanges its token
# with Entra and reads the latest version through the vault firewall (node
# subnet service endpoint, which "can take up to 15 minutes").
echo "aks-bootstrap: waiting for ESO to sync the key from Key Vault"
deadline=$(($(date +%s) + 1200))
until [[ "$(kubectl -n selfheal get externalsecret partner-api-key \
	-o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" == "True" ]]; do
	if (($(date +%s) > deadline)); then
		echo "aks-bootstrap: FAIL, ExternalSecret not Ready after 20 minutes:" >&2
		kubectl -n selfheal get externalsecret partner-api-key \
			-o jsonpath='{.status.conditions[?(@.type=="Ready")].message}{"\n"}' >&2 || true
		echo "  inspect: kubectl -n selfheal describe secretstore backend" >&2
		exit 1
	fi
	sleep 15
done
synced="$(kubectl -n selfheal get secret partner-api-key -o jsonpath='{.data.api-key}' | base64 -d | shasum -a 256)"
vaulted="$(az keyvault secret show --vault-name "$vault" --name "$secret" --query value -o tsv | tr -d '\n' | shasum -a 256)"
[[ "$synced" == "$vaulted" ]] || { echo "aks-bootstrap: FAIL, the synced key differs from the vault's latest version" >&2; exit 1; }
echo "  L5/L13: ESO synced the vault's latest version (no version field, through the firewall)"
echo "aks-bootstrap: done (Argo CD root app -> targets/aks/apps/baseline)"
