#!/usr/bin/env bash
# render-gke-profiles.sh: prove both GKE profiles render, without a Google
# Cloud project or the network, and that each carries the placement it is
# judged on (row A4) and the A3 shape (ADR-0027). If `make gke-images` has not
# written the generated component yet, a placeholder stands in for the render.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
gke="$root/gitops/targets/gke"
aks="$root/gitops/targets/aks"
gen="$gke/generated"

# The copies of the AKS placement and A3 files must not drift.
for f in baseline/colocate.yaml hardened/topology.yaml hardened/scaledobject.yaml; do
	cmp -s "$aks/$f" "$gke/$f" || {
		echo "render-gke-profiles: gitops/targets/gke/$f differs from the AKS copy" >&2
		exit 1
	}
done

placeholder=0
if [[ ! -f "$gen/kustomization.yaml" ]]; then
	placeholder=1
	mkdir -p "$gen"
	cat >"$gen/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - {name: selfheal-app, newName: registry.invalid/selfheal/app, newTag: placeholder-v1}
YAML
fi
out="$(mktemp -d)"
trap '[[ $placeholder -eq 1 ]] && rm -rf "$gen"; rm -rf "$out"' EXIT
for p in baseline hardened; do
	kubectl kustomize "$gke/$p" >"$out/$p.yaml"
	grep -q "path: targets/gke/$p$" "$gke/apps/$p/workload.yaml" || {
		echo "render-gke-profiles: apps/$p/workload.yaml does not point at targets/gke/$p" >&2
		exit 1
	}
done

python3 - "$out" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("render-gke-profiles: PyYAML is required for the assertions (pip install pyyaml)")

out = Path(sys.argv[1])
fail = []
for prof in ("baseline", "hardened"):
    docs = [d for d in yaml.safe_load_all((out / f"{prof}.yaml").read_text()) if d]
    kinds = {(d["kind"], d["metadata"]["name"]) for d in docs}
    web = next(d for d in docs if d["kind"] == "Rollout" and d["metadata"]["name"] == "web")
    pod = web["spec"]["template"]["spec"]
    images = [c["image"] for c in pod["containers"]]
    if not all("/selfheal/app:" in i for i in images):
        fail.append(f"{prof}: web image not rewritten to the registry: {images}")
    if pod.get("nodeSelector") != {"selfheal.lab/nodes": "workload"}:
        fail.append(f"{prof}: web is not on the workload pool")
    if not any(t.get("key") == "selfheal.lab/workload" for t in pod.get("tolerations", [])):
        fail.append(f"{prof}: web lacks the workload toleration")
    for name in ("prober", "partner"):
        dep = next((d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == name), None)
        if dep is None or dep["spec"]["template"]["spec"].get("nodeSelector") != {"selfheal.lab/nodes": "system"}:
            fail.append(f"{prof}: {name} is not on the system pool")
    spread = pod.get("topologySpreadConstraints") or []
    affinity = pod.get("affinity", {}).get("podAffinity")
    if prof == "baseline":
        if spread or not affinity:
            fail.append("baseline: expected the colocated placement and no spread")
        if ("ScaledObject", "web") in kinds:
            fail.append("baseline: must not carry the A3 ScaledObject")
    else:
        want = {"maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "DoNotSchedule",
                "minDomains": 3, "nodeTaintsPolicy": "Honor"}
        if not any(all(s.get(k) == v for k, v in want.items()) for s in spread):
            fail.append(f"hardened: strict hostname spread missing: {spread}")
        if ("ScaledObject", "web") not in kinds:
            fail.append("hardened: the A3 ScaledObject is missing")
        if ("HorizontalPodAutoscaler", "web") in kinds:
            fail.append("hardened: the shared HPA would fight KEDA's")
        if not any(d["kind"] == "CiliumNetworkPolicy" and d["metadata"]["name"] == "quarantine" for d in docs):
            fail.append("hardened: the R1 quarantine CiliumNetworkPolicy is missing")
if fail:
    sys.exit("render-gke-profiles: " + "; ".join(fail))
print("render-gke-profiles: both GKE profiles render; placement, A3 and R1 policy as judged")
PY
