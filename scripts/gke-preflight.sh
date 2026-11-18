#!/usr/bin/env bash
# gke-preflight.sh: read-only checks before any GKE plan (ADR-0027). Fails
# closed on anything the docs pass found would break a session or its
# teardown. Nothing here creates, changes or deletes a resource.
#
#   1. gcloud signed in; the lab project from infra/gcp/lab.auto.tfvars exists,
#      is ACTIVE, sits under the organization and has billing on (D1)
#   2. the landing zone teardown has finished: no jn-lz-* project ACTIVE
#      except the seed (Jordan's precondition, 2026-11-18)
#   3. org policies: nothing beyond Google's six defaults (row 2); no custom
#      constraints; no lien on the lab project
#   4. CPU quotas (regional, global, E2) cover 7 nodes plus one surge (16
#      vCPUs, L7) and SSD_TOTAL_GB covers 8 pd-balanced boot disks (400 GB)
#   5. the REGULAR channel offers the pinned minor (variables.tf)
#   6. no responder in the lab project that could act on lab resources
#      (row 15): Eventarc triggers, Pub/Sub push subscriptions, Cloud Run,
#      Cloud Functions
#   7. a before-picture of instances, disks, firewall rules, routes,
#      forwarding rules, NEGs and routers, for gke-verify-gone
# The project ID is printed masked (it is kept out of the public repo).
# ok() always returns 0, so `test && ok || bad` is an if-then-else here.
# shellcheck disable=SC2015
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/gcp"
vars="$tf/lab.auto.tfvars"
fail=0
bad() { echo "  FAIL  $*"; fail=1; }
ok() { echo "  ok    $*"; }

gcloud auth print-access-token >/dev/null 2>&1 || { echo "gke-preflight: gcloud is not signed in (gcloud auth login)" >&2; exit 1; }
[[ -f "$vars" ]] || { echo "gke-preflight: $vars is missing (copy lab.auto.tfvars.example; project from infra/gcp-project)" >&2; exit 1; }
project="$(sed -n 's/^project_id *= *"\(.*\)".*/\1/p' "$vars")"
[[ -n "$project" ]] || { echo "gke-preflight: no project_id in $vars" >&2; exit 1; }
masked="${project:0:12}***"
region="$(terraform -chdir="$tf" console <<<'var.region' 2>/dev/null | tr -d '"' || true)"
region="${region:-us-central1}"
minor="$(sed -n '/variable "kubernetes_version"/,/^}/s/.*default *= *"\(.*\)".*/\1/p' "$tf/variables.tf")"

echo "gke-preflight: project $masked, region $region, Kubernetes $minor"

echo "1. project and billing"
p="$(gcloud projects describe "$project" --format=json 2>/dev/null || echo '{}')"
[[ "$(jq -r '.lifecycleState // empty' <<<"$p")" == "ACTIVE" ]] && ok "project ACTIVE" || bad "project is not ACTIVE (run the infra/gcp-project root first)"
[[ "$(jq -r '.parent.type // empty' <<<"$p")" == "organization" ]] && ok "parent is the organization" ||
	bad "project parent is $(jq -r '.parent.type // "unknown"' <<<"$p"), expected organization"
# Three reads: one transient failure read as "not enabled" on 2026-11-18
# between two reads that both said True.
billing=""
for _ in 1 2 3; do
	billing="$(gcloud billing projects describe "$project" --format='value(billingEnabled)' 2>/dev/null </dev/null || true)"
	[[ "$billing" == "True" ]] && break
	sleep 5
done
[[ "$billing" == "True" ]] && ok "billing enabled" || bad "billing is not enabled on the project (read: ${billing:-error})"

echo "2. landing zone teardown"
lz="$(gcloud projects list --filter='projectId:jn-lz-* AND lifecycleState=ACTIVE' --format='value(projectId)' 2>/dev/null |
	grep -v -- '-seed-' || true)"
[[ -z "$lz" ]] && ok "no landing zone project ACTIVE (seed excepted)" ||
	bad "landing zone projects still ACTIVE: $(tr '\n' ' ' <<<"$lz")(teardown not finished)"

echo "3. org policy, constraints, liens"
org="$(jq -r '.parent.id // empty' <<<"$p")"
if [[ -n "$org" ]]; then
	defaults='compute.restrictProtocolForwardingCreationForTypes compute.setNewProjectDefaultToZonalDNSOnly iam.automaticIamGrantsForDefaultServiceAccounts iam.disableServiceAccountKeyCreation iam.disableServiceAccountKeyUpload storage.uniformBucketLevelAccess'
	extra=""
	for c in $(gcloud org-policies list --organization="$org" --format='value(constraint)' 2>/dev/null | sed 's#.*/##'); do
		grep -qw -- "$c" <<<"$defaults" || extra="$extra $c"
	done
	[[ -z "$extra" ]] && ok "only Google's default org policies" || bad "org policies beyond the defaults:$extra (re-read ADR-0027 row 2)"
	cc="$(gcloud org-policies list-custom-constraints --organization="$org" --format='value(name)' 2>/dev/null || true)"
	[[ -z "$cc" ]] && ok "no custom constraints" || bad "custom constraints present: $(wc -l <<<"$cc" | tr -d ' ')"
fi
# Liens have no GA gcloud command; the Resource Manager API is read directly.
liens="$(curl -fsS -H "Authorization: Bearer $(gcloud auth print-access-token)" \
	"https://cloudresourcemanager.googleapis.com/v3/liens?parent=projects/$project" 2>/dev/null | jq -r '.liens // [] | length' || echo error)"
[[ "$liens" == "0" ]] && ok "no liens on the project" || bad "liens on the project: $liens (would block teardown)"

echo "4. quotas"
q="$(gcloud compute regions describe "$region" --project="$project" --format=json 2>/dev/null |
	jq -r '.quotas[] | select(.metric == "CPUS") | "\(.limit) \(.usage)"' || true)"
if [[ -n "$q" ]]; then
	read -r limit usage <<<"$q"
	free=$(awk -v l="$limit" -v u="$usage" 'BEGIN { print int(l - u) }')
	((free >= 16)) && ok "CPUS free $free of ${limit%.*} (need 16)" || bad "CPUS free $free, need 16 (7 nodes + 1 surge)"
else
	bad "could not read the regional CPUS quota"
fi
# A new project also carries a global CPU cap and per-family caps, and
# pd-balanced boot disks count against SSD_TOTAL_GB (8 x 50 GB with a surge
# node). Found reading the new lab project's quotas, 2026-11-18.
quota_free() { # json, metric -> free (integer) or empty
	jq -r --arg m "$2" '.quotas[] | select(.metric == $m) | (.limit - .usage | floor)' <<<"$1"
}
need() { # label, free, wanted
	if [[ -z "$2" ]]; then bad "$1 quota unreadable"; elif (($2 >= $3)); then ok "$1 free $2 (need $3)"; else bad "$1 free $2, need $3"; fi
}
reg="$(gcloud compute regions describe "$region" --project="$project" --format=json 2>/dev/null || echo '{"quotas":[]}')"
glob="$(gcloud compute project-info describe --project="$project" --format=json 2>/dev/null || echo '{"quotas":[]}')"
need CPUS_ALL_REGIONS "$(quota_free "$glob" CPUS_ALL_REGIONS)" 16
need E2_CPUS "$(quota_free "$reg" E2_CPUS)" 16
need SSD_TOTAL_GB "$(quota_free "$reg" SSD_TOTAL_GB)" 400

echo "5. GKE versions"
offered="$(gcloud container get-server-config --location="$region" --project="$project" --format=json 2>/dev/null |
	jq -r '.channels[] | select(.channel == "REGULAR") | .validVersions[]' || true)"
grep -q "^$minor\." <<<"$offered" && ok "REGULAR offers $minor ($(grep "^$minor\." <<<"$offered" | head -1))" ||
	bad "REGULAR channel does not offer $minor (offered: $(head -3 <<<"$offered" | tr '\n' ' '))"

echo "6. responders in the lab project"
for kind in "eventarc triggers list --location=-" "pubsub subscriptions list" "run services list" "functions list"; do
	# shellcheck disable=SC2086
	# --quiet and no stdin: with an API off, gcloud otherwise asks whether to
	# enable it and waits forever (seen 2026-11-18 on eventarc).
	out="$(gcloud $kind --project="$project" --format=json --quiet 2>&1 </dev/null)" || {
		grep -qiE 'not enabled|has not been used|SERVICE_DISABLED' <<<"$out" && { ok "${kind%% *}: API off, none"; continue; }
		bad "${kind%% *}: could not list ($(head -1 <<<"$out" | cut -c1-80))"
		continue
	}
	if [[ "$kind" == pubsub* ]]; then
		n="$(jq '[.[] | select(.pushConfig.pushEndpoint)] | length' <<<"$out")"
	else
		n="$(jq 'length' <<<"$out")"
	fi
	((n == 0)) && ok "${kind%% *}: none" || bad "${kind%% *}: $n present that could act on lab resources"
done

echo "7. before-picture"
mkdir -p "$tf/.session"
snap() { gcloud compute "$@" --project="$project" --format='value(name)' 2>/dev/null | sort | jq -R . | jq -s . ; }
jq -n --arg at "$(date -u +%FT%TZ)" \
	--argjson instances "$(snap instances list)" --argjson disks "$(snap disks list)" \
	--argjson firewalls "$(snap firewall-rules list)" --argjson routes "$(snap routes list)" \
	--argjson forwarding "$(snap forwarding-rules list)" --argjson negs "$(snap network-endpoint-groups list)" \
	--argjson routers "$(snap routers list)" \
	'{recorded_at: $at, instances: $instances, disks: $disks, firewalls: $firewalls, routes: $routes,
	  forwarding: $forwarding, negs: $negs, routers: $routers}' >"$tf/.session/preflight.json"
ok "written to infra/gcp/.session/preflight.json"

((fail == 0)) || { echo "gke-preflight: FAIL"; exit 1; }
echo "gke-preflight: all checks passed"
