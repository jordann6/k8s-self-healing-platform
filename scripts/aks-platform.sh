#!/usr/bin/env bash
# aks-platform.sh: everything between `terraform apply` and the lab workload.
# Run by the operator (make aks-platform), right after the apply and
# make aks-images: with bring-your-own CNI every node is NotReady until
# Cilium is installed (ADR-0007 row 1). Idempotent.
#
#   1. kubeconfig of the lab's own (.kube/aks), never ~/.kube/config, signed
#      in through the Azure CLI (kubelogin), no local accounts
#   2. Cilium (cluster pool, VXLAN), immediately, then the readiness gate
#   3. the same platform charts as on kind, in the same order (Reloader after
#      Argo Rollouts: its chart only grants Rollout RBAC if that API exists),
#      with the AKS overrides from platform/aks/. NOT metrics-server: AKS
#      ships and manages its own, and a second one would fight it for the
#      metrics.k8s.io APIService. The VPA recommender (row Q3) is installed
#      from the upstream chart, never the managed VPA add-on (ADR-0020).
#   4. the R2 worker (cluster/aks/responder.yaml.tmpl)
#   5. proof that Prometheus remote write is accepted (the role assignment
#      "takes about 30 minutes", ADR-0007 row 3)
#
# Chart versions come from the Makefile (one pin list for every target).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/azure"
: "${KUBECONFIG:?run through make}"
: "${CILIUM_VERSION:?}" "${KPS_VERSION:?}" "${CERT_MANAGER_VERSION:?}" "${ESO_VERSION:?}"
: "${ROLLOUTS_VERSION:?}" "${RELOADER_VERSION:?}" "${ARGOCD_VERSION:?}" "${CHAOS_MESH_VERSION:?}"
: "${TALON_VERSION:?}" "${FALCO_VERSION:?}" "${VPA_VERSION:?}" "${KYVERNO_VERSION:?}"
[[ "$KUBECONFIG" == "$root/.kube/aks" ]] || { echo "aks-platform: KUBECONFIG must be $root/.kube/aks" >&2; exit 1; }
[[ "$root" != *" "* ]] || { echo "aks-platform: the repo path must not contain spaces" >&2; exit 1; }
gen="$root/gitops/targets/aks/generated"
[[ -f "$gen/RESPONDER_IMAGE" ]] || { echo "aks-platform: run make aks-images first (the R2 worker image)" >&2; exit 1; }

runner="$(terraform -chdir="$tf" output -json runner)"
ids="$(terraform -chdir="$tf" output -json workload_identities)"
r() { jq -r --arg k "$1" '.[$k]' <<<"$runner"; }
cluster="$(terraform -chdir="$tf" output -raw cluster_name)"
rg="$(r resource_group)"

# -f flags for a chart: the shared values, then the AKS override if one exists.
# Word-split on purpose (macOS ships bash 3.2, no mapfile); the guard above
# keeps the repo path free of spaces.
values() {
	printf -- '-f %s' "$root/platform/$1.yaml"
	[[ -f "$root/platform/aks/$1.yaml" ]] && printf -- ' -f %s' "$root/platform/aks/$1.yaml"
	return 0
}

echo "aks-platform: kubeconfig"
mkdir -p "$(dirname "$KUBECONFIG")"
az aks get-credentials --resource-group "$rg" --name "$cluster" --file "$KUBECONFIG" \
	--context selfheal-aks --overwrite-existing >/dev/null
kubelogin convert-kubeconfig -l azurecli --kubeconfig "$KUBECONFIG"
[[ "$(kubectl config current-context)" == "selfheal-aks" ]] || { echo "aks-platform: wrong context" >&2; exit 1; }
kubectl version --request-timeout=15s >/dev/null

echo "aks-platform: Cilium (bring your own CNI)"
make -C "$root" --no-print-directory repos
helm upgrade --install cilium cilium/cilium --version "$CILIUM_VERSION" -n kube-system \
	-f "$root/cilium/values.yaml" -f "$root/cilium/values-aks.yaml" >/dev/null
cilium status --wait --wait-duration 8m >/dev/null
kubectl wait --for=condition=Ready nodes --all --timeout=10m >/dev/null
# Pods that started before Cilium have no Cilium endpoint, so policy would
# not apply to them. Delete them; their controllers recreate them under
# Cilium. (With no CNI at all most could not start, but a pod that got an
# address some other way must not stay outside policy.)
kubectl get pods -A -o json | jq -r '.items[] | select(.spec.hostNetwork != true)
	| select(.metadata.deletionTimestamp == null) | select(.status.phase == "Running")
	| "\(.metadata.namespace) \(.metadata.name)"' |
	while read -r ns name; do
		kubectl -n "$ns" get ciliumendpoint "$name" >/dev/null 2>&1 ||
			kubectl -n "$ns" delete pod "$name" --wait=false >/dev/null
	done
"$root/cilium/readiness-gate.sh"

echo "aks-platform: namespaces"
kubectl apply -f "$root/bootstrap/namespaces.yaml" >/dev/null

echo "aks-platform: platform charts"
kps_values="$(mktemp)"
trap 'rm -f "$kps_values"' EXIT
sed -e "s|PROMETHEUS_CLIENT_ID|$(jq -r .prometheus <<<"$ids")|g" \
	-e "s|GRAFANA_CLIENT_ID|$(jq -r .grafana <<<"$ids")|g" \
	-e "s|AZURE_TENANT_ID|$(r tenant_id)|g" \
	-e "s|AMW_REMOTE_WRITE_URL|$(terraform -chdir="$tf" output -raw amw_remote_write_url)|g" \
	-e "s|AMW_QUERY_URL|$(terraform -chdir="$tf" output -raw amw_query_endpoint)|g" \
	"$root/platform/aks/kube-prometheus-stack.yaml" >"$kps_values"
if grep -qE 'PROMETHEUS_CLIENT_ID|GRAFANA_CLIENT_ID|AZURE_TENANT_ID|AMW_REMOTE_WRITE_URL|AMW_QUERY_URL|null' "$kps_values"; then
	echo "aks-platform: a kube-prometheus-stack placeholder was not filled" >&2
	exit 1
fi

install() { # release chart version namespace [extra helm args...]
	local rel="$1" chart="$2" ver="$3" ns="$4"
	shift 4
	echo "  $rel"
	helm upgrade --install "$rel" "$chart" --version "$ver" -n "$ns" --create-namespace "$@" --wait --timeout 10m >/dev/null
}
install kps prometheus-community/kube-prometheus-stack "$KPS_VERSION" monitoring \
	-f "$root/platform/kube-prometheus-stack.yaml" -f "$kps_values"
# Row Q3 (ADR-0020): the upstream chart, recommender only, against AKS's own
# metrics-server. The managed VPA add-on stays off (it always installs the
# updater and admission controller).
# shellcheck disable=SC2046
install vpa autoscalers/vertical-pod-autoscaler "$VPA_VERSION" vpa $(values vpa)
# Row P1 (ADR-0021): Kyverno with one workload identity per verifying controller
# (admission and reports), each holding AcrPull. The client ids are Terraform
# outputs, so the values go to a temp file, never Git.
kyverno_values="$(mktemp)"
trap 'rm -f "$kps_values" "$kyverno_values"' EXIT
cat >"$kyverno_values" <<EOV
admissionController:
  podLabels:
    azure.workload.identity/use: "true"
  rbac:
    serviceAccount:
      annotations:
        azure.workload.identity/client-id: $(jq -r .kyverno_admission <<<"$ids")
        azure.workload.identity/tenant-id: $(r tenant_id)
reportsController:
  podLabels:
    azure.workload.identity/use: "true"
  rbac:
    serviceAccount:
      annotations:
        azure.workload.identity/client-id: $(jq -r .kyverno_reports <<<"$ids")
        azure.workload.identity/tenant-id: $(r tenant_id)
EOV
if grep -qE ': (null|)$' "$kyverno_values"; then
	echo "aks-platform: a Kyverno identity value was not filled" >&2
	exit 1
fi
install kyverno kyverno/kyverno "$KYVERNO_VERSION" kyverno -f "$root/platform/kyverno-cloud.yaml" -f "$kyverno_values"
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

echo "aks-platform: R2 worker"
"$root/scripts/aks-worker.sh"

echo "aks-platform: checks"
# AKS's own metrics-server must serve the HPA's metrics (row A3).
kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' |
	grep -qx True || { echo "aks-platform: FAIL, metrics.k8s.io is not Available" >&2; exit 1; }
echo "  metrics.k8s.io served by AKS's metrics-server"
# Every DaemonSet must have a pod on every node it targets. The workload
# nodes carry a taint; the runner's steady state re-checks once web runs.
kubectl get ds -A -o json | jq -r '.items[] | select(.status.desiredNumberScheduled != .status.numberReady)
	| "  not ready: \(.metadata.namespace)/\(.metadata.name) \(.status.numberReady)/\(.status.desiredNumberScheduled)"'
# Live check L3: the Defender sensor on the tainted workload nodes.
workload="$(kubectl get nodes -l selfheal.lab/nodes=workload -o name | wc -l | tr -d ' ')"
wnodes="$(kubectl get nodes -l selfheal.lab/nodes=workload -o json | jq -c '[.items[].metadata.name]')"
sensor="$(kubectl -n kube-system get pods -o json | jq --argjson w "$wnodes" '[.items[]
	| select(.metadata.name | startswith("microsoft-defender-collector-ds"))
	| select(.status.phase == "Running") | select(.spec.nodeName as $n | $w | index($n))] | length')"
echo "  L3: Defender collector pods on workload nodes: $sensor of $workload"

# Live check L9: remote write accepted. Poll Prometheus through the API
# server proxy for up to 35 minutes: samples sent, none failed (a 403 is a
# non-recoverable error and counts as failed).
echo "aks-platform: waiting for remote write to be accepted (the docs allow about 30 minutes)"
promq() {
	kubectl get --raw "/api/v1/namespaces/monitoring/services/http:kps-prometheus:9090/proxy/api/v1/query?query=$(
		python3 -I -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1")" 2>/dev/null |
		jq -r '.data.result[0].value[1] // "none"'
}
deadline=$(($(date +%s) + 2100))
while :; do
	sent="$(promq 'sum(rate(prometheus_remote_storage_samples_total[2m]))')"
	failed="$(promq 'sum(rate(prometheus_remote_storage_samples_failed_total[2m]))')"
	if [[ "$sent" != "none" && "$failed" != "none" ]] && awk -v s="$sent" -v f="$failed" 'BEGIN { exit !(s > 0 && f == 0) }'; then
		echo "  L9: remote write accepted (sending $sent samples/s, 0 failed)"
		break
	fi
	if (($(date +%s) > deadline)); then
		echo "aks-platform: FAIL, remote write not accepted after 35 minutes (sent $sent/s, failed $failed/s)" >&2
		echo "  inspect: kubectl -n monitoring logs prometheus-kps-prometheus-0 -c prometheus | grep -i remote" >&2
		exit 1
	fi
	echo "  not yet (sent $sent/s, failed $failed/s); checking again in 60s"
	sleep 60
done
echo "aks-platform: done (next: make aks-bootstrap)"
