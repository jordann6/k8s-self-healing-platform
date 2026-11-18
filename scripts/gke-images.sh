#!/usr/bin/env bash
# gke-images.sh: build the images the GKE rows need for linux/amd64, push them
# to the lab's Artifact Registry under the git short SHA, and write the
# generated kustomize component that points both GKE profiles at them
# (gitops/targets/gke/generated, not committed: it names the project).
#
#   selfheal/app:<sha>-v1   the good release   selfheal/app:<sha>-v2   the bad release (row A2)
#   selfheal/gitserver:<sha>
#
# Only the in-cluster rows run on GKE (ADR-0027, Scope), so no Q1 to X1
# images. The repository has immutable tags: a tag that exists is not rebuilt.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/gcp"

registry="$(terraform -chdir="$tf" output -raw registry)"
[[ "$registry" == *-docker.pkg.dev/*/* ]] || { echo "gke-images: unexpected registry '$registry'; has the apply run?" >&2; exit 1; }
host="${registry%%/*}"

sha="$(git -C "$root" rev-parse --short HEAD)"
if [[ -n "$(git -C "$root" status --porcelain -- app)" ]]; then
	echo "gke-images: app/ has uncommitted changes; commit first so the tag names the build" >&2
	exit 1
fi

# A short-lived OAuth token for this push only; no credential helper is
# installed and no key exists.
gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin "https://$host" >/dev/null

push() { # image tag context [build args...]
	local r="$1" t="$2"
	shift 2
	if gcloud artifacts docker images describe "$registry/$r:$t" >/dev/null 2>&1; then
		echo "  $r:$t exists"
		return
	fi
	echo "  $r:$t"
	docker buildx build --platform linux/amd64 --provenance=false -q -t "$registry/$r:$t" --push "$@" >/dev/null
}

echo "gke-images: building for linux/amd64 at $sha"
push app "$sha-v1" --build-arg RELEASE=v1 "$root/app"
push app "$sha-v2" --build-arg RELEASE=v2 "$root/app"
push gitserver "$sha" "$root/app/gitserver"

gen="$root/gitops/targets/gke/generated"
mkdir -p "$gen"
cat >"$gen/kustomization.yaml" <<EOY
# Written by scripts/gke-images.sh at $sha. Not committed: it names this
# project's registry. The runner rewrites the selfheal-app tag for row A2.
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - name: selfheal-app
    newName: $registry/app
    newTag: $sha-v1
EOY
printf '%s\n' "$sha" >"$gen/IMAGE_SHA"
printf '%s/gitserver:%s\n' "$registry" "$sha" >"$gen/GITSERVER_IMAGE"
"$root/scripts/render-gke-profiles.sh" >/dev/null
echo "gke-images: wrote $gen (selfheal-app -> $sha-v1)"
