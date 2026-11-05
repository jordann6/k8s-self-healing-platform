#!/usr/bin/env bash
# render-aks-platform.sh: render the AKS platform values against the pinned
# charts, without an Azure subscription, and assert the facts the design rests
# on (ADR-0008). Needs the Helm repos (make repos) and network access to fetch
# the pinned charts; any failed render or assertion fails the run. Nothing is
# applied anywhere.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
: "${CILIUM_VERSION:?}" "${KPS_VERSION:?}" "${CHAOS_MESH_VERSION:?}" "${FALCO_VERSION:?}"

fail() { echo "render-aks-platform: FAIL: $*" >&2; exit 1; }
need() { grep -qE -- "$2" "$1" || fail "$3"; }

helm template cilium cilium/cilium --version "$CILIUM_VERSION" -n kube-system \
	-f "$root/cilium/values.yaml" -f "$root/cilium/values-aks.yaml" >"$tmp/cilium.yaml"
need "$tmp/cilium.yaml" '^  ipam: "cluster-pool"$' "Cilium ipam is not cluster-pool (aksbyocni writes no ipam key for other modes)"
need "$tmp/cilium.yaml" '^  cluster-pool-ipv4-cidr: "10.244.0.0/16"$' "Cilium pod pool is not 10.244.0.0/16 (the chart default 10.0.0.0/8 overlaps the VNet)"
need "$tmp/cilium.yaml" '^  routing-mode: "tunnel"$' "Cilium routing mode is not tunnel"
need "$tmp/cilium.yaml" '^  enable-ipv4-masquerade: "true"$' "masquerade is off (storage and Key Vault admit the node subnet only)"
echo "render-aks-platform: cilium ok"

helm template kps prometheus-community/kube-prometheus-stack --version "$KPS_VERSION" -n monitoring \
	-f "$root/platform/kube-prometheus-stack.yaml" -f "$root/platform/aks/kube-prometheus-stack.yaml" >"$tmp/kps.yaml"
need "$tmp/kps.yaml" 'workloadIdentity:' "remote write has no azureAd.workloadIdentity"
need "$tmp/kps.yaml" 'azure.workload.identity/use: "true"' "Prometheus or Grafana pod lacks the workload identity label"
need "$tmp/kps.yaml" 'type: grafana-azureprometheus-datasource' "Grafana data source is not the Azure Monitor Prometheus plugin"
echo "render-aks-platform: kube-prometheus-stack ok"

helm template chaos-mesh chaos-mesh/chaos-mesh --version "$CHAOS_MESH_VERSION" -n chaos-mesh \
	-f "$root/platform/chaos-mesh.yaml" -f "$root/platform/aks/chaos-mesh.yaml" >"$tmp/chaos.yaml"
need "$tmp/chaos.yaml" 'key: selfheal.lab/workload' "chaos daemon does not tolerate the workload taint"
echo "render-aks-platform: chaos-mesh ok"

helm template falco falcosecurity/falco --version "$FALCO_VERSION" -n falco \
	-f "$root/platform/falco.yaml" -f "$root/platform/aks/falco.yaml" >"$tmp/falco.yaml"
need "$tmp/falco.yaml" 'key: selfheal.lab/workload' "Falco does not tolerate the workload taint (R1 would never fire)"
echo "render-aks-platform: falco ok"

CLUSTER_NAME=selfheal CLUSTER_ID=placeholder NODE_RESOURCE_GROUP=placeholder STORAGE_ACCOUNT=placeholder \
	INCIDENTS_QUEUE=incidents EVIDENCE_CONTAINER=evidence RESPONDER_IMAGE=registry.invalid/r2worker:placeholder \
	RESPONDER_CLIENT_ID=placeholder envsubst <"$root/cluster/aks/responder.yaml.tmpl" >"$tmp/responder.yaml"
if grep -q '[$][{]' "$tmp/responder.yaml"; then
	fail "responder manifest has an unrendered placeholder"
fi
python3 - "$tmp/responder.yaml" <<'PY'
import sys, yaml
kinds = [d["kind"] for d in yaml.safe_load_all(open(sys.argv[1])) if d]
need = {"Namespace", "ServiceAccount", "ClusterRole", "Role", "Deployment", "CiliumNetworkPolicy"}
missing = need - set(kinds)
if missing:
    sys.exit(f"render-aks-platform: FAIL: responder manifest lacks {sorted(missing)}")
PY
echo "render-aks-platform: responder ok"
