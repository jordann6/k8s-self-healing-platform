#!/usr/bin/env bash
# eks-platform.sh: everything between `terraform apply` and the lab workload.
# Run by the operator (make eks-platform), after the apply. Idempotent.
#
#   1. kubeconfig of the lab's own (.kube/eks), never ~/.kube/config
#   2. VPC CNI off, Cilium in ENI mode, readiness gate
#   3. Karpenter (NodeRepair on), the workload EC2NodeClass pinned one AMI
#      release behind the newest (row S5 rolls it forward)
#   4. the same platform charts as on kind, in the same order (Reloader after
#      Argo Rollouts: its chart only grants Rollout RBAC if that API exists),
#      with the EKS overrides from platform/eks/
#   5. the R2 responder's Kubernetes RBAC
#
# Chart versions come from the Makefile (one pin list for both targets).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${MGMT_PROFILE:?set MGMT_PROFILE}" "${LAB_PROFILE:?set LAB_PROFILE}" "${KUBECONFIG:?run through make}"
: "${CILIUM_VERSION:?}" "${KARPENTER_VERSION:?}" "${METRICS_SERVER_VERSION:?}" "${VPA_VERSION:?}" "${KYVERNO_VERSION:?}" "${KPS_VERSION:?}"
: "${CERT_MANAGER_VERSION:?}" "${ESO_VERSION:?}" "${ROLLOUTS_VERSION:?}" "${RELOADER_VERSION:?}"
: "${ARGOCD_VERSION:?}" "${CHAOS_MESH_VERSION:?}" "${TALON_VERSION:?}" "${FALCO_VERSION:?}"
: "${KEDA_VERSION:?}"

out() { AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -raw "$1"; }
outj() { AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -json "$1"; }
cluster="$(out cluster_name)"
region="$(out region)"
export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="$region"
# -f flags for a chart: the shared values, then the EKS override if one exists.
# Word-split on purpose (macOS ships bash 3.2, no mapfile); the guard below
# keeps the repo path free of spaces.
values() {
	printf -- '-f %s' "$root/platform/$1.yaml"
	[[ -f "$root/platform/eks/$1.yaml" ]] && printf -- ' -f %s' "$root/platform/eks/$1.yaml"
	return 0
}
[[ "$root" != *" "* ]] || { echo "eks-platform: the repo path must not contain spaces" >&2; exit 1; }

echo "eks-platform: kubeconfig"
mkdir -p "$(dirname "$KUBECONFIG")"
aws eks update-kubeconfig --name "$cluster" --region "$region" --kubeconfig "$KUBECONFIG" --alias selfheal-eks >/dev/null
kubectl version --request-timeout=15s >/dev/null

echo "eks-platform: Cilium (ENI mode)"
# aws-node stays installed but can never schedule: no node carries this label.
kubectl -n kube-system patch daemonset aws-node --type=strategic \
	-p '{"spec":{"template":{"spec":{"nodeSelector":{"io.cilium/aws-node-enabled":"true"}}}}}' >/dev/null
make -C "$root" --no-print-directory repos
helm upgrade --install cilium cilium/cilium --version "$CILIUM_VERSION" -n kube-system \
	-f "$root/cilium/values.yaml" -f "$root/cilium/values-eks.yaml" >/dev/null
cilium status --wait --wait-duration 6m >/dev/null
# Pods that started before Cilium (CoreDNS, any add-on) hold VPC CNI
# addresses and no Cilium endpoint, so policy would not apply to them.
# Delete them; their controllers recreate them under Cilium.
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.hostNetwork != true)
	| select(.metadata.deletionTimestamp == null) | "\(.metadata.namespace) \(.metadata.name)"' |
	while read -r ns name; do
		kubectl -n "$ns" get ciliumendpoint "$name" >/dev/null 2>&1 ||
			kubectl -n "$ns" delete pod "$name" --wait=false >/dev/null
	done
"$root/cilium/readiness-gate.sh"

echo "eks-platform: Karpenter $KARPENTER_VERSION"
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter --version "$KARPENTER_VERSION" \
	-n kube-system -f "$root/platform/eks/karpenter.yaml" \
	--set "settings.clusterName=$cluster" \
	--set "settings.interruptionQueue=$(outj karpenter | jq -r .queue_name)" --wait >/dev/null

# One release behind the newest EKS-optimized AL2023 AMI for this Kubernetes
# version. Row S5 moves the alias to @latest, which Karpenter sees as drift.
k8s_version="$(kubectl version -o json | jq -r '.serverVersion.major + "." + (.serverVersion.minor | sub("[^0-9]";""))')"
releases="$(aws ssm get-parameters-by-path --recursive \
	--path "/aws/service/eks/optimized-ami/$k8s_version/amazon-linux-2023/x86_64/standard" \
	--query 'Parameters[].Name' --output text | tr '\t' '\n' | grep -o 'v[0-9]\{8\}' | sort -u)"
previous="$(tail -2 <<<"$releases" | head -1)"
[[ -n "$previous" ]] || { echo "eks-platform: could not list AL2023 AMI releases for $k8s_version" >&2; exit 1; }
echo "eks-platform: workload nodes on al2023@$previous (newest: $(tail -1 <<<"$releases"))"
KARPENTER_NODE_ROLE="$(outj karpenter | jq -r .node_role_name)" CLUSTER_NAME="$cluster" \
	AMI_ALIAS="al2023@$previous" COST_CENTER="${COST_CENTER:-cc-1010}" \
	envsubst <"$root/cluster/karpenter/ec2nodeclass.yaml.tmpl" | kubectl apply -f - >/dev/null

echo "eks-platform: namespaces and the responder's RBAC"
kubectl apply -f "$root/bootstrap/namespaces.yaml" >/dev/null
kubectl apply -f "$root/cluster/rbac-responder.yaml" >/dev/null

echo "eks-platform: platform charts"
kps_values="$(mktemp)"
trap 'rm -f "$kps_values"' EXIT
sed -e "s|AMP_REMOTE_WRITE_URL|$(out amp_remote_write_url)|" -e "s|AMP_QUERY_URL|$(out amp_query_url)|" \
	-e "s|region: us-east-1|region: $region|" -e "s|sigV4Region: us-east-1|sigV4Region: $region|" \
	"$root/platform/eks/kube-prometheus-stack.yaml" >"$kps_values"

install() { # release chart version namespace [extra helm args...]
	local rel="$1" chart="$2" ver="$3" ns="$4"
	shift 4
	echo "  $rel"
	helm upgrade --install "$rel" "$chart" --version "$ver" -n "$ns" --create-namespace "$@" --wait --timeout 10m >/dev/null
}
# shellcheck disable=SC2046
install metrics-server metrics-server/metrics-server "$METRICS_SERVER_VERSION" kube-system $(values metrics-server)
# Row Q3 (ADR-0020): the upstream chart, recommender only. Nothing here can change a workload.
# shellcheck disable=SC2046
install vpa autoscalers/vertical-pod-autoscaler "$VPA_VERSION" vpa $(values vpa)
# Row D1 (ADR-0022): the gp3 class the EBS CSI add-on serves. The class is harmless without the
# driver, the add-on itself exists only when the EKS root was planned with d1_store_bucket.
kubectl apply -f "$root/platform/eks/storageclass-gp3.yaml" >/dev/null
# Row P1 (ADR-0021): Kyverno with IRSA for its admission and reports controllers,
# the documented path for the `amazon` registry credential provider. The role
# ARN is a Terraform output, so the values are written to a temp file, never Git.
kyverno_values="$(mktemp)"
trap 'rm -f "$kps_values" "$kyverno_values"' EXIT
role_arn="$(out kyverno_registry_role_arn)"
cat >"$kyverno_values" <<EOV
admissionController:
  rbac:
    serviceAccount:
      annotations:
        eks.amazonaws.com/role-arn: $role_arn
reportsController:
  rbac:
    serviceAccount:
      annotations:
        eks.amazonaws.com/role-arn: $role_arn
EOV
install kyverno kyverno/kyverno "$KYVERNO_VERSION" kyverno -f "$root/platform/kyverno-cloud.yaml" -f "$kyverno_values"
# EKS has no managed KEDA add-on. Q1 and Q2 use the pinned upstream chart,
# matching AWS's documented EKS installation model.
# shellcheck disable=SC2046
install keda kedacore/keda "$KEDA_VERSION" keda $(values keda)
install kps prometheus-community/kube-prometheus-stack "$KPS_VERSION" monitoring \
	-f "$root/platform/kube-prometheus-stack.yaml" -f "$kps_values"
# shellcheck disable=SC2046
install cert-manager jetstack/cert-manager "$CERT_MANAGER_VERSION" cert-manager $(values cert-manager)
# shellcheck disable=SC2046
install external-secrets external-secrets/external-secrets "$ESO_VERSION" external-secrets $(values external-secrets)
# shellcheck disable=SC2046
install argo-rollouts argo/argo-rollouts "$ROLLOUTS_VERSION" argo-rollouts $(values argo-rollouts)
# After Rollouts, never before (phase 1, row S1: silent forbidden otherwise).
# shellcheck disable=SC2046
install reloader stakater/reloader "$RELOADER_VERSION" reloader $(values reloader)
# shellcheck disable=SC2046
install argocd argo/argo-cd "$ARGOCD_VERSION" argocd $(values argo-cd)
# shellcheck disable=SC2046
install chaos-mesh chaos-mesh/chaos-mesh "$CHAOS_MESH_VERSION" chaos-mesh $(values chaos-mesh)
# shellcheck disable=SC2046
install falco-talon falcosecurity/falco-talon "$TALON_VERSION" falco $(values falco-talon) \
	--set-file "config.rulesOverride=$root/platform/talon/baseline.yaml"
# shellcheck disable=SC2046
install falco falcosecurity/falco "$FALCO_VERSION" falco $(values falco)

echo "eks-platform: checks"
# Every DaemonSet the rows depend on must have a pod on every node, workload
# nodes included (they carry a taint). The workload pool is empty until the
# lab app schedules, so this is re-checked by the runner's steady state.
kubectl get ds -A -o json | jq -r '.items[] | select(.status.desiredNumberScheduled != .status.numberReady)
	| "  not ready: \(.metadata.namespace)/\(.metadata.name) \(.status.numberReady)/\(.status.desiredNumberScheduled)"'
kubectl -n kube-system get deploy karpenter -o jsonpath='{.spec.template.spec.containers[0].env}' |
	jq -r '.[] | select(.name=="FEATURE_GATES") | "  karpenter feature gates: \(.value)"'
echo "eks-platform: done (next: make eks-images eks-bootstrap)"
