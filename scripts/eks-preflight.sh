#!/usr/bin/env bash
# eks-preflight.sh: read-only checks before the first plan of a session.
# Fails (non-zero) on anything that would make the apply create resources in
# the wrong place, collide with something that already exists, or leave the
# teardown unable to finish. Never creates or changes anything.
#
#   MGMT_PROFILE  credentials Terraform starts from (it assumes into the lab
#                 and security accounts)
#   LAB_PROFILE   credentials for the lab account itself (AWS CLI, runner)
# ok() only prints and always succeeds, so `test && ok || bad` is a true
# if/else in this file.
# shellcheck disable=SC2015
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tfvars="$root/infra/aws/lab.auto.tfvars"
region="${AWS_REGION:-us-east-1}"
fail=0
ok() { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; fail=1; }

: "${MGMT_PROFILE:?set MGMT_PROFILE (Terraform base credentials)}"
: "${LAB_PROFILE:?set LAB_PROFILE (lab account credentials)}"

echo "preflight: accounts"
[[ -f "$tfvars" ]] || { echo "  FAIL  $tfvars missing (copy lab.auto.tfvars.example)"; exit 1; }
lab_id="$(sed -n 's/^lab_account_id *= *"\([0-9]*\)".*/\1/p' "$tfvars")"
sec_id="$(sed -n 's/^security_account_id *= *"\([0-9]*\)".*/\1/p' "$tfvars")"
[[ -n "$lab_id" && -n "$sec_id" ]] || bad "lab_account_id / security_account_id not set in lab.auto.tfvars"
got="$(AWS_PROFILE="$LAB_PROFILE" aws sts get-caller-identity --query Account --output text)"
[[ "$got" == "$lab_id" ]] && ok "LAB_PROFILE is the lab account" || bad "LAB_PROFILE is $got, expected $lab_id"
AWS_PROFILE="$MGMT_PROFILE" aws sts get-caller-identity --query Arn --output text >/dev/null &&
	ok "MGMT_PROFILE has credentials" || bad "MGMT_PROFILE has no working credentials"

export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="$region"

echo "preflight: one Config recorder per region"
recorders="$(aws configservice describe-configuration-recorders --query 'ConfigurationRecorders[].name' --output text)"
case "$recorders" in
"" | "None") ok "no recorder yet; the lab creates selfheal-recorder" ;;
"selfheal-recorder") ok "only the lab's own recorder (a previous session's state is still applied?)" ;;
*) bad "another recorder exists ($recorders): the lab cannot create its own; see docs/runbook-aws.md" ;;
esac

echo "preflight: GuardDuty"
det="$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text)"
if [[ "$det" == "None" || -z "$det" ]]; then
	bad "no GuardDuty detector in the lab account; the organization enrols members, check the security account"
else
	admin="$(aws guardduty get-administrator-account --detector-id "$det" --query 'Administrator.AccountId' --output text 2>/dev/null || true)"
	[[ "$admin" == "$sec_id" ]] && ok "member of the delegated administrator $sec_id" ||
		bad "administrator is '$admin', expected the security account $sec_id"
	rm_status="$(aws guardduty get-detector --detector-id "$det" --query "Features[?Name=='RUNTIME_MONITORING'].Status | [0]" --output text)"
	ok "Runtime Monitoring is $rm_status now (Terraform enables it)"
fi

echo "preflight: leftovers from an earlier session"
# A cluster in this root's own state is the session in progress (a re-plan
# mid-session), not a leftover; the checks below then only report.
# (Captured first: grep -q under pipefail would SIGPIPE terraform and fail.)
state="$(AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" state list 2>/dev/null || true)"
if grep -qx 'module.eks.aws_eks_cluster.this\[0\]' <<<"$state"; then
	ok "session in progress (the cluster is in this root's state): re-plan, leftover checks skipped"
else
	[[ -z "$(aws eks list-clusters --query "clusters[?@=='selfheal']" --output text)" ]] &&
		ok "no selfheal cluster" || bad "a selfheal cluster already exists"
	eps="$(aws ec2 describe-vpc-endpoints --filters "Name=service-name,Values=com.amazonaws.$region.guardduty-data" \
		--query 'VpcEndpoints[].VpcEndpointId' --output text)"
	[[ -z "$eps" ]] && ok "no GuardDuty agent endpoints" || bad "GuardDuty endpoints still exist: $eps"
	snaps="$(aws ec2 describe-snapshots --owner-ids self --filters Name=tag:selfheal.lab/managed,Values=true \
		--query 'Snapshots[].SnapshotId' --output text)"
	[[ -z "$snaps" ]] && ok "no evidence snapshots" || bad "evidence snapshots from an earlier session: $snaps"
fi

echo "preflight: capacity"
vcpu="$(aws service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A --query 'Quota.Value' --output text)"
awk -v v="$vcpu" 'BEGIN { exit !(v >= 24) }' && ok "on-demand vCPU quota $vcpu (the lab peaks near 20)" ||
	bad "on-demand vCPU quota $vcpu is below 24"
conc="$(aws lambda get-account-settings --query 'AccountLimit.ConcurrentExecutions' --output text)"
ok "Lambda concurrency quota $conc (no reservations are made)"

echo "preflight: operator address"
ip="$(curl -4 -s --max-time 5 ifconfig.me || true)"
[[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && ok "IPv4 $ip (the API endpoint is scoped to $ip/32)" ||
	bad "no IPv4 address from ifconfig.me ('$ip')"

if [[ $fail -ne 0 ]]; then
	echo "preflight: FAILED"
	exit 1
fi
echo "preflight: ok"
