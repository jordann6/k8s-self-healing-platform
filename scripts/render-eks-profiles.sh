#!/usr/bin/env bash
# render-eks-profiles.sh: prove both EKS profiles render, without an AWS
# account. If `make eks-images` has not written the generated component yet,
# a placeholder stands in for the duration of the render.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
gen="$root/gitops/targets/eks/generated"
placeholder=0
if [[ ! -f "$gen/kustomization.yaml" ]]; then
	placeholder=1
	mkdir -p "$gen"
	cat >"$gen/kustomization.yaml" <<'YAML'
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - {name: selfheal-app, newName: registry.invalid/selfheal/app, newTag: placeholder-v1}
  - {name: selfheal-dbprobe, newName: registry.invalid/selfheal/dbprobe, newTag: placeholder}
  - {name: selfheal-qworker, newName: registry.invalid/selfheal/qworker, newTag: placeholder}
  - {name: selfheal-q2, newName: registry.invalid/selfheal/q2, newTag: placeholder}
YAML
fi
out="$(mktemp -d)"
trap '[[ $placeholder -eq 1 ]] && rm -rf "$gen"; rm -rf "$out"' EXIT
for p in baseline hardened; do
	kubectl kustomize "$root/gitops/targets/eks/$p" >/dev/null
	kubectl kustomize "$root/gitops/targets/eks/q1/$p" >"$out/q1-$p.yaml"
	image="$(awk '/image: / {print $2}' "$out/q1-$p.yaml" | grep '/qworker:' | head -1)"
	[[ "$image" == */qworker:* ]] || {
		echo "render-eks-profiles: $p Q1 worker was not rewritten to ECR: ${image:-missing}" >&2
		exit 1
	}
	kubectl kustomize "$root/gitops/targets/eks/q2/$p" >"$out/q2-$p.yaml"
	image="$(awk '/image: / {print $2}' "$out/q2-$p.yaml" | grep '/q2:' | head -1 || true)"
	[[ "$image" == */q2:* ]] || {
		echo "render-eks-profiles: $p Q2 workload was not rewritten to ECR: ${image:-missing}" >&2
		exit 1
	}
	python3 - "$out/q2-$p.yaml" "$p" <<'PYQ2'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
prof = sys.argv[2]
dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "q2-target")
sig = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "q2-signal")
pod, spod = dep["spec"]["template"]["spec"], sig["spec"]["template"]["spec"]
bad = []
if pod.get("nodeSelector") != {"selfheal.lab/q2": "true"}:
    bad.append("q2-target is not pinned to the labelled workload nodes")
if not any(t.get("key") == "selfheal.lab/workload" for t in pod.get("tolerations", [])):
    bad.append("q2-target lacks the workload toleration")
if spod.get("nodeSelector") or spod.get("tolerations"):
    bad.append("q2-signal must stay unpinned on the system pool")
so = next(d for d in docs if d["kind"] == "ScaledObject")["spec"]
want = {"failureThreshold": 1, "replicas": 3, "behavior": "static"}
if (prof == "hardened") != (so.get("fallback") == want) or (prof == "baseline" and so.get("fallback")):
    bad.append(f"{prof} fallback is {so.get('fallback')}")
if bad:
    sys.exit("render-eks-profiles: Q2 " + prof + ": " + "; ".join(bad))
PYQ2
	echo "render-eks-profiles: $p ok"
done
