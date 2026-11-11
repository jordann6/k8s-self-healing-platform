#!/usr/bin/env bash
# d1-bootstrap.sh <eks|aks>: everything row D1 needs outside GitOps on a cloud
# (ADR-0022). Called by eks-bootstrap.sh and aks-bootstrap.sh. Idempotent.
#
#   the two D1 namespaces (Pod Security restricted)
#   the PostgreSQL password Secret in both, generated once and never printed
#   the d1-backup and d1-restore service accounts (Argo CD does not manage them);
#     on AKS annotated with the workload identity client and tenant
#   the d1-store ConfigMap: which store, bucket or account, region. These name
#     this account, so they live here and never in Git.
#
# D1 is off unless the WORM store root has been applied (make d1-store-<cloud>-apply),
# in which case this prints a note and does nothing.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:?usage: d1-bootstrap.sh eks|aks}"

case "$target" in
eks)
	store="$root/infra/d1-store-aws"
	bucket="$(terraform -chdir="$store" output -raw bucket 2>/dev/null || true)"
	[[ -n "$bucket" ]] || { echo "  D1 store not applied, skipping (make d1-store-eks-plan first)"; exit 0; }
	region="$(terraform -chdir="$store" output -raw region)"
	store_env=(--from-literal=D1_STORE=s3 --from-literal=S3_BUCKET="$bucket" --from-literal=D1_CONTAINER="$bucket"
		--from-literal=AWS_REGION="$region" --from-literal=AWS_DEFAULT_REGION="$region")
	annotate() { :; }
	;;
aks)
	store="$root/infra/d1-store-azure"
	account="$(terraform -chdir="$store" output -raw account_name 2>/dev/null || true)"
	[[ -n "$account" ]] || { echo "  D1 store not applied, skipping (make d1-store-aks-plan first)"; exit 0; }
	container="$(terraform -chdir="$store" output -raw container_name)"
	ids="$(terraform -chdir="$root/infra/azure" output -json workload_identities)"
	tenant="$(terraform -chdir="$root/infra/azure" output -json runner | jq -r .tenant_id)"
	store_env=(--from-literal=D1_STORE=azblob --from-literal=D1_CONTAINER="$container"
		--from-literal=AZ_ACCOUNT_URL="https://${account}.blob.core.windows.net")
	annotate() { # namespace service-account identity-key
		local client
		client="$(jq -r --arg k "$3" '.[$k] // empty' <<<"$ids")"
		[[ -n "$client" ]] || { echo "d1-bootstrap: no workload identity $3; was the AKS root planned with d1_store_account_id?" >&2; exit 1; }
		kubectl -n "$1" annotate serviceaccount "$2" --overwrite \
			azure.workload.identity/client-id="$client" azure.workload.identity/tenant-id="$tenant" >/dev/null
	}
	;;
*)
	echo "usage: d1-bootstrap.sh eks|aks" >&2
	exit 2
	;;
esac

for ns in selfheal-d1-source selfheal-d1-restore; do
	kubectl create namespace "$ns" --dry-run=client -o yaml |
		kubectl label --local -f - pod-security.kubernetes.io/enforce=restricted -o yaml |
		kubectl apply -f - >/dev/null
done

if ! kubectl -n selfheal-d1-source get secret d1-postgres >/dev/null 2>&1; then
	postgres_password="$(openssl rand -hex 24)"
	for ns in selfheal-d1-source selfheal-d1-restore; do
		kubectl -n "$ns" create secret generic d1-postgres --from-literal=password="$postgres_password" >/dev/null
	done
	unset postgres_password
fi

kubectl -n selfheal-d1-source create serviceaccount d1-backup --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n selfheal-d1-restore create serviceaccount d1-restore --dry-run=client -o yaml | kubectl apply -f - >/dev/null
annotate selfheal-d1-source d1-backup d1_backup
annotate selfheal-d1-restore d1-restore d1_restore

for ns in selfheal-d1-source selfheal-d1-restore; do
	kubectl -n "$ns" create configmap d1-store "${store_env[@]}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done
echo "  D1 ready for $target (store settings in the d1-store ConfigMap)"
