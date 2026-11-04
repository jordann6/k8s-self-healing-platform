#!/usr/bin/env bash
# eks-images.sh: build the lab images for linux/amd64, push them to ECR under
# the git short SHA, and write the generated kustomize component that points
# both EKS profiles at them (gitops/targets/eks/generated, not committed).
#
# Tags are immutable in ECR, so a report names the exact build it measured:
#   app:<sha>-v1   the good release        app:<sha>-v2   the bad release (row A2)
#   gitserver:<sha>                         dbprobe:<sha>
# A tag that already exists is not rebuilt.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
: "${MGMT_PROFILE:?set MGMT_PROFILE}" "${LAB_PROFILE:?set LAB_PROFILE}"

repos="$(AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -json ecr_repositories)"
region="$(AWS_PROFILE="$MGMT_PROFILE" terraform -chdir="$root/infra/aws" output -raw region)"
export AWS_PROFILE="$LAB_PROFILE" AWS_REGION="$region"
repo() { jq -r --arg k "$1" '.[$k]' <<<"$repos"; }

sha="$(git -C "$root" rev-parse --short HEAD)"
if [[ -n "$(git -C "$root" status --porcelain -- app)" ]]; then
	echo "eks-images: app/ has uncommitted changes; commit first so the tag names the build" >&2
	exit 1
fi

registry="$(repo app)"
registry="${registry%%/*}"
aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null

push() { # repository-key tag context [build args...]
	local r t
	r="$(repo "$1")"
	t="$2"
	shift 2
	if aws ecr describe-images --repository-name "${r#*/}" --image-ids "imageTag=$t" >/dev/null 2>&1; then
		echo "  $1:$t exists"
		return
	fi
	echo "  $1:$t"
	docker buildx build --platform linux/amd64 --provenance=false -q -t "$r:$t" --push "$@" >/dev/null
}

echo "eks-images: building for linux/amd64 at $sha"
push app "$sha-v1" --build-arg RELEASE=v1 "$root/app"
push app "$sha-v2" --build-arg RELEASE=v2 "$root/app"
push gitserver "$sha" "$root/app/gitserver"
push dbprobe "$sha" "$root/app/dbprobe"

gen="$root/gitops/targets/eks/generated"
mkdir -p "$gen"
cat >"$gen/kustomization.yaml" <<EOF
# Written by scripts/eks-images.sh at $sha. Not committed: it names this
# account's registry. The runner rewrites the selfheal-app tag for row A2.
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - name: selfheal-app
    newName: $(repo app)
    newTag: $sha-v1
  - name: selfheal-dbprobe
    newName: $(repo dbprobe)
    newTag: $sha
EOF
printf '%s\n' "$sha" >"$gen/IMAGE_SHA"
printf '%s:%s\n' "$(repo gitserver)" "$sha" >"$gen/GITSERVER_IMAGE"
echo "eks-images: wrote $gen (selfheal-app -> $sha-v1)"
