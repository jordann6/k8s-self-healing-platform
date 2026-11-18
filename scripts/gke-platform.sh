#!/usr/bin/env bash
# gke-platform.sh: everything between `terraform apply` and the lab workload
# on GKE. Run by the operator (make gke-platform) right after the apply: both
# pools register with Cilium's agent-not-ready taint, so no pod starts until
# Cilium is in (ADR-0027 row 1). Idempotent.
#
#   1. the lab's own kubeconfig (.kube/gke) through the DNS endpoint, never
#      ~/.kube/config; IAM authorizes, no client certificate
#   2. Cilium with the GKE values (NodeInit, native routing on the pod range),
#      immediately, then the readiness gate
#   3. the platform charts the in-cluster rows need, in the kind order
#      (Reloader after Argo Rollouts), with the GKE overrides from
#      platform/gke/. NOT metrics-server: GKE manages its own. KEDA from the
#      pinned chart (D4). Nothing for Q1 to X1 (out of GKE scope).
#   4. checks the docs left open: metrics API served (A3 precondition), every
#      DaemonSet on every node it targets, Falco's probe loaded on the COS
#      kernel (L3), metrics-server resolution and HPA profile (L6, recorded)
#
# Chart versions come from the Makefile (one pin list for every target).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/gcp"
: "${KUBECONFIG:?run through make}"
: "${CILIUM_VERSION:?}" "${KPS_VERSION:?}" "${CERT_MANAGER_VERSION:?}" "${ESO_VERSION:?}"
: "${ROLLOUTS_VERSION:?}" "${RELOADER_VERSION:?}" "${ARGOCD_VERSION:?}" "${CHAOS_MESH_VERSION:?}"
: "${TALON_VERSION:?}" "${FALCO_VERSION:?}" "${KEDA_VERSION:?}"
[[ "$KUBECONFIG" == "$root/.kube/gke" ]] || { echo "gke-platform: KUBECONFIG must be $root/.kube/gke" >&2; exit 1; }
[[ "$root" != *" "* ]] || { echo "gke-platform: the repo path must not contain spaces" >&2; exit 1; }

runner="$(terraform -chdir="$tf" output -json runner)"
r() { jq -r --arg k "$1" '.[$k]' <<<"$runner"; }
project="$(r project_id)"
zone="$(r zone)"
cluster="$(r cluster_name)"
pod_cidr="$(r pod_cidr)"
[[ "$pod_cidr" =~ ^[0-9.]+/[0-9]+$ ]] || { echo "gke-platform: no pod range in the outputs ('$pod_cidr')" >&2; exit 1; }

# -f flags for a chart: the shared values, then the GKE override if one exists.
values() {
	printf -- '-f %s' "$root/platform/$1.yaml"
	[[ -f "$root/platform/gke/$1.yaml" ]] && printf -- ' -f %s' "$root/platform/gke/$1.yaml"
	return 0
}

echo "gke-platform: kubeconfig (DNS endpoint)"
mkdir -p "$(dirname "$KUBECONFIG")"
gcloud container clusters get-credentials "$cluster" --location "$zone" --project "$project" --dns-endpoint >/dev/null
ctx="$(kubectl config current-context)"
[[ "$ctx" == "selfheal-gke" ]] || kubectl config rename-context "$ctx" selfheal-gke >/dev/null
kubectl version --request-timeout=15s >/dev/null

echo "gke-platform: Cilium (bring your own CNI, native routing on $pod_cidr)"
make -C "$root" --no-print-directory repos
helm upgrade --install cilium cilium/cilium --version "$CILIUM_VERSION" -n kube-system \
	-f "$root/cilium/values.yaml" -f "$root/cilium/values-gke.yaml" \
	--set "ipv4NativeRoutingCIDR=$pod_cidr" >/dev/null
cilium status --wait --wait-duration 10m >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=10m >/dev/null
# Live check L1: Cilium removes the agent-not-ready taint itself.
tainted="$(kubectl get nodes -o json | jq -r '[.items[] | select(any(.spec.taints[]?; .key == "node.cilium.io/agent-not-ready"))] | length')"
[[ "$tainted" == "0" ]] || { echo "gke-platform: FAIL, $tainted node(s) still carry the Cilium agent-not-ready taint" >&2; exit 1; }
echo "  L1: every node Ready, the agent-not-ready taint lifted by Cilium"
# Pods that started before Cilium have no Cilium endpoint, so policy would
# not apply to them. Delete them; their controllers recreate them under Cilium.
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.hostNetwork != true)
	| select(.metadata.deletionTimestamp == null) | select(.status.phase == "Running")
	| "\(.metadata.namespace) \(.metadata.name)"' |
	while read -r ns name; do
		kubectl -n "$ns" get ciliumendpoint "$name" >/dev/null 2>&1 ||
			kubectl -n "$ns" delete pod "$name" --wait=false >/dev/null
	done
"$root/cilium/readiness-gate.sh"

echo "gke-platform: namespaces"
kubectl apply -f "$root/bootstrap/namespaces.yaml" >/dev/null

echo "gke-platform: platform charts"
install() { # release chart version namespace [extra helm args...]
	local rel="$1" chart="$2" ver="$3" ns="$4"
	shift 4
	echo "  $rel"
	helm upgrade --install "$rel" "$chart" --version "$ver" -n "$ns" --create-namespace "$@" --wait --timeout 10m >/dev/null
}
# shellcheck disable=SC2046
install kps prometheus-community/kube-prometheus-stack "$KPS_VERSION" monitoring $(values kube-prometheus-stack)
# shellcheck disable=SC2046
install keda kedacore/keda "$KEDA_VERSION" keda $(values keda)
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
# The Talon response rules are a rules file, not Helm values.
# shellcheck disable=SC2046
install falco-talon falcosecurity/falco-talon "$TALON_VERSION" falco $(values falco-talon) \
	--set-file "config.rulesOverride=$root/platform/talon/baseline.yaml"
# shellcheck disable=SC2046
install falco falcosecurity/falco "$FALCO_VERSION" falco $(values falco)

echo "gke-platform: checks"
kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' |
	grep -qx True || { echo "gke-platform: FAIL, metrics.k8s.io is not Available" >&2; exit 1; }
echo "  metrics.k8s.io served by GKE's metrics-server"
notready="$(kubectl get ds -A -o json | jq -r '.items[] | select(.status.desiredNumberScheduled != .status.numberReady)
	| "\(.metadata.namespace)/\(.metadata.name) \(.status.numberReady)/\(.status.desiredNumberScheduled)"')"
if [[ -n "$notready" ]]; then
	echo "gke-platform: FAIL, DaemonSets not ready: $notready" >&2
	exit 1
fi
echo "  every DaemonSet ready on every node it targets"
# Live check L3: Falco's modern eBPF probe on the node image's COS kernel.
# A pod can be Ready a few seconds before it logs the probe line (the first
# GKE run counted 6 of 7, and all 7 had it moments later), so retry for 60s.
nodes="$(kubectl get nodes -o name | wc -l | tr -d ' ')"
for _ in $(seq 1 12); do
	loaded=0
	for pod in $(kubectl -n falco get pods -l app.kubernetes.io/name=falco -o name); do
		kubectl -n falco logs "$pod" -c falco 2>/dev/null | grep -qi 'modern bpf probe' && loaded=$((loaded + 1))
	done
	((loaded == nodes)) && break
	sleep 5
done
echo "  L3: Falco reports the modern eBPF probe on $loaded of $nodes nodes ($(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.kernelVersion}'))"
((loaded == nodes)) || { echo "gke-platform: FAIL, Falco's probe is not loaded on every node (L3)" >&2; exit 1; }
# Live check L6, recorded only: GKE's metrics-server flags and the HPA profile.
ms_args="$(kubectl -n kube-system get deploy -o json | jq -r '[.items[] | select(.metadata.name | startswith("metrics-server"))
	| .spec.template.spec.containers[].command[]?, .spec.template.spec.containers[].args[]?
	| select(test("metric-resolution"))] | join(" ")')"
hpa_profile="$(gcloud container clusters describe "$cluster" --location "$zone" --project "$project" \
	--format='value(podAutoscaling.hpaProfile)' 2>/dev/null || true)"
echo "  L6: metrics-server ${ms_args:-(no --metric-resolution flag, upstream default)}; HPA profile ${hpa_profile:-unset}"
echo "gke-platform: done (next: make gke-bootstrap)"
