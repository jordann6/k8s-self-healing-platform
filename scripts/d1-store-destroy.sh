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
	az storage blob list --account-name "$account" --container-name "$container" --auth-mode login \
		--include v --query '[].{n:name,v:versionId}' --output tsv |
		while IFS=$'\t' read -r name version; do
			if [[ -n "$version" && "$version" != None ]]; then
				az storage blob delete --account-name "$account" --container-name "$container" \
					--name "$name" --version-id "$version" --auth-mode login --only-show-errors
			else
				az storage blob delete --account-name "$account" --container-name "$container" \
					--name "$name" --auth-mode login --only-show-errors
			fi
		done
	az storage container delete --account-name "$account" --name "$container" --auth-mode login --only-show-errors
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
