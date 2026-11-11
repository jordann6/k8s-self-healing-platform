#!/usr/bin/env bash
# eks-images.sh: build the lab images for linux/amd64, push them to ECR under
# the git short SHA, and write the generated kustomize component that points
# both EKS profiles at them (gitops/targets/eks/generated, not committed).
#
# Tags are immutable in ECR, so a report names the exact build it measured:
#   app:<sha>-v1   the good release        app:<sha>-v2   the bad release (row A2)
#   gitserver:<sha>                         dbprobe:<sha>
#   qworker:<sha>                           q2:<sha>
#   o1:<sha>                                q3:<sha>
#   d1:<sha> (the D1 cloud tool image, app/d1/cloud.Dockerfile)
#   n1:<sha> and n1-capture:<sha> (row N1)
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
	local k r t
	k="$1"
	r="$(repo "$k")"
	t="$2"
	shift 2
	if aws ecr describe-images --repository-name "${r#*/}" --image-ids "imageTag=$t" >/dev/null 2>&1; then
		echo "  $k:$t exists"
		return
	fi
	echo "  $k:$t"
	docker buildx build --platform linux/amd64 --provenance=false -q -t "$r:$t" --push "$@" >/dev/null
}

echo "eks-images: building for linux/amd64 at $sha"
push app "$sha-v1" --build-arg RELEASE=v1 "$root/app"
push app "$sha-v2" --build-arg RELEASE=v2 "$root/app"
push gitserver "$sha" "$root/app/gitserver"
push dbprobe "$sha" "$root/app/dbprobe"
push qworker "$sha" "$root/app/qworker"
push q2 "$sha" "$root/app/q2"
push o1 "$sha" "$root/app/o1"
push q3 "$sha" "$root/app/q3"
push d1 "$sha" "$root/app/d1" -f "$root/app/d1/cloud.Dockerfile"
push n1 "$sha" "$root/app/n1"
push n1-capture "$sha" "$root/app/n1-capture"

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
  - name: selfheal-qworker
    newName: $(repo qworker)
    newTag: $sha
  - name: selfheal-q2
    newName: $(repo q2)
    newTag: $sha
  - name: selfheal-o1
    newName: $(repo o1)
    newTag: $sha
  - name: selfheal-q3
    newName: $(repo q3)
    newTag: $sha
  - name: selfheal-d1-cloud
    newName: $(repo d1)
    newTag: $sha
  - name: selfheal-n1
    newName: $(repo n1)
    newTag: $sha
  - name: selfheal-n1-capture
    newName: $(repo n1-capture)
    newTag: $sha
EOF
printf '%s\n' "$sha" >"$gen/IMAGE_SHA"
printf '%s:%s\n' "$(repo gitserver)" "$sha" >"$gen/GITSERVER_IMAGE"
printf '%s:%s\n' "$(repo qworker)" "$sha" >"$gen/QWORKER_IMAGE"
printf '%s:%s\n' "$(repo q2)" "$sha" >"$gen/Q2_IMAGE"
printf '%s:%s\n' "$(repo o1)" "$sha" >"$gen/O1_IMAGE"
printf '%s:%s\n' "$(repo d1)" "$sha" >"$gen/D1_IMAGE"
echo "eks-images: wrote $gen (selfheal-app -> $sha-v1)"
