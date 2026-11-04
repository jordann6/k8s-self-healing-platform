#!/usr/bin/env bash
# eks-predestroy.sh: clear what Terraform does not own, so the destroy can
# finish and nothing survives it. Run by the operator (make eks-predestroy)
# AFTER the runtime-off apply and BEFORE the destroy plan:
#
#   1. make eks-runtime-off-plan, apply it   GuardDuty Runtime Monitoring off.
#      The Terraform resource's delete is a no-op (it only leaves state), so
#      without this apply the feature would stay on after the destroy.
#   2. make eks-predestroy                    this script
#   3. make eks-destroy-plan, apply it
#   4. make eks-verify-gone
#
# What this script removes, in order:
#   Argo CD apps (stop syncing), termination protection the R2 responder set,
#   Karpenter's nodes (delete the NodePool and wait, so no instance outlives
#   its controller), Karpenter, Cilium (releases its ENIs), the GuardDuty agent
#   add-on and VPC endpoint if GuardDuty left them, and the evidence snapshots
#   (the runner copied the evidence manifest into the repo).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${LAB_PROFILE:?set LAB_PROFILE}" "${KUBECONFIG:?run through make}"
export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="${AWS_REGION:-us-east-1}"
r="$AWS_REGION"
cluster=selfheal

det="$(aws guardduty list-detectors --query 'DetectorIds[0]' --output text)"
rm_status="$(aws guardduty get-detector --detector-id "$det" \
	--query "Features[?Name=='RUNTIME_MONITORING'].Status | [0]" --output text)"
if [[ "$rm_status" == "ENABLED" ]]; then
	echo "eks-predestroy: Runtime Monitoring is still ENABLED. Apply the runtime-off plan first (make eks-runtime-off-plan)." >&2
	exit 1
fi

if kubectl version --request-timeout=10s >/dev/null 2>&1; then
	echo "eks-predestroy: stopping Argo CD sync"
	kubectl -n argocd delete application selfheal-root selfheal-workload --ignore-not-found --wait=false >/dev/null || true

	echo "eks-predestroy: lifting termination protection on lab instances"
	for i in $(aws ec2 describe-instances --filters Name=tag:karpenter.sh/discovery,Values="$cluster" \
		Name=instance-state-name,Values=pending,running,stopping,stopped \
		--query 'Reservations[].Instances[].InstanceId' --output text); do
		aws ec2 modify-instance-attribute --instance-id "$i" --no-disable-api-termination
		echo "  $i"
	done
	kubectl annotate nodes --all karpenter.sh/do-not-disrupt- >/dev/null 2>&1 || true

	echo "eks-predestroy: draining the workload pool through Karpenter"
	# The lab app goes first: with its PDB (minAvailable 2) in place and the
	# NodePool deleted, a replacement replica can never schedule, the PDB
	# allows 0 disruptions, and Karpenter's drain blocks (first EKS session:
	# Karpenter was then removed and two nodes were stranded, running).
	kubectl -n selfheal delete rollout,deployment,pdb --all --wait=false >/dev/null 2>&1 || true
	kubectl delete nodepool --all --wait=false >/dev/null 2>&1 || true
	for _ in $(seq 1 60); do
		[[ -z "$(kubectl get nodeclaims -o name 2>/dev/null)" ]] && break
		sleep 10
	done
	left="$(kubectl get nodeclaims -o name 2>/dev/null || true)"
	if [[ -n "$left" ]]; then
		# Never remove Karpenter while it still owns instances: nothing else
		# would terminate them, and their ENIs block the VPC delete.
		echo "eks-predestroy: FAIL, Karpenter has not terminated: $left" >&2
		echo "  inspect with: kubectl get nodeclaims; kubectl -n kube-system logs deploy/karpenter" >&2
		exit 1
	fi
	kubectl delete ec2nodeclass --all --wait=false >/dev/null 2>&1 || true

	echo "eks-predestroy: removing Karpenter and Cilium"
	helm uninstall karpenter -n kube-system --wait >/dev/null 2>&1 || true
	helm uninstall cilium -n kube-system --wait >/dev/null 2>&1 || true
else
	echo "eks-predestroy: cluster unreachable; skipping the in-cluster steps"
fi

echo "eks-predestroy: GuardDuty agent leftovers"
if aws eks describe-addon --cluster-name "$cluster" --addon-name aws-guardduty-agent >/dev/null 2>&1; then
	aws eks delete-addon --cluster-name "$cluster" --addon-name aws-guardduty-agent >/dev/null
	echo "  deleted add-on aws-guardduty-agent"
fi
vpc="$(aws ec2 describe-vpcs --filters Name=tag:Project,Values=k8s-self-healing-platform --query 'Vpcs[0].VpcId' --output text)"
if [[ "$vpc" != "None" && -n "$vpc" ]]; then
	for ep in $(aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values="$vpc" \
		"Name=service-name,Values=com.amazonaws.$r.guardduty-data" --query 'VpcEndpoints[].VpcEndpointId' --output text); do
		aws ec2 delete-vpc-endpoints --vpc-endpoint-ids "$ep" >/dev/null
		echo "  deleted endpoint $ep"
	done
	for _ in $(seq 1 30); do
		[[ -z "$(aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values="$vpc" \
			"Name=service-name,Values=com.amazonaws.$r.guardduty-data" \
			Name=vpc-endpoint-state,Values=available,deleting,pending --query 'VpcEndpoints[].VpcEndpointId' --output text)" ]] && break
		sleep 10
	done
	# The group GuardDuty created for its endpoint blocks the VPC delete.
	for sg in $(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$vpc" \
		Name=group-name,Values='GuardDutyManagedSecurityGroup*' --query 'SecurityGroups[].GroupId' --output text); do
		if aws ec2 delete-security-group --group-id "$sg" 2>/dev/null; then
			echo "  deleted security group $sg"
		else
			echo "  could not delete $sg yet (still attached?); verify-gone will show it"
		fi
	done
fi

echo "eks-predestroy: evidence snapshots"
for s in $(aws ec2 describe-snapshots --owner-ids self --filters Name=tag:selfheal.lab/managed,Values=true \
	--query 'Snapshots[].SnapshotId' --output text); do
	if aws ec2 delete-snapshot --snapshot-id "$s" 2>/dev/null; then
		echo "  deleted $s"
	else
		echo "  could not delete $s yet (a copy still pending?); re-run after it completes"
	fi
done

echo "eks-predestroy: done (next: make eks-destroy-plan)"
echo "Evidence manifests are in $root/evidence/; the S3 copies go with the bucket."
