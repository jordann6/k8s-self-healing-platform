#!/usr/bin/env bash
# aks-preflight.sh: read-only checks before the first plan of a session.
# Fails (non-zero) on anything that would make the apply land in the wrong
# subscription, run out of quota mid-row, collide with a leftover, be blocked
# (or be blocked at teardown) by a guardrail, or run into the weekly node image
# window. Never creates or changes anything in Azure.
#
# It also records what exists BEFORE the session (policy assignments,
# Defender's default workspace groups, workflow automations) in
# infra/azure/.session/preflight.json (gitignored), so aks-verify-gone can
# tell what the session created from what was already there (ADR-0007 row 11).
#
#   SESSION_HOURS  how long the session may run (default 6), for the
#                  maintenance window check
#   ALLOW_POLICY_ASSIGNMENTS  space-separated assignment names the operator
#                  has read and accepts (default: none beyond the known ones)
# ok() only prints and always succeeds, so `test && ok || bad` is a true
# if/else in this file.
# shellcheck disable=SC2015
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/azure"
tfvars="$tf/lab.auto.tfvars"
session_dir="$tf/.session"
snapshot="$session_dir/preflight.json"
fail=0
ok() { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; fail=1; }
note() { printf '  note  %s\n' "$*"; }

# Values from lab.auto.tfvars, else the variables.tf default.
tfvar() { # name default
	local v
	v="$(sed -n "s/^$1 *= *\"\([^\"]*\)\".*/\1/p" "$tfvars" 2>/dev/null | head -1)"
	printf '%s' "${v:-$2}"
}

echo "preflight: tools"
for t in az terraform kubectl kubelogin helm cilium hubble jq envsubst docker python3; do
	command -v "$t" >/dev/null 2>&1 && ok "$t" || bad "$t not installed$([[ $t == kubelogin ]] && echo ' (brew install Azure/kubelogin/kubelogin)')"
done

echo "preflight: subscription"
[[ -f "$tfvars" ]] || { echo "  FAIL  $tfvars missing (copy lab.auto.tfvars.example)"; exit 1; }
want_sub="$(tfvar subscription_id "")"
location="$(tfvar location eastus2)"
cluster="$(tfvar cluster_name selfheal)"
got_sub="$(az account show --query id -o tsv 2>/dev/null || true)"
[[ -n "$got_sub" ]] || { echo "  FAIL  az is not signed in (az login)"; exit 1; }
[[ "$got_sub" == "$want_sub" ]] && ok "az is on the lab subscription ($(az account show --query name -o tsv))" ||
	bad "az is on a different subscription than lab.auto.tfvars (az account set -s <lab subscription>)"
arm="https://management.azure.com/subscriptions/$want_sub"

# A cluster in this root's own state is the session in progress (a re-plan
# mid-session), not a leftover; leftover checks then only report.
# (Captured first: grep -q under pipefail would SIGPIPE terraform and fail.)
state="$(terraform -chdir="$tf" state list 2>/dev/null || true)"
in_progress=0
# Anything in this root's state is a session in progress, not only the
# cluster: a failed first apply leaves the rest (found on the first AKS apply,
# which stopped at the cluster), and its re-plan must neither call the lab's
# own resources leftovers nor overwrite the clean before-picture.
[[ -n "$state" ]] && in_progress=1

lab_rg="rg-$cluster-lab"
node_rg="rg-$cluster-lab-nodes"
lower() { tr '[:upper:]' '[:lower:]' <<<"$1"; }
lc_sub="$(lower "$want_sub")"
lc_lab="$(lower "$lab_rg")"
lc_node="$(lower "$node_rg")"
lc_ma="$(lower "ma_amw-$cluster")"
lab_scope() { # resource id -> 0 if it is the subscription itself or a lab group
	local id
	id="$(lower "$1")"
	[[ "$id" == "/subscriptions/$lc_sub" ]] && return 0
	[[ "$id" == */resourcegroups/"$lc_lab" || "$id" == */resourcegroups/"$lc_lab"/* ]] && return 0
	[[ "$id" == */resourcegroups/"$lc_node" || "$id" == */resourcegroups/"$lc_node"/* ]] && return 0
	[[ "$id" == */resourcegroups/"$lc_ma"_* ]] && return 0
	return 1
}

echo "preflight: guardrails that could block the apply or the teardown (ADR-0007 row 2)"
assignments="$(az policy assignment list --disable-scope-strict-match true \
	--query '[].{name:name, scope:scope, mode:enforcementMode}' -o json)"
known="SecurityCenterBuiltIn $cluster-close-open-ingress ${ALLOW_POLICY_ASSIGNMENTS:-}"
if [[ $in_progress -eq 1 && -f "$snapshot" ]]; then
	# Defender for Containers adds its own assignments once the plan is on;
	# mid-session they are expected and verify-gone checks they are removed.
	known="$known $(jq -r '.policy_assignments[]' "$snapshot" | tr '\n' ' ')"
	known="$known $(jq -r '.[].name' <<<"$assignments" | tr '\n' ' ')"
fi
unknown="$(jq -r --arg known " $known " '.[] | .name as $n | select(($known | contains(" " + $n + " ")) | not) | "\($n) at \(.scope)"' <<<"$assignments")"
[[ -z "$unknown" ]] && ok "policy assignments: only known ones ($(jq -r '[.[].name] | join(", ")' <<<"$assignments"))" ||
	bad "policy assignments the lab has not reviewed (read each; ALLOW_POLICY_ASSIGNMENTS='name ...' to accept): $(tr '\n' ';' <<<"$unknown")"

deny="$(az rest --method get --url "$arm/providers/Microsoft.Authorization/denyAssignments?api-version=2022-04-01" \
	--query 'value[].{name:properties.denyAssignmentName, scope:properties.scope}' -o json)"
blocking=""
while IFS=$'\t' read -r name scope; do
	[[ -n "$name" ]] || continue
	if lab_scope "$scope"; then blocking="$blocking $name($scope)"; else note "deny assignment outside the lab: $name"; fi
done < <(jq -r '.[] | [.name, .scope] | @tsv' <<<"$deny")
[[ -z "$blocking" ]] && ok "no deny assignment on the subscription or a lab group" || bad "deny assignments on lab scopes:$blocking"

locks="$(az lock list --query '[].{name:name, id:id, level:level}' -o json)"
blocking=""
while IFS=$'\t' read -r name id; do
	[[ -n "$name" ]] || continue
	# A lock's id is <scope>/providers/Microsoft.Authorization/locks/<name>.
	if lab_scope "${id%/providers/Microsoft.Authorization/locks/*}"; then blocking="$blocking $name"; else note "lock outside the lab: $name"; fi
done < <(jq -r '.[] | [.name, .id] | @tsv' <<<"$locks")
[[ -z "$blocking" ]] && ok "no lock on the subscription or a lab group" || bad "locks on lab scopes:$blocking"

echo "preflight: providers and features (the provider registers nothing itself)"
registered="$(az provider list --query "[?registrationState=='Registered'].namespace" -o tsv)"
for p in Microsoft.ContainerService Microsoft.Compute Microsoft.Network Microsoft.Storage Microsoft.KeyVault \
	Microsoft.ContainerRegistry Microsoft.Monitor Microsoft.Insights Microsoft.OperationalInsights Microsoft.Logic \
	Microsoft.Security Microsoft.PolicyInsights Microsoft.ManagedIdentity Microsoft.Authorization; do
	grep -qix "$p" <<<"$registered" && ok "$p registered" || bad "$p not registered (az provider register -n $p)"
done
eah="$(az feature show --namespace Microsoft.Compute --name EncryptionAtHost --query properties.state -o tsv 2>/dev/null || true)"
[[ "$eah" == "Registered" ]] && ok "EncryptionAtHost registered" || bad "EncryptionAtHost is '$eah'"

echo "preflight: capacity (decision D1)"
usage="$(az vm list-usage -l "$location" -o json)"
lim() { jq -r --arg n "$1" '.[] | select(.name.value == $n) | .limit' <<<"$usage"; }
cur() { jq -r --arg n "$1" '.[] | select(.name.value == $n) | .currentValue' <<<"$usage"; }
# Decision D1 (revised): system pool on DSv3 (already approved, cannot be
# raised), workload pool on Dsv6, regional limit raised to 16. Peak 14.
# spec = quota:minimum limit:free vCPUs needed:what uses it
for spec in "standardDSv3Family:6:6:3 system D2s_v3" "StandardDsv6Family:8:8:4 workload D2s_v6" "cores:16:14:3 system + 4 workload"; do
	IFS=: read -r q min need what <<<"$spec"
	l="$(lim "$q")"
	c="$(cur "$q")"
	if [[ -z "$l" ]]; then
		bad "$q quota not found in $location"
	elif ((l < min)); then
		bad "$q quota is $l in $location; the lab needs $min ($what; decision D1, see the runbook)"
	elif [[ $in_progress -eq 0 ]] && ((l - c < need)); then
		bad "$q: $c of $l in use; the lab needs $need free ($what)"
	else
		ok "$q quota $l, $c in use (lab: $need, $what)"
	fi
done

echo "preflight: weekly node image window"
maint_day="$(tfvar maintenance_day Sunday)"
maint_start="$(tfvar maintenance_start 08:00)"
if python3 -I - "$maint_day" "$maint_start" "${SESSION_HOURS:-6}" <<'PY'; then
import datetime as dt, sys
day, start, hours = sys.argv[1], sys.argv[2], float(sys.argv[3])
now = dt.datetime.now(dt.timezone.utc)
h, m = map(int, start.split(":"))
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
end = now + dt.timedelta(hours=hours)
for week in (-1, 0, 1):
    base = now.replace(hour=h, minute=m, second=0, microsecond=0)
    w0 = base + dt.timedelta(days=(days.index(day) - now.weekday()) + 7 * week)
    w1 = w0 + dt.timedelta(hours=4)
    if w0 < end and now < w1:
        print(f"  the session overlaps the window {w0:%a %H:%M}-{w1:%H:%M} UTC")
        sys.exit(1)
PY
	ok "the next ${SESSION_HOURS:-6}h are clear of the $maint_day $maint_start UTC window"
else
	bad "pick another time, or set SESSION_HOURS shorter"
fi

echo "preflight: Defender and responders (ADR-0007 rows 5 and 15)"
tier="$(az security pricing show --name Containers --query pricingTier -o tsv)" || tier="(unreadable)"
automations="$(az security automation list --query '[].name' -o tsv)" || { bad "could not list workflow automations"; automations="?"; }
others="$(grep -vx "auto-$cluster-r2" <<<"$automations" || true)"
[[ -z "$others" ]] && ok "no other workflow automation can act on lab alerts" ||
	bad "workflow automations that are not the lab's: $(tr '\n' ' ' <<<"$others")"

echo "preflight: leftovers from an earlier session"
if [[ $in_progress -eq 1 ]]; then
	ok "session in progress (this root's state is not empty): re-plan, leftover checks skipped"
	note "Containers plan is $tier"
else
	[[ "$tier" == "Free" ]] && ok "Containers plan is Free" || bad "Containers plan is '$tier' before the session"
	[[ -z "$automations" ]] && ok "no lab workflow automation" || bad "workflow automation left from an earlier session: $automations"
	groups="$(az group list --query "[?name=='$lab_rg' || name=='$node_rg' || starts_with(name, 'MA_amw-$cluster')].name" -o tsv)"
	[[ -z "$groups" ]] && ok "no lab resource groups" || bad "lab resource groups exist outside this root's state: $groups"
	vaults="$(az keyvault list-deleted --resource-type vault --query "[?starts_with(name, 'kv-$cluster-')].name" -o tsv)" || vaults="(could not list deleted vaults)"
	[[ -z "$vaults" ]] && ok "no soft-deleted lab vault" || bad "soft-deleted lab vaults (purge them): $vaults"
	roles="$(az role definition list --custom-role-only true --query "[?starts_with(roleName, '$cluster-r2-responder-')].roleName" -o tsv)"
	[[ -z "$roles" ]] && ok "no lab custom role" || bad "lab custom roles left: $roles"
	def="$(az policy definition list --query "[?name=='$cluster-close-open-ingress'].name" -o tsv)"
	[[ -z "$def" ]] && ok "no lab policy definition" || bad "lab policy definition left: $def"
	snaps="$(az snapshot list --query "[?starts_with(name, 'snap-r2-')].id" -o tsv)"
	[[ -z "$snaps" ]] && ok "no evidence snapshots" || bad "evidence snapshots from an earlier session: $snaps"
fi

echo "preflight: operator address"
ip="$(curl -4 -s --max-time 5 ifconfig.me || true)"
[[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && ok "IPv4 found, not shown (API server, Key Vault and storage admit only it)" ||
	bad "no IPv4 address from ifconfig.me"

if [[ $fail -ne 0 ]]; then
	echo "preflight: FAILED"
	exit 1
fi

# The before-picture for verify-gone. Kept across re-plans of the same
# session; a new session (empty state) records a fresh one.
if [[ $in_progress -eq 0 || ! -f "$snapshot" ]]; then
	mkdir -p "$session_dir"
	jq -n --arg at "$(date -u +%FT%TZ)" \
		--argjson pa "$(jq '[.[].name]' <<<"$assignments")" \
		--argjson rg "$(az group list --query "[?starts_with(name, 'DefaultResourceGroup-')].name" -o json)" \
		--argjson au "$(az security automation list --query '[].name' -o json 2>/dev/null || echo '[]')" \
		--arg tier "$tier" \
		'{recorded_at: $at, policy_assignments: $pa, default_resource_groups: $rg, automations: $au, containers_tier: $tier}' >"$snapshot"
	echo "preflight: recorded the before-picture in infra/azure/.session/preflight.json"
fi
echo "preflight: ok"
