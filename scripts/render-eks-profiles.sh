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
YAML
fi
trap '[[ $placeholder -eq 1 ]] && rm -rf "$gen"' EXIT
for p in baseline hardened; do
	kubectl kustomize "$root/gitops/targets/eks/$p" >/dev/null
	echo "render-eks-profiles: $p ok"
done
