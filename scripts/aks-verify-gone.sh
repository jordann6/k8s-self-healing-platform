#!/usr/bin/env bash
# aks-verify-gone.sh: read-only proof that nothing from the session survived.
# Every line must say "gone". Covers the Azure teardown gotchas (ADR-0007
# rows 2, 11, 15): the node and Azure Monitor managed groups Azure created,
# anything named or tagged for the lab anywhere in the subscription, the
# evidence snapshots, a soft-deleted vault or workspace, the custom role, the
# policy definition and assignment, the Defender plan back on Free, the
# workflow automation and Logic App, and anything Defender added that was not
# there before the session (compared with the preflight's before-picture).
# Values are read from Azure, never from Terraform state or outputs: after a
# destroy there are none to read.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
snapshot="$root/infra/azure/.session/preflight.json"
cluster="${CLUSTER_NAME:-selfheal}"
left=0

check() { # name, value (empty = gone)
	if [[ -z "$2" || "$2" == "None" || "$2" == "[]" ]]; then
		printf '  gone     %s\n' "$1"
	else
		printf '  LEFT     %s: %s\n' "$1" "$2"
		left=1
	fi
}
# A failed query prints a LEFT line, never an empty (gone) one.
q() {
	local o
	o="$("$@" 2>/dev/null)" || { echo "az call failed: $*" | cut -c1-80; return; }
	tr '\t\n' '  ' <<<"$o" | sed 's/ *$//'
}

az account show >/dev/null 2>&1 || { echo "verify-gone: az is not signed in"; exit 1; }
if [[ -f "$snapshot" ]]; then
	before="$(cat "$snapshot")"
	echo "verify-gone (before-picture from $(jq -r .recorded_at <<<"$before"))"
else
	# No preflight record: compare with what ADR-0007 found on 2026-11-04 (the
	# Central US default workspace group predates the lab and is left alone).
	before='{"policy_assignments":["SecurityCenterBuiltIn"],"default_resource_groups":["DefaultResourceGroup-CUS"],"automations":[],"containers_tier":"Free"}'
	echo "verify-gone (no preflight record; comparing with the subscription as ADR-0007 recorded it)"
fi
lab="rg-$cluster-lab"
# Azure upper-cases some group names (the node group's resources report
# RG-SELFHEAL-LAB-NODES), and az's JMESPath has no lower-case function, so the
# group filter runs in jq. A failed az call is LEFT, never "gone".
in_lab() { # az list command... -> names of resources in a lab group
	local out
	out="$("$@" --query '[].{n:name, g:resourceGroup}' -o json 2>/dev/null)" || { echo "az call failed: $*"; return; }
	jq -r --arg l "rg-$cluster-lab" --arg m "ma_amw-$cluster" '.[] | select((.g | ascii_downcase) as $g
		| ($g | startswith($l)) or ($g | startswith($m))) | .n' <<<"$out" | tr '\n' ' ' | sed 's/ *$//'
}

check "AKS cluster" "$(q az aks list --query "[?name=='$cluster'].id" -o tsv)"
check "lab resource groups (lab, nodes, MA_ managed)" "$(q az group list \
	--query "[?name=='$lab' || name=='$lab-nodes' || starts_with(name, 'MA_amw-$cluster')].name" -o tsv)"
check "resources tagged selfheal.lab=managed" "$(q az resource list --tag selfheal.lab=managed --query '[].id' -o tsv)"
check "disks in lab groups" "$(in_lab az resource list --resource-type Microsoft.Compute/disks)"
check "NICs in lab groups" "$(in_lab az resource list --resource-type Microsoft.Network/networkInterfaces)"
check "public IPs in lab groups" "$(in_lab az resource list --resource-type Microsoft.Network/publicIPAddresses)"
check "load balancers in lab groups" "$(in_lab az resource list --resource-type Microsoft.Network/loadBalancers)"
check "VM scale sets in lab groups" "$(in_lab az resource list --resource-type Microsoft.Compute/virtualMachineScaleSets)"
check "evidence snapshots (anywhere)" "$(q az snapshot list --query "[?starts_with(name, 'snap-r2-')].id" -o tsv)"
check "soft-deleted lab vault (purge it)" "$(q az keyvault list-deleted --resource-type vault \
	--query "[?starts_with(name, 'kv-$cluster-')].name" -o tsv)"
check "soft-deleted lab Log Analytics workspace" "$(q az monitor log-analytics workspace list-deleted-workspaces \
	--query "[?starts_with(name, 'log-$cluster-')].name" -o tsv)"
check "lab custom role" "$(q az role definition list --custom-role-only true \
	--query "[?starts_with(roleName, '$cluster-r2-responder-')].roleName" -o tsv)"
scopes="$(az role assignment list --all --query '[].scope' -o json 2>/dev/null)" || scopes='["az call failed: role assignment list /resourcegroups/rg-'"$cluster"'-lab"]'
check "role assignments on lab scopes" "$(jq -r --arg l "/resourcegroups/rg-$cluster-lab" \
	'.[] | select(ascii_downcase | contains($l))' <<<"$scopes" | tr '\n' ' ' | sed 's/ *$//')"
check "lab policy definition" "$(q az policy definition list --query "[?name=='$cluster-close-open-ingress'].name" -o tsv)"
check "lab policy assignment" "$(q az policy assignment list --disable-scope-strict-match true \
	--query "[?name=='$cluster-close-open-ingress'].id" -o tsv)"
check "Logic App" "$(q az resource list --resource-type Microsoft.Logic/workflows \
	--query "[?name=='logic-$cluster-r2'].id" -o tsv)"

tier="$(az security pricing show --name Containers --query pricingTier -o tsv 2>/dev/null)"
want_tier="$(jq -r '.containers_tier // "Free"' <<<"$before")"
check "Defender for Containers plan not back on $want_tier" "$([[ "$tier" != "$want_tier" ]] && echo "$tier")"

new_items() { # json-array-now, jq path in before
	jq -r --argjson b "$(jq "$2" <<<"$before")" '.[] | select(. as $x | $b | index($x) | not)' <<<"$1" | tr '\n' ' ' | sed 's/ *$//'
}
check "workflow automations not there before" "$(new_items "$(az security automation list --query '[].name' -o json 2>/dev/null || echo '[]')" .automations)"
check "policy assignments not there before (Defender adds some)" "$(new_items \
	"$(az policy assignment list --disable-scope-strict-match true --query '[].name' -o json)" .policy_assignments)"
check "DefaultResourceGroup-* not there before" "$(new_items \
	"$(az group list --query "[?starts_with(name, 'DefaultResourceGroup-')].name" -o json)" .default_resource_groups)"

if [[ $left -ne 0 ]]; then
	echo "verify-gone: something is LEFT (see docs/runbook-azure.md, teardown)"
	exit 1
fi
echo "verify-gone: nothing left"
