#!/usr/bin/env bash
# eks-verify-gone.sh: read-only proof that nothing from the session survived.
# Every line must say "gone" (or "kept", for the Config items the organization's
# SCP protects). Covers the teardown gotchas: GuardDuty Runtime
# Monitoring off AND its VPC endpoint gone, the lab's Config recorder, rule and
# remediation, AMP, Grafana, FIS templates, evidence snapshots, KMS keys.
# Values are read from AWS, never from Terraform state or outputs: after a
# destroy there are none to read.
set -uo pipefail
: "${LAB_PROFILE:?set LAB_PROFILE (lab account credentials)}"
export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="${AWS_REGION:-us-east-1}"
r="$AWS_REGION"
left=0

# The organization's SCP (deny-disable-detective, attached at the root) denies
# deleting or stopping Config recorders, delivery channels and rules in every
# member account, so the lab's own Config recorder, rule, delivery bucket and
# the key that encrypts it stay by design (ADR-0006). They are reported as
# "kept", by exact name; anything else of the lab's that survives is LEFT.
kept() { # name, value (empty = already gone)
	if [[ -z "$2" || "$2" == "None" ]]; then
		printf '  gone     %s
' "$1"
	else
		printf '  kept     %s: %s (org SCP keeps Config in member accounts)\n' "$1" "$2"
	fi
}

check() { # name, value (empty = gone)
	if [[ -z "$2" || "$2" == "None" ]]; then
		printf '  gone     %s\n' "$1"
	else
		printf '  LEFT     %s: %s\n' "$1" "$2"
		left=1
	fi
}
q() { "$@" 2>/dev/null | tr '\t\n' '  ' | sed 's/ *$//'; }

echo "verify-gone ($r)"
check "EKS cluster" "$(q aws eks list-clusters --query "clusters[?@=='selfheal']" --output text)"
check "running lab instances" "$(q aws ec2 describe-instances --filters Name=tag:Project,Values=k8s-self-healing-platform \
	Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)"
check "Karpenter instances" "$(q aws ec2 describe-instances --filters Name=tag:karpenter.sh/discovery,Values=selfheal \
	Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)"
check "NAT gateways" "$(q aws ec2 describe-nat-gateways --filter Name=tag:Project,Values=k8s-self-healing-platform \
	Name=state,Values=available,pending --query 'NatGateways[].NatGatewayId' --output text)"
check "lab VPC" "$(q aws ec2 describe-vpcs --filters Name=tag:Project,Values=k8s-self-healing-platform --query 'Vpcs[].VpcId' --output text)"
check "Cilium ENIs" "$(q aws ec2 describe-network-interfaces --filters Name=tag:cilium-managed,Values=true \
	--query 'NetworkInterfaces[].NetworkInterfaceId' --output text)"

det="$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text 2>/dev/null)"
rm="$(aws guardduty get-detector --detector-id "$det" \
	--query "Features[?Name=='RUNTIME_MONITORING'].Status | [0]" --output text 2>/dev/null)"
check "GuardDuty Runtime Monitoring enabled" "$([[ "$rm" == "ENABLED" ]] && echo ENABLED)"
check "GuardDuty agent VPC endpoints" "$(q aws ec2 describe-vpc-endpoints \
	--filters "Name=service-name,Values=com.amazonaws.$r.guardduty-data" --query 'VpcEndpoints[].VpcEndpointId' --output text)"
check "GuardDuty managed security groups" "$(q aws ec2 describe-security-groups \
	--filters Name=group-name,Values='GuardDutyManagedSecurityGroup*' --query 'SecurityGroups[].GroupId' --output text)"

kept "Config recorder (lab)" "$(q aws configservice describe-configuration-recorders \
	--query "ConfigurationRecorders[?name=='selfheal-recorder'].name" --output text)"
kept "Config rule" "$(q aws configservice describe-config-rules \
	--query "ConfigRules[?ConfigRuleName=='selfheal-sg-open-to-internet'].ConfigRuleName" --output text)"
check "SSM remediation document" "$(q aws ssm list-documents --filters Key=Owner,Values=Self \
	Key=Name,Values=selfheal-RevokePublicIngress --query 'DocumentIdentifiers[].Name' --output text)"

check "AMP workspace" "$(q aws amp list-workspaces --alias selfheal --query 'workspaces[].workspaceId' --output text)"
check "Managed Grafana" "$(q aws grafana list-workspaces --query "workspaces[?name=='selfheal'].id" --output text)"
check "FIS templates" "$(q aws fis list-experiment-templates \
	--query "experimentTemplates[?tags.Project=='k8s-self-healing-platform'].id" --output text)"
check "running FIS experiments" "$(q aws fis list-experiments \
	--query "experiments[?state.status=='running' && tags.Project=='k8s-self-healing-platform'].id" --output text)"
check "evidence snapshots" "$(q aws ec2 describe-snapshots --owner-ids self \
	--filters Name=tag:selfheal.lab/managed,Values=true --query 'Snapshots[].SnapshotId' --output text)"
check "RDS instance" "$(q aws rds describe-db-instances \
	--query "DBInstances[?DBInstanceIdentifier=='selfheal'].DBInstanceStatus" --output text)"
check "Secrets Manager secrets" "$(q aws secretsmanager list-secrets \
	--filters Key=name,Values=selfheal/ --query 'SecretList[].Name' --output text)"
check "Lambda functions" "$(q aws lambda list-functions \
	--query "Functions[?starts_with(FunctionName, 'selfheal-')].FunctionName" --output text)"
check "Step Functions" "$(q aws stepfunctions list-state-machines \
	--query "stateMachines[?starts_with(name, 'selfheal-')].name" --output text)"
check "ECR repositories" "$(q aws ecr describe-repositories \
	--query "repositories[?starts_with(repositoryName, 'selfheal/')].repositoryName" --output text)"
check "lab buckets" "$(q aws s3api list-buckets \
	--query "Buckets[?starts_with(Name, 'selfheal-') && !starts_with(Name, 'selfheal-config-')].Name" --output text)"
config_bucket="$(q aws s3api list-buckets --query "Buckets[?starts_with(Name, 'selfheal-config-')].Name" --output text)"
kept "Config delivery bucket" "$config_bucket"
# The delivery channel encrypts what it writes with the lab's data key, so
# that key stays with it.
config_key="$(q aws configservice describe-delivery-channels \
	--query "DeliveryChannels[?name=='selfheal-delivery'].s3KmsKeyArn" --output text)"

# Keys cannot be deleted at once: scheduled deletion (7 days) is the expected end
# state, an enabled key is not. Found by tag, because the aliases go first.
enabled=""
for k in $(aws resourcegroupstaggingapi get-resources --resource-type-filters kms:key \
	--tag-filters Key=Project,Values=k8s-self-healing-platform --query 'ResourceTagMappingList[].ResourceARN' \
	--output text 2>/dev/null); do
	st="$(aws kms describe-key --key-id "$k" --query 'KeyMetadata.KeyState' --output text 2>/dev/null)"
	if [[ -n "$config_key" && "${config_key##*/}" == "${k##*/}" ]]; then
		kept "KMS key of the Config delivery channel" "${k##*/}($st)"
		continue
	fi
	[[ "$st" != "PendingDeletion" ]] && enabled="$enabled ${k##*/}($st)"
done
check "KMS keys not pending deletion" "${enabled# }"

# Informational: anything else still tagged for the project. The tagging API
# lags deletes by minutes, so this line does not fail the check.
others="$(aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=k8s-self-healing-platform \
	--query 'ResourceTagMappingList[].ResourceARN' --output text 2>/dev/null | tr '\t' '\n' | grep -v ':kms:' | head -20)"
if [[ -n "$others" ]]; then
	echo "  note     still tagged (may be tagging-API lag):"
	while read -r arn; do printf '             %s\n' "$arn"; done <<<"$others"
fi

if [[ $left -ne 0 ]]; then
	echo "verify-gone: something is LEFT (see docs/runbook-aws.md, teardown)"
	exit 1
fi
echo "verify-gone: nothing left running"
