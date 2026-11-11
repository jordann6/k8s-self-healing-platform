#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

for profile in baseline hardened; do
	kubectl kustomize "$root/gitops/n1/$profile" >"$out/$profile.yaml"
done

python3 - "$out" "$root" <<'PY'
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
repo = Path(sys.argv[2])
fail = []


def objects(profile):
    return [doc for doc in yaml.safe_load_all((root / f"{profile}.yaml").read_text()) if doc]


for profile in ("baseline", "hardened"):
    docs = objects(profile)
    deployments = {doc["metadata"]["name"]: doc for doc in docs if doc.get("kind") == "Deployment"}
    if set(deployments) != {"n1-capture", "n1-client", "n1-denied", "n1-server"}:
        fail.append(f"{profile}: unexpected deployments {sorted(deployments)}")
    slots = {name: doc["spec"]["template"]["spec"]["nodeSelector"].get("selfheal.lab/n1-slot")
             for name, doc in deployments.items()}
    if slots != {"n1-capture": "server", "n1-client": "client",
                 "n1-denied": "client", "n1-server": "server"}:
        fail.append(f"{profile}: placements changed: {slots}")
    capture = deployments.get("n1-capture", {}).get("spec", {}).get("template", {}).get("spec", {})
    if capture.get("hostNetwork") is not True:
        fail.append(f"{profile}: capture deployment is not attached to the host network")
    if deployments.get("n1-capture", {}).get("metadata", {}).get("namespace") != "selfheal-n1-capture":
        fail.append(f"{profile}: capture deployment escaped its dedicated namespace")
    app_namespaces = {doc.get("metadata", {}).get("namespace") for doc in docs
                      if doc.get("kind") in {"Service", "CiliumNetworkPolicy"}
                      or doc.get("metadata", {}).get("name") in {"n1-client", "n1-denied", "n1-server"}}
    if app_namespaces != {"selfheal"}:
        fail.append(f"{profile}: application resources escaped the restricted namespace")

capture_ns = yaml.safe_load((repo / "bootstrap/n1-capture-namespace.yaml").read_text())
labels = capture_ns.get("metadata", {}).get("labels", {})
if capture_ns.get("metadata", {}).get("name") != "selfheal-n1-capture":
    fail.append("capture namespace name changed")
if labels.get("pod-security.kubernetes.io/enforce") != "privileged":
    fail.append("capture namespace is not explicitly privileged")
    policies = [doc for doc in docs if doc.get("kind") == "CiliumNetworkPolicy"]
    if len(policies) != 1:
        fail.append(f"{profile}: expected one CiliumNetworkPolicy, found {len(policies)}")

if (root / "baseline.yaml").read_text() != (root / "hardened.yaml").read_text():
    fail.append("workload profiles differ; N1 control belongs to Cilium Helm values")
if fail:
    raise SystemExit("render-n1-profiles:\n  " + "\n  ".join(fail))
print("render-n1-profiles: baseline and hardened workloads match")
PY

# ADR-0023: the cloud overlays keep the kind workload, selected by the slot labels the runner applies,
# and keep the capture pod on the host network with only NET_RAW.
for cloud in eks aks; do
	for profile in baseline hardened; do
		doc="$(kubectl kustomize "$root/gitops/targets/$cloud/n1/$profile")"
		for want in 'selfheal.lab/n1-slot: server' 'selfheal.lab/n1-slot: client' 'hostNetwork: true' 'NET_RAW' 'name: n1-capture'; do
			grep -q "$want" <<<"$doc" || { echo "N1 $cloud $profile is missing $want" >&2; exit 1; }
		done
		if grep -q 'imagePullPolicy: Never' <<<"$doc"; then
			echo "N1 $cloud $profile uses a kind-only pull policy" >&2
			exit 1
		fi
	done
done
echo "N1 cloud overlays render with the slot labels and the host-network capture"
