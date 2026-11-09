#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
baseline="$(kubectl kustomize "$root/gitops/o1/baseline")"
hardened="$(kubectl kustomize "$root/gitops/o1/hardened")"
grep -q 'name: o1-target' <<<"$baseline"
grep -q "  - '\*'" <<<"$baseline"
if grep -q 'kind: ResourceQuota' <<<"$baseline" || grep -q 'name: o1-controller' <<<"$baseline"; then
	echo "O1 baseline unexpectedly contains hardened readiness controls" >&2
	exit 1
fi
grep -q 'kind: ResourceQuota' <<<"$hardened"
grep -q 'kind: LimitRange' <<<"$hardened"
grep -q 'resourceNames:' <<<"$hardened"
grep -q 'name: o1-controller' <<<"$hardened"
grep -q 'name: o1-alert-receiver' <<<"$hardened"
echo "O1 profiles render with quota, least privilege, alerting, and release controls isolated to hardened"
