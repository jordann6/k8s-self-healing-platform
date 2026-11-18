#!/usr/bin/env bash
# render-gke-platform.sh: render the GKE platform values against the pinned
# charts, without a Google Cloud project, and assert the facts the design
# rests on (ADR-0027). Needs the Helm repos (make repos) and network access to
# fetch the pinned charts. Nothing is applied anywhere.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
: "${CILIUM_VERSION:?}" "${KPS_VERSION:?}" "${CHAOS_MESH_VERSION:?}" "${FALCO_VERSION:?}" "${KEDA_VERSION:?}"

fail() { echo "render-gke-platform: FAIL: $*" >&2; exit 1; }
need() { grep -qE -- "$2" "$1" || fail "$3"; }
none() { ! grep -qE -- "$2" "$1" || fail "$3"; }

# Row 1: the Cilium GKE install (NodeInit, the GKE CNI path, native routing on
# the pod range, kube-proxy kept). 10.64.0.0/16 stands in for the output.
helm template cilium cilium/cilium --version "$CILIUM_VERSION" -n kube-system \
	-f "$root/cilium/values.yaml" -f "$root/cilium/values-gke.yaml" \
	--set ipv4NativeRoutingCIDR=10.64.0.0/16 >"$tmp/cilium.yaml"
need "$tmp/cilium.yaml" '^  name: cilium-node-init$' "no NodeInit DaemonSet (the Cilium GKE install requires it)"
need "$tmp/cilium.yaml" '/home/kubernetes/bin' "the CNI binary path is not GKE's /home/kubernetes/bin"
need "$tmp/cilium.yaml" '^  ipam: "kubernetes"$' "Cilium ipam is not kubernetes (GKE pod alias ranges)"
need "$tmp/cilium.yaml" '^  routing-mode: "native"$' "Cilium routing mode is not native"
need "$tmp/cilium.yaml" '^  ipv4-native-routing-cidr: "?10.64.0.0/16"?$' "native routing CIDR is not the pod range"
need "$tmp/cilium.yaml" '^  kube-proxy-replacement: "false"$' "kube-proxy replacement is on (GKE's legacy datapath runs kube-proxy)"
need "$tmp/cilium.yaml" 'agent-not-ready' "the agent-not-ready taint key is not configured"
echo "render-gke-platform: cilium ok"

helm template kps prometheus-community/kube-prometheus-stack --version "$KPS_VERSION" -n monitoring \
	-f "$root/platform/kube-prometheus-stack.yaml" -f "$root/platform/gke/kube-prometheus-stack.yaml" >"$tmp/kps.yaml"
none "$tmp/kps.yaml" 'remoteWrite' "Prometheus remote-writes somewhere (row 3: nothing leaves the cluster on GKE)"
need "$tmp/kps.yaml" 'metrics/cadvisor' "no cAdvisor scrape (the A3 ScaledObject reads cAdvisor CPU)"
echo "render-gke-platform: kube-prometheus-stack ok"

helm template keda kedacore/keda --version "$KEDA_VERSION" -n keda \
	-f "$root/platform/keda.yaml" -f "$root/platform/gke/keda.yaml" >"$tmp/keda.yaml"
need "$tmp/keda.yaml" 'name: keda-operator$' "no KEDA operator Deployment"
echo "render-gke-platform: keda ok"

helm template chaos-mesh chaos-mesh/chaos-mesh --version "$CHAOS_MESH_VERSION" -n chaos-mesh \
	-f "$root/platform/chaos-mesh.yaml" -f "$root/platform/gke/chaos-mesh.yaml" >"$tmp/chaos.yaml"
need "$tmp/chaos.yaml" 'key: selfheal.lab/workload' "chaos daemon does not tolerate the workload taint"
echo "render-gke-platform: chaos-mesh ok"

helm template falco falcosecurity/falco --version "$FALCO_VERSION" -n falco \
	-f "$root/platform/falco.yaml" -f "$root/platform/gke/falco.yaml" >"$tmp/falco.yaml"
need "$tmp/falco.yaml" 'key: selfheal.lab/workload' "Falco does not tolerate the workload taint (R1 would never fire)"
need "$tmp/falco.yaml" 'modern_ebpf' "Falco driver is not modern_ebpf (row 16: no kernel module on COS)"
echo "render-gke-platform: falco ok"
