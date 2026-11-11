#!/usr/bin/env bash
# aks-images.sh: build the lab images for linux/amd64, push them to the lab's
# ACR under the git short SHA, and write the generated kustomize component that
# points both AKS profiles at them and at the lab's Key Vault
# (gitops/targets/aks/generated, not committed: it names this subscription's
# registry and vault).
#
#   selfheal/app:<sha>-v1   the good release   selfheal/app:<sha>-v2   the bad release (row A2)
#   selfheal/gitserver:<sha>                   selfheal/r2worker:<sha> (row R2)
#   selfheal/qworker:<sha> (row Q1)   selfheal/q2:<sha> (row Q2)
#   selfheal/o1:<sha> (row O1)   selfheal/q3:<sha> (row Q3)
#   selfheal/d1:<sha> (row D1 cloud tool image, app/d1/cloud.Dockerfile)
#   selfheal/n1:<sha> and selfheal/n1-capture:<sha> (row N1)
#
# Runs BEFORE aks-platform on AKS (unlike EKS): the platform step applies the
# R2 worker, which needs its image. A tag that already exists is not rebuilt.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tf="$root/infra/azure"

registry="$(terraform -chdir="$tf" output -raw acr_login_server)"
vault_uri="$(terraform -chdir="$tf" output -json runner | jq -r .key_vault_uri)"
[[ "$registry" == *.azurecr.io && "$vault_uri" == https://*.vault.azure.net/ ]] ||
	{ echo "aks-images: unexpected outputs (registry '$registry', vault '$vault_uri'); has the apply run?" >&2; exit 1; }
acr="${registry%%.*}"

sha="$(git -C "$root" rev-parse --short HEAD)"
if [[ -n "$(git -C "$root" status --porcelain -- app)" ]]; then
	echo "aks-images: app/ has uncommitted changes; commit first so the tag names the build" >&2
	exit 1
fi

# Entra token for the registry (no admin user, no password).
az acr login --name "$acr" >/dev/null

push() { # repository tag context [build args...]
	local r="$1" t="$2"
	shift 2
	if az acr repository show --name "$acr" --image "$r:$t" >/dev/null 2>&1; then
		echo "  $r:$t exists"
		return
	fi
	echo "  $r:$t"
	docker buildx build --platform linux/amd64 --provenance=false -q -t "$registry/$r:$t" --push "$@" >/dev/null
}

echo "aks-images: building for linux/amd64 at $sha"
push selfheal/app "$sha-v1" --build-arg RELEASE=v1 "$root/app"
push selfheal/app "$sha-v2" --build-arg RELEASE=v2 "$root/app"
push selfheal/gitserver "$sha" "$root/app/gitserver"
push selfheal/r2worker "$sha" "$root/app/r2worker"
push selfheal/qworker "$sha" "$root/app/qworker"
push selfheal/q2 "$sha" "$root/app/q2"
push selfheal/o1 "$sha" "$root/app/o1"
push selfheal/q3 "$sha" "$root/app/q3"
push selfheal/d1 "$sha" "$root/app/d1" -f "$root/app/d1/cloud.Dockerfile"
push selfheal/n1 "$sha" "$root/app/n1"
push selfheal/n1-capture "$sha" "$root/app/n1-capture"

gen="$root/gitops/targets/aks/generated"
mkdir -p "$gen"
cat >"$gen/kustomization.yaml" <<EOF
# Written by scripts/aks-images.sh at $sha. Not committed: it names this
# subscription's registry and vault. The runner rewrites the selfheal-app tag
# for row A2.
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
images:
  - name: selfheal-app
    newName: $registry/selfheal/app
    newTag: $sha-v1
  - name: selfheal-qworker
    newName: $registry/selfheal/qworker
    newTag: $sha
  - name: selfheal-q2
    newName: $registry/selfheal/q2
    newTag: $sha
  - name: selfheal-o1
    newName: $registry/selfheal/o1
    newTag: $sha
  - name: selfheal-q3
    newName: $registry/selfheal/q3
    newTag: $sha
  - name: selfheal-d1-cloud
    newName: $registry/selfheal/d1
    newTag: $sha
  - name: selfheal-n1
    newName: $registry/selfheal/n1
    newTag: $sha
  - name: selfheal-n1-capture
    newName: $registry/selfheal/n1-capture
    newTag: $sha
patches:
  - target: {kind: SecretStore, name: backend}
    patch: |-
      - op: add
        path: /spec/provider/azurekv/vaultUrl
        value: $vault_uri
EOF
printf '%s\n' "$sha" >"$gen/IMAGE_SHA"
printf '%s/selfheal/gitserver:%s\n' "$registry" "$sha" >"$gen/GITSERVER_IMAGE"
printf '%s/selfheal/r2worker:%s\n' "$registry" "$sha" >"$gen/RESPONDER_IMAGE"
printf '%s/selfheal/qworker:%s\n' "$registry" "$sha" >"$gen/QWORKER_IMAGE"
printf '%s/selfheal/q2:%s\n' "$registry" "$sha" >"$gen/Q2_IMAGE"
printf '%s/selfheal/o1:%s\n' "$registry" "$sha" >"$gen/O1_IMAGE"
printf '%s/selfheal/d1:%s\n' "$registry" "$sha" >"$gen/D1_IMAGE"
"$root/scripts/render-aks-profiles.sh" >/dev/null
echo "aks-images: wrote $gen (selfheal-app -> $sha-v1)"
