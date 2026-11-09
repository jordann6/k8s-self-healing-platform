#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
baseline="$(kubectl kustomize "$root/gitops/d1/baseline")"
hardened="$(kubectl kustomize "$root/gitops/d1/hardened")"
grep -q 'name: d1-source' <<<"$baseline"
grep -q 'name: d1-minio' <<<"$baseline"
grep -q 's3:GetBucketObjectLockConfiguration' <<<"$baseline"
if grep -q 'kind: CronJob' <<<"$baseline"; then
	echo "D1 baseline unexpectedly contains a backup CronJob" >&2
	exit 1
fi
grep -q 'kind: CronJob' <<<"$hardened"
grep -q 'name: d1-restore' <<<"$hardened"
grep -q 'suspend: true' <<<"$hardened"
echo "D1 profiles render with backup and restore isolated to hardened"
