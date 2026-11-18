#!/usr/bin/env bash
# Render the G1 and X1 cloud overlays offline and pin the cloud contract (ADR-0025 and ADR-0026 cloud
# addenda). Needs no cloud account. A placeholder generated component stands in when `make eks-images`
# or `make aks-images` has not written one.
#
#   scripts/render-cloud-g1x1.sh            both clouds
#   scripts/render-cloud-g1x1.sh eks        one cloud
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
clouds=("${@:-eks aks}")
[[ $# -eq 0 ]] && clouds=(eks aks)
out="$(mktemp -d)"
placeholder=""
cleanup() {
	for g in $placeholder; do rm -rf "$g"; done
	rm -rf "$out"
}
trap cleanup EXIT

for cloud in "${clouds[@]}"; do
	gen="$root/gitops/targets/$cloud/generated"
	if [[ ! -f "$gen/kustomization.yaml" ]]; then
		placeholder="$placeholder $gen"
		mkdir -p "$gen"
		cat >"$gen/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - {name: selfheal-g1, newName: registry.invalid/selfheal/g1, newTag: placeholder}
  - {name: selfheal-x1, newName: registry.invalid/selfheal/x1, newTag: placeholder}
  - {name: selfheal-x1-target, newName: registry.invalid/selfheal/g1, newTag: placeholder}
YAML
	fi
	for row in g1 x1; do
		for p in baseline hardened; do
			kubectl kustomize "$root/gitops/targets/$cloud/$row/$p" >"$out/$cloud-$row-$p.yaml"
		done
	done
	python3 - "$cloud" "$out" "$gen/kustomization.yaml" <<'PY'
import pathlib
import sys
import yaml

cloud, out, gen = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
generated = gen.read_text()


def docs(row, profile):
    return [d for d in yaml.safe_load_all((out / f"{cloud}-{row}-{profile}.yaml").read_text()) if d]


def of(items, kind):
    return [d for d in items if d.get("kind") == kind]


want_ann = {
    "eks": {"service.beta.kubernetes.io/aws-load-balancer-type": "nlb",
            "service.beta.kubernetes.io/aws-load-balancer-internal": "true"},
    "aks": {"service.beta.kubernetes.io/azure-load-balancer-internal": "true"},
}[cloud]
bad = []
for profile in ("baseline", "hardened"):
    g1 = docs("g1", profile)
    proxy = of(g1, "EnvoyProxy")[0]["spec"]["provider"]["kubernetes"]["envoyService"]
    if proxy.get("type") != "LoadBalancer":
        bad.append(f"{profile} g1 proxy Service is {proxy.get('type')}, not LoadBalancer")
    if proxy.get("annotations") != want_ann:
        bad.append(f"{profile} g1 proxy annotations are {proxy.get('annotations')}, not the internal-only set")
    policies = of(g1, "CiliumNetworkPolicy")
    if sorted(p["metadata"]["namespace"] for p in policies) != ["selfheal-g1", "selfheal-g1-other"]:
        bad.append(f"{profile} g1 lacks the two backend policies")
    listeners = of(g1, "Gateway")[0]["spec"]["listeners"]
    names = [item["name"] for item in listeners]
    certs = of(g1, "Certificate")
    if profile == "hardened":
        if names != ["http", "https"] or len(certs) != 1:
            bad.append(f"hardened g1 listeners {names}, certificates {len(certs)}")
        https = listeners[-1]
        if https["protocol"] != "HTTPS" or https["tls"]["certificateRefs"][0]["name"] != "g1-tls":
            bad.append("hardened https listener does not terminate with g1-tls")
        if https["allowedRoutes"]["namespaces"]["from"] != "Selector":
            bad.append("hardened https listener does not restrict route namespaces")
    elif names != ["http"] or certs:
        bad.append(f"baseline g1 is HTTP only, found {names} and {len(certs)} certificates")
    x1 = docs("x1", profile)
    deployments = of(x1, "Deployment")
    if not deployments:
        bad.append(f"{profile} x1 has no Deployment")
    for dep in deployments:
        if dep["spec"]["template"]["spec"].get("nodeSelector") != {"selfheal.lab/x1": "true"}:
            bad.append(f"{profile} x1 {dep['metadata']['name']} is not pinned to the labelled node")
    if profile == "hardened" and not any(d["metadata"]["name"] == "x1-controller" for d in deployments):
        bad.append("hardened x1 lacks the controller")
    # Image rewrite is asserted only once the real generated component names the images.
    for row, docs_ in (("g1", g1), ("x1", x1)):
        for dep in of(docs_, "Deployment"):
            image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
            for logical in ("selfheal-g1", "selfheal-x1-target", "selfheal-x1"):
                if f"name: {logical}\n" in generated and image.startswith(f"{logical}:"):
                    bad.append(f"{profile} {row} {dep['metadata']['name']} image still {image}")
if bad:
    sys.exit(f"render-cloud-g1x1 {cloud}: " + "; ".join(bad))
print(f"render-cloud-g1x1: {cloud} ok")
PY
done
