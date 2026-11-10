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
	echo "render-eks-profiles: $p ok"
done
