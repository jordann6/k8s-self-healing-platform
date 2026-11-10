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

# ADR-0022: the cloud overlays carry no MinIO and no static store keys, use the cloud tool
# image and a service account, and put the PostgreSQL volumes on the class the cloud serves.
for cloud in eks aks; do
	cloud_baseline="$(kubectl kustomize "$root/gitops/targets/$cloud/d1/baseline")"
	cloud_hardened="$(kubectl kustomize "$root/gitops/targets/$cloud/d1/hardened")"
	for doc in "$cloud_baseline" "$cloud_hardened"; do
		if grep -qiE 'minio|S3_ACCESS_KEY|S3_SECRET_KEY|d1-backup-store|selfheal-d1-store' <<<"$doc"; then
			echo "D1 $cloud overlay still references the kind store or static keys" >&2
			exit 1
		fi
		grep -q 'name: d1-source' <<<"$doc"
	done
	if grep -q 'kind: CronJob' <<<"$cloud_baseline"; then
		echo "D1 $cloud baseline unexpectedly contains a backup CronJob" >&2
		exit 1
	fi
	grep -q 'kind: CronJob' <<<"$cloud_hardened"
	grep -q 'name: d1-restore' <<<"$cloud_hardened"
	grep -q 'suspend: true' <<<"$cloud_hardened"
	grep -q 'serviceAccountName: d1-backup' <<<"$cloud_hardened"
	grep -q 'name: d1-store' <<<"$cloud_hardened"
	grep -q 'image: selfheal-d1-cloud' <<<"$cloud_hardened"
done
kubectl kustomize "$root/gitops/targets/eks/d1/hardened" | grep -q 'storageClassName: gp3'
kubectl kustomize "$root/gitops/targets/aks/d1/hardened" | grep -q 'azure.workload.identity/use: "true"'
if kubectl kustomize "$root/gitops/targets/aks/d1/hardened" | grep -q 'storageClassName'; then
	echo "D1 aks overlay should use the default managed-csi class" >&2
	exit 1
fi
echo "D1 cloud overlays render without the kind store, with workload identity and the cloud storage class"
