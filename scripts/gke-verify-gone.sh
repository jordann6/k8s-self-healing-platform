#!/usr/bin/env bash
# gke-verify-gone.sh: read-only proof that nothing from the session survived.
# Every line must say "gone". Covers the GKE teardown gotchas (ADR-0027 row
# 11): the cluster and its node VMs and MIGs, persistent disks GKE retains,
# firewall rules and routes GKE created, forwarding rules and NEGs a Service
# could leave, the Cloud NAT router, the registry, the node service account,
# and anything compute-side that was not there before the session (the
# preflight's before-picture). Values are read from Google Cloud, never from
# Terraform state. The project itself stays (ADR-0027, D1).
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
vars="$root/infra/gcp/lab.auto.tfvars"
snapshot="$root/infra/gcp/.session/preflight.json"
cluster="${CLUSTER_NAME:-selfheal}"
region="${REGION:-us-central1}"
project="$(sed -n 's/^project_id *= *"\(.*\)".*/\1/p' "$vars" 2>/dev/null)"
[[ -n "$project" ]] || { echo "verify-gone: no project_id in $vars" >&2; exit 1; }
left=0

check() { # name, value (empty = gone)
	if [[ -z "$2" ]]; then
		printf '  gone     %s\n' "$1"
	else
		printf '  LEFT     %s: %s\n' "$1" "$2"
		left=1
	fi
}
# A failed query prints a LEFT line, never an empty (gone) one.
q() {
	local o
	o="$("$@" --project="$project" 2>/dev/null)" || { echo "gcloud call failed: ${*:1:4}"; return; }
	tr '\t\n' '  ' <<<"$o" | sed 's/ *$//'
}
# Names that were not in the before-picture.
new() { # snapshot key, gcloud compute args...
	local key="$1" now
	shift
	now="$(gcloud compute "$@" --project="$project" --format='value(name)' 2>/dev/null)" || { echo "gcloud call failed: compute $1"; return; }
	if [[ -f "$snapshot" ]]; then
		comm -13 <(jq -r --arg k "$key" '.[$k][]' "$snapshot" | sort) <(sort <<<"$now") | tr '\n' ' ' | sed 's/ *$//'
	else
		tr '\n' ' ' <<<"$now" | sed 's/ *$//'
	fi
}

gcloud auth print-access-token >/dev/null 2>&1 || { echo "verify-gone: gcloud is not signed in"; exit 1; }
[[ -f "$snapshot" ]] && echo "verify-gone (before-picture from $(jq -r .recorded_at "$snapshot"))" ||
	echo "verify-gone (no before-picture: every compute resource in the project counts)"

check "GKE cluster $cluster" "$(q gcloud container clusters list --filter="name=$cluster" --format='value(name)')"
check "instances (new)" "$(new instances instances list)"
check "instance groups" "$(q gcloud compute instance-groups managed list --filter="name~^gke-$cluster-" --format='value(name)')"
check "disks (new, incl. retained PVs)" "$(new disks disks list)"
check "firewall rules (new)" "$(new firewalls firewall-rules list)"
check "routes (new, non-default)" "$(new routes routes list --filter='NOT name~^default-route')"
check "forwarding rules (new)" "$(new forwarding forwarding-rules list)"
check "network endpoint groups (new)" "$(new negs network-endpoint-groups list)"
check "routers and NAT (new)" "$(new routers routers list)"
check "lab VPC" "$(q gcloud compute networks list --filter="name=$cluster-vpc" --format='value(name)')"
check "Artifact Registry repository" "$(q gcloud artifacts repositories list --location="$region" --filter="name~/$cluster$" --format='value(name)')"
check "node service account" "$(q gcloud iam service-accounts list --filter="email~^$cluster-nodes@" --format='value(email)')"

if ((left)); then
	echo "verify-gone: something is LEFT (see above)"
	exit 1
fi
echo "verify-gone: nothing left"
