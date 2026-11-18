#!/usr/bin/env bash
# d1-store-destroy.sh <aws|azure> <plan|empty>: the second, later step that removes the
# D1 WORM store (ADR-0022 decision 3). The lab's own destroy never touches it.
#
#   plan    after the retention has ended, write a destroy plan for the store root and
#           print the command Jordan runs to apply it (credentialed apply is his).
#   empty   azure only: delete every expired blob version and the container, which Azure
#           requires before the account can be deleted. Refused before expiry.
#
# Both refuse unless .d1/<eks|aks>-store.json names a retention end that has passed (plus a
# margin). A missing record is a refusal, never a pass.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cloud="${1:?usage: d1-store-destroy.sh aws|azure plan|empty}"
action="${2:?usage: d1-store-destroy.sh aws|azure plan|empty}"

case "$cloud" in
aws) target=eks root_dir="$root/infra/d1-store-aws" ;;
azure) target=aks root_dir="$root/infra/d1-store-azure" ;;
*) echo "usage: d1-store-destroy.sh aws|azure plan|empty" >&2; exit 2 ;;
esac

python3 "$root/scripts/d1_store_guard.py" "$root/.d1/$target-store.json" "${D1_GUARD_MARGIN_S:-600}"

case "$cloud:$action" in
aws:plan)
	: "${MGMT_PROFILE:?set MGMT_PROFILE}"
	AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root_dir" init -input=false >/dev/null
	AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root_dir" plan -destroy -input=false -out=destroy.tfplan \
		-var-file="$root/infra/aws/lab.auto.tfvars"
	echo
	echo "Read the plan, then run:  AWS_PROFILE=$MGMT_PROFILE terraform -chdir=$root_dir apply $root_dir/destroy.tfplan"
	;;
azure:empty)
	account="$(terraform -chdir="$root_dir" output -raw account_name)"
	container="$(terraform -chdir="$root_dir" output -raw container_name)"
	echo "deleting every blob version in $account/$container (retention has ended)"
	# az storage blob delete has no --version-id (az 2.83), so versions go through the Blob REST API with
	# the operator's Entra token. The current version cannot be deleted by its version ID, so each base
	# blob is deleted first (it becomes a previous version), then every version by ID.
	list_versions() {
		az storage blob list --account-name "$account" --container-name "$container" --auth-mode login \
			--include v --query "$1" --output tsv
	}
	list_versions '[?isCurrentVersion].name' | sort -u | while IFS= read -r name; do
		[[ -n "$name" ]] || continue
		az storage blob delete --account-name "$account" --container-name "$container" \
			--name "$name" --auth-mode login --only-show-errors
	done
	list_versions '[].[name, versionId]' | while IFS=$'\t' read -r name version; do
		[[ -n "$name" && -n "$version" && "$version" != None ]] || continue
		url="$(python3 -c 'import sys, urllib.parse as u; a = sys.argv; print("https://" + a[1] + ".blob.core.windows.net/" + a[2] + "/" + u.quote(a[3]) + "?versionid=" + u.quote(a[4], safe=""))' \
			"$account" "$container" "$name" "$version")"
		az rest --method delete --url "$url" --resource https://storage.azure.com \
			--headers x-ms-version=2023-11-03 --only-show-errors >/dev/null
	done
	left="$(list_versions 'length(@)')"
	[[ "$left" == 0 ]] || { echo "d1-store-destroy: $left blob versions remain, not deleting the container" >&2; exit 1; }
	# A container with version-level immutability can only be deleted through the control plane
	# (Learn, "Version-level WORM policies", Deletion). The data-plane delete fails with
	# ContainerImmutableStorageWithVersioningEnabled even when empty, and the account delete fails with
	# AccountProtectedFromDeletion while the container exists (both seen 2026-11-18).
	rg="$(terraform -chdir="$root_dir" output -raw resource_group)"
	az storage container-rm delete --storage-account "$account" -g "$rg" -n "$container" --yes --only-show-errors
	terraform -chdir="$root_dir" state rm azurerm_storage_container.backups >/dev/null 2>&1 || true
	echo "  0 blob versions left, container deleted through the control plane"
	;;
azure:plan)
	terraform -chdir="$root_dir" init -input=false >/dev/null
	terraform -chdir="$root_dir" plan -destroy -input=false -out=destroy.tfplan \
		-var-file="$root/infra/azure/lab.auto.tfvars"
	echo
	echo "Read the plan, then run:  terraform -chdir=$root_dir apply $root_dir/destroy.tfplan"
	;;
*)
	echo "d1-store-destroy: $action is not valid for $cloud (aws: plan, azure: empty then plan)" >&2
	exit 2
	;;
esac
